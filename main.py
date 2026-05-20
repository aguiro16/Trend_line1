"""
main.py — نقطة البداية
"""
import logging
import threading
import time
from database import init_db
from scanner  import run_scanner
from reporter import check_reports

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("main")


def report_loop():
    """حلقة منفصلة للتقارير — تفحص كل 5 دقائق"""
    while True:
        try:
            check_reports()
        except Exception as e:
            log.error(f"report_loop error: {e}")
        time.sleep(300)


if __name__ == "__main__":
    log.info("🤖 بوت القناة الهابطة — بدأ")
    init_db()

    # تقارير في thread منفصل
    t = threading.Thread(target=report_loop, daemon=True)
    t.start()

    # الحلقة الرئيسية
    run_scanner()
