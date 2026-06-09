# main.py
import sys
from pathlib import Path
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).parent))

from data_loader    import load_all_data, NGAMBA_NOTE
from model          import PlantingModel
from weather_api    import (get_forecast_summary, get_all_sectors_forecast,
                             get_7day_forecast, get_current_weather, SECTOR_COORDS)
from crops          import suggest_crops_for_prediction
from recorder       import (init_db, start_scheduler, record_in_background,
                             backfill_actuals_from_excel, save_bulk_predictions,
                             get_prediction_vs_actual, get_actuals_summary,
                             get_all_records, get_recording_log, get_records_summary)
from season_status  import get_current_season_status
from decision_engine import make_planting_decision, make_all_sector_decisions
import config

# ── App setup ──────────────────────────────────────────────────────────────
app = FastAPI(title="Kamonyi Planting Date Prediction API", version="2.0.0")

BASE_DIR  = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Static files (GeoJSON sector boundaries)
import os as _os
_static_dir = str(BASE_DIR / "static")
_os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# ── Load data & model ──────────────────────────────────────────────────────
print("[startup] Loading data and fitting model ...")
_, _, _, MERGED_DF = load_all_data()
MODEL = PlantingModel(MERGED_DF)
print("[startup] Ready.")

SECTORS      = MODEL.available_sectors()
SEASONS      = ["A", "B"] if not config.SHOW_SEASON_C else ["A", "B", "C"]
SEASON_LABELS= {k: v for k, v in config.SEASON_LABELS.items() if k in SEASONS}
SEASON_INFO  = config.SEASON_INFO

# ── Start recorder & seed DB ───────────────────────────────────────────────
init_db()
backfill_actuals_from_excel(MERGED_DF)
start_scheduler()   # records at startup + 05:00 and 17:00 Rwanda time



# ── Prediction cache (10 min TTL — predictions don't change minute-to-minute) ─
import time as _time
_PRED_CACHE: dict = {}
_PRED_TTL = 600  # 10 minutes

def _pred_cache_get(key):
    e = _PRED_CACHE.get(key)
    return e["data"] if e and (_time.time()-e["ts"]) < _PRED_TTL else None

def _pred_cache_set(key, data):
    _PRED_CACHE[key] = {"data": data, "ts": _time.time()}

# ── Page routes ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Record on every visit in background — page loads instantly
    record_in_background()

    next_year = datetime.now().year + 1
    return templates.TemplateResponse(request, "index.html", {
        "sectors":      SECTORS,
        "seasons":      SEASONS,
        "season_labels":SEASON_LABELS,
        "season_info":  SEASON_INFO,
        "current_year": datetime.now().year,
        "next_year":    next_year,
        "show_season_c":config.SHOW_SEASON_C,
    })


@app.get("/sector/{sector_name}", response_class=HTMLResponse)
async def sector_page(request: Request, sector_name: str):
    if sector_name not in SECTORS:
        raise HTTPException(status_code=404, detail="Sector not found")
    return templates.TemplateResponse(request, "sector.html", {
        "sector":        sector_name,
        "sectors":       SECTORS,
        "seasons":       SEASONS,
        "season_labels": SEASON_LABELS,
        "season_info":   SEASON_INFO,
        "current_year":  datetime.now().year,
        "ngamba_note":   NGAMBA_NOTE if sector_name == "Ngamba" else None,
    })


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    return templates.TemplateResponse(request, "logs.html", {
        "sectors": SECTORS,
    })


# ── Prediction API ─────────────────────────────────────────────────────────

@app.get("/api/predict")
async def predict_all(
    season:       str  = Query("A"),
    year:         int  = Query(None),
    live_weather: bool = Query(False),
    include_crops:bool = Query(True),
):
    if season not in SEASONS:
        raise HTTPException(status_code=400, detail="Season must be A or B")
    target_year = year or (datetime.now().year + 1)
    forecasts   = {}
    if live_weather:
        forecasts = get_all_sectors_forecast(config.OWM_API_KEY)

    # Serve from cache if no live weather requested
    cache_key = f"pred_{season}_{target_year}"
    if not live_weather:
        cached = _pred_cache_get(cache_key)
        if cached:
            return cached

    results = MODEL.predict_all_sectors(season, target_year, forecasts)

    # Tag Ngamba as proxy
    for r in results:
        if r.get("sector") == "Ngamba":
            r["data_note"] = NGAMBA_NOTE
            r["is_proxy"]  = True

    # Add crop suggestions
    if include_crops:
        for r in results:
            r["crop_suggestions"] = suggest_crops_for_prediction(r)

    # Auto-save predictions
    save_bulk_predictions(results)

    response = {
        "season":       season,
        "season_label": SEASON_LABELS.get(season, season),
        "season_info":  SEASON_INFO.get(season, {}),
        "target_year":  target_year,
        "live_weather": live_weather and bool(forecasts),
        "generated_at": datetime.now().isoformat(),
        "predictions":  results,
    }
    if not live_weather:
        _pred_cache_set(cache_key, response)
    return response


