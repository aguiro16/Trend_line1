import os
import json
import time
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
SIGNAL_LOG     = "signals_log.csv"
ACTIVE_LOG     = "active_signals.json"
IMPROVE_LOG    = "improvement_history.json"
KSA            = ZoneInfo("Asia/Riyadh")
BASE           = "https://api.binance.com"

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

# ── Binance chart data ────────────────────────────────────────────────────────
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
    return dx.ewm(span=period, adjust=False).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def calc_vwap(df):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()

# ── Chart analysis for a losing signal ───────────────────────────────────────
def analyze_chart(signal):
    sym       = signal["symbol"]
    strategy  = signal["strategy"]
    entry     = signal["entry"]
    sl        = signal["sl"]
    tp1       = signal["tp1"]
    tp2       = signal["tp2"]
    direction = signal["direction"]

    try:
        df4h = get_klines(sym, "4h", 200)
        df1h = get_klines(sym, "1h", 200)

        adx4      = calc_adx(df4h).iloc[-1]
        ema50_4h  = ema(df4h["close"], 50).iloc[-1]
        ema200_4h = ema(df4h["close"], 200).iloc[-1]
        rsi4      = calc_rsi(df4h["close"]).iloc[-1]

        adx1      = calc_adx(df1h).iloc[-1]
        ema50_1h  = ema(df1h["close"], 50).iloc[-1]
        ema200_1h = ema(df1h["close"], 200).iloc[-1]
        rsi1      = calc_rsi(df1h["close"]).iloc[-1]
        vwap1     = calc_vwap(df1h).iloc[-1]
        vol_avg   = df1h["volume"].rolling(20).mean().iloc[-1]
        vol_last  = df1h["volume"].iloc[-1]
        vol_ratio = vol_last / vol_avg if vol_avg > 0 else 1.0

        recent4h  = df4h.tail(20)
        high20    = recent4h["high"].max()
        low20     = recent4h["low"].min()
        range_pct = (high20 - low20) / low20 * 100

        risk = abs(entry - sl)
        rr   = abs(tp2 - entry) / risk if risk > 0 else 0

        return {
            "symbol":             sym,
            "strategy":           strategy,
            "direction":          direction,
            "entry":              entry,
            "sl":                 sl,
            "tp1":                tp1,
            "tp2":                tp2,
            "result":             signal.get("result", "SL"),
            "pnl_pct":            signal.get("pnl_pct", 0),
            "adx_4h":             round(adx4, 2),
            "ema50_4h":           round(ema50_4h, 4),
            "ema200_4h":          round(ema200_4h, 4),
            "rsi_4h":             round(rsi4, 2),
            "adx_1h":             round(adx1, 2),
            "ema50_1h":           round(ema50_1h, 4),
            "ema200_1h":          round(ema200_1h, 4),
            "rsi_1h":             round(rsi1, 2),
            "vwap_1h":            round(vwap1, 4),
            "vol_ratio":          round(vol_ratio, 2),
            "range_20c_pct":      round(range_pct, 2),
            "risk_reward":        round(rr, 2),
            "price_vs_ema50_4h":  round((entry - ema50_4h) / ema50_4h * 100, 2),
            "price_vs_ema200_4h": round((entry - ema200_4h) / ema200_4h * 100, 2),
            "price_vs_vwap_1h":   round((entry - vwap1) / vwap1 * 100, 2),
        }
    except Exception as e:
        log.warning(f"Chart analysis failed for {sym}: {e}")
        return None

