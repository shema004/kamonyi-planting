# model.py
import numpy as np
import pandas as pd
from datetime import date, timedelta
from typing import Optional
from scipy import stats

PLANTING_WINDOW_DAYS = 10
MIN_YEARS_FOR_TREND  = 5

SEASON_ONSET_RANGE = {
    "A": (244, 310),
    "B": (50,  100),
}

def day_of_year_to_date_str(doy: int, year: int = 2000) -> str:
    try:
        d = date(year, 1, 1) + timedelta(days=int(doy) - 1)
        return d.strftime("%d %b")
    except:
        return "??"

def planting_window(onset_doy: int, target_year: int) -> tuple:
    try:
        base = date(target_year, 1, 1) + timedelta(days=int(onset_doy) - 1)
        end  = base + timedelta(days=PLANTING_WINDOW_DAYS)
        return base.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    except:
        return "unknown", "unknown"

def confidence_level(n_obs: int, std_days: float) -> str:
    if n_obs < 5 or np.isnan(std_days): return "LOW"
    if std_days > 30 or n_obs < 10:     return "MEDIUM"
    return "HIGH"

def fit_sector_season(df_ss: pd.DataFrame) -> dict:
    df_ss  = df_ss.dropna(subset=["onset_day"]).copy().sort_values("year")
    years  = df_ss["year"].values.astype(float)
    onsets = df_ss["onset_day"].values.astype(float)

    mean_onset  = float(np.mean(onsets))
    std_onset   = float(np.std(onsets, ddof=1)) if len(onsets) > 1 else np.nan
    slope, intercept, r_val, p_val, _ = stats.linregress(years, onsets)
    recent_mean = float(np.mean(onsets[-5:])) if len(onsets) >= 5 else mean_onset

    rain_vals = df_ss["total_rainfall"].dropna().values
    mean_rain = float(np.mean(rain_vals))            if len(rain_vals) > 0 else np.nan
    std_rain  = float(np.std(rain_vals, ddof=1))     if len(rain_vals) > 1 else np.nan

    tmax_vals = df_ss["mean_max_temp"].dropna().values
    tmin_vals = df_ss["mean_min_temp"].dropna().values
    mean_tmax = float(np.mean(tmax_vals)) if len(tmax_vals) > 0 else np.nan
    mean_tmin = float(np.mean(tmin_vals)) if len(tmin_vals) > 0 else np.nan

    # Temperature trends
    if len(tmax_vals) >= MIN_YEARS_FOR_TREND:
        tmax_years = df_ss.dropna(subset=["mean_max_temp"])["year"].values.astype(float)
        tmax_slope, tmax_inter, _, _, _ = stats.linregress(tmax_years, tmax_vals)
    else:
        tmax_slope, tmax_inter = 0.0, mean_tmax if not np.isnan(mean_tmax) else 25.0

    if len(tmin_vals) >= MIN_YEARS_FOR_TREND:
        tmin_years = df_ss.dropna(subset=["mean_min_temp"])["year"].values.astype(float)
        tmin_slope, tmin_inter, _, _, _ = stats.linregress(tmin_years, tmin_vals)
    else:
        tmin_slope, tmin_inter = 0.0, mean_tmin if not np.isnan(mean_tmin) else 14.0

    # Season length trend
    len_vals = df_ss["length_dekads"].dropna().values
    mean_len = float(np.mean(len_vals)) if len(len_vals)>0 else np.nan
    std_len  = float(np.std(len_vals,ddof=1)) if len(len_vals)>1 else np.nan
    if len(len_vals) >= MIN_YEARS_FOR_TREND:
        len_years = df_ss.dropna(subset=["length_dekads"])["year"].values.astype(float)
        len_slope, len_inter, _, _, _ = stats.linregress(len_years, len_vals)
    else:
        len_slope, len_inter = 0.0, mean_len if not np.isnan(mean_len) else 15.0

    return {
        "n_obs":              len(df_ss),
        "year_min":           int(years.min()),
        "year_max":           int(years.max()),
        "mean_onset":         mean_onset,
        "std_onset":          std_onset,
        "trend_slope":        float(slope),
        "trend_intercept":    float(intercept),
        "trend_r2":           float(r_val**2),
        "trend_p":            float(p_val),
        "recent_mean_onset":  recent_mean,
        "mean_rainfall":      mean_rain,
        "std_rainfall":       std_rain,
        "mean_max_temp":      mean_tmax,
        "mean_min_temp":      mean_tmin,
        "tmax_slope":         float(tmax_slope),
        "tmax_intercept":     float(tmax_inter),
        "tmin_slope":         float(tmin_slope),
        "tmin_intercept":     float(tmin_inter),
        "mean_length_dekads": mean_len,
        "std_length_dekads":  std_len,
        "len_slope":          float(len_slope),
        "len_intercept":      float(len_inter),
    }

