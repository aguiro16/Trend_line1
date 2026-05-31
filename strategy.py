"""
strategy.py — استراتيجية كسر القناة الهابطة (CryptoCove Style)
"""
import numpy as np
import pandas as pd
from scipy.stats import linregress
import logging

log = logging.getLogger("strategy")

# ─── إعدادات القناة ───────────────────────
MIN_CANDLES          = 30
MIN_CHANNEL_CANDLES  = 10
MIN_TOUCHES          = 2
VOLUME_MULTIPLIER    = 1.5
MIN_RR               = 2.0
MAX_SL_PCT           = 0.20
MIN_TP_SPACING       = 0.03


def find_pivots(series: pd.Series, window: int = 3):
    highs, lows = [], []
    for i in range(window, len(series) - window):
        if all(series[i] >= series[i - j] for j in range(1, window + 1)) and \
           all(series[i] >= series[i + j] for j in range(1, window + 1)):
            highs.append(i)
        if all(series[i] <= series[i - j] for j in range(1, window + 1)) and \
           all(series[i] <= series[i + j] for j in range(1, window + 1)):
            lows.append(i)
    return highs, lows


def fit_line(indices: list, values: pd.Series):
    if len(indices) < 2:
        return None
    x = np.array(indices, dtype=float)
    y = np.array([values.iloc[i] for i in indices], dtype=float)
    slope, intercept, r, _, _ = linregress(x, y)
    return slope, intercept, r


def line_val(slope, intercept, idx):
    return slope * idx + intercept


def detect_channel(df: pd.DataFrame) -> dict | None:
    if len(df) < MIN_CANDLES:
        return None

    high_pivots, low_pivots = find_pivots(df["high"], window=3)
    _, low_piv2             = find_pivots(df["low"],  window=3)
    low_pivots = low_piv2 if low_piv2 else low_pivots

    upper = fit_line(high_pivots, df["high"])
    lower = fit_line(low_pivots,  df["low"])

    if upper is None or lower is None:
        return None

    u_slope, u_intercept, _ = upper
    l_slope, l_intercept, _ = lower

    if u_slope >= 0 or l_slope >= 0:
        return None

    if abs(u_slope) > 0:
        if abs((u_slope - l_slope) / u_slope) > 0.30:
            return None

    u_touches = sum(
        1 for i in high_pivots
        if abs(df["high"].iloc[i] - line_val(u_slope, u_intercept, i)) /
           (line_val(u_slope, u_intercept, i) + 1e-12) < 0.03
    )
    l_touches = sum(
        1 for i in low_pivots
        if abs(df["low"].iloc[i] - line_val(l_slope, l_intercept, i)) /
           (line_val(l_slope, l_intercept, i) + 1e-12) < 0.03
    )

    if u_touches < MIN_TOUCHES or l_touches < MIN_TOUCHES:
        return None

    all_pivots = high_pivots + low_pivots
    if not all_pivots:
        return None
    duration = max(all_pivots) - min(all_pivots)
    if duration < MIN_CHANNEL_CANDLES:
        return None

    last_idx = len(df) - 1
    return {
        "u_slope":       u_slope,
        "u_intercept":   u_intercept,
        "l_slope":       l_slope,
        "l_intercept":   l_intercept,
        "u_touches":     u_touches,
        "l_touches":     l_touches,
        "duration":      duration,
        "upper_now":     line_val(u_slope, u_intercept, last_idx),
        "lower_now":     line_val(l_slope, l_intercept, last_idx),
        "channel_start": min(all_pivots),
    }


def check_breakout(df: pd.DataFrame, ch: dict) -> bool:
    if len(df) < 3:
        return False
    prev_idx = len(df) - 2
    prev     = df.iloc[prev_idx]
    upper_at = line_val(ch["u_slope"], ch["u_intercept"], prev_idx)
    if prev["close"] <= upper_at:
        return False
    avg_vol = df["volume"].iloc[-22:-2].mean()
    if avg_vol == 0 or prev["volume"] < VOLUME_MULTIPLIER * avg_vol:
        return False
    if prev["close"] <= prev["open"]:
        return False
    return True


def calculate_targets(df: pd.DataFrame, ch: dict) -> dict | None:
    prev_idx = len(df) - 2
    entry    = float(df.iloc[prev_idx]["close"])

    sl_line = line_val(ch["l_slope"], ch["l_intercept"], prev_idx)
    sl      = max(sl_line, entry * (1 - MAX_SL_PCT))
    if sl >= entry:
        return None

    start  = ch["channel_start"]
    pre_df = df.iloc[:start]

    if len(pre_df) >= 4:
        levels  = sorted(pre_df["high"].nlargest(8).unique())
        targets = [r for r in levels if r > entry * 1.05]
    else:
        targets = []

    base = targets[-1] if targets else entry
    while len(targets) < 4:
        base = base * 1.30
        targets.append(round(base, 8))

    clean_targets = []
    last = entry
    for t in sorted(targets):
        if t > last * (1 + MIN_TP_SPACING):
            clean_targets.append(t)
            last = t
        if len(clean_targets) == 4:
            break

    if len(clean_targets) < 4:
        clean_targets = []
        base = entry
        for _ in range(4):
            base = base * 1.30
            clean_targets.append(round(base, 8))

    rr = (clean_targets[0] - entry) / (entry - sl + 1e-12)
    if rr < MIN_RR:
        return None

    return {
        "entry": round(entry, 8),
        "sl":    round(sl,    8),
        "tp1":   round(clean_targets[0], 8),
        "tp2":   round(clean_targets[1], 8),
        "tp3":   round(clean_targets[2], 8),
        "tp4":   round(clean_targets[3], 8),
    }


def analyze(symbol: str, tf: str, df: pd.DataFrame) -> dict | None:
    ch = detect_channel(df)
    if ch is None:
        return None
    if not check_breakout(df, ch):
        return None
    targets = calculate_targets(df, ch)
    if targets is None:
        return None
    return {
        "symbol":           symbol,
        "timeframe":        tf,
        "channel_duration": ch["duration"],
        **targets,
    }
