"""
season_status.py
----------------
Determines the current active season and computes live status metrics:
  - Which season we are in right now
  - When it started (predicted onset date)
  - How many days / weeks have elapsed
  - How many weeks remain
  - Expected total rainfall for the season
  - Estimated rainfall received so far (proportional to elapsed time)
  - Current temperature context vs historical average
  - Progress percentage
"""

import numpy as np
from datetime import date, timedelta
from typing import Optional


# ── Season calendar definitions ────────────────────────────────────────────
# Day-of-year ranges for each season (approximate)
# Season A crosses year boundary: onset ~Sep (day 244), ends ~Feb (day 40 next yr)
SEASON_CALENDAR = {
    "A": {
        "doy_start":  244,   # Sep 1
        "doy_end":    365,   # Dec 31 (continues into next year up to day 40)
        "doy_end_wrap": 40,  # Jan 40 = Feb 9 next year
        "crosses_year": True,
        "name":       "Urugaryi / Umuhindo",
        "period":     "September – February",
        "plant_month":"September",
        "harvest":    "December – February",
    },
    "B": {
        "doy_start":  32,    # Feb 1
        "doy_end":    181,   # Jun 30
        "crosses_year": False,
        "name":       "Itumba",
        "period":     "March – June",
        "plant_month":"March",
        "harvest":    "June – July",
    },
    "C": {
        "doy_start":  182,   # Jul 1
        "doy_end":    273,   # Sep 30
        "crosses_year": False,
        "name":       "Impeshyi",
        "period":     "July – September",
        "plant_month":"July / August",
        "harvest":    "September – October",
    },
}


def get_current_season(today: date = None) -> Optional[str]:
    """Return the season key ('A','B','C') we are currently in, or None."""
    if today is None:
        today = date.today()
    doy = today.timetuple().tm_yday

    if SEASON_CALENDAR["B"]["doy_start"] <= doy <= SEASON_CALENDAR["B"]["doy_end"]:
        return "B"
    if SEASON_CALENDAR["C"]["doy_start"] <= doy <= SEASON_CALENDAR["C"]["doy_end"]:
        return "C"
    # Season A wraps across year boundary
    if doy >= SEASON_CALENDAR["A"]["doy_start"] or doy <= SEASON_CALENDAR["A"]["doy_end_wrap"]:
        return "A"
    return None  # transition gap


def get_season_progress(season: str, today: date = None) -> dict:
    """
    Compute how far through the current season we are.
    Returns fraction 0.0–1.0 and days elapsed/remaining.
    """
    if today is None:
        today = date.today()
    doy = today.timetuple().tm_yday
    cal = SEASON_CALENDAR[season]

    if season == "A":
        # Season A: Sep 1 (day 244) → Feb 9 next year (day 40)
        # Total length ~155 days
        total_days = (365 - cal["doy_start"]) + cal["doy_end_wrap"]
        if doy >= cal["doy_start"]:
            elapsed = doy - cal["doy_start"]
        else:
            elapsed = (365 - cal["doy_start"]) + doy
        # End date = Feb 9 of next year (if we're in Sep-Dec) or current year
        if doy >= cal["doy_start"]:
            end_date = date(today.year + 1, 2, 9)
        else:
            end_date = date(today.year, 2, 9)
        start_date = date(today.year if doy >= cal["doy_start"] else today.year - 1, 9, 1)
    else:
        total_days = cal["doy_end"] - cal["doy_start"]
        elapsed    = doy - cal["doy_start"]
        start_date = date(today.year, *_doy_to_md(cal["doy_start"]))
        end_date   = date(today.year, *_doy_to_md(cal["doy_end"]))

    elapsed   = max(0, elapsed)
    remaining = max(0, total_days - elapsed)
    fraction  = round(min(elapsed / total_days, 1.0), 3) if total_days > 0 else 0

    return {
        "total_days":      total_days,
        "elapsed_days":    elapsed,
        "elapsed_weeks":   round(elapsed / 7, 1),
        "remaining_days":  remaining,
        "remaining_weeks": round(remaining / 7, 1),
        "progress_pct":    round(fraction * 100, 1),
        "start_date":      start_date.strftime("%Y-%m-%d"),
        "end_date":        end_date.strftime("%Y-%m-%d"),
        "fraction":        fraction,
    }


