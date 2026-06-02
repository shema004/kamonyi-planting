"""
decision_engine.py
------------------
Real-time planting decision engine for Kamonyi district.

Answers the question: "Should I plant TODAY?" for each sector.

Logic (based on Rwanda agrometeorology):
─────────────────────────────────────────
A season onset is confirmed when:
  1. Current dekad rainfall ≥ ONSET_RAIN_THRESHOLD (20mm)
  2. Forecast next dekad rainfall ≥ ONSET_RAIN_THRESHOLD (20mm)
  3. Today falls within the historical onset window for the sector
  4. No forecast dry spell (dekad < DRY_SPELL_THRESHOLD = 5mm)
     in the next 2 dekads

Decision outcomes:
──────────────────
  ✅ PLANT NOW        — all conditions met, rains established
  🌱 PLANT SOON       — in onset window, rains starting
  ⏳ WAIT             — before window or insufficient rain
  ⚠  USE EARLY VARIETY — onset window passing, rains late
  📅 SEASON ACTIVE    — past onset, season underway, plant if not done
  🔚 SEASON ENDING    — <2 dekads left, too late for new planting
  💤 OFF SEASON       — between seasons, prepare for next
"""

import numpy as np
from datetime import date, timedelta
from typing import Optional

# ── Agronomic thresholds ───────────────────────────────────────────────────
ONSET_RAIN_THRESHOLD    = 20.0   # mm per dekad — minimum to confirm onset
DRY_SPELL_THRESHOLD     = 5.0    # mm per dekad — below this = dry spell risk
ONSET_WINDOW_BUFFER     = 15     # days either side of historical mean onset
SEASON_ENDING_THRESHOLD = 20     # days remaining — too late for new planting
FORECAST_RAIN_PER_DAY   = None   # computed from forecast

# ── Season onset day ranges (from historical data) ─────────────────────────
# mean ± buffer defines the "planting window"
HISTORICAL_ONSET = {
    # season: (mean_onset_day, std_days, early_variety_cutoff_days_past_mean)
    "A": {"mean": 248, "std": 7,  "early_cutoff": 20},
    "B": {"mean": 61,  "std": 2,  "early_cutoff": 10},
}

# Season end days (approximate)
SEASON_END_DOY = {
    "A": 40,    # Feb 9 next year (handled specially)
    "B": 181,   # Jun 30
    "C": 273,   # Sep 30
}


# ── Helpers ────────────────────────────────────────────────────────────────

def doy_to_date_str(doy: int, year: int = None) -> str:
    """Convert day-of-year to readable date string."""
    if year is None:
        year = date.today().year
    try:
        d = date(year, 1, 1) + timedelta(days=int(doy) - 1)
        return d.strftime("%d %b %Y")
    except:
        return "??"


def current_dekad_number(today: date = None) -> int:
    """Return the current dekad number (1-36) within the year."""
    if today is None:
        today = date.today()
    doy = today.timetuple().tm_yday
    return (doy - 1) // 10 + 1


def dekad_rain_from_forecast(forecast_days: list, target_dekad_offset: int = 0) -> float:
    """
    Estimate rainfall for the current or next dekad from the forecast.
    target_dekad_offset: 0 = current dekad, 1 = next dekad

    Since forecast only covers 5 days, we:
    - For current dekad: sum all forecast rain (proxy for dekad rate)
    - For next dekad: extrapolate from last 2 days of forecast
    """
    if not forecast_days:
        return 0.0

    if target_dekad_offset == 0:
        # Sum forecast rain and scale to 10 days
        days_available = len(forecast_days)
        total_forecast  = sum(d.get("total_rain_mm", 0) for d in forecast_days)
        if days_available >= 5:
            return round(total_forecast * (10 / days_available), 1)
        else:
            return round(total_forecast, 1)

    elif target_dekad_offset == 1:
        # Use the last 2 days of forecast as proxy for next dekad rate
        last_days = forecast_days[-2:] if len(forecast_days) >= 2 else forecast_days
        daily_rate = sum(d.get("total_rain_mm", 0) for d in last_days) / len(last_days)
        return round(daily_rate * 10, 1)

    return 0.0


def max_pop_from_forecast(forecast_days: list) -> float:
    """Maximum precipitation probability across all forecast days (0-1)."""
    if not forecast_days:
        return 0.0
    return max(d.get("max_pop", 0) for d in forecast_days)


def days_remaining_in_season(season: str, today: date = None) -> int:
    """How many days left in the current season."""
    if today is None:
        today = date.today()
    doy = today.timetuple().tm_yday

    if season == "A":
        # Season A ends around Feb 9 next year
        end_date = date(today.year + 1, 2, 9) if doy >= 244 else date(today.year, 2, 9)
        return max(0, (end_date - today).days)
    else:
        end_doy = SEASON_END_DOY.get(season, 181)
        return max(0, end_doy - doy)


