import anthropic
import os
import sqlite3
from datetime import datetime, date, timedelta
from telegram_bot import send_message, format_daily_report
from config import ANTHROPIC_API_KEY

DB_PATH = os.path.join(os.path.dirname(__file__), "signals.db")

def get_signals_by_period(hours):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    c.execute("SELECT * FROM signals WHERE created_at >= ?", (since,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def calc_stats(signals, label):
    wins   = [s for s in signals if s['status']=='CLOSED' and s['pnl_pct'] and s['pnl_pct']>0]
    losses = [s for s in signals if s['status']=='CLOSED' and s['pnl_pct'] and s['pnl_pct']<0]
    open_s = [s for s in signals if s['status']=='OPEN']
    total_pnl = sum(s['pnl_pct'] for s in signals if s['status']=='CLOSED' and s['pnl_pct'])
    best_signal = worst_signal = "—"
    if wins:
        best = max(wins, key=lambda x: x['pnl_pct'])
        best_signal = f"#{best['signal_number']} {best['symbol']} (+{best['pnl_pct']:.2f}%)"
    if losses:
        worst = min(losses, key=lambda x: x['pnl_pct'])
        worst_signal = f"#{worst['signal_number']} {worst['symbol']} ({worst['pnl_pct']:.2f}%)"
    return {
        'date': label, 'total': len(signals), 'wins': len(wins),
        'losses': len(losses), 'open': len(open_s),
        'total_pnl': round(total_pnl, 2),
        'best_signal': best_signal, 'worst_signal': worst_signal,
        'signals': signals,
    }

def get_daily_stats():
    return calc_stats(get_signals_by_period(24), date.today().strftime("%Y-%m-%d"))

def get_weekly_stats():
    from_date = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    to_date   = date.today().strftime("%Y-%m-%d")
    return calc_stats(get_signals_by_period(168), f"{from_date} → {to_date}")

def analyze_with_claude(prompt):
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    except Exception as e:
        print(f"Claude API error: {e}")
        return "⚠️ تعذر الحصول على تحليل كلود"

def build_daily_prompt(stats):
    losing = [s for s in stats['signals'] if s['status']=='CLOSED' and s['pnl_pct'] and s['pnl_pct']<0]
    if not losing:
        return ""
    lines = ["أنت خبير تحليل تقني للأسهم الأمريكية.", "حلل الإشارات الخاسرة:\n"]
    for s in losing:
        lines.append(f"إشارة #{s['signal_number']}: {s['symbol']} {s['direction']} | دخول:{s['entry_price']} SL:{s['sl']} | خسارة:{s['pnl_pct']}%")
    lines.append("\nالمطلوب بالعربية:\n1. الأخطاء الشائعة\n2. توصيات عملية")
    return "\n".join(lines)

def build_weekly_prompt(stats):
    signals  = stats['signals']
    closed   = [s for s in signals if s['status']=='CLOSED' and s['pnl_pct']]
    wins     = [s for s in closed if s['pnl_pct'] > 0]
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0
    long_trades  = [s for s in closed if s['direction']=='LONG']
    short_trades = [s for s in closed if s['direction']=='SHORT']
    long_wr  = round(len([s for s in long_trades if s['pnl_pct']>0]) / len(long_trades) * 100, 1) if long_trades else 0
    short_wr = round(len([s for s in short_trades if s['pnl_pct']>0]) / len(short_trades) * 100, 1) if short_trades else 0
    return f"""أنت خبير تحليل تقني للأسهم الأمريكية.
إحصائيات الأسبوع:
- إجمالي: {stats['total']} | Win Rate: {win_rate}% | PnL: {stats['total_pnl']}%
- LONG: {long_wr}% ({len(long_trades)} صفقة) | SHORT: {short_wr}% ({len(short_trades)} صفقة)

المطلوب بالعربية:
1. تقييم الأداء العام
2. مشكلة في LONG أم SHORT؟
3. أسهم يجب استبعادها
4. تعديلات مقترحة على الكود
"""

def format_weekly_report(stats, claude_analysis):
    total    = stats['total']
    win_rate = round(stats['wins'] / total * 100, 1) if total > 0 else 0
    is_pos   = stats['total_pnl'] >= 0
    pnl_str  = f"+{stats['total_pnl']:.2f}%" if is_pos else f"{stats['total_pnl']:.2f}%"
    return f"""
{"📊✅" if is_pos else "📊❌"} <b>التقرير الأسبوعي - الأسهم الأمريكية</b>
━━━━━━━━━━━━━━━━━━━━━
📅 {stats['date']}
  📨 إجمالي: {total} | ✅ {stats['wins']} | ❌ {stats['losses']}
  🎯 نسبة الفوز: {win_rate}% | 💰 PnL: {pnl_str}
━━━━━━━━━━━━━━━━━━━━━
🤖 <b>تحليل كلود AI:</b>

{claude_analysis}
━━━━━━━━━━━━━━━━━━━━━
""".strip()

def send_daily_report():
    stats = get_daily_stats()
    claude_analysis = None
    if stats['losses'] > 0:
        prompt = build_daily_prompt(stats)
        if prompt:
            claude_analysis = analyze_with_claude(prompt)
    send_message(format_daily_report(stats, claude_analysis))

def send_weekly_report():
    stats = get_weekly_stats()
    if stats['total'] == 0:
        send_message("📊 <b>التقرير الأسبوعي</b>\n\nلا توجد إشارات هذا الأسبوع.")
        return
    analysis = analyze_with_claude(build_weekly_prompt(stats))
    send_message(format_weekly_report(stats, analysis))
