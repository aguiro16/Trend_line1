import os
import time
import logging
import pandas as pd
import numpy as np
import requests
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from flask import Flask
from threading import Thread
from improver import run_improvement_analysis

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT   = os.environ["TELEGRAM_CHAT_ID"]
SCAN_INTERVAL   = 4 * 3600
SIGNAL_COOLDOWN = 8 * 3600
TOP_N           = 60
MIN_VOLUME_24H  = 200_000_000
MIN_CHANGE_24H  = 3.0
ADX_TREND_MIN   = 25
ADX_FILTER_MIN  = 20
OTE_LOW         = 0.618
OTE_HIGH        = 0.786
STABLES         = {"USDT","BUSD","USDC","DAI","TUSD","FDUSD","USDP","USDD"}
SIGNAL_LOG      = "signals_log.csv"
ACTIVE_LOG      = "active_signals.json"
COUNTER_FILE    = "signal_counter.json"
KSA             = ZoneInfo("Asia/Riyadh")

# ── Signal counter ────────────────────────────────────────────────────────────
def get_next_signal_number():
    if os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE) as f:
            data = json.load(f)
    else:
        data = {"counter": 0}
    data["counter"] += 1
    with open(COUNTER_FILE, "w") as f:
        json.dump(data, f)
    return data["counter"]

# ── Active signals tracker ────────────────────────────────────────────────────
def load_active_signals():
    if os.path.exists(ACTIVE_LOG):
        with open(ACTIVE_LOG) as f:
            return json.load(f)
    return {}

def save_active_signals(signals):
    with open(ACTIVE_LOG, "w") as f:
        json.dump(signals, f, indent=2)

def add_active_signal(sig_num, sym, sig, regime):
    active = load_active_signals()
    active[str(sig_num)] = {
        "number":    sig_num,
        "symbol":    sym,
        "direction": sig["direction"],
        "strategy":  sig["strategy"],
        "regime":    regime,
        "entry":     sig["entry"],
        "sl":        sig["sl"],
        "tp1":       sig["tp1"],
        "tp2":       sig["tp2"],
        "adx":       sig["adx"],
        "status":    "OPEN",
        "result":    None,
        "pnl_pct":   None,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None,
    }
    save_active_signals(active)

# ── Binance helpers ───────────────────────────────────────────────────────────
BASE = "https://api.binance.com"

def get_top_symbols(n=60):
    r = requests.get(f"{BASE}/api/v3/ticker/24hr", timeout=15)
    r.raise_for_status()
    data = r.json()
    usdt = [d for d in data
            if d["symbol"].endswith("USDT")
            and d["symbol"][:-4] not in STABLES
            and float(d["quoteVolume"]) > 0]
    usdt.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return usdt[:n]

def get_klines(symbol, interval, limit=200):
    r = requests.get(f"{BASE}/api/v3/klines",
                     params={"symbol": symbol, "interval": interval, "limit": limit},
                     timeout=15)
    r.raise_for_status()
    raw = r.json()
    df = pd.DataFrame(raw, columns=[
        "time","open","high","low","close","volume",
        "close_time","qv","trades","tbbv","tbqv","ignore"])
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df

def get_current_price(symbol):
    r = requests.get(f"{BASE}/api/v3/ticker/price",
                     params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])