# ── Core decision function ─────────────────────────────────────────────────

def make_planting_decision(
    sector:           str,
    season:           str,
    predicted_onset:  dict,          # from model.predict()
    forecast_summary: Optional[dict], # from weather_api.get_7day_forecast()
    today:            date = None,
) -> dict:
    """
    Make a real-time planting decision for one sector/season.

    Parameters
    ----------
    sector          : e.g. "Gacurabwenge"
    season          : "A" or "B"
    predicted_onset : model.predict() output for this sector/season
    forecast_summary: weather_api.get_7day_forecast() output (or None)
    today           : override today's date (for testing)

    Returns
    -------
    Full decision dict with recommendation, reasoning, and conditions
    """
    if today is None:
        today = date.today()

    doy          = today.timetuple().tm_yday
    hist         = HISTORICAL_ONSET.get(season, {"mean": 61, "std": 5, "early_cutoff": 14})
    mean_onset   = predicted_onset.get("predicted_onset_day", hist["mean"])
    hist_std     = hist["std"]

    # ── 1. Compute onset window ────────────────────────────────────────────
    window_start = mean_onset - ONSET_WINDOW_BUFFER
    window_end   = mean_onset + ONSET_WINDOW_BUFFER
    early_cutoff = mean_onset + hist["early_cutoff"]   # past this → use early variety

    in_window    = window_start <= doy <= window_end
    past_window  = doy > window_end
    before_window= doy < window_start
    past_cutoff  = doy > early_cutoff

    # ── 2. Compute days relative to onset ─────────────────────────────────
    days_to_window_start = max(0, window_start - doy)
    days_past_onset      = max(0, doy - mean_onset)
    days_left_in_season  = days_remaining_in_season(season, today)
    weeks_left           = round(days_left_in_season / 7, 1)

    # ── 3. Rainfall from forecast ─────────────────────────────────────────
    forecast_days        = (forecast_summary or {}).get("days", [])
    current_dekad_rain   = dekad_rain_from_forecast(forecast_days, 0)
    next_dekad_rain      = dekad_rain_from_forecast(forecast_days, 1)
    max_pop              = max_pop_from_forecast(forecast_days)
    has_forecast         = bool(forecast_days)

    # Rain conditions
    current_rain_ok = current_dekad_rain >= ONSET_RAIN_THRESHOLD
    next_rain_ok    = next_dekad_rain    >= ONSET_RAIN_THRESHOLD
    dry_spell_risk  = next_dekad_rain    <  DRY_SPELL_THRESHOLD and has_forecast

    # Historical comparison
    hist_mean_rain  = predicted_onset.get("expected_rainfall_mm", 0) or 0
    hist_dekad_avg  = hist_mean_rain / (predicted_onset.get("predicted_length_dekads", 10) or 10)

    # ── 4. Season ending check ────────────────────────────────────────────
    season_ending   = days_left_in_season <= SEASON_ENDING_THRESHOLD

    # ── 5. Decision logic ─────────────────────────────────────────────────

    if season_ending:
        decision     = "SEASON_ENDING"
        emoji        = "🔚"
        color        = "red"
        headline     = "Too Late to Plant"
        advice       = (
            f"Only {days_left_in_season} days ({weeks_left} weeks) remain in Season {season}. "
            f"It is too late to start new planting — crops would not have enough time to mature. "
            f"Focus on harvesting existing crops and preparing for the next season."
        )
        action       = "Prepare for harvest. Start planning for next season."

    elif before_window and not has_forecast:
        decision     = "WAIT"
        emoji        = "⏳"
        color        = "yellow"
        headline     = "Wait — Season Not Started"
        advice       = (
            f"The historical onset window for Season {season} in {sector} begins around "
            f"{doy_to_date_str(window_start)} (day {window_start}). "
            f"Today is {days_to_window_start} days before the expected planting window. "
            f"No live forecast available to refine this estimate."
        )
        action       = f"Wait approximately {days_to_window_start} more days then monitor rainfall closely."

    elif before_window and has_forecast:
        if current_rain_ok and next_rain_ok and not dry_spell_risk:
            # Early rains — unusual but possible
            decision  = "PLANT_NOW"
            emoji     = "✅"
            color     = "green"
            headline  = "Early Rains — Plant Now"
            advice    = (
                f"Unusually early rains detected. Current dekad forecast: {current_dekad_rain}mm "
                f"(threshold: {ONSET_RAIN_THRESHOLD}mm). Next dekad forecast: {next_dekad_rain}mm. "
                f"Although this is {days_to_window_start} days ahead of the historical window, "
                f"rainfall conditions support planting now."
            )
            action    = "Plant immediately using fast-maturing varieties as a precaution."
        else:
            decision  = "WAIT"
            emoji     = "⏳"
            color     = "yellow"
            headline  = "Wait — Season Not Started"
            advice    = (
                f"Season {season} onset window begins around {doy_to_date_str(window_start)}. "
                f"Still {days_to_window_start} days away. "
                f"Current forecast rain: {current_dekad_rain}mm — below the {ONSET_RAIN_THRESHOLD}mm threshold."
            )
            action    = f"Wait {days_to_window_start} more days. Prepare land and seeds now."

    elif in_window:
        if current_rain_ok and next_rain_ok and not dry_spell_risk:
            decision  = "PLANT_NOW"
            emoji     = "✅"
            color     = "green"
            headline  = "Plant Now!"
            advice    = (
                f"All conditions are met for {sector}, Season {season}. "
                f"Current dekad rainfall: {current_dekad_rain}mm ✔ "
                f"(threshold: {ONSET_RAIN_THRESHOLD}mm). "
                f"Next dekad forecast: {next_dekad_rain}mm ✔. "
                f"No dry spell risk detected. "
                f"You have {days_left_in_season} days ({weeks_left} weeks) remaining in the season."
            )
            action    = "Plant immediately. Conditions are optimal."

        elif current_rain_ok and dry_spell_risk:
            decision  = "PLANT_SOON"
            emoji     = "🌱"
            color     = "orange"
            headline  = "Plant Soon — Monitor Closely"
            advice    = (
                f"Rains have started in {sector} ({current_dekad_rain}mm this dekad ✔) "
                f"but the forecast suggests a possible dry spell next dekad "
                f"({next_dekad_rain}mm forecast, threshold: {DRY_SPELL_THRESHOLD}mm). "
                f"Consider planting drought-tolerant varieties."
            )
            action    = "Plant drought-tolerant varieties. Monitor rainfall daily."

        elif current_rain_ok and not next_rain_ok:
            decision  = "PLANT_SOON"
            emoji     = "🌱"
            color     = "orange"
            headline  = "Rains Starting — Wait for Confirmation"
            advice    = (
                f"Current rains look promising ({current_dekad_rain}mm ✔) but next dekad "
                f"forecast ({next_dekad_rain}mm) hasn't reached the {ONSET_RAIN_THRESHOLD}mm "
                f"threshold yet. Wait 2–3 more days for confirmation before planting."
            )
            action    = "Wait 2–3 days. Watch for consistent daily rainfall of 2mm+."

        else:
            decision  = "WAIT"
            emoji     = "⏳"
            color     = "yellow"
            headline  = "In Window — Rains Not Yet Sufficient"
            advice    = (
                f"We are within the expected onset window for {sector} Season {season} "
                f"(day {window_start}–{window_end}), but current rainfall ({current_dekad_rain}mm) "
                f"is below the {ONSET_RAIN_THRESHOLD}mm threshold. Monitor daily."
            )
            action    = "Check rainfall daily. Do not plant yet — seeds may fail in dry soil."

    elif past_window and not past_cutoff:
        if current_rain_ok and next_rain_ok:
            decision  = "PLANT_NOW"
            emoji     = "✅"
            color     = "green"
            headline  = "Late Rains — Plant Now with Early Variety"
            advice    = (
                f"Rains arrived slightly late for {sector} Season {season} "
                f"({days_past_onset} days past the historical mean onset). "
                f"However rainfall conditions are now met: {current_dekad_rain}mm ✔, "
                f"forecast {next_dekad_rain}mm ✔. "
                f"Plant immediately using fast-maturing varieties to fit within the remaining "
                f"{days_left_in_season} days ({weeks_left} weeks)."
            )
            action    = "Plant now using fast-maturing varieties. Do not delay further."
        else:
            decision  = "USE_EARLY_VARIETY"
            emoji     = "⚠️"
            color     = "orange"
            headline  = "Late Season — Use Early-Maturing Variety"
            advice    = (
                f"The onset window has passed for {sector} Season {season} "
                f"({days_past_onset} days past expected onset). "
                f"Rainfall: {current_dekad_rain}mm (threshold: {ONSET_RAIN_THRESHOLD}mm). "
                f"Only {days_left_in_season} days ({weeks_left} weeks) remain. "
                f"Switch to fast-maturing crop varieties that can complete in {days_left_in_season} days."
            )
            action    = "Use only fast-maturing varieties (≤90 days). Prepare irrigation if possible."

    elif past_cutoff:
        decision      = "USE_EARLY_VARIETY"
        emoji         = "⚠️"
        color         = "orange"
        headline      = "Critical Delay — Fast Varieties Only"
        advice        = (
            f"Season {season} onset in {sector} is significantly delayed "
            f"({days_past_onset} days past the expected window). "
            f"Only {days_left_in_season} days ({weeks_left} weeks) left. "
            f"Standard varieties will not have enough time to mature. "
            f"Use only the fastest-maturing varieties available (60–80 days)."
        )
        action        = "Emergency: plant fast varieties immediately or skip this season."

    else:
        # Season is well underway — past onset, not ending
        decision      = "SEASON_ACTIVE"
        emoji         = "📅"
        color         = "blue"
        headline      = "Season Underway"
        advice        = (
            f"Season {season} is active in {sector}. "
            f"The rains established {days_past_onset} days ago. "
            f"{days_left_in_season} days ({weeks_left} weeks) remain. "
            f"If you haven't planted yet, use appropriate varieties for the time remaining."
        )
        action        = (
            f"{'Plant immediately with fast varieties.' if days_left_in_season < 90 else 'Plant now if not done. Season is progressing normally.'}"
        )

    # ── 6. Suitable crops for decision ────────────────────────────────────
    # Filter crops that fit in remaining days
    from crops import CROP_RULES
    fitting_crops = []
    for crop, rules in CROP_RULES.items():
        if season in rules.get("seasons", []) and rules.get("min_days", 0) <= days_left_in_season:
            fitting_crops.append({
                "crop":     crop,
                "min_days": rules["min_days"],
                "max_days": rules.get("max_days", 999),
            })
    fitting_crops.sort(key=lambda x: x["min_days"])

    # ── 7. Build conditions checklist ─────────────────────────────────────
    conditions = [
        {
            "label":  "In onset window",
            "met":    in_window or (past_window and not past_cutoff and current_rain_ok),
            "value":  f"Day {doy} (window: {window_start}–{window_end})",
        },
        {
            "label":  f"Current dekad rain ≥ {ONSET_RAIN_THRESHOLD}mm",
            "met":    current_rain_ok,
            "value":  f"{current_dekad_rain}mm {'✔' if current_rain_ok else '✖'}",
        },
        {
            "label":  f"Next dekad forecast ≥ {ONSET_RAIN_THRESHOLD}mm",
            "met":    next_rain_ok,
            "value":  f"{next_dekad_rain}mm {'✔' if next_rain_ok else '✖'}" if has_forecast else "No forecast",
        },
        {
            "label":  "No dry spell risk",
            "met":    not dry_spell_risk,
            "value":  f"{'Dry spell risk detected ⚠' if dry_spell_risk else 'No dry spell forecast ✔'}",
        },
        {
            "label":  "Season has time remaining",
            "met":    not season_ending,
            "value":  f"{days_left_in_season} days ({weeks_left} weeks) left",
        },
    ]

    return {
        "sector":               sector,
        "season":               season,
        "today":                today.isoformat(),
        "day_of_year":          doy,
        "decision":             decision,
        "emoji":                emoji,
        "color":                color,
        "headline":             headline,
        "advice":               advice,
        "action":               action,
        "conditions":           conditions,
        "conditions_met":       sum(1 for c in conditions if c["met"]),
        "conditions_total":     len(conditions),
        "fitting_crops":        fitting_crops[:8],  # top 8 by min days
        "days_to_window":       days_to_window_start,
        "days_past_onset":      days_past_onset,
        "days_left_in_season":  days_left_in_season,
        "weeks_left_in_season": weeks_left,
        "current_dekad":        current_dekad_number(today),
        "current_dekad_rain_mm":current_dekad_rain,
        "next_dekad_rain_mm":   next_dekad_rain,
        "onset_threshold_mm":   ONSET_RAIN_THRESHOLD,
        "dry_spell_risk":       dry_spell_risk,
        "precipitation_prob_pct": round(max_pop * 100),
        "has_live_forecast":    has_forecast,
        "hist_dekad_avg_mm":    round(hist_dekad_avg, 1),
        "predicted_onset_date": predicted_onset.get("predicted_onset_date", "?"),
        "predicted_onset_day":  mean_onset,
    }


def make_all_sector_decisions(
    model,
    season:   str,
    forecasts: dict,
    today:    date = None,
) -> list:
    """
    Run the decision engine for all sectors for a given season.

    Parameters
    ----------
    model     : PlantingModel instance
    season    : "A" or "B"
    forecasts : {sector: forecast_summary} from weather_api
    """
    results = []
    for sector in model.available_sectors():
        prediction = model.predict(sector, season, (today or date.today()).year)
        forecast   = forecasts.get(sector) if forecasts else None
        decision   = make_planting_decision(sector, season, prediction, forecast, today)
        results.append(decision)
    return results