@app.get("/api/predict/{sector}")
async def predict_sector(
    sector: str,
    season: str = Query("A"),
    year:   int = Query(None),
):
    if sector not in SECTORS:
        raise HTTPException(status_code=404, detail="Sector not found")
    target_year = year or (datetime.now().year + 1)
    result_a = MODEL.predict(sector, "A", target_year)
    result_b = MODEL.predict(sector, "B", target_year)
    for r in [result_a, result_b]:
        r["crop_suggestions"] = suggest_crops_for_prediction(r)
    return {"sector": sector, "season_A": result_a, "season_B": result_b}


# ── Weather API ────────────────────────────────────────────────────────────

@app.get("/api/weather/forecast/{sector}")
async def weather_forecast(sector: str):
    if sector not in SECTORS:
        raise HTTPException(status_code=404, detail="Sector not found")
    data = get_7day_forecast(config.OWM_API_KEY, sector)
    if not data:
        raise HTTPException(status_code=503, detail="Weather API unavailable")
    return data


@app.get("/api/weather/forecast")
async def weather_forecast_district():
    data = get_7day_forecast(config.OWM_API_KEY)
    if not data:
        raise HTTPException(status_code=503, detail="Weather API unavailable")
    return data


@app.get("/api/weather/current/{sector}")
async def weather_current(sector: str):
    if sector not in SECTORS:
        raise HTTPException(status_code=404, detail="Sector not found")
    data = get_current_weather(config.OWM_API_KEY, sector)
    if not data:
        raise HTTPException(status_code=503, detail="Weather API unavailable")
    return data


# ── Decision API ───────────────────────────────────────────────────────────

@app.get("/api/decision")
async def all_decisions(season: str = Query("A"), year: int = Query(None)):
    if season not in ["A", "B"]:
        raise HTTPException(status_code=400, detail="Season must be A or B")
    target_year = year or (datetime.now().year + 1)

    # Never fetch live weather for decisions automatically - it causes 12 slow API calls
    # Decisions use historical/predicted data only for speed
    is_future = target_year > datetime.now().year
    forecasts  = {}   # always empty - decisions are fast without live weather

    # Build a simulated "today" anchored to the target year
    # so onset windows are calculated relative to the correct year
    from datetime import date as date_cls
    sim_today = date_cls(target_year, date.today().month, date.today().day)

    results = make_all_sector_decisions(MODEL, season, forecasts, sim_today, target_year)
    urgency = {
        "PLANT_NOW":0,"PLANT_SOON":1,"USE_EARLY_VARIETY":2,
        "SEASON_ACTIVE":3,"WAIT":4,"PREPARE":5,"SEASON_ENDING":6,"OFF_SEASON":7
    }
    results.sort(key=lambda r: urgency.get(r["decision"], 99))
    return {
        "season":      season,
        "target_year": target_year,
        "is_future":   is_future,
        "today":       sim_today.isoformat(),
        "decisions":   results,
        "summary": {
            "plant_now":         sum(1 for r in results if r["decision"]=="PLANT_NOW"),
            "plant_soon":        sum(1 for r in results if r["decision"]=="PLANT_SOON"),
            "wait":              sum(1 for r in results if r["decision"]=="WAIT"),
            "use_early_variety": sum(1 for r in results if r["decision"]=="USE_EARLY_VARIETY"),
            "season_active":     sum(1 for r in results if r["decision"]=="SEASON_ACTIVE"),
            "season_ending":     sum(1 for r in results if r["decision"]=="SEASON_ENDING"),
            "prepare":           sum(1 for r in results if r["decision"]=="PREPARE"),
        }
    }


