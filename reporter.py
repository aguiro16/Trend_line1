"""
reporter.py — التقارير اليومية والأسبوعية
"""
import os
import logging
from datetime import datetime, timedelta
from database import get_signals_today, get_signals_this_week, get_conn
from notifier import _send

log = logging.getLogger("reporter")

DAILY_HOUR = int(os.getenv("DAILY_REPORT_HOUR", "20"))
WEEKLY_DAY = int(os.getenv("WEEKLY_REPORT_DAY", "6"))


def _stats(signals: list) -> dict:
    closed    = [s for s in signals if s["status"] == "CLOSED"]
    wins      = [s for s in closed  if s["result"] == "WIN"]
    losses    = [s for s in closed  if s["result"] == "LOSS"]
    open_s    = [s for s in signals if s["status"] == "OPEN"]
    total_pct = sum(s["result_pct"] or 0 for s in closed)
    win_rate  = round(len(wins) / len(closed) * 100) if closed else 0
    return {
        "total":     len(signals),
        "closed":    len(closed),
        "wins":      len(wins),
        "losses":    len(losses),
        "open":      len(open_s),
        "total_pct": round(total_pct, 1),
        "win_rate":  win_rate,
    }


def _format_signal_line(s: dict) -> str:
    num = s["number"]
    sym = s["symbol"]
    if s["status"] == "OPEN":
        return f"  #{num:03d} {sym} — 🔄 مفتوحة"
    pct = s.get("result_pct", 0) or 0
    tp  = s.get("hit_tp", 0) or 0
    if s["result"] == "WIN":
        return f"  #{num:03d} {sym} — ✅ TP{tp}  +{pct}%"
    return f"  #{num:03d} {sym} — ❌ SL  {pct}%"


def send_daily_report():
    signals = get_signals_today()
    st      = _stats(signals)
    today   = datetime.utcnow().strftime("%Y-%m-%d")
    lines   = "\n".join(_format_signal_line(s) for s in signals) or "  — لا توجد إشارات"
    msg = (
        f"📊 <b>التقرير اليومي — {today}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📨 إجمالي الإشارات: <b>{st['total']}</b>\n"
        f"✅ رابحة: <b>{st['wins']}</b>  |  ❌ خاسرة: <b>{st['losses']}</b>\n"
        f"🔄 مفتوحة: <b>{st['open']}</b>\n"
        f"📈 نسبة النجاح: <b>{st['win_rate']}%</b>\n"
        f"💰 إجمالي الربح: <b>{st['total_pct']}%</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>التفاصيل:</b>\n{lines}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    _send(msg)
    log.info("📊 التقرير اليومي أُرسل")


def send_weekly_report():
    signals    = get_signals_this_week()
    st         = _stats(signals)
    week_end   = datetime.utcnow().strftime("%Y-%m-%d")
    week_start = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    lines      = "\n".join(_format_signal_line(s) for s in signals) or "  — لا توجد إشارات"
    best = max(
        (s for s in signals if s.get("result_pct")),
        key=lambda x: x["result_pct"],
        default=None
    )
    best_line = (
        f"🏆 أفضل إشارة: #{best['number']:03d} {best['symbol']}  +{best['result_pct']}%"
        if best else ""
    )
    msg = (
        f"📅 <b>التقرير الأسبوعي</b>\n"
        f"<i>{week_start} → {week_end}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📨 إجمالي الإشارات: <b>{st['total']}</b>\n"
        f"✅ رابحة: <b>{st['wins']}</b>  |  ❌ خاسرة: <b>{st['losses']}</b>\n"
        f"🔄 مفتوحة: <b>{st['open']}</b>\n"
        f"📈 نسبة النجاح: <b>{st['win_rate']}%</b>\n"
        f"💰 إجمالي الأرباح: <b>{st['total_pct']}%</b>\n"
        f"{best_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>جميع الإشارات:</b>\n{lines}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    _send(msg)
    log.info("📅 التقرير الأسبوعي أُرسل")


def check_reports():
    now = datetime.utcnow()
    if now.hour == DAILY_HOUR and now.minute < 5:
        send_daily_report()
    if now.weekday() == WEEKLY_DAY and now.hour == (DAILY_HOUR + 1) % 24 and now.minute < 5:
        send_weekly_report()
