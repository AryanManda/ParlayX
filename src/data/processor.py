"""
Data processor — cleans raw API data, computes ELO ratings,
rolling averages, rest days, and other derived features.
"""
import math
from datetime import datetime
from typing import Optional
import numpy as np


# ─── Odds conversion ──────────────────────────────────────────────────────────

def american_to_decimal(american: float) -> float:
    if american > 0:
        return (american / 100) + 1
    return (100 / abs(american)) + 1


def decimal_to_american(decimal: float) -> float:
    if decimal >= 2.0:
        return (decimal - 1) * 100
    return -100 / (decimal - 1)


def american_to_prob(american: float) -> float:
    """Convert American odds to implied probability (no vig removal)."""
    if american > 0:
        return 100 / (american + 100)
    return abs(american) / (abs(american) + 100)


def remove_vig(prob_a: float, prob_b: float) -> tuple[float, float]:
    """Remove the bookmaker's vig from a two-outcome market."""
    total = prob_a + prob_b
    return prob_a / total, prob_b / total


def best_line(odds_list: list[float]) -> float:
    """Return the best (highest) odds from a list of bookmaker prices."""
    if not odds_list:
        return -110
    return max(odds_list, key=lambda x: american_to_decimal(x))


# ─── ELO Rating System ────────────────────────────────────────────────────────

K_FACTORS = {
    "NBA": 20,
    "NFL": 25,
    "MLB": 15,
    "NHL": 20,
    "NCAAB": 22,
    "NCAAF": 28,
}
HOME_ADVANTAGE = {
    "NBA": 100,   # ~3 points
    "NFL": 65,    # ~2 points
    "MLB": 24,    # slight
    "NHL": 50,
    "NCAAB": 120,
    "NCAAF": 70,
}


def elo_win_prob(home_elo: float, away_elo: float, sport: str = "NBA") -> float:
    """Calculate win probability from ELO ratings with home advantage."""
    ha = HOME_ADVANTAGE.get(sport, 65)
    diff = (home_elo + ha) - away_elo
    return 1 / (1 + 10 ** (-diff / 400))


def update_elo(
    home_elo: float, away_elo: float,
    home_score: float, away_score: float,
    sport: str = "NBA"
) -> tuple[float, float]:
    """Update ELO ratings after a game."""
    k = K_FACTORS.get(sport, 20)
    expected_home = elo_win_prob(home_elo, away_elo, sport)
    actual_home = 1.0 if home_score > away_score else 0.0

    # Margin of victory multiplier (reduces upset punishing)
    score_diff = abs(home_score - away_score)
    if sport == "NBA":
        mov_mult = math.log(max(score_diff, 1) + 1) * (2.2 / (abs(home_elo - away_elo) * 0.001 + 2.2))
    elif sport == "NFL":
        mov_mult = math.log(max(score_diff, 1) + 1) * (2.2 / (abs(home_elo - away_elo) * 0.001 + 2.2))
    else:
        mov_mult = 1.0

    delta = k * mov_mult * (actual_home - expected_home)
    return home_elo + delta, away_elo - delta


class EloTracker:
    """Tracks ELO ratings across a season for all teams in a league."""

    def __init__(self, sport: str, initial_elo: float = 1500.0, reversion: float = 0.33):
        self.sport = sport
        self.initial_elo = initial_elo
        self.reversion = reversion   # Season-to-season mean reversion factor
        self.ratings: dict[str, float] = {}

    def get(self, team: str) -> float:
        return self.ratings.get(team, self.initial_elo)

    def regress_to_mean(self):
        """Apply mean reversion at season start."""
        for team in self.ratings:
            self.ratings[team] = (
                self.ratings[team] * (1 - self.reversion) +
                self.initial_elo * self.reversion
            )

    def process_game(self, home: str, away: str, home_score: float, away_score: float):
        h_elo = self.get(home)
        a_elo = self.get(away)
        new_h, new_a = update_elo(h_elo, a_elo, home_score, away_score, self.sport)
        self.ratings[home] = new_h
        self.ratings[away] = new_a
        return new_h, new_a

    def win_prob(self, home: str, away: str) -> float:
        return elo_win_prob(self.get(home), self.get(away), self.sport)


# ─── Rolling stats ────────────────────────────────────────────────────────────

def rolling_avg(values: list[float], window: int) -> Optional[float]:
    if not values:
        return None
    tail = values[-window:]
    return sum(tail) / len(tail)


def compute_rest_days(last_game_date: Optional[datetime], game_date: datetime) -> float:
    """Days since last game (back-to-back = 1, 3+ days = 3+)."""
    if last_game_date is None:
        return 7.0  # Assume well-rested for first game
    delta = (game_date - last_game_date).total_seconds() / 86400
    return min(delta, 14.0)


# ─── Team form scorer ─────────────────────────────────────────────────────────

