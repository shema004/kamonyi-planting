"""
recorder.py
-----------
Daily weather data recorder for Kamonyi district.
Records today's weather (per sector) into a local SQLite database.
Runs automatically at app startup and on every dashboard visit.
Database: data/daily_records.db
"""

import sqlite3
import time
import threading
from datetime import datetime, timezone, date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import config

DB_PATH = Path(config.DATA_DB_PATH)


# ── Database setup ─────────────────────────────────────────────────────────

def init_db():
    """Create the database and tables if they don't exist."""
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
    print(f"[recorder] Database ready at: {DB_PATH}")


def already_recorded_today() -> bool:
    """Check if today's data has already been recorded for all sectors."""
    today = date.today().isoformat()
    try:
        conn  = sqlite3.connect(str(DB_PATH))
        c     = conn.cursor()
        c.execute("SELECT COUNT(*) FROM daily_weather WHERE date = ?", (today,))
        count = c.fetchone()[0]
        conn.close()
        return count >= 12  # all 12 sectors recorded
    except:
        return False


def get_recorded_dates() -> list:
    """Return all distinct dates that have been recorded, newest first."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_weather ORDER BY date DESC"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except:
        return []


def record_today(api_key: str) -> dict:
    """
    Fetch today's weather for all sectors and save to the database.
    Returns a summary of what was recorded.
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
                    (date, sector, temp_max, temp_min, rainfall_mm, humidity, description, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today,
                sector,
                w.get("temp_max_c"),
                w.get("temp_min_c"),
                w.get("rain_1h_mm", 0.0),
                w.get("humidity_pct"),
                w.get("description", ""),
                recorded_at,
            ))
            saved.append(sector)
            time.sleep(0.3)   # respect free-tier rate limit

        except Exception as e:
            print(f"[recorder] Error recording {sector}: {e}")
            failed.append(sector)

    conn.commit()

    # Log the recording attempt
    status  = "ok" if saved else "failed"
    message = f"Saved: {len(saved)} sectors. Failed: {len(failed)}"
    if failed:
        message += f" ({', '.join(failed)})"
    c.execute("""
        INSERT INTO recording_log (date, status, message, recorded_at)
        VALUES (?, ?, ?, ?)
    """, (today, status, message, recorded_at))
    conn.commit()
    conn.close()

    print(f"[recorder] {message}")
    return {"date": today, "status": status, "saved": saved, "failed": failed, "message": message}


# ── Startup + scheduled recording ─────────────────────────────────────────

def try_record_today(api_key: str):
    """
    Called at app startup and on every dashboard visit.
    Records immediately in background thread — never blocks the page.
    Also schedules recordings at 05:00 and 17:00 Rwanda time.
    """
    def _run():
        try:
            print("[recorder] Recording today's weather data...")
            record_today(api_key)
        except Exception as e:
            print(f"[recorder] Recording failed: {e}")

    def _scheduler():
        # Record immediately
        _run()

        # Then check every minute for scheduled times
        # 05:00 Rwanda (UTC+2) = 03:00 UTC
        # 17:00 Rwanda (UTC+2) = 15:00 UTC
        SCHEDULED_UTC = {(3, 0), (15, 0)}
        last_slot = None

        while True:
            time.sleep(60)
            now  = datetime.now(timezone.utc)
            slot = (now.hour, now.minute)
            if slot in SCHEDULED_UTC and slot != last_slot:
                rw_hour = (now.hour + 2) % 24
                print(f"[recorder] Scheduled recording at {rw_hour:02d}:00 Rwanda time...")
                _run()
                last_slot = slot

    t = threading.Thread(target=_scheduler, daemon=True, name="recorder")
    t.start()


# ── Query helpers ──────────────────────────────────────────────────────────

