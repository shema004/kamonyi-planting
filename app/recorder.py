"""
recorder.py
-----------
Daily weather recorder for Kamonyi district.

Records daily weather per sector into SQLite.
After 365 days of data, it is automatically included in the predictive
model to improve accuracy (alongside the historical Excel data).
"""

import sqlite3, time, threading, math
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).parent))
import config

DB_PATH = Path(config.DATA_DB_PATH)

# ── Database setup ─────────────────────────────────────────────────────────
def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c    = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_weather (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            sector      TEXT NOT NULL,
            temp_max    REAL, temp_min REAL, temp_current REAL,
            rainfall_mm REAL, humidity REAL, wind_speed REAL,
            cloud_cover REAL, pressure REAL,
            description TEXT, icon TEXT, lat REAL, lon REAL,
            recorded_at TEXT NOT NULL,
            UNIQUE(date, sector)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS recording_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL, status TEXT NOT NULL,
            message TEXT, recorded_at TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS seasonal_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            predicted_at TEXT NOT NULL,
            target_year INTEGER NOT NULL,
            sector TEXT NOT NULL, season TEXT NOT NULL,
            predicted_onset_day INTEGER, predicted_onset_date TEXT,
            predicted_length_dekads REAL, predicted_length_weeks REAL,
            predicted_tmax REAL, predicted_tmin REAL,
            predicted_rainfall_mm REAL, confidence TEXT,
            model_version TEXT DEFAULT '2.0',
            UNIQUE(target_year, sector, season, predicted_at)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS seasonal_actuals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            year INTEGER NOT NULL,
            sector TEXT NOT NULL, season TEXT NOT NULL,
            actual_onset_day INTEGER, actual_onset_date TEXT,
            actual_length_dekads REAL, actual_length_weeks REAL,
            actual_tmax REAL, actual_tmin REAL,
            actual_rainfall_mm REAL,
            data_source TEXT DEFAULT 'excel', notes TEXT,
            UNIQUE(year, sector, season)
        )
    """)

    # Admin users table
    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL UNIQUE,
            password    TEXT NOT NULL,
            name        TEXT,
            role        TEXT DEFAULT 'admin',
            created_at  TEXT NOT NULL,
            last_login  TEXT
        )
    """)

    # System settings table
    c.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key         TEXT PRIMARY KEY,
            value       TEXT,
            updated_at  TEXT,
            updated_by  TEXT
        )
    """)

    # Admin users table
    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL UNIQUE,
            password    TEXT NOT NULL,
            name        TEXT NOT NULL DEFAULT 'Admin',
            role        TEXT NOT NULL DEFAULT 'admin',
            created_at  TEXT NOT NULL,
            last_login  TEXT
        )
    """)

    # System settings table
    c.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key         TEXT PRIMARY KEY,
            value       TEXT,
            updated_at  TEXT,
            updated_by  TEXT
        )
    """)

    # Calendar-based daily recordings (one row per date per sector)
    c.execute("""
        CREATE TABLE IF NOT EXISTS recorded_calendar (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            sector      TEXT NOT NULL,
            temp_max    REAL,
            temp_min    REAL,
            rainfall_mm REAL,
            humidity    REAL,
            wind_speed  REAL,
            cloud_cover REAL,
            pressure    REAL,
            description TEXT,
            icon        TEXT,
            auto_integrated INTEGER DEFAULT 0,
            recorded_at TEXT NOT NULL,
            UNIQUE(date, sector)
        )
    """)

    # Insert first admin if none exist
    c.execute("SELECT COUNT(*) FROM admin_users")
    if c.fetchone()[0] == 0:
        from datetime import datetime, timezone
        import hashlib
        pw_hash = hashlib.sha256("Kamonyi@2026!".encode()).hexdigest()
        c.execute("""
            INSERT INTO admin_users (email, password, name, role, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, ("hemarfait@gmail.com", pw_hash, "Hemarfait", "superadmin",
              datetime.now(timezone.utc).isoformat()))
        print("[recorder] First admin created: hemarfait@gmail.com")

    # Default system settings
    defaults = {
        "site_title":        "Kamonyi Planting Predictor",
        "ribbon_image_url":  "",
        "auto_integrate_months": "12",
        "planting_window_days":  "10",
        "onset_threshold_mm":    "20",
    }
    for key, val in defaults.items():
        c.execute("INSERT OR IGNORE INTO system_settings (key,value,updated_at,updated_by) VALUES (?,?,?,?)",
                  (key, val, "", "system"))

    conn.commit()
    conn.close()
    print(f"[recorder] Database ready at: {DB_PATH}")

    # Seed first admin
    _seed_first_admin()

