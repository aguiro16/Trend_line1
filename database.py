"""
database.py — إدارة قاعدة البيانات SQLite
"""
import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.getenv("DB_PATH", "signals.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            number           INTEGER UNIQUE,
            symbol           TEXT NOT NULL,
            timeframe        TEXT NOT NULL,
            entry            REAL NOT NULL,
            sl               REAL NOT NULL,
            tp1              REAL NOT NULL,
            tp2              REAL NOT NULL,
            tp3              REAL NOT NULL,
            tp4              REAL NOT NULL,
            channel_duration INTEGER,
            status           TEXT DEFAULT 'OPEN',
            result           TEXT,
            result_pct       REAL,
            hit_tp           INTEGER DEFAULT 0,
            created_at       TEXT NOT NULL,
            closed_at        TEXT
        );
        CREATE TABLE IF NOT EXISTS signal_counter (
            id    INTEGER PRIMARY KEY CHECK (id = 1),
            count INTEGER DEFAULT 0
        );
        INSERT OR IGNORE INTO signal_counter (id, count) VALUES (1, 0);
    """)
    conn.commit()
    conn.close()


def next_signal_number() -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE signal_counter SET count = count + 1 WHERE id = 1")
    conn.commit()
    num = c.execute("SELECT count FROM signal_counter").fetchone()[0]
    conn.close()
    return num


def save_signal(signal: dict) -> int:
    num = next_signal_number()
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO signals
        (number, symbol, timeframe, entry, sl, tp1, tp2, tp3, tp4,
         channel_duration, status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        num, signal["symbol"], signal["timeframe"],
        signal["entry"], signal["sl"],
        signal["tp1"], signal["tp2"], signal["tp3"], signal["tp4"],
        signal.get("channel_duration", 0),
        "OPEN", datetime.utcnow().isoformat(),
    ))
    conn.commit()
    conn.close()
    return num


def get_open_signals() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM signals WHERE status = 'OPEN' ORDER BY number"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def close_signal(number: int, result: str, result_pct: float, hit_tp: int):
    conn = get_conn()
    conn.execute("""
        UPDATE signals
        SET status='CLOSED', result=?, result_pct=?, hit_tp=?, closed_at=?
        WHERE number=?
    """, (result, result_pct, hit_tp, datetime.utcnow().isoformat(), number))
    conn.commit()
    conn.close()


def get_signals_today() -> list:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    conn  = get_conn()
    rows  = conn.execute(
        "SELECT * FROM signals WHERE created_at LIKE ? ORDER BY number",
        (f"{today}%",)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_signals_this_week() -> list:
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM signals WHERE created_at >= ? ORDER BY number",
        (week_ago,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def signal_exists(symbol: str, timeframe: str) -> bool:
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    conn = get_conn()
    row  = conn.execute(
        """SELECT id FROM signals
           WHERE symbol=? AND timeframe=?
           AND (status='OPEN' OR created_at >= ?)""",
        (symbol, timeframe, cutoff)
    ).fetchone()
    conn.close()
    return row is not None
