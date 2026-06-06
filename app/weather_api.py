# weather_api.py
# Fetches live weather forecasts from OpenWeatherMap for each sector
# Free tier: /forecast endpoint gives up to 7 days (56 x 3-hour steps)

import time
import requests
import numpy as np
from datetime import datetime, timezone
from typing import Optional
from collections import defaultdict

SECTOR_COORDS = {
    "Gacurabwenge": (-2.05, 29.85),
    "Karama":       (-2.12, 29.82),
    "Kayenzi":      (-2.08, 29.78),
    "Kayumbu":      (-2.15, 29.75),
    "Mugina":       (-2.10, 29.70),
    "Musambira":    (-2.18, 29.80),
    "Nyamiyaga":    (-2.20, 29.85),
    "Nyarubaka":    (-2.25, 29.78),
    "Rugarika":     (-2.02, 29.90),
    "Rukoma":       (-2.00, 29.82),
    "Runda":        (-2.22, 29.92),
    "Ngamba":       (-2.08, 29.83),  # approximate — central Kamonyi
}

KAMONYI_CENTER  = (-2.12, 29.83)
BASE_FORECAST   = "https://api.openweathermap.org/data/2.5/forecast"
BASE_CURRENT    = "https://api.openweathermap.org/data/2.5/weather"


def _get(url, params, timeout=10):
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"[weather_api] Request failed: {e}")
        return None


def get_current_weather(api_key: str, sector: str = None) -> Optional[dict]:
    lat, lon = SECTOR_COORDS.get(sector, KAMONYI_CENTER) if sector else KAMONYI_CENTER
    data = _get(BASE_CURRENT, {
        "lat": lat, "lon": lon, "appid": api_key, "units": "metric",
    })
    if not data:
        return None
    return {
        "sector":          sector or "Kamonyi_District",
        "dt":              datetime.fromtimestamp(data["dt"], tz=timezone.utc).isoformat(),
        "temp_c":          data["main"]["temp"],
        "temp_min_c":      data["main"]["temp_min"],
        "temp_max_c":      data["main"]["temp_max"],
        "humidity_pct":    data["main"]["humidity"],
        "rain_1h_mm":      data.get("rain", {}).get("1h", 0.0),
        "wind_speed_ms":   data.get("wind", {}).get("speed", 0.0),
        "cloud_cover_pct": data.get("clouds", {}).get("all", 0),
        "pressure_hpa":    data["main"].get("pressure", 0),
        "description":     data["weather"][0]["description"],
        "icon":            data["weather"][0]["icon"],
        "lat":             lat,
        "lon":             lon,
    }


def get_7day_forecast(api_key: str, sector: str = None) -> Optional[dict]:
    """
    Fetch 7-day forecast using OWM free /forecast endpoint.
    Uses cnt=56 (56 x 3-hour steps = 168 hours = 7 days).
    Returns daily aggregates.
    """
    lat, lon = SECTOR_COORDS.get(sector, KAMONYI_CENTER) if sector else KAMONYI_CENTER

    data = _get(BASE_FORECAST, {
        "lat": lat, "lon": lon,
        "appid": api_key, "units": "metric",
        "cnt": 56,          # 7 days × 8 slots/day
    })
    if not data:
        return None

    # Aggregate 3-hour steps into daily summaries
    daily = defaultdict(list)
    for item in data.get("list", []):
        day_key = item["dt_txt"][:10]   # "YYYY-MM-DD"
        daily[day_key].append(item)

    days = []
    for day_str, items in sorted(daily.items()):
        temps    = [i["main"]["temp"]     for i in items]
        t_max    = [i["main"]["temp_max"] for i in items]
        t_min    = [i["main"]["temp_min"] for i in items]
        humidity = [i["main"]["humidity"] for i in items]
        rain     = [i.get("rain", {}).get("3h", 0.0) for i in items]
        pop      = [i.get("pop", 0.0)    for i in items]

        # Pick the most common weather description (daytime slot preferred)
        descriptions = [i["weather"][0]["description"] for i in items]
        icons        = [i["weather"][0]["icon"]        for i in items]
        desc  = max(set(descriptions), key=descriptions.count)
        icon  = max(set(icons),        key=icons.count)

        days.append({
            "date":           day_str,
            "mean_temp_c":    round(np.mean(temps), 1),
            "max_temp_c":     round(max(t_max), 1),
            "min_temp_c":     round(min(t_min), 1),
            "mean_humidity":  round(np.mean(humidity), 0),
            "total_rain_mm":  round(sum(rain), 1),
            "max_pop":        round(max(pop), 2),          # prob of precipitation
            "description":    desc,
            "icon":           icon,
            "icon_url":       f"https://openweathermap.org/img/wn/{icon}@2x.png",
        })

    sector_label = sector or "Kamonyi_District"
    return {
        "sector":         sector_label,
        "lat":            lat,
        "lon":            lon,
        "forecast_start": days[0]["date"] if days else None,
        "forecast_end":   days[-1]["date"] if days else None,
        "days":           days,
        "n_days":         len(days),
        "api_limit_note":  "OWM free tier: up to 5 days (40 x 3-hour steps)",
    }


# Keep old name as alias so existing code doesn't break
def get_forecast_summary(api_key: str, sector: str = None) -> Optional[dict]:
    return get_7day_forecast(api_key, sector)


def get_all_sectors_forecast(api_key: str, delay: float = 0.5) -> dict:
    results = {}
    for sector in SECTOR_COORDS:
        print(f"[weather_api] Fetching 7-day forecast for {sector} ...")
        results[sector] = get_7day_forecast(api_key, sector)
        time.sleep(delay)
    return results
