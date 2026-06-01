"""
recorder.py
-----------
Daily weather data recorder for Kamonyi district.

Records today's weather (per sector) into a local SQLite database.
This data will supplement the Excel historical data in future years,
making the model more accurate over time.

The recorder runs automatically every time the FastAPI app starts
(it records today once and skips if already recorded).

You can also run it standalone:   python app/recorder.py

Database: data/daily_records.db
Table:    daily_weather
  columns: date, sector, temp_max, temp_min, rainfall_mm,
           humidity, description, recorded_at
"""

import sqlite3
import time
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).parent))
import config


DB_PATH = Path(config.DATA_DB_PATH)


# ─── Database setup ────────────────────────────────────────────────────────

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

    # Table: model predictions saved at prediction time
    c.execute("""
        CREATE TABLE IF NOT EXISTS seasonal_predictions (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            predicted_at          TEXT NOT NULL,
            target_year           INTEGER NOT NULL,
            sector                TEXT NOT NULL,
            season                TEXT NOT NULL,
            predicted_onset_day   INTEGER,
            predicted_onset_date  TEXT,
            predicted_length_dekads REAL,
            predicted_length_weeks  REAL,
            predicted_tmax        REAL,
            predicted_tmin        REAL,
            predicted_rainfall_mm REAL,
            confidence            TEXT,
            model_version         TEXT DEFAULT '2.0',
            UNIQUE(target_year, sector, season, predicted_at)
        )
    """)

    # Table: actual observed season values (filled in after season ends)
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

    conn.commit()
    conn.close()
    print(f"[recorder] Database ready at: {DB_PATH}")


def already_recorded_today() -> bool:
    """Check if today's data has already been recorded for all sectors."""
    today = date.today().isoformat()
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        c.execute("SELECT COUNT(*) FROM daily_weather WHERE date = ?", (today,))
        count = c.fetchone()[0]
        conn.close()
        return count >= 5   # at least 5 sectors recorded = consider done
    except:
        return False


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
                INSERT OR REPLACE INTO daily_weather
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
            time.sleep(0.3)     # respect free-tier rate limit

        except Exception as e:
            print(f"[recorder] Error recording {sector}: {e}")
            failed.append(sector)

    conn.commit()

    # Log the recording attempt
    status  = "ok" if saved else "failed"
    message = f"Saved: {len(saved)} sectors. Failed: {len(failed)}"
    c.execute("""
        INSERT INTO recording_log (date, status, message, recorded_at)
        VALUES (?, ?, ?, ?)
    """, (today, status, message, recorded_at))
    conn.commit()
    conn.close()

    print(f"[recorder] {message}")
    return {"date": today, "status": status, "saved": saved, "failed": failed}


# ─── Query helpers (used by main.py API) ──────────────────────────────────

def get_recorded_history(sector: str = None, limit_days: int = 365) -> list:
    """
    Return recorded daily data for charts / model supplementation.
    If sector is None, returns all sectors.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if sector:
            c.execute("""
                SELECT date, sector, temp_max, temp_min, rainfall_mm, humidity, description
                FROM daily_weather
                WHERE sector = ?
                ORDER BY date DESC LIMIT ?
            """, (sector, limit_days))
        else:
            c.execute("""
                SELECT date, sector, temp_max, temp_min, rainfall_mm, humidity, description
                FROM daily_weather
                ORDER BY date DESC LIMIT ?
            """, (limit_days * 11,))  # 11 sectors
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except:
        return []


def get_recording_log(limit: int = 30) -> list:
    """Return recent recording log entries."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT date, status, message, recorded_at
            FROM recording_log
            ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except:
        return []


def get_recorded_summary() -> dict:
    """Summary stats about what's been recorded so far."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute("SELECT COUNT(DISTINCT date) FROM daily_weather")
        days = c.fetchone()[0]
        c.execute("SELECT MIN(date), MAX(date) FROM daily_weather")
        row = c.fetchone()
        c.execute("SELECT COUNT(*) FROM daily_weather")
        total = c.fetchone()[0]
        conn.close()
        return {
            "total_records": total,
            "unique_days":   days,
            "date_from":     row[0],
            "date_to":       row[1],
        }
    except:
        return {"total_records": 0, "unique_days": 0, "date_from": None, "date_to": None}


# ─── Startup trigger ───────────────────────────────────────────────────────

def try_record_today(api_key: str):
    """
    Called at app startup. Records today if not already done.
    Runs in background so it doesn't delay app startup.
    """
    import threading
    def _run():
        try:
            init_db()
            if already_recorded_today():
                print("[recorder] Today already recorded — skipping.")
                return
            print("[recorder] Recording today's weather data ...")
            result = record_today(api_key)
            if result["saved"]:
                print(f"[recorder] ✅ Saved {len(result['saved'])} sectors for {result['date']}")
            else:
                print(f"[recorder] ⚠ No sectors saved. Check API key and internet connection.")
                print(f"[recorder]   Failed sectors: {result.get('failed', [])}")
        except Exception as e:
            print(f"[recorder] ❌ Startup recording failed: {e}")
            import traceback
            traceback.print_exc()
    t = threading.Thread(target=_run, daemon=True)
    t.start()




# ── Seasonal predictions storage ───────────────────────────────────────────

def save_prediction(prediction: dict) -> bool:
    """Save a model prediction to DB. Called automatically."""
    from datetime import datetime, timezone
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        c.execute("""
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
        return c.rowcount > 0
    except Exception as e:
        print(f"[recorder] Could not save prediction: {e}")
        return False


