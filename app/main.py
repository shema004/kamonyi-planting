# main.py
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).parent))

from data_loader import load_all_data, NGAMBA_NOTE
from model       import PlantingModel
from weather_api import get_forecast_summary, get_all_sectors_forecast, get_7day_forecast, get_current_weather
from crops       import suggest_crops_for_prediction
from recorder    import (try_record_today, get_recorded_history, get_recording_log,
                         get_recorded_summary, init_db, save_bulk_predictions,
                         backfill_actuals_from_excel, get_prediction_vs_actual, get_actuals_summary)
from season_status import get_current_season_status
from decision_engine import make_planting_decision, make_all_sector_decisions
import config

app = FastAPI(
    title="Kamonyi Planting Date Prediction API",
    version="2.0.0",
)

BASE_DIR  = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ── Load data & model ──────────────────────────────────────────────────────
print("[startup] Loading data and fitting model ...")
_, _, _, MERGED_DF = load_all_data()
MODEL = PlantingModel(MERGED_DF)
print("[startup] Ready.")

SECTORS = MODEL.available_sectors()

# Visible seasons (C hidden until data is available)
SEASONS = ["A", "B"] if not config.SHOW_SEASON_C else ["A", "B", "C"]
SEASON_LABELS = {k: v for k, v in config.SEASON_LABELS.items()
                 if k in SEASONS}
SEASON_INFO   = config.SEASON_INFO      # all 3, used for info panel

# ── Start background daily recorder ───────────────────────────────────────
init_db()
try_record_today(config.OWM_API_KEY)

# ── HTML pages ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "sectors":       SECTORS,
        "seasons":       SEASONS,
        "season_labels": SEASON_LABELS,
        "season_info":   SEASON_INFO,
        "show_season_c": config.SHOW_SEASON_C,
        "current_year":  datetime.now().year,
        "next_year":     datetime.now().year + 1,
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

# ── JSON API ───────────────────────────────────────────────────────────────

@app.get("/api/sectors")
async def list_sectors():
    return {"sectors": SECTORS, "count": len(SECTORS),
            "seasons": SEASON_LABELS, "season_info": SEASON_INFO}

@app.get("/api/predict")
async def predict_all(
    season:        str  = Query("A"),
    year:          int  = Query(None),
    live_weather:  bool = Query(False),
    api_key:       Optional[str] = Query(None),
    include_crops: bool = Query(True),
):
    if season not in SEASONS:
        raise HTTPException(status_code=400, detail=f"Season must be one of {SEASONS}")
    target_year = year or (datetime.now().year + 1)
    forecasts   = {}
    if live_weather:
        key = api_key or config.OWM_API_KEY
        if key: forecasts = get_all_sectors_forecast(key)

    results = MODEL.predict_all_sectors(season, target_year, forecasts or None)
    if include_crops:
        for r in results:
            r["crop_suggestions"] = suggest_crops_for_prediction(r)

    # Tag Ngamba predictions with proxy note
    for r in results:
        if r.get("sector") == "Ngamba":
            r["data_note"] = NGAMBA_NOTE
            r["is_proxy"]  = True
    # Auto-save predictions to DB for later comparison with actuals
    save_bulk_predictions(results)

    return {
        "season":       season,
        "season_label": SEASON_LABELS.get(season, season),
        "season_info":  SEASON_INFO.get(season, {}),
        "target_year":  target_year,
        "live_weather": live_weather and bool(forecasts),
        "generated_at": datetime.now().isoformat(),
        "predictions":  results,
    }

@app.get("/api/predict/{sector}")
async def predict_sector(
    sector:        str,
    year:          int  = Query(None),
    live_weather:  bool = Query(False),
    api_key:       Optional[str] = Query(None),
    include_crops: bool = Query(True),
):
    if sector not in SECTORS:
        raise HTTPException(status_code=404, detail=f"Sector not found. Available: {SECTORS}")
    target_year = year or (datetime.now().year + 1)
    results = []
    for season in SEASONS:
        forecast = None
        if live_weather:
            key = api_key or config.OWM_API_KEY
            if key: forecast = get_forecast_summary(key, sector)
        r = MODEL.predict(sector, season, target_year, forecast)
        if include_crops:
            r["crop_suggestions"] = suggest_crops_for_prediction(r)
        results.append(r)
    return {
        "sector": sector, "target_year": target_year,
        "live_weather": live_weather,
        "generated_at": datetime.now().isoformat(),
        "predictions": results,
    }

# ── 7-day live weather endpoints ───────────────────────────────────────────