def predict_onset_from_history(params: dict, target_year: int) -> float:
    if params["n_obs"] < MIN_YEARS_FOR_TREND:
        return params["mean_onset"]
    trend_pred  = params["trend_slope"] * target_year + params["trend_intercept"]
    recent_pred = params["recent_mean_onset"]
    r2 = params["trend_r2"]
    w_trend  = 0.4 + 0.4 * r2
    w_recent = 1.0 - w_trend
    return w_trend * trend_pred + w_recent * recent_pred

def predict_temperature(params: dict, target_year: int) -> tuple:
    """Return (predicted_tmax, predicted_tmin) for target_year."""
    pred_tmax = params["tmax_slope"] * target_year + params["tmax_intercept"]
    pred_tmin = params["tmin_slope"] * target_year + params["tmin_intercept"]
    return round(float(pred_tmax), 2), round(float(pred_tmin), 2)


def predict_season_length(params: dict, target_year: int) -> float:
    """Predict season length in dekads for target_year."""
    pred = params["len_slope"] * target_year + params["len_intercept"]
    # Clamp to realistic range: 5–25 dekads
    return round(float(np.clip(pred, 5.0, 25.0)), 1)

def temperature_label(tmax: float, tmin: float) -> dict:
    """Describe how hot/cold the season is expected to be."""
    if tmax >= 28:   heat = "Very Hot"
    elif tmax >= 26: heat = "Hot"
    elif tmax >= 24: heat = "Warm"
    else:            heat = "Mild"

    if tmin <= 12:   cold = "Very Cold nights"
    elif tmin <= 14: cold = "Cold nights"
    elif tmin <= 16: cold = "Cool nights"
    else:            cold = "Mild nights"

    return {
        "heat_label": heat,
        "cold_label": cold,
        "summary":    f"{heat}, {cold}",
    }

def weather_adjustment(forecast_summary: Optional[dict], params: dict) -> float:
    if forecast_summary is None: return 0.0
    forecast_rain = sum(d.get("total_rain_mm",0.0) for d in forecast_summary.get("days",[]))
    hist_daily    = (params["mean_rainfall"] or 0) / 90.0
    expected_5d   = hist_daily * 5
    rain_adj      = 0.0
    if expected_5d > 0:
        ratio    = forecast_rain / expected_5d
        rain_adj = float(np.clip(-10.0*(ratio-1.0), -15.0, 15.0))
    days     = forecast_summary.get("days",[])
    temp_adj = 0.0
    if days:
        fc_tmax = np.mean([d["max_temp_c"] for d in days])
        fc_tmin = np.mean([d["min_temp_c"] for d in days])
        if not np.isnan(params["mean_max_temp"]):
            temp_adj += -1.5*(fc_tmax - params["mean_max_temp"])
        if not np.isnan(params["mean_min_temp"]):
            temp_adj += -1.0*(fc_tmin - params["mean_min_temp"])
        temp_adj = float(np.clip(temp_adj, -10.0, 10.0))
    return round(0.7*rain_adj + 0.3*temp_adj, 1)

def _build_notes(params, fc_adj, forecast_summary, season):
    from config import SEASON_INFO
    info  = SEASON_INFO.get(season, {})
    lines = [f"Prediction for {info.get('local_name',season)} ({info.get('period','')})."]
    slope = params["trend_slope"]
    if abs(slope) > 0.3:
        direction = "later" if slope > 0 else "earlier"
        lines.append(f"Trend: onset shifting {direction} by {abs(slope):.2f} days/year (R²={params['trend_r2']:.2f}).")
    if not np.isnan(params["std_onset"]):
        lines.append(f"Typical variability: ±{params['std_onset']:.1f} days.")
    if fc_adj != 0.0 and forecast_summary:
        direction = "earlier" if fc_adj < 0 else "later"
        lines.append(f"Live forecast shifts onset {abs(fc_adj):.1f} days {direction}.")
    else:
        lines.append("Prediction based on historical data only.")
    if not np.isnan(params["mean_rainfall"]):
        lines.append(f"Expected rainfall: {params['mean_rainfall']:.0f} mm (±{params['std_rainfall']:.0f} mm).")
    lines.append("Prepare fields and seed within the planting window. Monitor daily rainfall after onset.")
    return " ".join(lines)


