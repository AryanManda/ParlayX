"""
Feature engineering pipeline.
Transforms raw game/team/player data into ML-ready feature vectors
for each sport. All features are normalized to [-1, 1] or [0, 1].
"""
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from src.data.processor import (
    elo_win_prob, pythagorean_expectation, compute_form_score,
    compute_rest_days, PYTHAGOREAN_EXP
)


@dataclass
class GameFeatures:
    """Feature vector for a single game matchup."""
    # Identity (not used in ML, for reference)
    game_id: str = ""
    sport: str = ""
    home_team: str = ""
    away_team: str = ""

    # ELO-based
    elo_diff: float = 0.0           # home_elo - away_elo (normalized)
    elo_win_prob: float = 0.5       # ELO predicted win prob for home

    # Season performance
    home_win_pct: float = 0.5
    away_win_pct: float = 0.5
    win_pct_diff: float = 0.0       # home - away

    # Home/away splits
    home_home_win_pct: float = 0.5  # Home team's record at home
    away_away_win_pct: float = 0.5  # Away team's record on road

    # Pythagorean expectation
    home_pyth: float = 0.5
    away_pyth: float = 0.5
    pyth_diff: float = 0.0

    # Scoring
    home_ppg: float = 0.0           # Points per game (offense)
    away_ppg: float = 0.0
    home_papg: float = 0.0          # Points allowed per game (defense)
    away_papg: float = 0.0
    home_net_rating: float = 0.0    # ppg - papg
    away_net_rating: float = 0.0
    net_rating_diff: float = 0.0    # home - away

    # Recent form (last 5/10)
    home_form5: float = 0.5
    away_form5: float = 0.5
    home_form10: float = 0.5
    away_form10: float = 0.5
    form_diff5: float = 0.0

    # Rest advantage
    home_rest: float = 3.0          # Days since last game
    away_rest: float = 3.0
    rest_diff: float = 0.0          # home - away (positive = home more rested)

    # Season context
    games_played_home: float = 0.0
    games_played_away: float = 0.0
    season_pct: float = 0.5         # 0=early season, 1=late season

    # Head-to-head
    h2h_home_win_pct: float = 0.5
    h2h_games: float = 0.0

    # Sport-specific
    extra: dict = field(default_factory=dict)

    def to_array(self) -> np.ndarray:
        """Convert to numpy array for ML models."""
        base = [
            self.elo_diff / 400,           # normalize ELO diff
            self.elo_win_prob,
            self.win_pct_diff,
            self.home_home_win_pct,
            self.away_away_win_pct,
            self.pyth_diff,
            self.net_rating_diff / 20,     # normalize net rating
            self.form_diff5,
            self.home_form10,
            self.away_form10,
            self.rest_diff / 7,            # normalize rest diff
            self.home_rest / 7,
            self.away_rest / 7,
            self.season_pct,
            self.h2h_home_win_pct,
            min(self.h2h_games / 10, 1.0),
        ]
        # Append sport-specific features
        extra_vals = list(self.extra.values()) if self.extra else []
        return np.array(base + extra_vals, dtype=float)


@dataclass
class PropFeatures:
    """Feature vector for a player prop bet."""
    player_name: str = ""
    sport: str = ""
    prop_type: str = ""
    line: float = 0.0

    # Player form
    last5_avg: float = 0.0
    last10_avg: float = 0.0
    season_avg: float = 0.0
    line_vs_avg: float = 0.0        # season_avg - line (positive = favorable over)

    # Consistency
    last5_std: float = 0.0
    hit_rate_over: float = 0.5      # Historical rate of clearing this line

    # Context
    is_home: float = 0.5
    rest_days: float = 3.0
    is_back_to_back: float = 0.0
    minutes_trend: float = 0.0      # Positive = trending up in minutes

    # Opponent defense
    opp_rank_vs_prop: float = 0.5   # 0=best defense, 1=worst (easier for over)
    opp_ppg_allowed: float = 0.0

    # Player health
    injury_status: float = 0.0      # 0=healthy, 0.5=questionable, 1=doubtful

    def to_array(self) -> np.ndarray:
        return np.array([
            self.last5_avg / max(self.season_avg, 1),
            self.last10_avg / max(self.season_avg, 1),
            self.line_vs_avg / max(self.season_avg, 1),
            self.last5_std / max(self.season_avg, 1),
            self.hit_rate_over,
            self.is_home,
            self.rest_days / 7,
            self.is_back_to_back,
            self.minutes_trend,
            self.opp_rank_vs_prop,
            self.injury_status,
        ], dtype=float)