@app.get("/api/weather/current/{sector}")
async def current_weather(sector: str, api_key: Optional[str] = Query(None)):
    """Current weather conditions for a sector."""
    if sector not in SECTORS:
        raise HTTPException(status_code=404, detail="Sector not found")
    key = api_key or config.OWM_API_KEY
    if not key: raise HTTPException(status_code=400, detail="No API key")
    w = get_current_weather(key, sector)
    if not w: raise HTTPException(status_code=503, detail="Weather unavailable")
    return w

@app.get("/api/weather/forecast/{sector}")
async def weather_forecast(sector: str, api_key: Optional[str] = Query(None)):
    """7-day weather forecast for a sector."""
    if sector not in SECTORS:
        raise HTTPException(status_code=404, detail="Sector not found")
    key = api_key or config.OWM_API_KEY
    if not key: raise HTTPException(status_code=400, detail="No API key")
    fc = get_7day_forecast(key, sector)
    if not fc: raise HTTPException(status_code=503, detail="Forecast unavailable")
    return fc

@app.get("/api/weather/forecast")
async def weather_forecast_all(
    api_key: Optional[str] = Query(None),
    sector:  Optional[str] = Query(None),
):
    """7-day forecast for one sector or district centre."""
    key = api_key or config.OWM_API_KEY
    if not key: raise HTTPException(status_code=400, detail="No API key")
    fc = get_7day_forecast(key, sector)
    if not fc: raise HTTPException(status_code=503, detail="Forecast unavailable")
    return fc

# ── Recorder endpoints ─────────────────────────────────────────────────────

@app.get("/api/recorder/summary")
async def recorder_summary():
    """Summary of how much daily data has been recorded."""
    return get_recorded_summary()

@app.get("/api/recorder/log")
async def recorder_log(limit: int = Query(30)):
    """Recent recording log."""
    return {"log": get_recording_log(limit)}

@app.get("/api/recorder/history")
async def recorder_history(
    sector: Optional[str] = Query(None),
    days:   int           = Query(365),
):
    """Recorded daily weather history."""
    return {"records": get_recorded_history(sector, days)}

@app.post("/api/recorder/record-now")
async def record_now(api_key: Optional[str] = Query(None)):
    """Manually trigger today's recording."""
    from recorder import record_today, init_db
    key = api_key or config.OWM_API_KEY
    if not key: raise HTTPException(status_code=400, detail="No API key")
    init_db()
    result = record_today(key)
    return result

# ── Other endpoints ────────────────────────────────────────────────────────

@app.get("/api/crops/{sector}")
async def crop_suggestions(sector: str, season: str = Query("A"), year: int = Query(None)):
    if sector not in SECTORS: raise HTTPException(status_code=404, detail="Sector not found")
    target_year = year or (datetime.now().year + 1)
    prediction  = MODEL.predict(sector, season, target_year)
    return {
        "sector": sector, "season": season, "target_year": target_year,
        "predicted_tmax":      prediction.get("predicted_tmax"),
        "predicted_tmin":      prediction.get("predicted_tmin"),
        "temp_summary":        prediction.get("temp_summary"),
        "expected_rainfall_mm":prediction.get("expected_rainfall_mm"),
        "crop_suggestions":    suggest_crops_for_prediction(prediction),
    }

@app.get("/api/forecast/{sector}")
async def live_forecast_legacy(sector: str, api_key: Optional[str] = Query(None)):
    """Legacy endpoint — use /api/weather/forecast/{sector} instead."""
    return await weather_forecast(sector, api_key)

@app.get("/api/stats/{sector}")
async def sector_stats(sector: str, season: str = Query("A")):
    if sector not in SECTORS: raise HTTPException(status_code=404, detail="Sector not found")
    if season not in SEASONS:  raise HTTPException(status_code=400, detail="Invalid season")
    stats = MODEL.sector_season_stats(sector, season)
    if not stats: raise HTTPException(status_code=404, detail="No stats")
    return stats

@app.get("/api/history/{sector}")
async def sector_history(sector: str, season: str = Query("A")):
    if sector not in SECTORS: raise HTTPException(status_code=404, detail="Sector not found")
    df = MERGED_DF[(MERGED_DF["sector"]==sector)&(MERGED_DF["season"]==season)].sort_values("year")
    if df.empty: raise HTTPException(status_code=404, detail="No data found")
    def clean(vals): return [round(v,2) if v==v else None for v in vals]
    return {
        "sector": sector, "season": season,
        "season_label": SEASON_LABELS.get(season, season),
        "years":         df["year"].tolist(),
        "onset_days":    df["onset_day"].tolist(),
        "total_rainfall":clean(df["total_rainfall"].tolist()),
        "length_dekads": clean(df["length_dekads"].tolist()),
        "mean_max_temp": clean(df["mean_max_temp"].tolist()),
        "mean_min_temp": clean(df["mean_min_temp"].tolist()),
    }