def _doy_to_md(doy: int):
    """Convert day-of-year to (month, day) tuple."""
    d = date(2000, 1, 1) + timedelta(days=doy - 1)
    return d.month, d.day


def get_current_season_status(merged_df, today: date = None) -> dict:
    """
    Full current season status across all sectors.

    Returns a dict with:
      season, season_info, progress, per_sector stats,
      district averages, comparisons vs historical
    """
    if today is None:
        today = date.today()

    season = get_current_season(today)
    if season is None:
        return {
            "active":  False,
            "message": "Currently between seasons (transition period).",
            "today":   today.isoformat(),
        }

    cal      = SEASON_CALENDAR[season]
    progress = get_season_progress(season, today)

    # Historical data for this season
    hist = merged_df[merged_df["season"] == season]

    # District-wide historical averages
    hist_avg_rain   = float(hist["total_rainfall"].mean())    if not hist.empty else 0
    hist_avg_tmax   = float(hist["mean_max_temp"].mean())     if not hist.empty else 25.0
    hist_avg_tmin   = float(hist["mean_min_temp"].mean())     if not hist.empty else 14.0
    hist_avg_length = float(hist["length_dekads"].mean())     if not hist.empty else 10.0
    hist_avg_onset  = float(hist["onset_day"].mean())         if not hist.empty else 60.0

    # Expected rainfall so far (proportional to season elapsed)
    expected_rain_so_far = hist_avg_rain * progress["fraction"]

    # Per-sector breakdown
    sector_stats = []
    for sector, grp in hist.groupby("sector"):
        sec_rain   = float(grp["total_rainfall"].mean())
        sec_tmax   = float(grp["mean_max_temp"].mean())   if grp["mean_max_temp"].notna().any() else None
        sec_tmin   = float(grp["mean_min_temp"].mean())   if grp["mean_min_temp"].notna().any() else None
        sec_length = float(grp["length_dekads"].mean())

        sec_rain_so_far = sec_rain * progress["fraction"]

        sector_stats.append({
            "sector":                    sector,
            "hist_avg_total_rain_mm":    round(sec_rain, 1),
            "hist_avg_tmax":             round(sec_tmax, 2)   if sec_tmax else None,
            "hist_avg_tmin":             round(sec_tmin, 2)   if sec_tmin else None,
            "hist_avg_length_dekads":    round(sec_length, 1),
            "hist_avg_length_weeks":     round(sec_length * 10 / 7, 1),
            "expected_rain_so_far_mm":   round(sec_rain_so_far, 1),
        })

    # Next season info
    season_order = ["A","B","C"]
    next_season  = season_order[(season_order.index(season) + 1) % 3]
    next_info    = SEASON_CALENDAR[next_season]

    return {
        "active":           True,
        "today":            today.isoformat(),
        "season":           season,
        "season_name":      cal["name"],
        "season_period":    cal["period"],
        "planting_month":   cal["plant_month"],
        "harvest_period":   cal["harvest"],
        "progress":         progress,
        "district_averages":{
            "hist_avg_total_rain_mm":   round(hist_avg_rain, 1),
            "hist_avg_tmax":            round(hist_avg_tmax, 2),
            "hist_avg_tmin":            round(hist_avg_tmin, 2),
            "hist_avg_length_dekads":   round(hist_avg_length, 1),
            "hist_avg_length_weeks":    round(hist_avg_length * 10 / 7, 1),
            "hist_avg_onset_day":       round(hist_avg_onset, 0),
            "expected_rain_so_far_mm":  round(expected_rain_so_far, 1),
        },
        "sector_stats":     sector_stats,
        "next_season":      next_season,
        "next_season_name": next_info["name"],
        "next_season_start":next_info["period"].split("–")[0].strip(),
    }