# ─── Sport-specific feature builders ─────────────────────────────────────────

def build_nba_features(
    game: dict, home_stats: dict, away_stats: dict,
    h2h_records: list[dict] = None
) -> GameFeatures:
    """Build NBA game features."""
    f = GameFeatures(
        game_id=game.get("id", ""),
        sport="NBA",
        home_team=game.get("home_team", ""),
        away_team=game.get("away_team", ""),
    )

    # ELO
    f.elo_win_prob = elo_win_prob(
        home_stats.get("elo_rating", 1500),
        away_stats.get("elo_rating", 1500),
        "NBA"
    )
    f.elo_diff = home_stats.get("elo_rating", 1500) - away_stats.get("elo_rating", 1500)

    # Win pct
    h_gp = max(home_stats.get("games_played", 1), 1)
    a_gp = max(away_stats.get("games_played", 1), 1)
    f.home_win_pct = home_stats.get("wins", 0) / h_gp
    f.away_win_pct = away_stats.get("wins", 0) / a_gp
    f.win_pct_diff = f.home_win_pct - f.away_win_pct

    f.home_home_win_pct = (home_stats.get("home_wins", 0) /
                           max(home_stats.get("home_wins", 0) + home_stats.get("home_losses", 0), 1))
    f.away_away_win_pct = (away_stats.get("away_wins", 0) /
                           max(away_stats.get("away_wins", 0) + away_stats.get("away_losses", 0), 1))

    # Scoring
    f.home_ppg = home_stats.get("points_per_game", 110)
    f.away_ppg = away_stats.get("points_per_game", 110)
    f.home_papg = home_stats.get("points_allowed_per_game", 110)
    f.away_papg = away_stats.get("points_allowed_per_game", 110)
    f.home_net_rating = f.home_ppg - f.home_papg
    f.away_net_rating = f.away_ppg - f.away_papg
    f.net_rating_diff = f.home_net_rating - f.away_net_rating

    # Pythagorean
    f.home_pyth = pythagorean_expectation(f.home_ppg, f.home_papg, PYTHAGOREAN_EXP["NBA"])
    f.away_pyth = pythagorean_expectation(f.away_ppg, f.away_papg, PYTHAGOREAN_EXP["NBA"])
    f.pyth_diff = f.home_pyth - f.away_pyth

    # Form
    f.home_form5 = home_stats.get("last5_wins", 2) / 5
    f.away_form5 = away_stats.get("last5_wins", 2) / 5
    f.home_form10 = home_stats.get("last10_wins", 5) / 10
    f.away_form10 = away_stats.get("last10_wins", 5) / 10
    f.form_diff5 = f.home_form5 - f.away_form5

    # Rest
    f.home_rest = home_stats.get("rest_days", 3.0)
    f.away_rest = away_stats.get("rest_days", 3.0)
    f.rest_diff = f.home_rest - f.away_rest

    f.games_played_home = h_gp
    f.games_played_away = a_gp
    f.season_pct = min(max(h_gp / 82, 0), 1)

    # H2H
    if h2h_records:
        home_h2h_wins = sum(1 for g in h2h_records if g.get("winner") == f.home_team)
        f.h2h_home_win_pct = home_h2h_wins / len(h2h_records)
        f.h2h_games = len(h2h_records)

    # NBA-specific: pace, three-point rate
    f.extra = {
        "home_fg_pct": home_stats.get("field_goal_pct", 0.46),
        "away_fg_pct": away_stats.get("field_goal_pct", 0.46),
        "home_3pt_pct": home_stats.get("three_point_pct", 0.36),
        "away_3pt_pct": away_stats.get("three_point_pct", 0.36),
    }
    return f