# ── Build prompt for Claude ───────────────────────────────────────────────────
def build_analysis_prompt(losing_signals_data):
    signals_text = ""
    for i, s in enumerate(losing_signals_data, 1):
        signals_text += f"""
--- إشارة خاسرة #{i} ---
العملة: {s['symbol']}
الاستراتيجية: {s['strategy']}
الاتجاه: {s['direction']}
سعر الدخول: {s['entry']}
وقف الخسارة: {s['sl']}
TP1: {s['tp1']} | TP2: {s['tp2']}
النتيجة: {s['result']} | الخسارة: {s['pnl_pct']}%

مؤشرات وقت الإشارة:
- ADX (4H): {s['adx_4h']} | ADX (1H): {s['adx_1h']}
- EMA50/EMA200 (4H): {s['ema50_4h']} / {s['ema200_4h']}
- EMA50/EMA200 (1H): {s['ema50_1h']} / {s['ema200_1h']}
- RSI (4H): {s['rsi_4h']} | RSI (1H): {s['rsi_1h']}
- VWAP (1H): {s['vwap_1h']}
- نسبة الحجم: {s['vol_ratio']}x من المتوسط
- نطاق آخر 20 شمعة (4H): {s['range_20c_pct']}%
- نسبة المخاطرة/العائد: 1:{s['risk_reward']}
- السعر vs EMA50 (4H): {s['price_vs_ema50_4h']}%
- السعر vs EMA200 (4H): {s['price_vs_ema200_4h']}%
- السعر vs VWAP (1H): {s['price_vs_vwap_1h']}%
"""

    return f"""أنت خبير تحليل فني محترف متخصص في أسواق العملات الرقمية.

لديك بوت تداول آلي يستخدم 3 استراتيجيات:
1. Trend Breakout Long: يدخل عند اختراق مقاومة في سوق صاعد (ADX>25 + EMA50>EMA200)
2. VWAP Short: يدخل عند ارتداد من VWAP في سوق هابط (ADX>25 + EMA50<EMA200)
3. Fibonacci OTE: يدخل عند مستويات فيبوناتشي 61.8%-78.6% في سوق عرضي (ADX<25)

فلاتر الدخول الحالية:
- حجم تداول 24h > 200 مليون دولار
- تغير السعر 24h > 3%
- ADX > 20 كفلتر مبدئي
- حجم الشمعة > متوسط 20 شمعة
- RSI بين 40-60 (للاستراتيجيات 2 و 3)

هذه هي الإشارات التي سجلت خسارة هذا الأسبوع مع بياناتها الفنية الكاملة:

{signals_text}

المطلوب منك:
1. تشخيص الخطأ لكل إشارة خاسرة: ما السبب الحقيقي للخسارة؟
2. الأنماط المتكررة: هل هناك أخطاء مشتركة بين الإشارات الخاسرة؟
3. اقتراحات تطوير محددة يمكن تطبيقها في الكود:
   - هل يجب تغيير حدود ADX؟
   - هل يجب إضافة فلاتر جديدة؟
   - هل حجم وقف الخسارة مناسب؟
   - هل نسبة المخاطرة/العائد صحيحة؟
   - هل يجب استبعاد حالات معينة؟
4. الأولوية: رتب التعديلات من الأهم للأقل أهمية.

اكتب تحليلك بالعربية بشكل واضح ومنظم مع أرقام وأمثلة من البيانات المعطاة.
"""

# ── Call Claude API ───────────────────────────────────────────────────────────
def ask_claude(prompt):
    url     = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key":         ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    body = {
        "model":      "claude-opus-4-5",
        "max_tokens": 4000,
        "messages":   [{"role": "user", "content": prompt}],
    }
    r = requests.post(url, headers=headers, json=body, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data["content"][0]["text"]

# ── Save improvement history ──────────────────────────────────────────────────
def save_improvement(analysis_text, losing_count, week_str):
    history = []
    if os.path.exists(IMPROVE_LOG):
        with open(IMPROVE_LOG) as f:
            history = json.load(f)
    history.append({
        "week":         week_str,
        "losing_count": losing_count,
        "analysis":     analysis_text,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })
    with open(IMPROVE_LOG, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ── Main improvement function ─────────────────────────────────────────────────
def run_improvement_analysis():
    global TELEGRAM_TOKEN, TELEGRAM_CHAT, ANTHROPIC_KEY
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")
    ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")

    log.info("=== Starting weekly improvement analysis ===")

    if not os.path.exists(ACTIVE_LOG):
        log.info("No active signals log found.")
        return

    with open(ACTIVE_LOG) as f:
        active = json.load(f)

    now      = datetime.now(KSA)
    week_ago = (now - timedelta(days=7)).date()
    week_str = str(now.date())

    losing = [
        s for s in active.values()
        if s.get("status") == "CLOSED"
        and "SL" in (s.get("result") or "")
        and s.get("closed_at", "")[:10] >= str(week_ago)
    ]

    if not losing:
        send_telegram(
            "🤖 <b>تحليل التطوير الأسبوعي</b>\n\n"
            "✅ لا توجد إشارات خاسرة هذا الأسبوع!\n"
            "البوت يعمل بشكل ممتاز 🎉"
        )
        log.info("No losing signals this week.")
        return

    send_telegram(
        f"🔬 <b>جاري تحليل {len(losing)} إشارة خاسرة...</b>\n"
        f"سيصل التقرير خلال دقيقة واحدة."
    )

    losing_data = []
    for s in losing:
        data = analyze_chart(s)
        if data:
            losing_data.append(data)
        time.sleep(1)

    if not losing_data:
        send_telegram("⚠️ تعذر جلب بيانات الإشارات الخاسرة.")
        return

    prompt   = build_analysis_prompt(losing_data)
    analysis = ask_claude(prompt)

    save_improvement(analysis, len(losing_data), week_str)

    header = (
        f"🧠 <b>تقرير التطوير الأسبوعي — {week_str}</b>\n"
        f"📉 إشارات خاسرة محللة: {len(losing_data)}\n"
        f"{'─'*30}\n\n"
    )

    full_text  = header + analysis
    chunk_size = 3800
    chunks     = [full_text[i:i+chunk_size] for i in range(0, len(full_text), chunk_size)]

    for i, chunk in enumerate(chunks):
        if i > 0:
            chunk = "<i>(تابع...)</i>\n\n" + chunk
        send_telegram(chunk)
        time.sleep(2)

    log.info(f"Improvement analysis sent — {len(losing_data)} signals analyzed")


# ── Run standalone ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_improvement_analysis()