def _seed_first_admin():
    """Create the first admin user if no admins exist."""
    import hashlib, os
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM admin_users")
    if c.fetchone()[0] == 0:
        now = datetime.now(timezone.utc).isoformat()
        pwd = hashlib.sha256("Kamonyi@2026!".encode()).hexdigest()
        c.execute("""
            INSERT INTO admin_users (email, password, name, role, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, ("hemarfait@gmail.com", pwd, "System Administrator", "superadmin", now))
        conn.commit()
        print("[recorder] First admin created: hemarfait@gmail.com")
    conn.close()

# ── Recording ──────────────────────────────────────────────────────────────
def already_recorded_today():
    today = date.today().isoformat()
    conn  = sqlite3.connect(str(DB_PATH))
    c     = conn.cursor()
    c.execute("SELECT COUNT(*) FROM daily_weather WHERE date=?", (today,))
    count = c.fetchone()[0]
    conn.close()
    return count > 0

def record_today(api_key: str) -> dict:
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
                failed.append(sector); continue
            c.execute("""
                INSERT OR REPLACE INTO daily_weather
                  (date, sector, temp_max, temp_min, temp_current,
                   rainfall_mm, humidity, wind_speed, cloud_cover,
                   pressure, description, icon, lat, lon, recorded_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (today, sector,
                  w.get("temp_max_c"), w.get("temp_min_c"), w.get("temp_c"),
                  w.get("rain_1h_mm",0.0), w.get("humidity_pct"),
                  w.get("wind_speed_ms"), w.get("cloud_cover_pct"),
                  w.get("pressure_hpa"), w.get("description",""),
                  w.get("icon",""), w.get("lat"), w.get("lon"),
                  recorded_at))
            saved.append(sector)
            time.sleep(config.API_CALL_DELAY)
        except Exception as e:
            failed.append(sector)
            print(f"[recorder] Error recording {sector}: {e}")

    # Log the result
    msg = f"Saved: {len(saved)} sectors. Failed: {len(failed)}"
    if failed: msg += f" ({', '.join(failed)})"
    status = "ok" if len(saved) > 0 else "error"
    c.execute("INSERT INTO recording_log (date,status,message,recorded_at) VALUES (?,?,?,?)",
              (today, status, msg, recorded_at))
    conn.commit()
    conn.close()
    return {"date": today, "saved": saved, "failed": failed, "status": status}

def try_record_today(api_key: str):
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
                print(f"[recorder] ⚠ No sectors saved. Check API key.")
                print(f"[recorder]   Failed: {result.get('failed', [])}")
        except Exception as e:
            print(f"[recorder] ❌ Startup recording failed: {e}")
            import traceback; traceback.print_exc()
    t = threading.Thread(target=_run, daemon=True)
    t.start()

# ── Auto-integration into model ────────────────────────────────────────────
def get_recorded_data_for_model() -> dict:
    """
    Returns recorded daily_weather data formatted for model integration.
    Only returns data for sectors/seasons that have >= 365 days of records
    (approximately one full year), making it reliable for predictions.

    Returns dict with:
      - ready: bool — whether any data is ready to integrate
      - seasons_ready: list of (sector, season, year) tuples with enough data
      - summary: stats about what's available
      - dataframe: pandas DataFrame ready to merge with Excel data (if ready)
    """
    import pandas as pd
    import numpy as np

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Get all daily weather records
    c.execute("""
        SELECT date, sector, temp_max, temp_min, rainfall_mm
        FROM daily_weather
        WHERE temp_max IS NOT NULL
        ORDER BY date, sector
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        return {"ready": False, "seasons_ready": [], "summary": {"total_days": 0},
                "dataframe": None}

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"]  = pd.to_datetime(df["date"])
    df["year"]  = df["date"].dt.year
    df["doy"]   = df["date"].dt.dayofyear
    df["month"] = df["date"].dt.month

    # Assign season based on day of year
    def assign_season(doy):
        if 32 <= doy <= 181:  return "B"
        if doy >= 244 or doy <= 40: return "A"
        return None  # transition

    df["season"] = df["doy"].apply(assign_season)
    df = df[df["season"].notna()]

    # Count days per sector per season per year
    season_counts = df.groupby(["sector","season","year"]).size().reset_index(name="n_days")

    # Only seasons with >= 60 days of data qualify (roughly 2 dekads minimum)
    MIN_DAYS = 60
    ready_seasons = season_counts[season_counts["n_days"] >= MIN_DAYS]

    if ready_seasons.empty:
        total_days = df["date"].nunique()
        return {"ready": False, "seasons_ready": [],
                "summary": {"total_days": total_days,
                             "days_needed": MIN_DAYS,
                             "max_days_so_far": int(season_counts["n_days"].max()) if not season_counts.empty else 0},
                "dataframe": None}

    # Aggregate into seasonal summaries (like the Excel data)
    seasonal_rows = []
    for _, row in ready_seasons.iterrows():
        sector, season, year = row["sector"], row["season"], row["year"]
        subset = df[(df["sector"]==sector) & (df["season"]==season) & (df["year"]==year)]

        # Estimate onset day: first day with rainfall > 2mm after season start
        onset_candidates = subset[subset["rainfall_mm"] > 2.0]
        if not onset_candidates.empty:
            onset_day = int(onset_candidates.iloc[0]["doy"])
        else:
            onset_day = int(subset["doy"].min())

        # Season length in dekads
        doy_min = int(subset["doy"].min())
        doy_max = int(subset["doy"].max())
        length_dekads = round((doy_max - doy_min) / 10, 1)

        # Total rainfall (extrapolate from recorded days to full season)
        recorded_days = len(subset)
        season_len_days = doy_max - doy_min
        if season_len_days > 0 and recorded_days > 0:
            daily_rate = subset["rainfall_mm"].fillna(0).sum() / recorded_days
            total_rainfall = round(daily_rate * season_len_days, 1)
        else:
            total_rainfall = None

        mean_tmax = round(float(subset["temp_max"].mean()), 2) if not subset["temp_max"].isna().all() else None
        mean_tmin = round(float(subset["temp_min"].mean()), 2) if not subset["temp_min"].isna().all() else None

        seasonal_rows.append({
            "year":           year,
            "sector":         sector,
            "season":         season,
            "onset_day":      onset_day,
            "length_dekads":  length_dekads,
            "total_rainfall": total_rainfall,
            "mean_max_temp":  mean_tmax,
            "mean_min_temp":  mean_tmin,
            "data_source":    "recorded",
            "n_days":         int(row["n_days"]),
        })

    result_df = pd.DataFrame(seasonal_rows)
    seasons_ready = list(ready_seasons[["sector","season","year"]].itertuples(index=False, name=None))

    return {
        "ready":         True,
        "seasons_ready": seasons_ready,
        "dataframe":     result_df,
        "summary": {
            "total_days":       df["date"].nunique(),
            "sectors_with_data":df["sector"].nunique(),
            "seasons_integrated": len(seasons_ready),
            "first_date":       str(df["date"].min().date()),
            "last_date":        str(df["date"].max().date()),
        }
    }

# ── Predictions & actuals ──────────────────────────────────────────────────
def save_prediction(prediction: dict) -> bool:
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
            prediction.get("sector"), prediction.get("season"),
            prediction.get("predicted_onset_day"),
            prediction.get("predicted_onset_date"),
            prediction.get("predicted_length_dekads"),
            prediction.get("predicted_length_weeks"),
            prediction.get("predicted_tmax"),
            prediction.get("predicted_tmin"),
            prediction.get("expected_rainfall_mm"),
            prediction.get("confidence"),
        ))
        conn.commit(); conn.close()
        return c.rowcount > 0
    except Exception as e:
        print(f"[recorder] Could not save prediction: {e}")
        return False

def save_bulk_predictions(predictions: list) -> int:
    saved = sum(1 for p in predictions if save_prediction(p))
    if saved: print(f"[recorder] Saved {saved} predictions to DB")
    return saved

def backfill_actuals_from_excel(merged_df) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    c    = conn.cursor()
    now  = datetime.now(timezone.utc).isoformat()
    count = 0
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
                d = date(int(row["year"]),1,1) + timedelta(days=onset_day-1)
                onset_date = d.strftime("%d %b")
            length_dek = safe(row.get("length_dekads"))
            length_wks = round(length_dek*10/7,1) if length_dek else None
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
    conn.commit(); conn.close()
    print(f"[recorder] Backfilled {count} historical actuals from Excel")
    return count

def get_prediction_vs_actual(sector=None, season=None) -> list:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        wheres, params = [], []
        if sector: wheres.append("p.sector=?"); params.append(sector)
        if season: wheres.append("p.season=?"); params.append(season)
        where = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        c.execute(f"""
            SELECT p.target_year AS year, p.sector, p.season,
                   p.predicted_onset_day, p.predicted_onset_date,
                   p.predicted_length_weeks, p.predicted_tmax, p.predicted_tmin,
                   p.predicted_rainfall_mm, p.confidence, p.predicted_at,
                   a.actual_onset_day, a.actual_onset_date, a.actual_length_weeks,
                   a.actual_tmax, a.actual_tmin, a.actual_rainfall_mm, a.data_source,
                   (a.actual_onset_day - p.predicted_onset_day) AS onset_error_days,
                   (a.actual_rainfall_mm - p.predicted_rainfall_mm) AS rainfall_error_mm,
                   (a.actual_tmax - p.predicted_tmax) AS tmax_error,
                   (a.actual_length_weeks - p.predicted_length_weeks) AS length_error_weeks
            FROM seasonal_predictions p
            JOIN seasonal_actuals a
              ON p.target_year=a.year AND p.sector=a.sector AND p.season=a.season
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

# ── Summary & log ──────────────────────────────────────────────────────────
def get_recorded_summary() -> dict:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute("SELECT COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) FROM daily_weather")
        r = c.fetchone()
        conn.close()
        return {"total_records": r[0], "unique_days": r[1],
                "date_from": r[2], "date_to": r[3]}
    except:
        return {"total_records": 0, "unique_days": 0}

def get_recording_log(limit=20) -> list:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM recording_log ORDER BY id DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except:
        return []

def get_recorded_history(sector=None, days=30) -> list:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        if sector:
            c.execute("SELECT * FROM daily_weather WHERE sector=? AND date>=? ORDER BY date DESC", (sector, cutoff))
        else:
            c.execute("SELECT * FROM daily_weather WHERE date>=? ORDER BY date DESC, sector", (cutoff,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except:
        return []

# ── Admin user management ──────────────────────────────────────────────────
def get_all_admins() -> list:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT id, email, name, role, created_at, last_login FROM admin_users ORDER BY id")
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except:
        return []

def create_admin(email: str, password: str, name: str, role: str = "admin") -> bool:
    import hashlib
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        pwd  = hashlib.sha256(password.encode()).hexdigest()
        now  = datetime.now(timezone.utc).isoformat()
        c.execute("INSERT INTO admin_users (email,password,name,role,created_at) VALUES (?,?,?,?,?)",
                  (email, pwd, name, role, now))
        conn.commit(); conn.close()
        return True
    except Exception as e:
        print(f"[recorder] create_admin error: {e}")
        return False

def update_admin_password(email: str, new_password: str) -> bool:
    import hashlib
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        pwd  = hashlib.sha256(new_password.encode()).hexdigest()
        c.execute("UPDATE admin_users SET password=? WHERE email=?", (pwd, email))
        conn.commit(); conn.close()
        return c.rowcount > 0
    except:
        return False

def delete_admin(admin_id: int) -> bool:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        c.execute("DELETE FROM admin_users WHERE id=? AND role != 'superadmin'", (admin_id,))
        conn.commit(); conn.close()
        return c.rowcount > 0
    except:
        return False

def verify_admin(email: str, password: str) -> Optional[dict]:
    import hashlib
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c    = conn.cursor()
        pwd  = hashlib.sha256(password.encode()).hexdigest()
        c.execute("SELECT id,email,name,role FROM admin_users WHERE email=? AND password=?",
                  (email, pwd))
        row = c.fetchone()
        if row:
            now = datetime.now(timezone.utc).isoformat()
            c.execute("UPDATE admin_users SET last_login=? WHERE email=?", (now, email))
            conn.commit()
        conn.close()
        return dict(row) if row else None
    except:
        return None

# ── System settings ────────────────────────────────────────────────────────
def get_setting(key: str, default=None):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        c.execute("SELECT value FROM system_settings WHERE key=?", (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default
    except:
        return default

def set_setting(key: str, value: str, updated_by: str = "system"):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        now  = datetime.now(timezone.utc).isoformat()
        c.execute("""
            INSERT INTO system_settings (key,value,updated_at,updated_by)
            VALUES (?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,
            updated_at=excluded.updated_at, updated_by=excluded.updated_by
        """, (key, str(value), now, updated_by))
        conn.commit(); conn.close()
        return True
    except:
        return False

def get_all_settings() -> list:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM system_settings ORDER BY key")
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except:
        return []



# ── Calendar recording & auto-integration ─────────────────────────────────

def record_to_calendar(api_key: str) -> dict:
    """Record today's weather into calendar table. One row per sector per date."""
    from datetime import datetime, timezone, date as date_type
    from weather_api import get_current_weather, SECTOR_COORDS
    today = date_type.today().isoformat()
    now   = datetime.now(timezone.utc).isoformat()
    conn  = sqlite3.connect(str(DB_PATH))
    c     = conn.cursor()
    saved, failed = [], []
    for sector in SECTOR_COORDS:
        try:
            w = get_current_weather(api_key, sector)
            if not w: failed.append(sector); continue
            c.execute("""
                INSERT OR REPLACE INTO recorded_calendar
                  (date,sector,temp_max,temp_min,rainfall_mm,humidity,
                   wind_speed,cloud_cover,pressure,description,icon,recorded_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (today, sector,
                  w.get("temp_max_c"), w.get("temp_min_c"), w.get("rain_1h_mm",0),
                  w.get("humidity_pct"), w.get("wind_speed_ms"), w.get("cloud_cover_pct"),
                  w.get("pressure_hpa"), w.get("description",""), w.get("icon",""), now))
            saved.append(sector)
        except Exception as e:
            failed.append(sector)
    conn.commit()
    msg = f"Calendar: {len(saved)} saved, {len(failed)} failed"
    c.execute("INSERT INTO recording_log (date,status,message,recorded_at) VALUES (?,?,?,?)",
              (today, "ok" if saved else "fail", msg, now))
    conn.commit(); conn.close()
    print(f"[recorder] {msg}")
    return {"date":today,"saved":saved,"failed":failed}


def check_and_auto_integrate() -> int:
    """After 12 months of recording, auto-integrate calendar data into seasonal_actuals."""
    from datetime import datetime, timezone, date as date_type, timedelta
    from collections import defaultdict
    cutoff = (date_type.today() - timedelta(days=365)).isoformat()
    conn   = sqlite3.connect(str(DB_PATH))
    c      = conn.cursor()
    c.execute("SELECT date,sector,temp_max,temp_min,rainfall_mm FROM recorded_calendar WHERE date<=? AND auto_integrated=0", (cutoff,))
    rows   = c.fetchall()
    if not rows: conn.close(); return 0
    groups = defaultdict(list)
    for date_str,sector,tmax,tmin,rain in rows:
        doy = date_type.fromisoformat(date_str).timetuple().tm_yday
        yr  = int(date_str[:4])
        season = "B" if 32<=doy<=181 else ("A" if doy>=244 else "C")
        groups[(yr,season,sector)].append((tmax,tmin,rain))
    now = datetime.now(timezone.utc).isoformat()
    integrated = 0
    for (yr,season,sector), readings in groups.items():
        tmaxes=[r[0] for r in readings if r[0] is not None]
        tmins =[r[1] for r in readings if r[1] is not None]
        rains =[r[2] for r in readings if r[2] is not None]
        if not rains: continue
        n_dek = round(len(readings)/10,1)
        c.execute("""
            INSERT OR IGNORE INTO seasonal_actuals
              (recorded_at,year,sector,season,actual_length_dekads,actual_length_weeks,
               actual_tmax,actual_tmin,actual_rainfall_mm,data_source)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (now,yr,sector,season,n_dek,round(n_dek*10/7,1),
              round(sum(tmaxes)/len(tmaxes),2) if tmaxes else None,
              round(sum(tmins)/len(tmins),2)   if tmins  else None,
              round(sum(rains),1), "recorded_calendar"))
        integrated += 1
    conn.execute("UPDATE recorded_calendar SET auto_integrated=1 WHERE date<=?", (cutoff,))
    conn.commit(); conn.close()
    if integrated:
        print(f"[recorder] Auto-integrated {integrated} calendar records into model data")
    return integrated


def get_calendar_summary() -> dict:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        c.execute("SELECT COUNT(*),MIN(date),MAX(date),COUNT(DISTINCT date),COUNT(DISTINCT sector) FROM recorded_calendar")
        total,d_from,d_to,udays,usectors = c.fetchone()
        c.execute("SELECT COUNT(*) FROM recorded_calendar WHERE auto_integrated=0")
        pending = c.fetchone()[0]
        conn.close()
        return {"total_records":total or 0,"date_from":d_from,"date_to":d_to,
                "unique_days":udays or 0,"unique_sectors":usectors or 0,"pending_integration":pending or 0}
    except: return {"total_records":0,"unique_days":0,"pending_integration":0}


def get_admin_users() -> list:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id,email,name,role,created_at,last_login FROM admin_users ORDER BY id").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except: return []


def create_admin_user(email:str, password:str, name:str, role:str="admin") -> bool:
    import hashlib
    from datetime import datetime, timezone
    try:
        pw = hashlib.sha256(password.encode()).hexdigest()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("INSERT INTO admin_users (email,password,name,role,created_at) VALUES (?,?,?,?,?)",
                     (email,pw,name,role,datetime.now(timezone.utc).isoformat()))
        conn.commit(); conn.close(); return True
    except: return False


def update_admin_password(user_id:int, new_password:str) -> bool:
    import hashlib
    try:
        pw = hashlib.sha256(new_password.encode()).hexdigest()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("UPDATE admin_users SET password=? WHERE id=?", (pw,user_id))
        conn.commit(); conn.close(); return True
    except: return False


def delete_admin_user(user_id:int) -> bool:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("DELETE FROM admin_users WHERE id=?", (user_id,))
        conn.commit(); conn.close(); return True
    except: return False


def verify_admin_login(email:str, password:str):
    import hashlib
    from datetime import datetime, timezone
    try:
        pw = hashlib.sha256(password.encode()).hexdigest()
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM admin_users WHERE email=? AND password=?", (email,pw)).fetchone()
        if row:
            conn.execute("UPDATE admin_users SET last_login=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat(), row["id"]))
            conn.commit()
        conn.close()
        return dict(row) if row else None
    except: return None


def get_settings() -> dict:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        result = {r[0]:r[1] for r in conn.execute("SELECT key,value FROM system_settings").fetchall()}
        conn.close(); return result
    except: return {}


def update_setting(key:str, value:str, updated_by:str="admin") -> bool:
    from datetime import datetime, timezone
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("INSERT OR REPLACE INTO system_settings (key,value,updated_at,updated_by) VALUES (?,?,?,?)",
                     (key,value,datetime.now(timezone.utc).isoformat(),updated_by))
        conn.commit(); conn.close(); return True
    except: return False


# ── Standalone run ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    print("DB ready. Recording today...")
    result = record_today(config.OWM_API_KEY)
    print(result)