def build_nfl_features(
    game: dict, home_stats: dict, away_stats: dict,
    weather: dict = None, h2h_records: list[dict] = None
) -> GameFeatures:
    """Build NFL game features."""
    f = GameFeatures(
        game_id=game.get("id", ""),
        sport="NFL",
        home_team=game.get("home_team", ""),
        away_team=game.get("away_team", ""),
    )

    f.elo_win_prob = elo_win_prob(
        home_stats.get("elo_rating", 1500),
        away_stats.get("elo_rating", 1500),
        "NFL"
    )
    f.elo_diff = home_stats.get("elo_rating", 1500) - away_stats.get("elo_rating", 1500)

    h_gp = max(home_stats.get("games_played", 1), 1)
    a_gp = max(away_stats.get("games_played", 1), 1)
    f.home_win_pct = home_stats.get("wins", 0) / h_gp
    f.away_win_pct = away_stats.get("wins", 0) / a_gp
    f.win_pct_diff = f.home_win_pct - f.away_win_pct

    f.home_ppg = home_stats.get("points_per_game", 23)
    f.away_ppg = away_stats.get("points_per_game", 23)
    f.home_papg = home_stats.get("points_allowed_per_game", 23)
    f.away_papg = away_stats.get("points_allowed_per_game", 23)
    f.home_net_rating = f.home_ppg - f.home_papg
    f.away_net_rating = f.away_ppg - f.away_papg
    f.net_rating_diff = f.home_net_rating - f.away_net_rating

    f.home_pyth = pythagorean_expectation(f.home_ppg, f.home_papg, PYTHAGOREAN_EXP["NFL"])
    f.away_pyth = pythagorean_expectation(f.away_ppg, f.away_papg, PYTHAGOREAN_EXP["NFL"])
    f.pyth_diff = f.home_pyth - f.away_pyth

    f.home_form5 = home_stats.get("last5_wins", 2) / 5
    f.away_form5 = away_stats.get("last5_wins", 2) / 5
    f.form_diff5 = f.home_form5 - f.away_form5
    f.home_form10 = home_stats.get("last10_wins", 5) / 10
    f.away_form10 = away_stats.get("last10_wins", 5) / 10

    f.home_rest = home_stats.get("rest_days", 7.0)
    f.away_rest = away_stats.get("rest_days", 7.0)
    f.rest_diff = f.home_rest - f.away_rest

    f.season_pct = min(max(h_gp / 18, 0), 1)

    if h2h_records:
        home_h2h_wins = sum(1 for g in h2h_records if g.get("winner") == f.home_team)
        f.h2h_home_win_pct = home_h2h_wins / len(h2h_records)
        f.h2h_games = len(h2h_records)

    # NFL-specific
    weather_adj = 0.0
    if weather:
        from src.data.processor import weather_total_adjustment
        weather_adj = weather_total_adjustment(
            weather.get("temp_f", 65),
            weather.get("wind_mph", 0),
            weather.get("precip", False)
        )

    f.extra = {
        "home_yards_per_game": home_stats.get("yards_per_game", 350) / 500,
        "away_yards_per_game": away_stats.get("yards_per_game", 350) / 500,
        "home_yards_allowed": home_stats.get("yards_allowed_per_game", 350) / 500,
        "away_yards_allowed": away_stats.get("yards_allowed_per_game", 350) / 500,
        "weather_adj": weather_adj / 10,
    }
    return f