def compute_form_score(results: list[bool], weights: list[float] = None) -> float:
    """
    Weighted win percentage over recent games.
    More recent games are weighted higher.
    results: list of True/False (win/loss), most recent last
    """
    if not results:
        return 0.5
    n = len(results)
    if weights is None:
        weights = [1.0 + i * 0.5 for i in range(n)]  # linear recency weighting
    total_w = sum(weights[-n:])
    w_wins = sum(w * r for w, r in zip(weights[-n:], results))
    return w_wins / total_w


# ─── Pythagorean expectation ──────────────────────────────────────────────────

def pythagorean_expectation(points_for: float, points_against: float, exp: float = 13.91) -> float:
    """
    Estimate true win probability from points scored/allowed.
    Default exponent 13.91 works for NBA; use 2.37 for NFL, 1.83 for MLB.
    """
    if points_for + points_against == 0:
        return 0.5
    pf_exp = points_for ** exp
    pa_exp = points_against ** exp
    return pf_exp / (pf_exp + pa_exp)


PYTHAGOREAN_EXP = {"NBA": 13.91, "NFL": 2.37, "MLB": 1.83, "NHL": 2.15, "NCAAB": 11.5}


# ─── Injury impact ────────────────────────────────────────────────────────────

INJURY_IMPACT = {
    # NBA star player impact (rough ELO point equivalent)
    "NBA": {"starter": 25, "key_player": 15, "bench": 5},
    "NFL": {"qb": 40, "skill": 10, "lineman": 5},
    "MLB": {"sp": 20, "closer": 8, "lineup": 5},
    "NHL": {"top_line": 15, "goalie": 25, "depth": 5},
}


def adjust_elo_for_injuries(
    elo: float, injuries: list[dict], sport: str, is_home: bool
) -> float:
    """Adjust ELO rating downward for reported injuries."""
    impact = INJURY_IMPACT.get(sport, {})
    total_deduction = 0.0
    for injury in injuries:
        role = injury.get("role", "bench").lower()
        for role_key, value in impact.items():
            if role_key in role:
                total_deduction += value
                break
    return elo - total_deduction


# ─── Weather impact (outdoor sports) ─────────────────────────────────────────

def weather_total_adjustment(temp_f: float, wind_mph: float, precip: bool) -> float:
    """
    Estimate total points adjustment for weather in outdoor sports (NFL, MLB).
    Returns negative number (weather reduces scoring).
    """
    adjustment = 0.0
    # Cold temperature effect
    if temp_f < 40:
        adjustment -= (40 - temp_f) * 0.1
    # Wind effect (reduces passing and field goals)
    if wind_mph > 15:
        adjustment -= (wind_mph - 15) * 0.3
    # Precipitation
    if precip:
        adjustment -= 3.5
    return adjustment


# ─── Line movement analysis ───────────────────────────────────────────────────

def detect_sharp_money(
    opening_odds: float, current_odds: float, public_bet_pct: float = None
) -> dict:
    """
    Detect potential sharp (professional) money movement.
    Sharp money moves lines opposite to public betting percentages.
    """
    opening_prob = american_to_prob(opening_odds)
    current_prob = american_to_prob(current_odds)
    movement = current_prob - opening_prob

    sharp = False
    confidence = 0.0

    # Line moved significantly
    if abs(movement) > 0.03:
        if public_bet_pct is not None:
            # Classic reverse line movement: public on one side, line moves other way
            if public_bet_pct > 0.6 and movement < 0:
                sharp = True
                confidence = min(abs(movement) * 10, 1.0)
            elif public_bet_pct < 0.4 and movement > 0:
                sharp = True
                confidence = min(abs(movement) * 10, 1.0)
        else:
            # Without public data, just flag significant moves
            if abs(movement) > 0.05:
                sharp = True
                confidence = 0.4

    return {
        "sharp_money": sharp,
        "confidence": confidence,
        "movement": movement,
        "opening_prob": opening_prob,
        "current_prob": current_prob,
    }


# ─── Normalize team names ─────────────────────────────────────────────────────

_TEAM_ALIASES = {
    # NBA
    "los angeles lakers": "lakers", "la lakers": "lakers",
    "golden state warriors": "warriors", "gsw": "warriors",
    "boston celtics": "celtics",
    # NFL
    "new england patriots": "patriots",
    "kansas city chiefs": "chiefs",
    "san francisco 49ers": "49ers",
    # MLB
    "new york yankees": "yankees",
    "los angeles dodgers": "dodgers",
    # NHL
    "toronto maple leafs": "maple leafs",
    "colorado avalanche": "avalanche",
}


def normalize_team(name: str) -> str:
    key = name.lower().strip()
    return _TEAM_ALIASES.get(key, key)


# ─── Stat z-score normalization ───────────────────────────────────────────────

def zscore_normalize(values: list[float]) -> list[float]:
    """Normalize a list of values to z-scores."""
    arr = np.array(values, dtype=float)
    mu, sigma = arr.mean(), arr.std()
    if sigma == 0:
        return [0.0] * len(values)
    return ((arr - mu) / sigma).tolist()
