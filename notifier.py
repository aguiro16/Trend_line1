"""
notifier.py — رسائل Telegram
"""
import os
import logging
import requests

log = logging.getLogger("notifier")

TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _send(text: str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id":    TG_CHAT_ID,
            "text":       text,
            "parse_mode": "HTML",
        }, timeout=10)
        if r.status_code != 200:
            log.warning(f"Telegram error: {r.text}")
    except Exception as e:
        log.warning(f"Telegram failed: {e}")


def send_signal(sig: dict):
    num   = sig["number"]
    sym   = sig["symbol"]
    tf    = sig["timeframe"].upper()
    entry = sig["entry"]
    sl    = sig["sl"]
    dur   = sig.get("channel_duration", "?")
    sl_pct  = round((entry - sl) / entry * 100, 1)
    tp1_pct = round((sig["tp1"] - entry) / entry * 100, 1)
    tp2_pct = round((sig["tp2"] - entry) / entry * 100, 1)
    tp3_pct = round((sig["tp3"] - entry) / entry * 100, 1)
    tp4_pct = round((sig["tp4"] - entry) / entry * 100, 1)
    msg = (
        f"🚀 <b>إشارة #{num:03d} — كسر قناة هابطة</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{sym}</b> | ⏱ {tf}\n"
        f"📊 مدة القناة: <b>{dur} شمعة</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>دخول:</b>  <code>{entry}</code>\n"
        f"🛑 <b>SL:</b>    <code>{sl}</code>  (-{sl_pct}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>TP1:</b>   <code>{sig['tp1']}</code>  (+{tp1_pct}%)\n"
        f"🎯 <b>TP2:</b>   <code>{sig['tp2']}</code>  (+{tp2_pct}%)\n"
        f"🎯 <b>TP3:</b>   <code>{sig['tp3']}</code>  (+{tp3_pct}%)\n"
        f"🎯 <b>TP4:</b>   <code>{sig['tp4']}</code>  (+{tp4_pct}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ ليست نصيحة استثمارية"
    )
    _send(msg)


def send_result(sig: dict, hit_tp: int, price: float, pct: float):
    num = sig["number"]
    sym = sig["symbol"]
    if hit_tp == 0:
        emoji  = "🛑"
        result = f"وقف الخسارة ❌  ({pct}%)"
    elif hit_tp == 4:
        emoji  = "🏆"
        result = f"TP{hit_tp} محقق ✅  (+{pct}%)"
    else:
        emoji  = "✅"
        result = f"TP{hit_tp} محقق ✅  (+{pct}%)"
    msg = (
        f"{emoji} <b>نتيجة إشارة #{num:03d}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{sym}</b>\n"
        f"📌 الدخول: <code>{sig['entry']}</code>\n"
        f"💰 السعر الحالي: <code>{price}</code>\n"
        f"📊 النتيجة: <b>{result}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    _send(msg)