@app.get("/api/season/current")
async def current_season_status():
    """
    Current active season status — progress, rainfall so far,
    remaining weeks, temperature context vs historical average.
    """
    status = get_current_season_status(MERGED_DF)
    return status


@app.get("/api/records/actuals")
async def get_actuals(
    sector: Optional[str] = Query(None),
    season: Optional[str] = Query(None),
):
    """Historical actual season data from Excel (backfilled into DB)."""
    summary = get_actuals_summary()
    return {"summary": summary}


@app.get("/api/records/predictions-vs-actuals")
async def predictions_vs_actuals(
    sector: Optional[str] = Query(None),
    season: Optional[str] = Query(None),
):
    """
    Compare model predictions against actual observed values.
    Only returns rows where both a saved prediction AND an actual exist.
    Not shown on dashboard — for analysis only.
    """
    rows = get_prediction_vs_actual(sector, season)
    return {
        "count":   len(rows),
        "records": rows,
        "note":    "Predictions are auto-saved each time /api/predict is called. "
                   "Actuals are backfilled from the Excel file at startup.",
    }


@app.get("/api/decision/{sector}")
async def sector_decision(
    sector: str,
    season: str = Query("A"),
    api_key: Optional[str] = Query(None),
):
    """
    Real-time planting decision for one sector.
    Reads current date, compares forecast rainfall to onset threshold,
    and returns: PLANT_NOW / PLANT_SOON / WAIT / USE_EARLY_VARIETY /
                 SEASON_ACTIVE / SEASON_ENDING / OFF_SEASON
    """
    if sector not in SECTORS:
        raise HTTPException(status_code=404, detail="Sector not found")
    if season not in SEASONS:
        raise HTTPException(status_code=400, detail="Season must be A or B")

    # Get prediction for this sector/season
    prediction = MODEL.predict(sector, season, datetime.now().year)

    # Fetch live forecast
    forecast = None
    key = api_key or config.OWM_API_KEY
    if key:
        forecast = get_forecast_summary(key, sector)

    from datetime import date
    decision = make_planting_decision(sector, season, prediction, forecast, date.today())
    return decision


@app.get("/api/decision")
async def all_sector_decisions(
    season:  str = Query("A"),
    api_key: Optional[str] = Query(None),
):
    """
    Real-time planting decisions for ALL sectors for a given season.
    Returns a list sorted by urgency (PLANT_NOW first).
    """
    if season not in SEASONS:
        raise HTTPException(status_code=400, detail="Season must be A or B")

    key       = api_key or config.OWM_API_KEY
    forecasts = {}
    if key:
        forecasts = get_all_sectors_forecast(key)

    from datetime import date
    results = make_all_sector_decisions(MODEL, season, forecasts, date.today())

    # Sort by urgency
    urgency_order = {
        "PLANT_NOW": 0, "PLANT_SOON": 1, "USE_EARLY_VARIETY": 2,
        "SEASON_ACTIVE": 3, "WAIT": 4, "SEASON_ENDING": 5, "OFF_SEASON": 6
    }
    results.sort(key=lambda r: urgency_order.get(r["decision"], 99))

    return {
        "season":      season,
        "today":       date.today().isoformat(),
        "decisions":   results,
        "summary": {
            "plant_now":        sum(1 for r in results if r["decision"]=="PLANT_NOW"),
            "plant_soon":       sum(1 for r in results if r["decision"]=="PLANT_SOON"),
            "wait":             sum(1 for r in results if r["decision"]=="WAIT"),
            "use_early_variety":sum(1 for r in results if r["decision"]=="USE_EARLY_VARIETY"),
            "season_active":    sum(1 for r in results if r["decision"]=="SEASON_ACTIVE"),
            "season_ending":    sum(1 for r in results if r["decision"]=="SEASON_ENDING"),
        }
    }

@app.get("/health")
async def health():
    db_summary = get_recorded_summary()
    actuals_summary = get_actuals_summary()
    return {
        "status":           "ok",
        "sectors":          len(SECTORS),
        "db_daily_records": db_summary.get("total_records", 0),
        "db_days":          db_summary.get("unique_days", 0),
        "db_actuals":       actuals_summary.get("total_actuals", 0),
        "db_predictions":   actuals_summary.get("total_predictions", 0),
        "timestamp":        datetime.now().isoformat(),
    }
