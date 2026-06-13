"""
recorder.py
-----------
Daily weather recorder using SQLite (daily_records.db).
Database is committed to GitHub so it survives every deploy.
INSERT OR IGNORE ensures existing data is never overwritten.
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
    Fetch weather for all 12 sectors and save to SQLite.
    INSERT OR IGNORE — once a day is recorded it is never overwritten.
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

    # Push DB to GitHub so data survives restarts and redeploys
    if saved:
        _push_db_to_github()

    return {"date": today, "status": status, "saved": saved,
            "failed": failed, "message": message}




def _push_db_to_github():
    """
    After every recording:
    1. Download GitHub DB
    2. Merge ALL records from GitHub into local DB (so nothing is lost)
    3. Push the merged DB back to GitHub
    This guarantees GitHub always has the MOST records, never fewer.
    """
    import base64, requests, tempfile, os

    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
    if not GITHUB_TOKEN:
        print("[recorder] ⚠️ GITHUB_TOKEN not set — skipping DB push")
        return
    REPO         = "shema004/kamonyi-planting"
    FILE_PATH    = "data/daily_records.db"
    API_URL      = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        # Step 1: Download the GitHub version of the DB
        r = requests.get(API_URL, headers=headers, timeout=10)
        sha = None
        if r.status_code == 200:
            data        = r.json()
            sha         = data.get("sha")
            github_bytes= base64.b64decode(data["content"])

            # Save GitHub DB to a temp file
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
            tmp.write(github_bytes)
            tmp.close()

            # Step 2: Merge GitHub records INTO local DB
            # (copy any rows from GitHub that local doesn't have)
            github_conn = sqlite3.connect(tmp.name)
            local_conn  = sqlite3.connect(str(DB_PATH))

            github_rows = github_conn.execute(
                "SELECT date, sector, temp_max, temp_min, rainfall_mm, humidity, description, recorded_at FROM daily_weather"
            ).fetchall()

            for row in github_rows:
                try:
                    local_conn.execute("""
                        INSERT OR IGNORE INTO daily_weather
                            (date, sector, temp_max, temp_min, rainfall_mm, humidity, description, recorded_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, row)
                except: pass

            local_conn.commit()
            local_conn.close()
            github_conn.close()
            os.unlink(tmp.name)

            print(f"[recorder] Merged GitHub records into local DB")

        # Step 3: Push the merged local DB to GitHub
        with open(str(DB_PATH), "rb") as f:
            db_content = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "message": f"Auto-backup DB {date.today().isoformat()}",
            "content": db_content,
        }
        if sha:
            payload["sha"] = sha

        r = requests.put(API_URL, headers=headers, json=payload, timeout=30)

        if r.status_code in (200, 201):
            # Count total dates now in DB
            conn   = sqlite3.connect(str(DB_PATH))
            dates  = conn.execute("SELECT COUNT(DISTINCT date) FROM daily_weather").fetchone()[0]
            conn.close()
            print(f"[recorder] ✅ DB pushed to GitHub — {dates} dates total")
        else:
            print(f"[recorder] ⚠️ GitHub push failed: {r.status_code} {r.text[:100]}")

    except Exception as e:
        print(f"[recorder] ⚠️ GitHub push error: {e}")

# ── Scheduler ──────────────────────────────────────────────────────────────