@app.get("/api/decision/{sector}")
async def sector_decision(sector: str, season: str = Query("A"), year: int = Query(None)):
    if sector not in SECTORS:
        raise HTTPException(status_code=404, detail="Sector not found")
    target_year = year or (datetime.now().year + 1)
    is_future   = target_year > datetime.now().year
    from datetime import date as date_cls
    sim_today  = date_cls(target_year, date.today().month, date.today().day)
    prediction = MODEL.predict(sector, season, target_year)
    forecast   = None if is_future else get_forecast_summary(config.OWM_API_KEY, sector)
    return make_planting_decision(sector, season, prediction, forecast, sim_today)


# ── Season status ──────────────────────────────────────────────────────────

@app.get("/api/season/current")
async def current_season():
    return get_current_season_status(MERGED_DF)


# ── History & stats ────────────────────────────────────────────────────────

@app.get("/api/history/{sector}")
async def sector_history(sector: str, season: str = Query("A")):
    if sector not in SECTORS:
        raise HTTPException(status_code=404, detail="Sector not found")
    df = MERGED_DF[(MERGED_DF["sector"]==sector) & (MERGED_DF["season"]==season)]
    return {
        "sector": sector, "season": season,
        "years":        df["year"].tolist(),
        "onset_days":   df["onset_day"].tolist(),
        "rainfall":     df["total_rainfall"].tolist(),
        "tmax":         df["mean_max_temp"].tolist(),
        "tmin":         df["mean_min_temp"].tolist(),
        "length_dekads":df["length_dekads"].tolist(),
    }


@app.get("/api/stats/{sector}")
async def sector_stats(sector: str, season: str = Query("A")):
    if sector not in SECTORS:
        raise HTTPException(status_code=404, detail="Sector not found")
    return MODEL.sector_season_stats(sector, season)


@app.get("/api/crops/{sector}")
async def sector_crops(sector: str, season: str = Query("A")):
    if sector not in SECTORS:
        raise HTTPException(status_code=404, detail="Sector not found")
    prediction = MODEL.predict(sector, season, datetime.now().year + 1)
    return suggest_crops_for_prediction(prediction)


# ── Recorder API ───────────────────────────────────────────────────────────

@app.get("/api/recorder/summary")
async def recorder_summary():
    return get_records_summary()


@app.get("/api/recorder/log")
async def recorder_log(limit: int = Query(30)):
    rows = get_recording_log(limit)
    # Normalise field names so dashboard JS works
    normalised = []
    for r in rows:
        normalised.append({
            "date":        r.get("date"),
            "recorded_at": r.get("recorded_at"),
            "sectors_ok":  r.get("sectors_ok", 0),
            "sectors_failed": r.get("sectors_failed", 0),
            "failed_list": r.get("failed_list",""),
            "status":      r.get("status",""),
            "message":     f"{r.get('sectors_ok',0)}/12 sectors recorded",
        })
    return {"log": normalised}


@app.get("/api/recorder/history")
async def recorder_history(days: int = Query(30), sector: str = Query(None)):
    records = get_all_records(days)
    if sector:
        records = [r for r in records if r["sector"] == sector]
    return {"records": records, "count": len(records)}


# ── Data logs API ──────────────────────────────────────────────────────────

@app.get("/api/records/daily")
async def daily_records(days: int = Query(30)):
    records = get_all_records(days)
    log     = get_recording_log(days)
    summary = get_records_summary()
    # Build list of dates that actually have data
    dates   = sorted(set(r["date"] for r in records), reverse=True)
    return {
        "summary": summary,
        "log":     log,
        "records": records,
        "sectors": SECTORS,
        "dates":   dates,
    }


@app.get("/api/records/predictions-vs-actuals")
async def predictions_vs_actuals(
    sector: Optional[str] = Query(None),
    season: Optional[str] = Query(None),
):
    rows = get_prediction_vs_actual(sector, season)
    return {"count": len(rows), "records": rows}


# ── Health ─────────────────────────────────────────────────────────────────


@app.get("/api/test-record")
async def test_record():
    """Test endpoint - triggers one recording and returns the result."""
    from recorder import record_now
    result = record_now()
    return result

@app.get("/health")
async def health():
    summary = get_records_summary()
    actuals = get_actuals_summary()
    return {
        "status":          "ok",
        "sectors":         len(SECTORS),
        "daily_records":   summary.get("total_records", 0),
        "days_recorded":   summary.get("unique_days", 0),
        "last_recording":  summary.get("last_recorded_at"),
        "db_actuals":      actuals.get("total_actuals", 0),
        "db_predictions":  actuals.get("total_predictions", 0),
        "timestamp":       datetime.now().isoformat(),
    }
