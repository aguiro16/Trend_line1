import requests
import pandas as pd
from datetime import datetime, timedelta
from config import MASSIVE_API_KEY, MASSIVE_BASE_URL
from database import is_duplicate_signal

MIN_RR = 1.5

TOP_STOCKS = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","AVGO","JPM","V",
    "UNH","XOM","LLY","MA","JNJ","PG","HD","MRK","COST","ABBV",
    "CRM","BAC","NFLX","CVX","WMT","KO","PEP","TMO","MCD","CSCO",
    "AMD","ADBE","ACN","LIN","ABT","DHR","NKE","TXN","NEE","UPS",
    "QCOM","PM","AMGN","LOW","MS","GS","INTU","CAT","RTX","SPGI",
    "BLK","ISRG","SYK","NOW","ADP","BKNG","GILD","ADI","AMAT","MU"
]

def get_klines(symbol, multiplier, timespan, limit=200):
    try:
        to_date = datetime.utcnow().strftime("%Y-%m-%d")
        if timespan == "day":
            from_date = (datetime.utcnow() - timedelta(days=limit * 2)).strftime("%Y-%m-%d")
        elif timespan == "hour":
            from_date = (datetime.utcnow() - timedelta(hours=limit * multiplier * 2)).strftime("%Y-%m-%d")
        else:
            from_date = (datetime.utcnow() - timedelta(minutes=limit * multiplier * 2)).strftime("%Y-%m-%d")

        url = f"{MASSIVE_BASE_URL}/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
        params = {"adjusted": "true", "sort": "asc", "limit": 50000}
        headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()

        if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
            return pd.DataFrame()

        df = pd.DataFrame(data["results"])
        df.rename(columns={"o": "open", "h": "high", "l": "low",
                            "c": "close", "v": "volume", "t": "time"}, inplace=True)
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        df.set_index("time", inplace=True)
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        return df.tail(limit)

    except Exception as e:
        print(f"Klines error {symbol} {multiplier}{timespan}: {e}")
        return pd.DataFrame()

def get_trend(df_4h):
    if len(df_4h) < 51:
        return None
    ema50 = df_4h["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    last  = df_4h["close"].iloc[-1]
    if last > ema50:
        return "LONG"
    if last < ema50:
        return "SHORT"
    return None

def find_swing_points(df_1h, window=5):
    highs = df_1h["high"].values
    lows  = df_1h["low"].values
    n     = len(df_1h)
    sh = sl = None
    for i in range(window, n - window):
        is_sh = (all(highs[i] >= highs[i-j] for j in range(1, window+1)) and
                 all(highs[i] >= highs[i+j] for j in range(1, window+1)))
        if is_sh and (sh is None or highs[i] > sh):
            sh = highs[i]
        is_sl = (all(lows[i] <= lows[i-j] for j in range(1, window+1)) and
                 all(lows[i] <= lows[i+j] for j in range(1, window+1)))
        if is_sl and (sl is None or lows[i] < sl):
            sl = lows[i]
    return sh, sl

def calc_fib_levels(swing_high, swing_low, direction):
    diff = swing_high - swing_low
    if diff <= 0:
        return None
    if direction == "LONG":
        return {
            "ote_low":  swing_high - 0.786 * diff,
            "ote_high": swing_high - 0.618 * diff,
            "tp1":      swing_high - 0.618 * diff,
            "tp2":      swing_low  + diff * 0.764,
            "tp3":      swing_high,
            "sl":       swing_low * 0.999,
        }
    else:
        return {
            "ote_low":  swing_low + 0.618 * diff,
            "ote_high": swing_low + 0.786 * diff,
            "tp1":      swing_low + 0.618 * diff,
            "tp2":      swing_high - diff * 0.764,
            "tp3":      swing_low,
            "sl":       swing_high * 1.001,
        }

def in_ote(price, fib):
    low  = min(fib["ote_low"], fib["ote_high"])
    high = max(fib["ote_low"], fib["ote_high"])
    return low <= price <= high

def detect_bos(df_15m, direction):
    if len(df_15m) < 6:
        return False
    prev = df_15m.iloc[-6:-1]
    last = df_15m["close"].iloc[-1]
    if direction == "LONG":
        return last > prev["high"].max()
    else:
        return last < prev["low"].min()

def build_tv_url(symbol):
    return f"https://www.tradingview.com/chart/?symbol=NASDAQ:{symbol}&interval=60"

def is_market_open():
    now_et = datetime.utcnow() - timedelta(hours=4)
    if now_et.weekday() >= 5:
        return False
    market_open  = now_et.replace(hour=9,  minute=30, second=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0)
    return market_open <= now_et <= market_close

def analyze_symbol(symbol):
    try:
        df_4h  = get_klines(symbol, 4,  "hour",   200)
        df_1h  = get_klines(symbol, 1,  "hour",   100)
        df_15m = get_klines(symbol, 15, "minute",  50)

        if df_4h.empty or df_1h.empty or df_15m.empty:
            return None

        direction = get_trend(df_4h)
        if not direction:
            return None

        swing_high, swing_low = find_swing_points(df_1h, window=5)
        if swing_high is None or swing_low is None or swing_high <= swing_low:
            return None

        fib = calc_fib_levels(swing_high, swing_low, direction)
        if fib is None:
            return None

        price = df_15m["close"].iloc[-1]
        if not in_ote(price, fib):
            return None

        if not detect_bos(df_15m, direction):
            return None

        risk = abs(price - fib["sl"])
        if risk == 0:
            return None

        rr = round(abs(fib["tp3"] - price) / risk, 2)
        if rr < MIN_RR:
            return None

        wave_size = round((swing_high - swing_low) / swing_low * 100, 1)

        return {
            "symbol":          symbol,
            "market_type":     "STOCKS",
            "direction":       direction,
            "entry_price":     round(price, 4),
            "sl":              round(fib["sl"], 4),
            "tp1":             round(fib["tp1"], 4),
            "tp2":             round(fib["tp2"], 4),
            "tp3":             round(fib["tp3"], 4),
            "swing_high":      round(swing_high, 4),
            "swing_low":       round(swing_low, 4),
            "fib_618":         round(fib["ote_high"], 4),
            "fib_786":         round(fib["ote_low"], 4),
            "rr":              rr,
            "wave_size":       wave_size,
            "timeframe":       "4H/1H/15M",
            "tradingview_url": build_tv_url(symbol),
        }

    except Exception as e:
        print(f"Analyze error {symbol}: {e}")
        return None

def scan_all_markets():
    if not is_market_open():
        print("Market is closed. Skipping scan.")
        return []

    results = []
    print(f"Scanning {len(TOP_STOCKS)} US stocks...")

    for symbol in TOP_STOCKS:
        signal = analyze_symbol(symbol)
        if not signal:
            continue
        if is_duplicate_signal(symbol, signal["direction"], hours=4):
            print(f"  Skipped duplicate: {symbol} {signal['direction']}")
            continue
        results.append(signal)
        print(f"  Signal: {symbol} {signal['direction']} RR:{signal['rr']}")

    return results
