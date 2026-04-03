"""
ParlayX Configuration
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", encoding='utf-8-sig', override=True)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# API Keys (set in .env file)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")  # https://the-odds-api.com (free tier)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Database
DATABASE_URL = f"sqlite:///{DATA_DIR}/parlayx.db"

# The Odds API
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_REGIONS = "us"
ODDS_MARKETS = "h2h,spreads,totals"
ODDS_FORMAT = "american"

# ESPN public API (no key needed)
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_SPORTS = {
    "NBA": "basketball/nba",
    "NFL": "football/nfl",
    "MLB": "baseball/mlb",
    "NHL": "hockey/nhl",
    "NCAAB": "basketball/mens-college-basketball",
    "NCAAF": "football/college-football",
    "MLS": "soccer/usa.1",
}

# Ball Don't Lie API (free NBA stats)
BALLDONTLIE_BASE = "https://www.balldontlie.io/api/v1"

# NFL Data
NFL_DATA_SEASONS = [2022, 2023, 2024]

# ML Model settings
MODEL_CONFIDENCE_THRESHOLD = 0.55   # Min confidence to consider a bet
EV_THRESHOLD = 0.01                  # Min +EV (1%) to flag as +EV bet
KELLY_FRACTION = 0.25                # Fractional Kelly (conservative)
MAX_BANKROLL_PCT = 0.02              # Max 2% per parlay

# Parlay settings
MIN_PARLAY_LEGS = 2
MAX_PARLAY_LEGS = 5
MIN_LEG_PROBABILITY = 0.50           # Min win prob per leg
MAX_CORRELATION = 0.3                # Max allowed correlation between legs
SIMULATIONS = 100_000                # Monte Carlo simulations

# Claude AI model
CLAUDE_MODEL = "claude-opus-4-6"