def build_mlb_features(
    game: dict, home_stats: dict, away_stats: dict,
    home_sp: dict = None, away_sp: dict = None
) -> GameFeatures:
    """Build MLB game features. Starting pitcher is critical."""
    f = GameFeatures(
        game_id=game.get("id", ""),
        sport="MLB",
        home_team=game.get("home_team", ""),
        away_team=game.get("away_team", ""),
    )

    f.elo_win_prob = elo_win_prob(
        home_stats.get("elo_rating", 1500),
        away_stats.get("elo_rating", 1500),
        "MLB"
    )
    f.elo_diff = home_stats.get("elo_rating", 1500) - away_stats.get("elo_rating", 1500)

    h_gp = max(home_stats.get("games_played", 1), 1)
    a_gp = max(away_stats.get("games_played", 1), 1)
    f.home_win_pct = home_stats.get("wins", 0) / h_gp
    f.away_win_pct = away_stats.get("wins", 0) / a_gp
    f.win_pct_diff = f.home_win_pct - f.away_win_pct

    f.home_ppg = home_stats.get("runs_per_game", 4.5)
    f.away_ppg = away_stats.get("runs_per_game", 4.5)
    f.home_papg = home_stats.get("runs_allowed_per_game", 4.5)
    f.away_papg = away_stats.get("runs_allowed_per_game", 4.5)
    f.net_rating_diff = (f.home_ppg - f.home_papg) - (f.away_ppg - f.away_papg)

    f.home_pyth = pythagorean_expectation(f.home_ppg, f.home_papg, PYTHAGOREAN_EXP["MLB"])
    f.away_pyth = pythagorean_expectation(f.away_ppg, f.away_papg, PYTHAGOREAN_EXP["MLB"])
    f.pyth_diff = f.home_pyth - f.away_pyth

    f.home_form5 = home_stats.get("last5_wins", 2) / 5
    f.away_form5 = away_stats.get("last5_wins", 2) / 5
    f.form_diff5 = f.home_form5 - f.away_form5
    f.season_pct = min(max(h_gp / 162, 0), 1)

    # Starting pitcher ERA adjustment (very important in MLB)
    home_era = (home_sp or {}).get("era", 4.20)
    away_era = (away_sp or {}).get("era", 4.20)
    home_whip = (home_sp or {}).get("whip", 1.25)
    away_whip = (away_sp or {}).get("whip", 1.25)

    f.extra = {
        "home_sp_era": (5.0 - home_era) / 5.0,   # inverted: lower ERA = better
        "away_sp_era": (5.0 - away_era) / 5.0,
        "home_sp_whip": (2.0 - home_whip) / 2.0,
        "away_sp_whip": (2.0 - away_whip) / 2.0,
        "sp_era_diff": (away_era - home_era) / 5.0,  # positive = home pitcher better
        "home_batting_avg": home_stats.get("batting_avg", 0.250) / 0.300,
        "away_batting_avg": away_stats.get("batting_avg", 0.250) / 0.300,
    }
    return f


