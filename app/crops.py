"""
crops.py
--------
Crop suggestion engine for Kamonyi district.

Evaluates each crop against FOUR factors:
  1. Rainfall        — total seasonal rainfall (mm)
  2. Max temperature — daytime heat stress
  3. Min temperature — night cold stress (germination risk)
  4. Season length   — growing days needed vs available (dekads → days)

Returns: Suitable / Marginal / Not Recommended  with detailed reasons.
"""

# ── Crop rules ─────────────────────────────────────────────────────────────
# Each crop has:
#   min_rain / max_rain      — total seasonal rainfall (mm)
#   min_tmax / max_tmax      — daytime temperature range (°C)
#   min_tmin                 — minimum night temperature tolerated (°C)
#   min_days / max_days      — growing season length needed (days)
#   seasons                  — compatible seasons (A = long rains, B = short rains)
#   notes                    — agronomic note for farmers

CROP_RULES = {
    # ── Cereals & Grains ───────────────────────────────────────────────────
    "Maize": {
        "min_rain":300,"max_rain":1200,"min_tmax":20,"max_tmax":32,"min_tmin":10,
        "min_days":90,"max_days":200,"seasons":["A","B"],
        "notes":"Fast-maturing varieties (90 days) for Season B; long-season varieties for Season A.",
    },
    "Rice": {
        "min_rain":900,"max_rain":2500,"min_tmax":22,"max_tmax":35,"min_tmin":15,
        "min_days":110,"max_days":200,"seasons":["A"],
        "notes":"Needs high rainfall and warm nights. Season A only.",
    },
    "Wheat": {
        "min_rain":250,"max_rain":900,"min_tmax":15,"max_tmax":26,"min_tmin":5,
        "min_days":90,"max_days":150,"seasons":["B"],
        "notes":"Prefers cooler temperatures. Well suited for Season B in highlands.",
    },
    "Barley": {
        "min_rain":200,"max_rain":800,"min_tmax":14,"max_tmax":26,"min_tmin":4,
        "min_days":80,"max_days":140,"seasons":["B"],
        "notes":"Drought-tolerant cereal, good for drier Season B sectors.",
    },
    "Sorghum": {
        "min_rain":300,"max_rain":1000,"min_tmax":20,"max_tmax":36,"min_tmin":12,
        "min_days":90,"max_days":180,"seasons":["A","B"],
        "notes":"Drought and heat tolerant. Reliable in variable rainfall years.",
    },
    # ── Legumes ────────────────────────────────────────────────────────────
    "Beans": {
        "min_rain":300,"max_rain":1000,"min_tmax":18,"max_tmax":30,"min_tmin":10,
        "min_days":60,"max_days":120,"seasons":["A","B"],
        "notes":"Climbing beans need 90–120 days; bush beans 60–70 days. Both seasons.",
    },
    "Soybeans": {
        "min_rain":450,"max_rain":900,"min_tmax":20,"max_tmax":32,"min_tmin":12,
        "min_days":90,"max_days":140,"seasons":["A","B"],
        "notes":"Fixes nitrogen in soil. Best in well-drained soils with moderate rain.",
    },
    # ── Roots & Tubers ─────────────────────────────────────────────────────
    "Cassava": {
        "min_rain":500,"max_rain":2500,"min_tmax":22,"max_tmax":35,"min_tmin":15,
        "min_days":180,"max_days":365,"seasons":["A"],
        "notes":"Long-season crop (6–18 months). Only viable in Season A (long rains). Drought-tolerant once established.",
    },
    "Sweet Potatoes": {
        "min_rain":400,"max_rain":1200,"min_tmax":20,"max_tmax":32,"min_tmin":12,
        "min_days":90,"max_days":160,"seasons":["A","B"],
        "notes":"Adaptable and fast-growing. Good for both seasons.",
    },
    "Irish Potatoes": {
        "min_rain":400,"max_rain":900,"min_tmax":15,"max_tmax":25,"min_tmin":7,
        "min_days":80,"max_days":130,"seasons":["B"],
        "notes":"Requires cool temperatures. Ideal for Season B in Kamonyi highlands.",
    },
    # ── Fruits ─────────────────────────────────────────────────────────────
    "Passion Fruits": {
        "min_rain":600,"max_rain":2000,"min_tmax":18,"max_tmax":30,"min_tmin":10,
        "min_days":180,"max_days":365,"seasons":["A","B"],
        "notes":"Perennial crop — plant in Season A for best establishment. Produces year-round once mature.",
    },
    "Pineapple": {
        "min_rain":600,"max_rain":1500,"min_tmax":22,"max_tmax":32,"min_tmin":15,
        "min_days":365,"max_days":730,"seasons":["A"],
        "notes":"Takes 18–24 months to first harvest. Long-term investment, needs Season A establishment.",
    },
    "Coffee": {
        "min_rain":600,"max_rain":1400,"min_tmax":18,"max_tmax":28,"min_tmin":12,
        "min_days":365,"max_days":730,"seasons":["A","B"],
        "notes":"Perennial. Established coffee plants benefit from both seasons. New planting best in Season A.",
    },
    # ── Vegetables ─────────────────────────────────────────────────────────
    "Tomatoes": {
        "min_rain":400,"max_rain":1000,"min_tmax":20,"max_tmax":30,"min_tmin":12,
        "min_days":70,"max_days":120,"seasons":["A","B"],
        "notes":"Sensitive to excessive rain (disease risk). Consider drip irrigation in high-rainfall sectors.",
    },
    "Leafy Greens": {
        "min_rain":250,"max_rain":1000,"min_tmax":15,"max_tmax":28,"min_tmin":8,
        "min_days":30,"max_days":90,"seasons":["A","B"],
        "notes":"Fast-growing (30–60 days). Can be planted multiple times per season.",
    },
    "Carrots": {
        "min_rain":250,"max_rain":800,"min_tmax":14,"max_tmax":26,"min_tmin":7,
        "min_days":70,"max_days":110,"seasons":["B"],
        "notes":"Cooler Season B conditions ideal. Needs loose, well-drained soil.",
    },
    # ── Other ──────────────────────────────────────────────────────────────
    "Sunflower": {
        "min_rain":300,"max_rain":900,"min_tmax":20,"max_tmax":34,"min_tmin":10,
        "min_days":90,"max_days":130,"seasons":["A","B"],
        "notes":"Drought-tolerant. Good for oil production and soil improvement.",
    },
    "Fodder Crops": {
        "min_rain":350,"max_rain":1500,"min_tmax":16,"max_tmax":34,"min_tmin":8,
        "min_days":45,"max_days":180,"seasons":["A","B"],
        "notes":"Napier grass, alfalfa etc. Support livestock during dry periods.",
    },
}