class PlantingModel:

    def __init__(self, merged_df: pd.DataFrame):
        self.df     = merged_df
        self.params = {}
        self._fit_all()

    def _fit_all(self):
        for (sector, season), grp in self.df.groupby(["sector","season"]):
            self.params[(sector,season)] = fit_sector_season(grp)
        print(f"[PlantingModel] Fitted {len(self.params)} sector-season pairs.")

    def predict(self, sector, season, target_year, forecast_summary=None):
        key = (sector, season)
        if key not in self.params:
            return {"error": f"No data for {sector} / {season}"}
        p = self.params[key]

        hist_onset = predict_onset_from_history(p, target_year)
        fc_adj     = weather_adjustment(forecast_summary, p)
        lo, hi     = SEASON_ONSET_RANGE.get(season, (1,365))
        final      = int(round(np.clip(hist_onset + fc_adj, lo, hi)))

        start_str, end_str = planting_window(final, target_year)
        conf               = confidence_level(p["n_obs"], p["std_onset"])
        notes              = _build_notes(p, fc_adj, forecast_summary, season)

        pred_tmax, pred_tmin = predict_temperature(p, target_year)
        temp_info            = temperature_label(pred_tmax, pred_tmin)

        pred_len = predict_season_length(p, target_year)
        len_days  = int(round(pred_len * 10))
        len_weeks = round(pred_len * 10 / 7, 1)

        return {
            "sector":                        sector,
            "season":                        season,
            "target_year":                   target_year,
            "predicted_onset_day":           final,
            "predicted_onset_date":          day_of_year_to_date_str(final),
            "planting_window_start":         start_str,
            "planting_window_end":           end_str,
            "confidence":                    conf,
            "historical_mean_onset":         round(p["mean_onset"],1),
            "historical_std_onset":          round(p["std_onset"],1) if not np.isnan(p["std_onset"]) else None,
            "trend_slope_days_per_yr":       round(p["trend_slope"],3),
            "trend_r2":                      round(p["trend_r2"],3),
            "forecast_adjustment_days":      fc_adj,
            "expected_rainfall_mm":          round(p["mean_rainfall"],1) if not np.isnan(p["mean_rainfall"]) else None,
            "historical_mean_tmax":          round(p["mean_max_temp"],2) if not np.isnan(p["mean_max_temp"]) else None,
            "historical_mean_tmin":          round(p["mean_min_temp"],2) if not np.isnan(p["mean_min_temp"]) else None,
            "predicted_tmax":                pred_tmax,
            "predicted_tmin":               pred_tmin,
            "temp_heat_label":               temp_info["heat_label"],
            "temp_cold_label":               temp_info["cold_label"],
            "temp_summary":                  temp_info["summary"],
            "predicted_length_dekads":       pred_len,
            "predicted_length_days":         len_days,
            "predicted_length_weeks":        len_weeks,
            "historical_mean_length_dekads": round(p["mean_length_dekads"],1) if not np.isnan(p["mean_length_dekads"]) else None,
            "n_years_data":                  p["n_obs"],
            "notes":                         notes,
        }

    def predict_all_sectors(self, season, target_year, forecasts=None):
        sectors = sorted({k[0] for k in self.params if k[1]==season})
        return [self.predict(s, season, target_year, (forecasts or {}).get(s))
                for s in sectors]

    def sector_season_stats(self, sector, season):
        key = (sector, season)
        if key not in self.params: return {}
        p = self.params[key].copy()
        p["onset_date_mean"] = day_of_year_to_date_str(p["mean_onset"])
        p["mean_length_weeks"] = round(p["mean_length_dekads"]*10/7,1) if not (p.get("mean_length_dekads") is None or (isinstance(p.get("mean_length_dekads"),float) and p["mean_length_dekads"]!=p["mean_length_dekads"])) else None
        return p

    def available_sectors(self):
        return sorted({k[0] for k in self.params})

    def available_seasons(self):
        return sorted({k[1] for k in self.params})