def build_nhl_features(
    game: dict, home_stats: dict, away_stats: dict,
    h2h_records: list[dict] = None
) -> GameFeatures:
    """Build NHL game features."""
    f = GameFeatures(
        game_id=game.get("id", ""),
        sport="NHL",
        home_team=game.get("home_team", ""),
        away_team=game.get("away_team", ""),
    )

    f.elo_win_prob = elo_win_prob(
        home_stats.get("elo_rating", 1500),
        away_stats.get("elo_rating", 1500),
        "NHL"
    )
    f.elo_diff = home_stats.get("elo_rating", 1500) - away_stats.get("elo_rating", 1500)

    total_gp_home = home_stats.get("wins", 0) + home_stats.get("losses", 0) + home_stats.get("ot_losses", 0)
    total_gp_away = away_stats.get("wins", 0) + away_stats.get("losses", 0) + away_stats.get("ot_losses", 0)
    h_gp = max(total_gp_home, 1)
    a_gp = max(total_gp_away, 1)

    # NHL uses points (2 for win, 1 for OT loss)
    h_pts = home_stats.get("points", 0)
    a_pts = away_stats.get("points", 0)
    f.home_win_pct = h_pts / (h_gp * 2)
    f.away_win_pct = a_pts / (a_gp * 2)
    f.win_pct_diff = f.home_win_pct - f.away_win_pct

    f.home_ppg = home_stats.get("goals_for", 3.0) / max(h_gp / 82 * 82, 1)
    f.away_ppg = away_stats.get("goals_for", 3.0) / max(a_gp / 82 * 82, 1)
    f.home_papg = home_stats.get("goals_against", 3.0) / max(h_gp / 82 * 82, 1)
    f.away_papg = away_stats.get("goals_against", 3.0) / max(a_gp / 82 * 82, 1)
    f.net_rating_diff = (f.home_ppg - f.home_papg) - (f.away_ppg - f.away_papg)

    f.home_pyth = pythagorean_expectation(f.home_ppg, f.home_papg, PYTHAGOREAN_EXP["NHL"])
    f.away_pyth = pythagorean_expectation(f.away_ppg, f.away_papg, PYTHAGOREAN_EXP["NHL"])
    f.pyth_diff = f.home_pyth - f.away_pyth

    f.home_form5 = home_stats.get("last10_wins", 5) / 10
    f.away_form5 = away_stats.get("last10_wins", 5) / 10
    f.form_diff5 = f.home_form5 - f.away_form5
    f.season_pct = min(max(h_gp / 82, 0), 1)

    f.extra = {
        "home_power_play_pct": home_stats.get("pp_pct", 0.20),
        "away_power_play_pct": away_stats.get("pp_pct", 0.20),
        "home_pk_pct": home_stats.get("pk_pct", 0.80),
        "away_pk_pct": away_stats.get("pk_pct", 0.80),
    }
    return f


def build_prop_features(
    player_stats: list[dict],
    prop_type: str,
    line: float,
    is_home: bool,
    rest_days: float,
    opp_defense_rank: float = 0.5,
) -> PropFeatures:
    """Build player prop features from recent game logs."""
    f = PropFeatures(prop_type=prop_type, line=line)

    stat_key = _prop_stat_map(prop_type)
    values = [g.get(stat_key, 0) for g in player_stats if g.get(stat_key) is not None]

    if not values:
        return f

    f.season_avg = np.mean(values)
    f.last5_avg = np.mean(values[-5:]) if len(values) >= 5 else f.season_avg
    f.last10_avg = np.mean(values[-10:]) if len(values) >= 10 else f.season_avg
    f.last5_std = float(np.std(values[-5:])) if len(values) >= 5 else float(np.std(values))

    f.line_vs_avg = f.season_avg - line
    f.hit_rate_over = sum(1 for v in values if v > line) / len(values)

    f.is_home = float(is_home)
    f.rest_days = rest_days
    f.is_back_to_back = float(rest_days <= 1.0)
    f.opp_rank_vs_prop = opp_defense_rank

    # Minutes trend (simple linear regression slope)
    if "minutes" in prop_type.lower() or len(values) >= 3:
        mins = np.arange(len(values))
        slope = np.polyfit(mins, values, 1)[0] if len(values) >= 2 else 0.0
        f.minutes_trend = float(np.clip(slope / max(f.season_avg, 1), -1, 1))

    return f


def _prop_stat_map(prop_type: str) -> str:
    """Map prop type string to stat column name."""
    mapping = {
        "points": "points",
        "rebounds": "rebounds",
        "assists": "assists",
        "threes": "three_point_pct",
        "blocks": "blocks",
        "steals": "steals",
        "pass_yds": "passing_yards",
        "rush_yds": "rushing_yards",
        "rec_yds": "receiving_yards",
        "receptions": "receptions",
        "touchdowns": "touchdowns",
        "home_runs": "home_runs",
        "rbis": "rbi",
        "strikeouts": "strikeouts",
        "hits": "batting_avg",
        "goals": "goals",
        "shots": "shots",
    }
    for key, val in mapping.items():
        if key in prop_type.lower():
            return val
    return prop_type