def get_recorded_history(sector: str = None, limit_days: int = 365) -> list:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if sector:
            c.execute("""
                SELECT date, sector, temp_max, temp_min, rainfall_mm, humidity, description
                FROM daily_weather WHERE sector = ?
                ORDER BY date DESC LIMIT ?
            """, (sector, limit_days))
        else:
            c.execute("""
                SELECT date, sector, temp_max, temp_min, rainfall_mm, humidity, description
                FROM daily_weather
                ORDER BY date DESC LIMIT ?
            """, (limit_days * 12,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except:
        return []


def get_recording_log(limit: int = 30) -> list:
    """Return one entry per day — the best recording for that day."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        # Get the best (most sectors saved) recording per day
        rows = conn.execute("""
            SELECT date,
                   recorded_at,
                   status,
                   message,
                   COUNT(DISTINCT sector) AS sectors_ok
            FROM daily_weather
            GROUP BY date
            ORDER BY date DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            d["sectors_failed"] = 12 - d["sectors_ok"]
            d["failed_list"]    = ""
            result.append(d)
        return result
    except Exception as e:
        print(f"[recorder] get_recording_log error: {e}")
        return []


def get_recorded_summary() -> dict:
    try:
        conn  = sqlite3.connect(str(DB_PATH))
        c     = conn.cursor()
        c.execute("SELECT COUNT(DISTINCT date) FROM daily_weather")
        days  = c.fetchone()[0]
        c.execute("SELECT MIN(date), MAX(date) FROM daily_weather")
        row   = c.fetchone()
        c.execute("SELECT COUNT(*) FROM daily_weather")
        total = c.fetchone()[0]
        # Last recording info
        c.execute("SELECT date, status, message, recorded_at FROM recording_log ORDER BY id DESC LIMIT 1")
        last = c.fetchone()
        conn.close()
        return {
            "total_records":     total,
            "unique_days":       days,
            "date_from":         row[0],
            "date_to":           row[1],
            "last_date":         last[0] if last else None,
            "last_status":       last[1] if last else None,
            "last_message":      last[2] if last else None,
            "last_recorded_at":  last[3] if last else None,
        }
    except:
        return {"total_records": 0, "unique_days": 0}


# ── Additional helpers used by main.py ────────────────────────────────────

def get_all_records(days: int = 30) -> list:
    """Return all daily records within the last N days — one row per sector per date."""
    try:
        from datetime import timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        conn   = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows   = conn.execute("""
            SELECT date,
                   recorded_at,
                   sector,
                   rainfall_mm,
                   temp_max,
                   temp_min
            FROM daily_weather
            WHERE date >= ?
            ORDER BY date DESC, sector ASC
        """, (cutoff,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[recorder] get_all_records error: {e}")
        return []


def get_records_summary() -> dict:
    """Summary stats from daily_weather (the active recording table)."""
    result = {
        "total_records":       0,
        "unique_days":         0,
        "date_from":           None,
        "date_to":             None,
        "last_recorded_at":    None,
        "last_sectors_ok":     0,
        "last_sectors_failed": 0,
        "last_status":         None,
    }
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        # Stats from daily_weather
        c.execute("SELECT COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) FROM daily_weather")
        row = c.fetchone()
        if row:
            result["total_records"] = row[0] or 0
            result["unique_days"]   = row[1] or 0
            result["date_from"]     = row[2]
            result["date_to"]       = row[3]
        # Last recording: most recent date in daily_weather
        c.execute("""
            SELECT date, MAX(recorded_at), COUNT(DISTINCT sector)
            FROM daily_weather
            GROUP BY date
            ORDER BY date DESC LIMIT 1
        """)
        last = c.fetchone()
        if last:
            result["last_recorded_at"]    = last[1]
            result["last_sectors_ok"]     = last[2] or 0
            result["last_sectors_failed"] = 12 - (last[2] or 0)
            result["last_status"]         = "ok" if (last[2] or 0) >= 12 else "partial"
        conn.close()
    except Exception as e:
        print(f"[recorder] get_records_summary error: {e}")
    return result


# Stubs for seasonal data (used by main.py)
def backfill_actuals_from_excel(merged_df) -> int:
    return 0

def save_bulk_predictions(predictions: list) -> int:
    return 0

def get_prediction_vs_actual(sector=None, season=None) -> list:
    return []

def get_actuals_summary() -> dict:
    return {"total_actuals": 0, "total_predictions": 0}

# Scheduler alias
def start_scheduler():
    """Alias — called by main.py startup."""
    try_record_today(config.OWM_API_KEY)

def record_in_background():
    """Record now in background — called on every dashboard visit."""
    def _run():
        try:
            record_today(config.OWM_API_KEY)
        except Exception as e:
            print(f"[recorder] Background record failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
