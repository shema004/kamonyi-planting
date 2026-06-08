"""
recorder.py — Daily weather recorder for Kamonyi Planting System
Records rainfall, tmax, tmin for all 12 sectors every day automatically.
Runs as a persistent background thread — checks every hour, records if new day.
"""

import sqlite3, time, threading
from datetime import datetime, timezone, date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import config

DB_PATH = Path(config.DATA_DB_PATH)

SECTORS = [
    "Gacurabwenge","Karama","Kayenzi","Kayumbu",
    "Mugina","Musambira","Ngamba","Nyamiyaga",
    "Nyarubaka","Rugarika","Rukoma","Runda",
]


# ── Database setup ─────────────────────────────────────────────────────────

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c    = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            sector      TEXT NOT NULL,
            rainfall_mm REAL,
            temp_max    REAL,
            temp_min    REAL,
            UNIQUE(date, sector)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS recording_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT NOT NULL,
            recorded_at     TEXT NOT NULL,
            sectors_ok      INTEGER NOT NULL DEFAULT 0,
            sectors_failed  INTEGER NOT NULL DEFAULT 0,
            failed_list     TEXT DEFAULT '',
            status          TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS seasonal_actuals (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at          TEXT NOT NULL,
            year                 INTEGER NOT NULL,
            sector               TEXT NOT NULL,
            season               TEXT NOT NULL,
            actual_onset_day     INTEGER,
            actual_onset_date    TEXT,
            actual_length_dekads REAL,
            actual_length_weeks  REAL,
            actual_tmax          REAL,
            actual_tmin          REAL,
            actual_rainfall_mm   REAL,
            data_source          TEXT DEFAULT 'excel',
            notes                TEXT,
            UNIQUE(year, sector, season)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS seasonal_predictions (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            predicted_at            TEXT NOT NULL,
            target_year             INTEGER NOT NULL,
            sector                  TEXT NOT NULL,
            season                  TEXT NOT NULL,
            predicted_onset_day     INTEGER,
            predicted_onset_date    TEXT,
            predicted_length_dekads REAL,
            predicted_length_weeks  REAL,
            predicted_tmax          REAL,
            predicted_tmin          REAL,
            predicted_rainfall_mm   REAL,
            confidence              TEXT,
            UNIQUE(target_year, sector, season, predicted_at)
        )
    """)
    conn.commit()
    conn.close()
    print(f"[recorder] DB ready: {DB_PATH}")


# ── Recording ──────────────────────────────────────────────────────────────

def _fetch_weather(sector: str) -> dict | None:
    """Fetch current weather for one sector with 3 retries."""
    from weather_api import get_current_weather
    for attempt in range(3):
        try:
            w = get_current_weather(config.OWM_API_KEY, sector)
            if w:
                return w
            time.sleep(2)
        except Exception as e:
            print(f"[recorder] {sector} attempt {attempt+1} error: {e}")
            time.sleep(3)
    return None


def record_today() -> dict:
    """
    Fetch weather for all 12 sectors and save rainfall, tmax, tmin to DB.
    Returns summary dict.
    """
    today       = date.today().isoformat()
    recorded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    saved, failed = [], []

    conn = sqlite3.connect(str(DB_PATH))
    c    = conn.cursor()

    for sector in SECTORS:
        w = _fetch_weather(sector)
        if not w:
            failed.append(sector)
            print(f"[recorder] ❌ {sector}: all 3 attempts failed")
            time.sleep(0.5)
            continue

        try:
            c.execute("""
                INSERT OR REPLACE INTO daily_records
                  (date, recorded_at, sector, rainfall_mm, temp_max, temp_min)
                VALUES (?,?,?,?,?,?)
            """, (
                today,
                recorded_at,
                sector,
                round(float(w.get("rain_1h_mm") or 0.0), 2),
                round(float(w.get("temp_max_c") or 0.0), 2),
                round(float(w.get("temp_min_c") or 0.0), 2),
            ))
            saved.append(sector)
        except Exception as e:
            print(f"[recorder] DB write failed for {sector}: {e}")
            failed.append(sector)

        time.sleep(0.3)   # polite delay between sectors

    status = "ok" if not failed else ("partial" if saved else "failed")
    c.execute("""
        INSERT INTO recording_log
          (date, recorded_at, sectors_ok, sectors_failed, failed_list, status)
        VALUES (?,?,?,?,?,?)
    """, (today, recorded_at, len(saved), len(failed), ",".join(failed), status))

    conn.commit()
    conn.close()

    emoji = "✅" if not failed else ("⚠️" if saved else "❌")
    msg   = f"{emoji} {today} — {len(saved)}/12 sectors recorded"
    if failed:
        msg += f" | Failed: {', '.join(failed)}"
    print(f"[recorder] {msg}")
    return {"date": today, "saved": saved, "failed": failed, "status": status, "message": msg}


def already_recorded_today() -> bool:
    """True only if ALL 12 sectors recorded for today."""
    today = date.today().isoformat()
    try:
        conn  = sqlite3.connect(str(DB_PATH))
        count = conn.execute(
            "SELECT COUNT(DISTINCT sector) FROM daily_records WHERE date=?", (today,)
        ).fetchone()[0]
        conn.close()
        return count >= 12
    except:
        return False


def start_daily_recorder():
    """
    Records immediately on startup, then every day at 05:00 and 17:00 Rwanda time.
    Rwanda is UTC+2, so scheduled times in UTC are 03:00 and 15:00.
    Thread runs forever — checks every minute if it's time to record.
    """
    # Scheduled recording times in UTC (Rwanda UTC+2)
    RECORD_TIMES_UTC = [
        (3, 0),   # 05:00 Rwanda time
        (15, 0),  # 17:00 Rwanda time
    ]

    def _is_record_time(now_utc):
        """True if current UTC time matches one of the scheduled times (within 1 min)."""
        for h, m in RECORD_TIMES_UTC:
            if now_utc.hour == h and now_utc.minute == m:
                return True
        return False

    def _scheduler():
        from datetime import datetime, timezone

        # ── 1. Record immediately on startup ──────────────────────────────
        print("[recorder] 🚀 Startup — recording all 12 sectors now...")
        try:
            record_today()
        except Exception as e:
            print(f"[recorder] ❌ Startup record failed: {e}")

        last_recorded_slot = None   # track which slot we last recorded

        # ── 2. Loop forever — check every 60 seconds ──────────────────────
        while True:
            time.sleep(60)
            try:
                now_utc  = datetime.now(timezone.utc)
                slot_key = (now_utc.hour, now_utc.minute)   # unique per minute

                if _is_record_time(now_utc) and slot_key != last_recorded_slot:
                    rw_hour = (now_utc.hour + 2) % 24
                    print(f"[recorder] 🕐 Scheduled recording at {rw_hour:02d}:00 Rwanda time...")
                    record_today()
                    last_recorded_slot = slot_key

            except Exception as e:
                print(f"[recorder] ❌ Scheduled record failed: {e}")
                import traceback; traceback.print_exc()

    t = threading.Thread(target=_scheduler, daemon=True, name="daily-recorder")
    t.start()
    print("[recorder] ✅ Scheduler started — records at startup, 05:00 and 17:00 Rwanda time")


# ── Query functions ────────────────────────────────────────────────────────

def get_all_records(days: int = 30) -> list:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT date, recorded_at, sector, rainfall_mm, temp_max, temp_min
            FROM daily_records
            ORDER BY date DESC, sector ASC
            LIMIT ?
        """, (days * 12,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[recorder] get_all_records error: {e}")
        return []


def get_recording_log(limit: int = 30) -> list:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT date, recorded_at, sectors_ok, sectors_failed, failed_list, status
            FROM recording_log ORDER BY date DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except:
        return []


def get_records_summary() -> dict:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        c.execute("SELECT COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) FROM daily_records")
        total, days, d_from, d_to = c.fetchone()
        c.execute("""SELECT recorded_at, sectors_ok, sectors_failed, status
                     FROM recording_log ORDER BY date DESC LIMIT 1""")
        last = c.fetchone()
        conn.close()
        return {
            "total_records":       total or 0,
            "unique_days":         days  or 0,
            "date_from":           d_from,
            "date_to":             d_to,
            "last_recorded_at":    last[0] if last else None,
            "last_sectors_ok":     last[1] if last else 0,
            "last_sectors_failed": last[2] if last else 0,
            "last_status":         last[3] if last else None,
        }
    except:
        return {"total_records": 0, "unique_days": 0}


# ── Seasonal actuals (seeded from Excel) ──────────────────────────────────

def backfill_actuals_from_excel(merged_df) -> int:
    import math
    from datetime import timedelta
    conn  = sqlite3.connect(str(DB_PATH))
    c     = conn.cursor()
    now   = datetime.now(timezone.utc).isoformat()
    count = 0
    for _, row in merged_df.iterrows():
        try:
            def safe(v):
                try:
                    f = float(v)
                    return None if math.isnan(f) else f
                except:
                    return None
            onset_day  = safe(row["onset_day"])
            onset_day  = int(onset_day) if onset_day else None
            onset_date = None
            if onset_day:
                d = date(int(row["year"]), 1, 1) + timedelta(days=onset_day - 1)
                onset_date = d.strftime("%d %b")
            length_dek = safe(row.get("length_dekads"))
            length_wks = round(length_dek * 10 / 7, 1) if length_dek else None
            c.execute("""
                INSERT OR IGNORE INTO seasonal_actuals
                  (recorded_at, year, sector, season, actual_onset_day, actual_onset_date,
                   actual_length_dekads, actual_length_weeks, actual_tmax, actual_tmin,
                   actual_rainfall_mm, data_source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (now, int(row["year"]), str(row["sector"]), str(row["season"]),
                  onset_day, onset_date, length_dek, length_wks,
                  safe(row.get("mean_max_temp")), safe(row.get("mean_min_temp")),
                  safe(row.get("total_rainfall")), "excel"))
            count += 1
        except:
            continue
    conn.commit()
    conn.close()
    if count:
        print(f"[recorder] Seeded {count} historical actuals from Excel")
    return count


def save_prediction(prediction: dict) -> bool:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT OR IGNORE INTO seasonal_predictions
              (predicted_at, target_year, sector, season,
               predicted_onset_day, predicted_onset_date,
               predicted_length_dekads, predicted_length_weeks,
               predicted_tmax, predicted_tmin, predicted_rainfall_mm, confidence)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            prediction.get("target_year"),
            prediction.get("sector"),
            prediction.get("season"),
            prediction.get("predicted_onset_day"),
            prediction.get("predicted_onset_date"),
            prediction.get("predicted_length_dekads"),
            prediction.get("predicted_length_weeks"),
            prediction.get("predicted_tmax"),
            prediction.get("predicted_tmin"),
            prediction.get("expected_rainfall_mm"),
            prediction.get("confidence"),
        ))
        conn.commit()
        conn.close()
        return True
    except:
        return False


def save_bulk_predictions(predictions: list) -> int:
    saved = sum(1 for p in predictions if save_prediction(p))
    if saved:
        print(f"[recorder] Saved {saved} predictions")
    return saved


def get_prediction_vs_actual(sector: str = None, season: str = None) -> list:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        wheres, params = [], []
        if sector:
            wheres.append("p.sector=?"); params.append(sector)
        if season:
            wheres.append("p.season=?"); params.append(season)
        where = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        rows = conn.execute(f"""
            SELECT p.target_year AS year, p.sector, p.season,
                   p.predicted_onset_day, p.predicted_onset_date,
                   p.predicted_length_weeks, p.predicted_tmax, p.predicted_tmin,
                   p.predicted_rainfall_mm, p.confidence, p.predicted_at,
                   a.actual_onset_day, a.actual_onset_date, a.actual_length_weeks,
                   a.actual_tmax, a.actual_tmin, a.actual_rainfall_mm,
                   (a.actual_onset_day - p.predicted_onset_day) AS onset_error_days,
                   (a.actual_rainfall_mm - p.predicted_rainfall_mm) AS rainfall_error_mm
            FROM seasonal_predictions p
            JOIN seasonal_actuals a
              ON p.target_year=a.year AND p.sector=a.sector AND p.season=a.season
            {where}
            ORDER BY p.target_year DESC, p.sector, p.season
        """, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[recorder] get_prediction_vs_actual error: {e}")
        return []


def get_actuals_summary() -> dict:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        c.execute("SELECT COUNT(*), MIN(year), MAX(year) FROM seasonal_actuals")
        total, y_from, y_to = c.fetchone()
        c.execute("SELECT COUNT(*) FROM seasonal_predictions")
        preds = c.fetchone()[0]
        conn.close()
        return {"total_actuals": total or 0, "year_from": y_from,
                "year_to": y_to, "total_predictions": preds or 0}
    except:
        return {"total_actuals": 0, "total_predictions": 0}
