"""
scanner.py — حلقة الفحص + متابعة الإشارات المفتوحة
"""
import os
import time
import logging
from fetcher  import get_top_pairs, fetch_ohlcv, get_current_price, fetch_1h_confirmation
from strategy import analyze
from database import save_signal, get_open_signals, close_signal, signal_exists
from notifier import send_signal, send_result

log = logging.getLogger("scanner")

TIMEFRAMES     = ["3d", "1d"]
SCAN_HOURS     = float(os.getenv("SCAN_HOURS", "6"))
TRACK_INTERVAL = float(os.getenv("TRACK_INTERVAL_MIN", "30")) * 60
MAX_ENTRY_DEVIATION = float(os.getenv("MAX_ENTRY_DEVIATION", "0.05"))  # 5%


def scan_new_signals():
    log.info("📡 فحص أزواج جديدة...")
    pairs = get_top_pairs()
    found = 0

    for symbol in pairs:
        for tf in TIMEFRAMES:
            if signal_exists(symbol, tf):
                continue

            df = fetch_ohlcv(symbol, tf, limit=150)
            if df is None:
                continue

            signal = analyze(symbol, tf, df)
            if signal is None:
                continue

            # ─── تأكيد الدخول على 1H ──────────────────
            breakout_level = signal["entry"]

            entry_1h = fetch_1h_confirmation(symbol, breakout_level)

            if entry_1h is None:
                log.info(f"{symbol} {tf}: انتظار تأكيد 1H فوق {breakout_level}")
                continue

            # تحقق أن السعر لم يبتعد كثيراً
            current = get_current_price(symbol)
            if current and current > entry_1h * (1 + MAX_ENTRY_DEVIATION):
                log.info(f"{symbol} {tf}: السعر {current} بعيد عن الدخول {entry_1h} — فرصة فاتت")
                continue

            signal["entry"] = round(entry_1h, 8)

            num = save_signal(signal)
            signal["number"] = num
            send_signal(signal)
            found += 1
            log.info(f"✅ إشارة #{num:03d} — {symbol} {tf} | دخول 1H: {entry_1h}")
            time.sleep(1)
            break

    log.info(f"انتهى الفحص — {found} إشارة جديدة")


def track_open_signals():
    open_sigs = get_open_signals()
    if not open_sigs:
        return
    log.info(f"🔍 متابعة {len(open_sigs)} إشارة مفتوحة...")

    for sig in open_sigs:
        price = get_current_price(sig["symbol"])
        if price is None:
            continue

        entry = sig["entry"]
        sl    = sig["sl"]
        tp1   = sig["tp1"]
        tp2   = sig["tp2"]
        tp3   = sig["tp3"]
        tp4   = sig["tp4"]
        num   = sig["number"]

        if price >= tp4:
            pct = round((tp4 - entry) / entry * 100, 1)
            close_signal(num, "WIN", pct, 4)
            send_result(sig, hit_tp=4, price=price, pct=pct)
        elif price >= tp3:
            pct = round((tp3 - entry) / entry * 100, 1)
            close_signal(num, "WIN", pct, 3)
            send_result(sig, hit_tp=3, price=price, pct=pct)
        elif price >= tp2:
            pct = round((tp2 - entry) / entry * 100, 1)
            close_signal(num, "WIN", pct, 2)
            send_result(sig, hit_tp=2, price=price, pct=pct)
        elif price >= tp1:
            pct = round((tp1 - entry) / entry * 100, 1)
            close_signal(num, "WIN", pct, 1)
            send_result(sig, hit_tp=1, price=price, pct=pct)
        elif price <= sl:
            pct = round((price - entry) / entry * 100, 1)
            close_signal(num, "LOSS", pct, 0)
            send_result(sig, hit_tp=0, price=price, pct=pct)


def run_scanner():
    log.info("🚀 Scanner بدأ...")
    scan_cycle  = 0
    track_every = max(1, int(SCAN_HOURS * 3600 / TRACK_INTERVAL))

    while True:
        try:
            track_open_signals()
            if scan_cycle % track_every == 0:
                scan_new_signals()
            scan_cycle += 1
        except Exception as e:
            log.error(f"خطأ في الحلقة: {e}", exc_info=True)

        log.info(f"⏳ انتظار {int(TRACK_INTERVAL // 60)} دقيقة...")
        time.sleep(TRACK_INTERVAL)
