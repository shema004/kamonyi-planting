# config.py

OWM_API_KEY = "00746cb05cf641c4e8059aa1444b8cd4"
XLSX_PATH   = None

PLANTING_WINDOW_DAYS  = 10
MIN_YEARS_FOR_TREND   = 5
ROLLING_WINDOW_YEARS  = 5
API_CALL_DELAY        = 0.5

# ── Corrected Rwanda season definitions ────────────────────────────────────
SEASON_ONSET_RANGE = {
    "A": (244, 310),   # Urugaryi/Umuhindo: mid-Sep onset  (day 244-310)
    "B": (50,  100),   # Itumba:            March onset    (day 50-100)
    # Season C (Impeshyi Jul-Sep) not in the dataset — shown as info only
}

SEASON_INFO = {
    "A": {
        "local_name":   "Urugaryi / Umuhindo",
        "period":       "September – February",
        "planting":     "Mid-September",
        "harvest":      "Mid-December to Mid-February",
        "description":  "Long rainy season (main season)",
    },
    "B": {
        "local_name":   "Itumba",
        "period":       "March – June",
        "planting":     "March",
        "harvest":      "Early June to Mid-July",
        "description":  "Short rainy season",
    },
    "C": {
        "local_name":   "Impeshyi",
        "period":       "July – September",
        "planting":     "July / August",
        "harvest":      "September – October",
        "description":  "Dry season (limited irrigation farming only). No historical data available.",
    },
}

SEASON_LABELS = {
    "A": "Season A – Urugaryi/Umuhindo (Sep–Feb)",
    "B": "Season B – Itumba (Mar–Jun)",
    "C": "Season C – Impeshyi (Jul–Sep)",
}

# ── Crop suitability rules ─────────────────────────────────────────────────
# Evaluated per sector/season based on predicted rainfall + temperature.
# Format: { crop_name: { min_rain, max_rain, min_tmax, max_tmax, seasons } }
CROP_RULES = {
    # Cereals & Grains
    "Maize":         {"min_rain": 400,  "max_rain": 1200, "min_tmax": 22, "max_tmax": 32, "seasons": ["A","B"]},
    "Rice":          {"min_rain": 800,  "max_rain": 2000, "min_tmax": 22, "max_tmax": 35, "seasons": ["A"]},
    "Wheat":         {"min_rain": 300,  "max_rain": 900,  "min_tmax": 18, "max_tmax": 28, "seasons": ["B"]},
    "Barley":        {"min_rain": 250,  "max_rain": 800,  "min_tmax": 16, "max_tmax": 26, "seasons": ["B"]},
    "Sorghum":       {"min_rain": 300,  "max_rain": 1000, "min_tmax": 22, "max_tmax": 34, "seasons": ["A","B"]},
    # Legumes
    "Beans":         {"min_rain": 300,  "max_rain": 900,  "min_tmax": 18, "max_tmax": 30, "seasons": ["A","B"]},
    "Soybeans":      {"min_rain": 400,  "max_rain": 900,  "min_tmax": 20, "max_tmax": 30, "seasons": ["A","B"]},
    # Roots & Tubers
    "Cassava":       {"min_rain": 500,  "max_rain": 2000, "min_tmax": 22, "max_tmax": 34, "seasons": ["A"]},
    "Sweet Potatoes":{"min_rain": 400,  "max_rain": 1200, "min_tmax": 20, "max_tmax": 32, "seasons": ["A","B"]},
    "Irish Potatoes":{"min_rain": 400,  "max_rain": 900,  "min_tmax": 16, "max_tmax": 26, "seasons": ["B"]},
    # Fruits
    "Passion Fruits":{"min_rain": 600,  "max_rain": 2000, "min_tmax": 20, "max_tmax": 30, "seasons": ["A","B"]},
    "Pineapple":     {"min_rain": 600,  "max_rain": 1500, "min_tmax": 22, "max_tmax": 32, "seasons": ["A"]},
    "Coffee":        {"min_rain": 600,  "max_rain": 1200, "min_tmax": 20, "max_tmax": 28, "seasons": ["A","B"]},
    # Vegetables
    "Tomatoes":      {"min_rain": 400,  "max_rain": 1000, "min_tmax": 20, "max_tmax": 30, "seasons": ["A","B"]},
    "Leafy Greens":  {"min_rain": 300,  "max_rain": 900,  "min_tmax": 16, "max_tmax": 28, "seasons": ["A","B"]},
    "Carrots":       {"min_rain": 300,  "max_rain": 800,  "min_tmax": 16, "max_tmax": 26, "seasons": ["B"]},
    # Other
    "Sunflower":     {"min_rain": 300,  "max_rain": 900,  "min_tmax": 20, "max_tmax": 32, "seasons": ["A","B"]},
    "Fodder Crops":  {"min_rain": 400,  "max_rain": 1500, "min_tmax": 18, "max_tmax": 32, "seasons": ["A","B"]},
}

CROP_CATEGORIES = {
    "Cereals & Grains": ["Maize","Rice","Wheat","Barley","Sorghum"],
    "Legumes":          ["Beans","Soybeans"],
    "Roots & Tubers":   ["Cassava","Sweet Potatoes","Irish Potatoes"],
    "Fruits":           ["Passion Fruits","Pineapple","Coffee"],
    "Vegetables":       ["Tomatoes","Leafy Greens","Carrots"],
    "Other":            ["Sunflower","Fodder Crops"],
}

# ── Dashboard visibility ───────────────────────────────────────────────────
# Set to True when Season C data becomes available
SHOW_SEASON_C = False

# ── Daily data recorder ───────────────────────────────────────────────────
# Path to the SQLite database that stores daily recorded weather
import os
DATA_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "daily_records.db"
)