def try_record_today(api_key: str):
    """Record immediately + schedule 05:00 and 17:00 Rwanda time."""

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
        conn   = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows   = conn.execute("""
            SELECT date, recorded_at, sector, rainfall_mm, temp_max, temp_min
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
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT date,
                   MAX(recorded_at)       AS recorded_at,
                   COUNT(DISTINCT sector) AS sectors_ok,
                   (12 - COUNT(DISTINCT sector)) AS sectors_failed,
                   '' AS failed_list,
                   CASE WHEN COUNT(DISTINCT sector)>=12
                        THEN 'ok' ELSE 'partial' END AS status
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
            FROM daily_weather
            WHERE date=(SELECT MAX(date) FROM daily_weather)
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


def get_recorded_history(sector=None, limit_days=30):
    return get_all_records(limit_days)


def get_recorded_summary():
    return get_records_summary()


# ── Seasonal data ──────────────────────────────────────────────────────────

def backfill_actuals_from_excel(merged_df) -> int:
    import math
    count = 0
    try:
        conn = sqlite3.connect(str(DB_PATH))
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
                    d = date(int(row["year"]), 1, 1) + timedelta(days=onset_day-1)
                    onset_date = d.strftime("%d %b")
                length_dek = safe(row.get("length_dekads"))
                length_wks = round(length_dek*10/7,1) if length_dek else None
                c.execute("""
                    INSERT OR IGNORE INTO seasonal_actuals
                      (recorded_at,year,sector,season,actual_onset_day,
                       actual_onset_date,actual_length_dekads,actual_length_weeks,
                       actual_tmax,actual_tmin,actual_rainfall_mm,data_source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
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


def auto_integrate_recorded_data() -> int:
    """
    After 12 months of daily recordings, integrate into seasonal_actuals
    so the prediction model uses real recorded data — improving accuracy each year.
    """
    from collections import defaultdict
    cutoff = (date.today() - timedelta(days=365)).isoformat()
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c    = conn.cursor()
        c.execute("""
            SELECT date, sector, temp_max, temp_min, rainfall_mm
            FROM daily_weather WHERE date <= ?
        """, (cutoff,))
        rows = c.fetchall()
        if not rows:
            conn.close()
            return 0
        groups = defaultdict(list)
        for date_str, sector, tmax, tmin, rain in rows:
            d   = date.fromisoformat(date_str)
            doy = d.timetuple().tm_yday
            yr  = d.year
            if 32 <= doy <= 181:   season = "B"
            elif doy >= 244:       season = "A"
            else:                  continue
            groups[(yr, season, sector)].append((tmax, tmin, rain))
        now = datetime.now(timezone.utc).isoformat()
        integrated = 0
        for (yr, season, sector), readings in groups.items():
            tmaxes = [r[0] for r in readings if r[0]]
            tmins  = [r[1] for r in readings if r[1]]
            rains  = [r[2] for r in readings if r[2] is not None]
            if not rains: continue
            n_dek = round(len(readings)/10, 1)
            c.execute("""
                INSERT OR IGNORE INTO seasonal_actuals
                  (recorded_at,year,sector,season,actual_length_dekads,
                   actual_length_weeks,actual_tmax,actual_tmin,
                   actual_rainfall_mm,data_source)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (now, yr, sector, season, n_dek, round(n_dek*10/7,1),
                  round(sum(tmaxes)/len(tmaxes),2) if tmaxes else None,
                  round(sum(tmins)/len(tmins),2)   if tmins  else None,
                  round(sum(rains),1), "recorded_daily"))
            integrated += 1
        conn.commit()
        conn.close()
        if integrated:
            print(f"[recorder] Auto-integrated {integrated} seasons from recorded data")
        return integrated
    except Exception as e:
        print(f"[recorder] auto_integrate error: {e}")
        return 0


def save_prediction(prediction: dict) -> bool:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT OR IGNORE INTO seasonal_predictions
              (predicted_at,target_year,sector,season,
               predicted_onset_day,predicted_onset_date,
               predicted_length_dekads,predicted_length_weeks,
               predicted_tmax,predicted_tmin,predicted_rainfall_mm,confidence)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
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
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        wheres, params = [], []
        if sector: wheres.append("p.sector=?"); params.append(sector)
        if season: wheres.append("p.season=?"); params.append(season)
        where = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        rows = conn.execute(f"""
            SELECT p.target_year AS year, p.sector, p.season,
                   p.predicted_onset_day, p.predicted_onset_date,
                   p.predicted_rainfall_mm, p.confidence, p.predicted_at,
                   a.actual_onset_day, a.actual_rainfall_mm,
                   (a.actual_onset_day - p.predicted_onset_day) AS onset_error_days
            FROM seasonal_predictions p
            JOIN seasonal_actuals a
              ON p.target_year=a.year AND p.sector=a.sector AND p.season=a.season
            {where}
            ORDER BY p.target_year DESC, p.sector, p.season
        """, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[recorder] get_prediction_vs_actual: {e}")
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