CROP_CATEGORIES = {
    "Cereals & Grains": ["Maize","Rice","Wheat","Barley","Sorghum"],
    "Legumes":          ["Beans","Soybeans"],
    "Roots & Tubers":   ["Cassava","Sweet Potatoes","Irish Potatoes"],
    "Fruits":           ["Passion Fruits","Pineapple","Coffee"],
    "Vegetables":       ["Tomatoes","Leafy Greens","Carrots"],
    "Other":            ["Sunflower","Fodder Crops"],
}

# Reverse lookup: crop → category
_CAT_LOOKUP = {crop: cat for cat, crops in CROP_CATEGORIES.items() for crop in crops}


def dekads_to_weeks(dekads: float) -> float:
    """Convert dekads (10-day periods) to weeks."""
    return round(dekads * 10 / 7, 1)


def dekads_to_days(dekads: float) -> int:
    """Convert dekads to days."""
    return int(round(dekads * 10))


def suggest_crops(
    season: str,
    predicted_rainfall_mm: float,
    predicted_tmax: float,
    predicted_tmin: float,
    predicted_length_dekads: float,
) -> dict:
    """
    Evaluate all crops against four factors:
      1. Rainfall        (mm)
      2. Max temperature (°C daytime)
      3. Min temperature (°C night)
      4. Season length   (dekads → days)

    Returns:
        {
          "suitable":        [{crop, category, score, factors, reason, notes}],
          "marginal":        [{crop, category, score, factors, reason, notes}],
          "not_recommended": [{crop, category, reason, notes}],
          "season_length_days":  int,
          "season_length_weeks": float,
        }
    """
    available_days  = dekads_to_days(predicted_length_dekads)
    available_weeks = dekads_to_weeks(predicted_length_dekads)

    suitable, marginal, not_recommended = [], [], []

    for crop, rules in CROP_RULES.items():
        category = _CAT_LOOKUP.get(crop, "Other")

        # ── Factor checks ────────────────────────────────────────────────
        # 1. Season compatibility
        if season not in rules.get("seasons", []):
            not_recommended.append({
                "crop": crop, "category": category,
                "reason": f"Not suited for this season (suitable for: {', '.join(rules['seasons'])})",
                "notes":  rules.get("notes",""),
            })
            continue

        # 2. Season length — crop needs enough days to complete its cycle
        needs_min = rules["min_days"]
        needs_max = rules["max_days"]
        length_ok  = available_days >= needs_min
        length_msg = None
        if not length_ok:
            length_msg = (f"season too short ({available_days} days available, "
                          f"needs {needs_min}+ days)")

        # 3. Rainfall
        rain_ok   = rules["min_rain"] <= predicted_rainfall_mm <= rules["max_rain"]
        rain_msg  = None
        if predicted_rainfall_mm < rules["min_rain"]:
            rain_msg = f"rainfall low ({predicted_rainfall_mm:.0f} mm, needs {rules['min_rain']}+)"
        elif predicted_rainfall_mm > rules["max_rain"]:
            rain_msg = f"rainfall high ({predicted_rainfall_mm:.0f} mm, max {rules['max_rain']})"

        # 4. Max temperature (heat stress)
        tmax_ok   = rules["min_tmax"] <= predicted_tmax <= rules["max_tmax"]
        tmax_msg  = None
        if predicted_tmax > rules["max_tmax"]:
            tmax_msg = f"too hot ({predicted_tmax:.1f}°C, max {rules['max_tmax']}°C)"
        elif predicted_tmax < rules["min_tmax"]:
            tmax_msg = f"too cold days ({predicted_tmax:.1f}°C, needs {rules['min_tmax']}°C+)"

        # 5. Min temperature (cold night stress)
        tmin_ok   = predicted_tmin >= rules["min_tmin"]
        tmin_msg  = None
        if not tmin_ok:
            tmin_msg = f"cold nights ({predicted_tmin:.1f}°C, needs {rules['min_tmin']}°C+ for germination)"

        # ── Count issues ─────────────────────────────────────────────────
        issues = [m for m in [length_msg, rain_msg, tmax_msg, tmin_msg] if m]
        n_ok   = sum([length_ok, rain_ok, tmax_ok, tmin_ok])

        # Factor summary for display
        factors = {
            "length": {"ok": length_ok, "available": available_days,
                       "needed": needs_min, "msg": length_msg},
            "rainfall":{"ok": rain_ok,   "value": predicted_rainfall_mm,
                        "range": f"{rules['min_rain']}–{rules['max_rain']} mm", "msg": rain_msg},
            "heat":    {"ok": tmax_ok,   "value": predicted_tmax,
                        "range": f"{rules['min_tmax']}–{rules['max_tmax']} °C", "msg": tmax_msg},
            "cold":    {"ok": tmin_ok,   "value": predicted_tmin,
                        "min": rules["min_tmin"], "msg": tmin_msg},
        }

        if n_ok == 4:
            # All four factors good
            reason = (f"✔ Rain {predicted_rainfall_mm:.0f} mm ✔ "
                      f"Heat {predicted_tmax:.1f}°C ✔ "
                      f"Cold {predicted_tmin:.1f}°C ✔ "
                      f"Season {available_days} days")
            suitable.append({
                "crop": crop, "category": category,
                "score": 4, "factors": factors,
                "reason": reason, "notes": rules.get("notes",""),
            })

        elif n_ok >= 2 and length_ok:
            # Length is OK but 1–2 climate issues
            reason = "Possible with care — " + "; ".join(issues)
            marginal.append({
                "crop": crop, "category": category,
                "score": n_ok, "factors": factors,
                "reason": reason, "notes": rules.get("notes",""),
            })

        elif n_ok >= 2 and not length_ok:
            # Season length is the main blocker
            reason = "Season too short — " + "; ".join(issues)
            marginal.append({
                "crop": crop, "category": category,
                "score": n_ok, "factors": factors,
                "reason": reason, "notes": rules.get("notes",""),
            })

        else:
            reason = "Not recommended — " + "; ".join(issues) if issues else "Conditions not met"
            not_recommended.append({
                "crop": crop, "category": category,
                "factors": factors,
                "reason": reason, "notes": rules.get("notes",""),
            })

    # Sort suitable by score desc, then name
    suitable.sort(key=lambda x: (-x["score"], x["crop"]))
    marginal.sort(key=lambda x: (-x["score"], x["crop"]))

    return {
        "suitable":              suitable,
        "marginal":              marginal,
        "not_recommended":       not_recommended,
        "season_length_days":    available_days,
        "season_length_weeks":   available_weeks,
        "season_length_dekads":  round(predicted_length_dekads, 1),
        "predicted_rainfall_mm": round(predicted_rainfall_mm, 1),
        "predicted_tmax":        round(predicted_tmax, 2),
        "predicted_tmin":        round(predicted_tmin, 2),
    }


def suggest_crops_for_prediction(prediction: dict) -> dict:
    """Convenience wrapper — takes a prediction dict from model.predict()."""
    return suggest_crops(
        season                  = prediction.get("season", "A"),
        predicted_rainfall_mm   = prediction.get("expected_rainfall_mm") or 0,
        predicted_tmax          = prediction.get("predicted_tmax") or
                                  prediction.get("historical_mean_tmax") or 25.0,
        predicted_tmin          = prediction.get("predicted_tmin") or
                                  prediction.get("historical_mean_tmin") or 14.0,
        predicted_length_dekads = prediction.get("predicted_length_dekads") or
                                  prediction.get("historical_mean_length_dekads") or 10.0,
    )