def save_bulk_predictions(predictions: list) -> int:
    """Save a list of predictions. Returns count saved."""
    saved = sum(1 for p in predictions if save_prediction(p))
    if saved:
        print(f"[recorder] Saved {saved} predictions to DB")
    return saved


def backfill_actuals_from_excel(merged_df) -> int:
    """Populate seasonal_actuals from the historical Excel data."""
    from datetime import datetime, timezone, date, timedelta
    import math
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
                except: return None
            onset_day = safe(row["onset_day"])
            onset_day = int(onset_day) if onset_day else None
            onset_date = None
            if onset_day:
                d = date(int(row["year"]), 1, 1) + timedelta(days=onset_day - 1)
                onset_date = d.strftime("%d %b")
            length_dek = safe(row.get("length_dekads"))
            length_wks = round(length_dek * 10 / 7, 1) if length_dek else None
            c.execute("""
                INSERT OR IGNORE INTO seasonal_actuals
                  (recorded_at, year, sector, season,
                   actual_onset_day, actual_onset_date,
                   actual_length_dekads, actual_length_weeks,
                   actual_tmax, actual_tmin, actual_rainfall_mm, data_source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (now, int(row["year"]), str(row["sector"]), str(row["season"]),
                  onset_day, onset_date, length_dek, length_wks,
                  safe(row.get("mean_max_temp")), safe(row.get("mean_min_temp")),
                  safe(row.get("total_rainfall")), "excel"))
            count += 1
        except: continue
    conn.commit()
    conn.close()
    print(f"[recorder] Backfilled {count} historical actuals from Excel")
    return count


def get_prediction_vs_actual(sector: str = None, season: str = None) -> list:
    """Compare saved predictions against actual observed values."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        wheres, params = [], []
        if sector: wheres.append("p.sector = ?"); params.append(sector)
        if season: wheres.append("p.season = ?"); params.append(season)
        where = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        c.execute(f"""
            SELECT p.target_year AS year, p.sector, p.season,
                   p.predicted_onset_day, p.predicted_onset_date,
                   p.predicted_length_weeks, p.predicted_tmax, p.predicted_tmin,
                   p.predicted_rainfall_mm, p.confidence, p.predicted_at,
                   a.actual_onset_day, a.actual_onset_date, a.actual_length_weeks,
                   a.actual_tmax, a.actual_tmin, a.actual_rainfall_mm, a.data_source,
                   (a.actual_onset_day - p.predicted_onset_day)       AS onset_error_days,
                   (a.actual_rainfall_mm - p.predicted_rainfall_mm)   AS rainfall_error_mm,
                   (a.actual_tmax - p.predicted_tmax)                 AS tmax_error,
                   (a.actual_length_weeks - p.predicted_length_weeks) AS length_error_weeks
            FROM seasonal_predictions p
            JOIN seasonal_actuals a
              ON p.target_year = a.year AND p.sector = a.sector AND p.season = a.season
            {where}
            ORDER BY p.target_year DESC, p.sector, p.season
        """, params)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[recorder] get_prediction_vs_actual error: {e}")
        return []


def get_actuals_summary() -> dict:
    """Summary stats about what is stored in DB."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM seasonal_actuals")
        total = c.fetchone()[0]
        c.execute("SELECT MIN(year), MAX(year) FROM seasonal_actuals")
        row = c.fetchone()
        c.execute("SELECT COUNT(*) FROM seasonal_predictions")
        preds = c.fetchone()[0]
        conn.close()
        return {"total_actuals": total, "year_from": row[0], "year_to": row[1],
                "total_predictions": preds}
    except:
        return {"total_actuals": 0, "total_predictions": 0}


# ─── Standalone run ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    key = sys.argv[1] if len(sys.argv) > 1 else config.OWM_API_KEY
    print(f"[recorder] Running standalone recording with key: {key[:8]}...")
    init_db()
    result = record_today(key)
    print(f"[recorder] Done: {result}")
    summary = get_recorded_summary()
    print(f"[recorder] Total in DB: {summary}")