# ── Technical indicators ──────────────────────────────────────────────────────
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_adx(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l,
                    (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)
    dm_plus  = ((h - h.shift()) > (l.shift() - l)).astype(float) * (h - h.shift()).clip(lower=0)
    dm_minus = ((l.shift() - l) > (h - h.shift())).astype(float) * (l.shift() - l).clip(lower=0)
    atr  = tr.ewm(span=period, adjust=False).mean()
    di_p = 100 * dm_plus.ewm(span=period, adjust=False).mean() / atr
    di_m = 100 * dm_minus.ewm(span=period, adjust=False).mean() / atr
    dx   = (100 * (di_p - di_m).abs() / (di_p + di_m)).fillna(0)
    adx  = dx.ewm(span=period, adjust=False).mean()
    return adx, di_p, di_m

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def calc_vwap(df):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()

def vol_above_avg(df, period=20):
    return df["volume"].iloc[-1] > df["volume"].rolling(period).mean().iloc[-1]

# ── Swing detection ───────────────────────────────────────────────────────────
def find_swing_high(df, lookback=5):
    highs = df["high"].values
    for i in range(len(highs)-1, lookback-1, -1):
        if highs[i] == max(highs[i-lookback:i+1]):
            return highs[i], i
    return None, None

def find_swing_low(df, lookback=5):
    lows = df["low"].values
    for i in range(len(lows)-1, lookback-1, -1):
        if lows[i] == min(lows[i-lookback:i+1]):
            return lows[i], i
    return None, None

# ── Strategy checks ───────────────────────────────────────────────────────────
def check_trend_breakout_long(sym):
    try:
        df4h = get_klines(sym, "4h", 100)
        df1h = get_klines(sym, "1h", 100)
        if len(df4h) < 50 or len(df1h) < 50:
            return None
        recent = df4h.tail(10)
        hh = recent["high"].is_monotonic_increasing
        hl = recent["low"].diff().dropna().gt(0).sum() >= 6
        if not (hh or hl):
            return None
        resistance = df4h["high"].tail(20).max()
        last_close = df1h["close"].iloc[-1]
        prev_close = df1h["close"].iloc[-2]
        if not (prev_close < resistance <= last_close):
            return None
        if not vol_above_avg(df1h):
            return None
        entry = last_close
        sl    = df1h["low"].tail(3).min()
        risk  = entry - sl
        if risk <= 0:
            return None
        tp1 = entry + 1.5 * risk
        tp2 = entry + 3.0 * risk
        adx4, _, _ = calc_adx(df4h)
        return {"direction":"LONG","strategy":"Trend Breakout",
                "entry":entry,"sl":sl,"tp1":tp1,"tp2":tp2,
                "adx":round(adx4.iloc[-1],1)}
    except Exception as e:
        log.warning(f"TrendBreakout {sym}: {e}")
        return None

def check_vwap_short(sym):
    try:
        df1h = get_klines(sym, "1h", 100)
        if len(df1h) < 30:
            return None
        vwap       = calc_vwap(df1h)
        rsi        = calc_rsi(df1h["close"])
        last_close = df1h["close"].iloc[-1]
        prev_close = df1h["close"].iloc[-2]
        last_vwap  = vwap.iloc[-1]
        if not (prev_close > last_vwap > last_close):
            return None
        if not (40 <= rsi.iloc[-1] <= 60):
            return None
        if not vol_above_avg(df1h):
            return None
        entry = last_close
        sl    = df1h["high"].tail(3).max()
        risk  = sl - entry
        if risk <= 0:
            return None
        tp1 = entry - 1.5 * risk
        tp2 = entry - 3.0 * risk
        adx1, _, _ = calc_adx(df1h)
        return {"direction":"SHORT","strategy":"VWAP Short",
                "entry":entry,"sl":sl,"tp1":tp1,"tp2":tp2,
                "adx":round(adx1.iloc[-1],1)}
    except Exception as e:
        log.warning(f"VWAPShort {sym}: {e}")
        return None

def check_fibonacci_ote(sym, regime_direction):
    try:
        df4h = get_klines(sym, "4h", 100)
        df1h = get_klines(sym, "1h", 100)
        if len(df4h) < 50 or len(df1h) < 50:
            return None
        ema50  = ema(df1h["close"], 50).iloc[-1]
        ema200 = ema(df1h["close"], 200).iloc[-1]
        rsi    = calc_rsi(df1h["close"]).iloc[-1]
        if not (40 <= rsi <= 60):
            return None
        if not vol_above_avg(df1h):
            return None
        swing_high, _ = find_swing_high(df4h)
        swing_low,  _ = find_swing_low(df4h)
        if swing_high is None or swing_low is None:
            return None
        if abs(swing_high - swing_low) / swing_low < 0.05:
            return None
        last = df1h["close"].iloc[-1]
        if ema50 > ema200:
            fib618 = swing_high - OTE_LOW  * (swing_high - swing_low)
            fib786 = swing_high - OTE_HIGH * (swing_high - swing_low)
            if not (fib786 <= last <= fib618):
                return None
            entry = last
            sl    = swing_low * 0.998
            risk  = entry - sl
            if risk <= 0:
                return None
            tp1 = entry + 1.5 * risk
            tp2 = entry + 3.0 * risk
            direction = "LONG"
        else:
            fib618 = swing_low + OTE_LOW  * (swing_high - swing_low)
            fib786 = swing_low + OTE_HIGH * (swing_high - swing_low)
            if not (fib618 <= last <= fib786):
                return None
            entry = last
            sl    = swing_high * 1.002
            risk  = sl - entry
            if risk <= 0:
                return None
            tp1 = entry - 1.5 * risk
            tp2 = entry - 3.0 * risk
            direction = "SHORT"
        adx4, _, _ = calc_adx(df4h)
        return {"direction":direction,"strategy":"Fibonacci OTE",
                "entry":entry,"sl":sl,"tp1":tp1,"tp2":tp2,
                "adx":round(adx4.iloc[-1],1)}
    except Exception as e:
        log.warning(f"FibOTE {sym}: {e}")
        return None

# ── Regime detection ──────────────────────────────────────────────────────────
def detect_regime(sym):
    try:
        df4h = get_klines(sym, "4h", 220)
        if len(df4h) < 210:
            return None, None
        adx_s, _, _ = calc_adx(df4h)
        adx_val = adx_s.iloc[-1]
        e50  = ema(df4h["close"], 50).iloc[-1]
        e200 = ema(df4h["close"], 200).iloc[-1]
        if adx_val >= ADX_TREND_MIN:
            regime = "BULLISH" if e50 > e200 else "BEARISH"
        else:
            regime = "RANGING"
        return regime, round(adx_val, 1)
    except Exception as e:
        log.warning(f"Regime {sym}: {e}")
        return None, None

# ── Signal log ────────────────────────────────────────────────────────────────
recent_signals: dict[str, float] = {}

def already_sent(sym):
    t = recent_signals.get(sym, 0)
    return (time.time() - t) < SIGNAL_COOLDOWN

def mark_sent(sym):
    recent_signals[sym] = time.time()

def log_signal(sig_num, sym, sig, regime):
    row = {
        "signal_number": sig_num,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "symbol":        sym,
        "direction":     sig["direction"],
        "strategy":      sig["strategy"],
        "regime":        regime,
        "entry":         sig["entry"],
        "sl":            sig["sl"],
        "tp1":           sig["tp1"],
        "tp2":           sig["tp2"],
        "adx":           sig["adx"],
        "result":        "OPEN",
        "pnl_pct":       "",
    }
    df     = pd.DataFrame([row])
    header = not os.path.exists(SIGNAL_LOG)
    df.to_csv(SIGNAL_LOG, mode="a", header=header, index=False)

def update_signal_result_in_csv(sig_num, result, pnl_pct):
    if not os.path.exists(SIGNAL_LOG):
        return
    df = pd.read_csv(SIGNAL_LOG)
    mask = df["signal_number"] == sig_num
    df.loc[mask, "result"]  = result
    df.loc[mask, "pnl_pct"] = pnl_pct
    df.to_csv(SIGNAL_LOG, index=False)

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(url,
                              json={"chat_id": TELEGRAM_CHAT,
                                    "text": text,
                                    "parse_mode": "HTML"},
                              timeout=10)
            r.raise_for_status()
            return
        except Exception as e:
            log.warning(f"Telegram attempt {attempt+1}: {e}")
            time.sleep(5)

def format_signal(sig_num, sym, sig, regime, adx_val):
    emoji   = "🟢" if sig["direction"] == "LONG" else "🔴"
    r_emoji = "📈" if regime == "BULLISH" else ("📉" if regime == "BEARISH" else "➡️")
    e   = sig["entry"]
    sl  = sig["sl"]
    t1  = sig["tp1"]
    t2  = sig["tp2"]
    sl_pct = abs(sl - e) / e * 100
    t1_pct = abs(t1 - e) / e * 100
    t2_pct = abs(t2 - e) / e * 100
    sl_sign = "-" if sig["direction"] == "LONG" else "+"
    t_sign  = "+" if sig["direction"] == "LONG" else "-"
    return (
        f"{emoji} <b>إشارة #{sig_num:03d} — {sig['direction']}</b>\n"
        f"💲 <b>العملة:</b> {sym}\n"
        f"📊 <b>الاستراتيجية:</b> {sig['strategy']}\n"
        f"🌍 <b>السوق:</b> {regime} {r_emoji}\n"
        f"💵 <b>الدخول:</b> ${e:,.4f}\n"
        f"🔴 <b>وقف الخسارة:</b> ${sl:,.4f} ({sl_sign}{sl_pct:.2f}%)\n"
        f"🎯 <b>TP1:</b> ${t1:,.4f} ({t_sign}{t1_pct:.2f}%) — أغلق 50%\n"
        f"🏆 <b>TP2:</b> ${t2:,.4f} ({t_sign}{t2_pct:.2f}%) — أغلق 50%\n"
        f"📈 <b>ADX:</b> {adx_val}\n"
        f"⏰ <b>الإطار:</b> 4H / 1H\n"
        f"⚠️ <b>المخاطرة:</b> 1% من رأس المال"
    )

def format_result_message(s):
    result = s["result"]
    pnl    = s["pnl_pct"]
    emoji  = "✅" if "TP" in result else "❌"
    sign   = "+" if pnl > 0 else ""
    return (
        f"{emoji} <b>نتيجة إشارة #{s['number']:03d}</b>\n"
        f"💲 <b>العملة:</b> {s['symbol']}\n"
        f"📊 <b>الاستراتيجية:</b> {s['strategy']}\n"
        f"🏁 <b>النتيجة:</b> {result}\n"
        f"💰 <b>الربح/الخسارة:</b> {sign}{pnl:.2f}%\n"
        f"📅 <b>أُغلقت:</b> {s['closed_at'][:16]} UTC"
    )

def format_summary(scanned, passed, longs, shorts, next_time):
    return (
        f"🔍 <b>انتهى الفحص</b> — {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n"
        f"📊 عدد العملات المفحوصة: {scanned}\n"
        f"✅ اجتازت الفلتر: {passed}\n"
        f"🟢 إشارات شراء: {longs}\n"
        f"🔴 إشارات بيع: {shorts}\n"
        f"⏭️ الفحص القادم: {next_time} UTC"
    )

# ── Result checker ────────────────────────────────────────────────────────────
def check_open_signals():
    active  = load_active_signals()
    updated = False
    for key, s in list(active.items()):
        if s["status"] != "OPEN":
            continue
        try:
            price = get_current_price(s["symbol"])
            entry = s["entry"]
            sl    = s["sl"]
            tp1   = s["tp1"]
            tp2   = s["tp2"]
            result = None
            pnl    = None
            if s["direction"] == "LONG":
                if price >= tp2:
                    result = "TP2 ✅✅"
                    pnl    = (tp2 - entry) / entry * 100
                elif price >= tp1:
                    result = "TP1 ✅"
                    pnl    = (tp1 - entry) / entry * 100
                elif price <= sl:
                    result = "SL ❌"
                    pnl    = (sl - entry) / entry * 100
            else:
                if price <= tp2:
                    result = "TP2 ✅✅"
                    pnl    = (entry - tp2) / entry * 100
                elif price <= tp1:
                    result = "TP1 ✅"
                    pnl    = (entry - tp1) / entry * 100
                elif price >= sl:
                    result = "SL ❌"
                    pnl    = (entry - sl) / entry * 100
            if result:
                s["status"]    = "CLOSED"
                s["result"]    = result
                s["pnl_pct"]   = round(pnl, 2)
                s["closed_at"] = datetime.now(timezone.utc).isoformat()
                active[key]    = s
                updated        = True
                send_telegram(format_result_message(s))
                update_signal_result_in_csv(s["number"], result, round(pnl, 2))
                log.info(f"Signal #{s['number']} closed: {result} {pnl:.2f}%")
        except Exception as e:
            log.warning(f"Result check {s['symbol']}: {e}")
    if updated:
        save_active_signals(active)

# ── Daily report ──────────────────────────────────────────────────────────────
def build_daily_report():
    active = load_active_signals()
    now    = datetime.now(KSA)
    today  = now.date()
    closed = [s for s in active.values()
              if s["status"] == "CLOSED"
              and s.get("closed_at","")[:10] == str(today)]
    open_s = [s for s in active.values() if s["status"] == "OPEN"]
    wins   = [s for s in closed if "TP" in (s.get("result") or "")]
    losses = [s for s in closed if "SL"  in (s.get("result") or "")]
    total_pnl = sum(s.get("pnl_pct",0) or 0 for s in closed)
    lines  = [f"📅 <b>التقرير اليومي — {today}</b>\n"]
    lines.append(f"✅ رابحة: {len(wins)}  ❌ خاسرة: {len(losses)}")
    lines.append(f"💰 صافي الربح/الخسارة: {'+' if total_pnl>=0 else ''}{total_pnl:.2f}%\n")
    if closed:
        lines.append("<b>📋 الإشارات المغلقة اليوم:</b>")
        for s in closed:
            p = s.get("pnl_pct", 0) or 0
            lines.append(f"  #{s['number']:03d} {s['symbol']} → {s['result']} ({'+' if p>=0 else ''}{p:.2f}%)")
    if open_s:
        lines.append(f"\n<b>⏳ إشارات مفتوحة ({len(open_s)}):</b>")
        for s in open_s:
            try:
                price = get_current_price(s["symbol"])
                pnl   = (price - s["entry"]) / s["entry"] * 100
                if s["direction"] == "SHORT":
                    pnl = -pnl
                lines.append(f"  #{s['number']:03d} {s['symbol']} {s['direction']} | الآن: ${price:,.4f} ({'+' if pnl>=0 else ''}{pnl:.2f}%)")
            except:
                lines.append(f"  #{s['number']:03d} {s['symbol']} {s['direction']}")
    send_telegram("\n".join(lines))

# ── Weekly report ─────────────────────────────────────────────────────────────
def build_weekly_report():
    active   = load_active_signals()
    now      = datetime.now(KSA)
    week_ago = (now - timedelta(days=7)).date()
    all_closed = [s for s in active.values()
                  if s["status"] == "CLOSED"
                  and s.get("closed_at","")[:10] >= str(week_ago)]
    wins   = [s for s in all_closed if "TP" in (s.get("result") or "")]
    losses = [s for s in all_closed if "SL"  in (s.get("result") or "")]
    tp2w   = [s for s in all_closed if "TP2" in (s.get("result") or "")]
    tp1w   = [s for s in all_closed if "TP1" in (s.get("result") or "")]
    total  = len(all_closed)
    wr     = (len(wins) / total * 100) if total else 0
    total_pnl = sum(s.get("pnl_pct",0) or 0 for s in all_closed)
    lines  = [f"📊 <b>التقرير الأسبوعي — {week_ago} ← {now.date()}</b>\n"]
    lines.append(f"📈 إجمالي الإشارات: {total}")
    lines.append(f"✅ رابحة: {len(wins)} | ❌ خاسرة: {len(losses)}")
    lines.append(f"🎯 TP1: {len(tp1w)} | 🏆 TP2: {len(tp2w)}")
    lines.append(f"📉 نسبة النجاح: {wr:.1f}%")
    lines.append(f"💰 صافي الأسبوع: {'+' if total_pnl>=0 else ''}{total_pnl:.2f}%\n")
    if all_closed:
        lines.append("<b>📋 تفاصيل الإشارات:</b>")
        for s in all_closed:
            p = s.get("pnl_pct", 0) or 0
            lines.append(f"  #{s['number']:03d} {s['symbol']} {s['direction']} → {s['result']} ({'+' if p>=0 else ''}{p:.2f}%)")
    send_telegram("\n".join(lines))

# ── Report scheduler ──────────────────────────────────────────────────────────
def report_scheduler():
    while True:
        now = datetime.now(KSA)

        # Daily at 11:00 KSA
        if now.hour == 11 and now.minute == 0:
            try:
                build_daily_report()
            except Exception as e:
                log.error(f"Daily report error: {e}")

        # Weekly Saturday 11:00 KSA
        if now.weekday() == 5 and now.hour == 11 and now.minute == 0:
            try:
                build_weekly_report()
                time.sleep(30)
                run_improvement_analysis()
            except Exception as e:
                log.error(f"Weekly report/improvement error: {e}")

        time.sleep(60)

# ── Main scan ─────────────────────────────────────────────────────────────────
def run_scan():
    log.info("=== Starting scan ===")
    longs = shorts = passed = 0
    check_open_signals()
    try:
        top = get_top_symbols(TOP_N)
    except Exception as e:
        log.error(f"Failed to fetch symbols: {e}")
        return
    scanned = len(top)
    for item in top:
        sym   = item["symbol"]
        vol24 = float(item["quoteVolume"])
        chg24 = abs(float(item["priceChangePercent"]))
        if vol24 < MIN_VOLUME_24H or chg24 < MIN_CHANGE_24H:
            continue
        try:
            df4h = get_klines(sym, "4h", 50)
            adx_s, _, _ = calc_adx(df4h)
            if adx_s.iloc[-1] < ADX_FILTER_MIN:
                continue
        except:
            continue
        passed += 1
        if already_sent(sym):
            continue
        regime, adx_val = detect_regime(sym)
        if regime is None:
            continue
        sig = None
        if regime == "BULLISH":
            sig = check_trend_breakout_long(sym)
        elif regime == "BEARISH":
            sig = check_vwap_short(sym)
        elif regime == "RANGING":
            sig = check_fibonacci_ote(sym, regime)
        if sig is None:
            continue
        sig_num = get_next_signal_number()
        msg = format_signal(sig_num, sym, sig, regime, adx_val)
        send_telegram(msg)
        log_signal(sig_num, sym, sig, regime)
        add_active_signal(sig_num, sym, sig, regime)
        mark_sent(sym)
        if sig["direction"] == "LONG":
            longs += 1
        else:
            shorts += 1
        time.sleep(1)
    next_scan = datetime.fromtimestamp(
        time.time() + SCAN_INTERVAL, tz=timezone.utc
    ).strftime("%H:%M")
    summary = format_summary(scanned, passed, longs, shorts, next_scan)
    send_telegram(summary)
    log.info(f"Scan done — {longs} longs, {shorts} shorts, {passed} passed filter")

# ── Scan scheduler ────────────────────────────────────────────────────────────
def scan_scheduler():
    while True:
        try:
            run_scan()
        except Exception as e:
            log.error(f"Scan error: {e}")
        time.sleep(SCAN_INTERVAL)

# ── Flask keep-alive ──────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def health():
    return "Bot is running ✅", 200

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Bot starting...")
    send_telegram("🤖 <b>Trading Signal Bot Started</b>\n"
                  "فحص أعلى 60 عملة كل 4 ساعات\n"
                  "الاستراتيجيات: Trend Breakout | VWAP Short | Fibonacci OTE\n"
                  "📅 تقرير يومي الساعة 11:00 صباحاً\n"
                  "📊 تقرير أسبوعي + تحليل تطوير كل سبت 11:00 صباحاً")
    Thread(target=scan_scheduler, daemon=True).start()
    Thread(target=report_scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
