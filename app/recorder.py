"""
recorder.py
-----------
Daily weather data recorder for Kamonyi district.
Records today's weather (per sector) into SQLite.
One row per sector per date — INSERT OR IGNORE means data is never overwritten.
"""

import sqlite3
import time
import threading
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import config

DB_PATH = Path(config.DATA_DB_PATH)


# ── Database ───────────────────────────────────────────────────────────────

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c    = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_weather (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT NOT NULL,
            sector        TEXT NOT NULL,
            temp_max      REAL,
            temp_min      REAL,
            rainfall_mm   REAL,
            humidity      REAL,
            description   TEXT,
            recorded_at   TEXT NOT NULL,
            UNIQUE(date, sector)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS recording_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            status      TEXT NOT NULL,
            message     TEXT,
            recorded_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    print(f"[recorder] Database ready: {DB_PATH}")


def already_recorded_today() -> bool:
    today = date.today().isoformat()
    try:
        conn  = sqlite3.connect(str(DB_PATH))
        count = conn.execute(
            "SELECT COUNT(*) FROM daily_weather WHERE date=?", (today,)
        ).fetchone()[0]
        conn.close()
        return count >= 12
    except:
        return False


# ── Core recording ─────────────────────────────────────────────────────────

def record_today(api_key: str) -> dict:
    """
    Fetch weather for all 12 sectors and save to daily_weather.
    INSERT OR IGNORE — once a day is saved it is never overwritten.
    """
    from weather_api import get_current_weather, SECTOR_COORDS

    today       = date.today().isoformat()
    recorded_at = datetime.now(timezone.utc).isoformat()
    saved, failed = [], []

    conn = sqlite3.connect(str(DB_PATH))
    c    = conn.cursor()

    for sector in SECTOR_COORDS:
        try:
            w = get_current_weather(api_key, sector)
            if not w:
                failed.append(sector)
                continue
            c.execute("""
                INSERT OR IGNORE INTO daily_weather
                    (date, sector, temp_max, temp_min, rainfall_mm,
                     humidity, description, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today, sector,
                w.get("temp_max_c"),
                w.get("temp_min_c"),
                w.get("rain_1h_mm", 0.0),
                w.get("humidity_pct"),
                w.get("description", ""),
                recorded_at,
            ))
            saved.append(sector)
            time.sleep(0.2)
        except Exception as e:
            print(f"[recorder] {sector}: {e}")
            failed.append(sector)

    conn.commit()

    status  = "ok" if saved and not failed else ("partial" if saved else "failed")
    message = f"Saved: {len(saved)} sectors. Failed: {len(failed)}"
    if failed:
        message += f" ({', '.join(failed)})"
    c.execute(
        "INSERT INTO recording_log (date,status,message,recorded_at) VALUES (?,?,?,?)",
        (today, status, message, recorded_at)
    )
    conn.commit()
    conn.close()

    print(f"[recorder] {today}: {message}")
    return {"date": today, "status": status, "saved": saved,
            "failed": failed, "message": message}


# ── Scheduler ─────────────────────────────────────────────────────────────

def try_record_today(api_key: str):
    """Record immediately in background. Also schedules 05:00 and 17:00 Rwanda time."""

    def _run():
        try:
            record_today(api_key)
        except Exception as e:
            print(f"[recorder] record failed: {e}")

    def _scheduler():
        # Record immediately on startup
        _run()
        # Then schedule 05:00 (UTC 03:00) and 17:00 (UTC 15:00) Rwanda time
        SCHEDULED_UTC = {(3, 0), (15, 0)}
        last_slot = None
        while True:
            time.sleep(60)
            now  = datetime.now(timezone.utc)
            slot = (now.hour, now.minute)
            if slot in SCHEDULED_UTC and slot != last_slot:
                rw = (now.hour + 2) % 24
                print(f"[recorder] Scheduled recording at {rw:02d}:00 Rwanda time")
                _run()
                last_slot = slot

    threading.Thread(target=_scheduler, daemon=True, name="recorder").start()


def start_scheduler():
    try_record_today(config.OWM_API_KEY)


def record_in_background():
    def _run():
        try:
            record_today(config.OWM_API_KEY)
        except Exception as e:
            print(f"[recorder] background record failed: {e}")
    threading.Thread(target=_run, daemon=True).start()


# ── Query functions ────────────────────────────────────────────────────────

def get_all_records(days: int = 30) -> list:
    """All daily_weather records within the last N days."""
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        conn   = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows   = conn.execute("""
            SELECT date, recorded_at, sector,
                   rainfall_mm, temp_max, temp_min
            FROM daily_weather
            WHERE date >= ?
            ORDER BY date DESC, sector ASC
        """, (cutoff,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[recorder] get_all_records: {e}")
        return []


def get_recording_log(limit: int = 30) -> list:
    """One entry per day showing how many sectors were recorded."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT date,
                   MAX(recorded_at)        AS recorded_at,
                   COUNT(DISTINCT sector)  AS sectors_ok,
                   (12 - COUNT(DISTINCT sector)) AS sectors_failed,
                   '' AS failed_list,
                   CASE WHEN COUNT(DISTINCT sector)>=12 THEN 'ok' ELSE 'partial' END AS status
            FROM daily_weather
            GROUP BY date
            ORDER BY date DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[recorder] get_recording_log: {e}")
        return []


def get_records_summary() -> dict:
    """Summary stats always returns all required keys."""
    result = {
        "total_records": 0, "unique_days": 0,
        "date_from": None, "date_to": None,
        "last_recorded_at": None,
        "last_sectors_ok": 0, "last_sectors_failed": 0,
        "last_status": None,
    }
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        c.execute("SELECT COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) FROM daily_weather")
        row = c.fetchone()
        if row:
            result["total_records"] = row[0] or 0
            result["unique_days"]   = row[1] or 0
            result["date_from"]     = row[2]
            result["date_to"]       = row[3]
        c.execute("""
            SELECT MAX(recorded_at), COUNT(DISTINCT sector)
            FROM daily_weather WHERE date=(SELECT MAX(date) FROM daily_weather)
        """)
        last = c.fetchone()
        if last and last[0]:
            result["last_recorded_at"]    = last[0]
            result["last_sectors_ok"]     = last[1] or 0
            result["last_sectors_failed"] = 12 - (last[1] or 0)
            result["last_status"]         = "ok" if (last[1] or 0) >= 12 else "partial"
        conn.close()
    except Exception as e:
        print(f"[recorder] get_records_summary: {e}")
    return result


def get_recorded_history(sector: str = None, limit_days: int = 30) -> list:
    return get_all_records(limit_days)


def get_recorded_summary() -> dict:
    return get_records_summary()


# ── Seasonal data stubs ────────────────────────────────────────────────────

def backfill_actuals_from_excel(merged_df) -> int:
    return 0

def save_bulk_predictions(predictions: list) -> int:
    return 0

def get_prediction_vs_actual(sector=None, season=None) -> list:
    return []

def get_actuals_summary() -> dict:
    return {"total_actuals": 0, "total_predictions": 0}
