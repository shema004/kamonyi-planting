"""
recorder.py
-----------
Daily weather recorder — stores data in Supabase PostgreSQL.
Data is permanent and survives all deploys forever.
"""

import time
import threading
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
import sys
import os

sys.path.insert(0, str(Path(__file__).parent))
import config

# ── Database connection ────────────────────────────────────────────────────

SUPABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:0781468728shema@db.mxdrzafnhwpttdiquqjo.supabase.co:5432/postgres"
)

def get_conn():
    """Get a Supabase PostgreSQL connection."""
    import psycopg2
    return psycopg2.connect(SUPABASE_URL, sslmode="require", connect_timeout=15)


# ── Database setup ─────────────────────────────────────────────────────────

def init_db():
    """Create tables in Supabase if they don't exist."""
    try:
        conn = get_conn()
        c    = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS daily_weather (
                id          SERIAL PRIMARY KEY,
                date        TEXT NOT NULL,
                sector      TEXT NOT NULL,
                temp_max    REAL,
                temp_min    REAL,
                rainfall_mm REAL,
                humidity    REAL,
                description TEXT,
                recorded_at TEXT NOT NULL,
                UNIQUE(date, sector)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS recording_log (
                id          SERIAL PRIMARY KEY,
                date        TEXT NOT NULL,
                status      TEXT NOT NULL,
                message     TEXT,
                recorded_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS seasonal_predictions (
                id                      SERIAL PRIMARY KEY,
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
        c.execute("""
            CREATE TABLE IF NOT EXISTS seasonal_actuals (
                id                   SERIAL PRIMARY KEY,
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
                UNIQUE(year, sector, season)
            )
        """)
        conn.commit()
        conn.close()
        print("[recorder] ✅ Supabase tables ready")
    except Exception as e:
        print(f"[recorder] ⚠️ init_db error: {e}")


def migrate_sqlite_to_supabase():
    """
    One-time migration: copy all data from SQLite to Supabase.
    Safe to run multiple times — INSERT OR IGNORE skips existing rows.
    """
    import sqlite3
    from pathlib import Path

    sqlite_path = Path(config.DATA_DB_PATH)
    if not sqlite_path.exists():
        print("[recorder] No SQLite DB found — skipping migration")
        return

    try:
        sqlite_conn = sqlite3.connect(str(sqlite_path))
        sqlite_conn.row_factory = sqlite3.Row

        # Migrate daily_weather
        rows = sqlite_conn.execute("SELECT * FROM daily_weather").fetchall()
        if rows:
            pg_conn = get_conn()
            c = pg_conn.cursor()
            for r in rows:
                try:
                    c.execute("""
                        INSERT INTO daily_weather
                            (date, sector, temp_max, temp_min, rainfall_mm,
                             humidity, description, recorded_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (date, sector) DO NOTHING
                    """, (r["date"], r["sector"], r["temp_max"], r["temp_min"],
                          r["rainfall_mm"], r["humidity"], r["description"],
                          r["recorded_at"]))
                except:
                    pass
            pg_conn.commit()
            pg_conn.close()
            print(f"[recorder] ✅ Migrated {len(rows)} rows from SQLite to Supabase")

        sqlite_conn.close()
    except Exception as e:
        print(f"[recorder] Migration error: {e}")


def already_recorded_today() -> bool:
    today = date.today().isoformat()
    try:
        conn  = get_conn()
        cur   = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM daily_weather WHERE date=%s", (today,))
        count = cur.fetchone()[0]
        conn.close()
        return count >= 12
    except:
        return False


# ── Core recording ─────────────────────────────────────────────────────────

def record_today(api_key: str) -> dict:
    """Fetch weather for all 12 sectors and save to Supabase."""
    from weather_api import get_current_weather, SECTOR_COORDS

    today       = date.today().isoformat()
    recorded_at = datetime.now(timezone.utc).isoformat()
    saved, failed = [], []

    try:
        conn = get_conn()
        c    = conn.cursor()
    except Exception as e:
        print(f"[recorder] DB connection failed: {e}")
        return {"date": today, "status": "failed", "saved": [], "failed": list(SECTOR_COORDS.keys()), "message": str(e)}

    for sector in SECTOR_COORDS:
        try:
            w = get_current_weather(api_key, sector)
            if not w:
                failed.append(sector)
                continue
            c.execute("""
                INSERT INTO daily_weather
                    (date, sector, temp_max, temp_min, rainfall_mm,
                     humidity, description, recorded_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (date, sector) DO NOTHING
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

    try:
        c.execute(
            "INSERT INTO recording_log (date,status,message,recorded_at) VALUES (%s,%s,%s,%s)",
            (today, status, message, recorded_at)
        )
        conn.commit()
    except:
        pass

    conn.close()
    print(f"[recorder] {today}: {message}")
    return {"date": today, "status": status, "saved": saved,
            "failed": failed, "message": message}


# ── Scheduler ──────────────────────────────────────────────────────────────

def try_record_today(api_key: str):
    def _run():
        try:
            record_today(api_key)
        except Exception as e:
            print(f"[recorder] record failed: {e}")

    def _scheduler():
        _run()
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
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        conn   = get_conn()
        cur    = conn.cursor()
        cur.execute("""
            SELECT date, recorded_at, sector, rainfall_mm, temp_max, temp_min
            FROM daily_weather
            WHERE date >= %s
            ORDER BY date DESC, sector ASC
        """, (cutoff,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[recorder] get_all_records: {e}")
        return []


def get_recording_log(limit: int = 30) -> list:
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT date,
                   MAX(recorded_at)       AS recorded_at,
                   COUNT(DISTINCT sector) AS sectors_ok,
                   (12 - COUNT(DISTINCT sector)) AS sectors_failed,
                   '' AS failed_list,
                   CASE WHEN COUNT(DISTINCT sector)>=12 THEN 'ok' ELSE 'partial' END AS status
            FROM daily_weather
            GROUP BY date
            ORDER BY date DESC
            LIMIT %s
        """, (limit,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[recorder] get_recording_log: {e}")
        return []


def get_records_summary() -> dict:
    result = {
        "total_records": 0, "unique_days": 0,
        "date_from": None, "date_to": None,
        "last_recorded_at": None,
        "last_sectors_ok": 0, "last_sectors_failed": 0,
        "last_status": None,
    }
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) FROM daily_weather")
        row = cur.fetchone()
        if row:
            result["total_records"] = row[0] or 0
            result["unique_days"]   = row[1] or 0
            result["date_from"]     = row[2]
            result["date_to"]       = row[3]
        cur.execute("""
            SELECT MAX(recorded_at), COUNT(DISTINCT sector)
            FROM daily_weather
            WHERE date=(SELECT MAX(date) FROM daily_weather)
        """)
        last = cur.fetchone()
        if last and last[0]:
            result["last_recorded_at"]    = last[0]
            result["last_sectors_ok"]     = last[1] or 0
            result["last_sectors_failed"] = 12 - (last[1] or 0)
            result["last_status"]         = "ok" if (last[1] or 0) >= 12 else "partial"
        conn.close()
    except Exception as e:
        print(f"[recorder] get_records_summary: {e}")
    return result


def get_recorded_history(sector=None, limit_days=30):
    return get_all_records(limit_days)


def get_recorded_summary():
    return get_records_summary()


# ── Seasonal data ──────────────────────────────────────────────────────────

def backfill_actuals_from_excel(merged_df) -> int:
    import math
    from datetime import timedelta as td
    count = 0
    try:
        conn = get_conn()
        c    = conn.cursor()
        now  = datetime.now(timezone.utc).isoformat()
        for _, row in merged_df.iterrows():
            try:
                def safe(v):
                    try:
                        f = float(v)
                        return None if math.isnan(f) else f
                    except: return None
                onset_day  = safe(row["onset_day"])
                onset_day  = int(onset_day) if onset_day else None
                onset_date = None
                if onset_day:
                    d = date(int(row["year"]), 1, 1) + td(days=onset_day-1)
                    onset_date = d.strftime("%d %b")
                length_dek = safe(row.get("length_dekads"))
                length_wks = round(length_dek*10/7,1) if length_dek else None
                c.execute("""
                    INSERT INTO seasonal_actuals
                      (recorded_at,year,sector,season,actual_onset_day,
                       actual_onset_date,actual_length_dekads,actual_length_weeks,
                       actual_tmax,actual_tmin,actual_rainfall_mm,data_source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (year,sector,season) DO NOTHING
                """, (now,int(row["year"]),str(row["sector"]),str(row["season"]),
                      onset_day,onset_date,length_dek,length_wks,
                      safe(row.get("mean_max_temp")),safe(row.get("mean_min_temp")),
                      safe(row.get("total_rainfall")),"excel"))
                count += 1
            except: continue
        conn.commit()
        conn.close()
        if count: print(f"[recorder] Seeded {count} historical actuals")
    except Exception as e:
        print(f"[recorder] backfill error: {e}")
    return count


def save_prediction(prediction: dict) -> bool:
    try:
        conn = get_conn()
        conn.cursor().execute("""
            INSERT INTO seasonal_predictions
              (predicted_at,target_year,sector,season,
               predicted_onset_day,predicted_onset_date,
               predicted_length_dekads,predicted_length_weeks,
               predicted_tmax,predicted_tmin,predicted_rainfall_mm,confidence)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
        """, (
            datetime.now(timezone.utc).isoformat(),
            prediction.get("target_year"), prediction.get("sector"),
            prediction.get("season"), prediction.get("predicted_onset_day"),
            prediction.get("predicted_onset_date"),
            prediction.get("predicted_length_dekads"),
            prediction.get("predicted_length_weeks"),
            prediction.get("predicted_tmax"), prediction.get("predicted_tmin"),
            prediction.get("expected_rainfall_mm"), prediction.get("confidence"),
        ))
        conn.commit()
        conn.close()
        return True
    except: return False


def save_bulk_predictions(predictions: list) -> int:
    saved = sum(1 for p in predictions if save_prediction(p))
    if saved: print(f"[recorder] Saved {saved} predictions")
    return saved


def get_prediction_vs_actual(sector=None, season=None) -> list:
    return []


def get_actuals_summary() -> dict:
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM seasonal_actuals")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM seasonal_predictions")
        preds = cur.fetchone()[0]
        conn.close()
        return {"total_actuals": total, "total_predictions": preds}
    except:
        return {"total_actuals": 0, "total_predictions": 0}
