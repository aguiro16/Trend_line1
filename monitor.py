import requests
from config import MASSIVE_API_KEY, MASSIVE_BASE_URL
from database import get_open_signals, get_signal_by_number, close_signal
from telegram_bot import send_message, format_result_message

def get_current_price(symbol):
    try:
        url = f"{MASSIVE_BASE_URL}/v2/last/trade/{symbol}"
        headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"}
        resp = requests.get(url, headers=headers, timeout=5)
        data = resp.json()
        if data.get("status") in ("OK", "DELAYED") and data.get("results"):
            return float(data["results"]["p"])
        return None
    except Exception as e:
        print(f"Price error {symbol}: {e}")
        return None

def check_signal(signal):
    price = get_current_price(signal['symbol'])
    if price is None:
        return None
    direction = signal['direction']
    sl  = signal['sl']
    tp1 = signal['tp1']
    tp2 = signal['tp2']
    tp3 = signal['tp3']
    if direction == "LONG":
        if price <= sl:    return "SL"
        elif price >= tp3: return "TP3"
        elif price >= tp2: return "TP2"
        elif price >= tp1: return "TP1"
    else:
        if price >= sl:    return "SL"
        elif price <= tp3: return "TP3"
        elif price <= tp2: return "TP2"
        elif price <= tp1: return "TP1"
    return None

def calc_pnl(signal, result):
    entry = signal['entry_price']
    targets = {
        'TP1': signal['tp1'],
        'TP2': signal['tp2'],
        'TP3': signal['tp3'],
        'SL':  signal['sl'],
    }
    exit_price = targets.get(result, signal['sl'])
    if signal['direction'] == "LONG":
        pnl = ((exit_price - entry) / entry) * 100
    else:
        pnl = ((entry - exit_price) / entry) * 100
    return round(pnl, 2)

def monitor_open_signals():
    open_signals = get_open_signals()
    if not open_signals:
        return
    print(f"Monitoring {len(open_signals)} open signals...")
    for signal in open_signals:
        result = check_signal(signal)
        if result:
            pnl = calc_pnl(signal, result)
            if result == 'SL' and pnl > 0:
                pnl = -abs(pnl)
            elif result in ('TP1', 'TP2', 'TP3') and pnl < 0:
                pnl = abs(pnl)
            close_signal(signal['signal_number'], result, pnl)
            updated = get_signal_by_number(signal['signal_number'])
            if updated:
                msg = format_result_message(updated)
                send_message(msg)
                print(f"Signal #{signal['signal_number']} closed: {result} | PnL: {pnl}%")
