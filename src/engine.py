"""
ParlayX Engine — the central orchestrator.
Pulls live data, runs models, identifies +EV bets, builds optimal parlays,
and cross-references with Claude AI.
"""
from datetime import datetime
from typing import Optional

from src.data.fetcher import (
    fetch_espn_scoreboard, fetch_upcoming_games, fetch_game_odds,
    fetch_player_props, fetch_nhl_standings, get_mock_odds,
)
from src.data.processor import EloTracker
from src.features.engineering import (
    build_nba_features, build_nfl_features, build_mlb_features, build_nhl_features
)
from src.models.probability import get_model, train_all_models
from src.models.props import evaluate_prop
from src.analysis.ev_calculator import (
    evaluate_bet, screen_bets, BetEvaluation
)
from src.analysis.correlation import suggest_uncorrelated_combo
from src.parlay.builder import build_parlay, find_best_parlays, quick_parlay, ParlaySlip
from src.parlay.simulator import simulate_parlay
from src.ai.advisor import get_advisor
from config import MIN_PARLAY_LEGS, MAX_PARLAY_LEGS


# ELO trackers per sport (in-memory, updated as games are processed)
_elo_trackers: dict[str, EloTracker] = {}

# Sport → Odds API key mapping
SPORT_ODDS_KEYS = {
    "NBA": "basketball_nba",
    "NFL": "americanfootball_nfl",
    "MLB": "baseball_mlb",
    "NHL": "icehockey_nhl",
    "NCAAB": "basketball_ncaab",
    "NCAAF": "americanfootball_ncaaf",
}


def get_elo(sport: str) -> EloTracker:
    if sport not in _elo_trackers:
        _elo_trackers[sport] = EloTracker(sport)
    return _elo_trackers[sport]


def _default_team_stats(sport: str, team: str, elo: float = 1500.0) -> dict:
    """Default stats when real data isn't available."""
    import hashlib
    # Seed from team name so same team always gets same stats (deterministic)
    seed = int(hashlib.md5(team.encode()).hexdigest()[:8], 16) % 1000
    rng = __import__('random').Random(seed)

    wins = rng.randint(14, 36)
    losses = 40 - wins
    # Scale offensive/defensive stats with win rate
    win_rate = wins / 40

    defaults = {
        "NBA": {
            "points_per_game": 104 + win_rate * 16,
            "points_allowed_per_game": 116 - win_rate * 16,
            "field_goal_pct": 0.43 + win_rate * 0.06,
            "three_point_pct": 0.33 + win_rate * 0.06,
        },
        "NFL": {
            "points_per_game": 18 + win_rate * 12,
            "points_allowed_per_game": 28 - win_rate * 12,
            "yards_per_game": 310 + win_rate * 80,
            "yards_allowed_per_game": 390 - win_rate * 80,
        },
        "MLB": {
            "runs_per_game": 3.5 + win_rate * 2.0,
            "runs_allowed_per_game": 5.0 - win_rate * 2.0,
            "batting_avg": 0.230 + win_rate * 0.04,
        },
        "NHL": {
            "goals_for": 2.5 + win_rate * 1.2,
            "goals_against": 3.5 - win_rate * 1.2,
            "pp_pct": 0.16 + win_rate * 0.08,
            "pk_pct": 0.76 + win_rate * 0.08,
        },
    }
    base = defaults.get(sport, {})
    return {
        "team": team, "sport": sport, "elo_rating": elo,
        "games_played": 40, "wins": wins, "losses": losses,
        "home_wins": int(wins * 0.6), "home_losses": 20 - int(wins * 0.6),
        "away_wins": wins - int(wins * 0.6), "away_losses": 20 - (wins - int(wins * 0.6)),
        "last5_wins": rng.randint(1, 5), "last10_wins": rng.randint(3, 9),
        "rest_days": rng.choice([1, 2, 2, 3, 4]),
        **base
    }


# ─── Core analysis pipeline ───────────────────────────────────────────────────

def analyze_game(
    sport: str,
    game: dict,
    bookmaker_odds: list[dict] = None,
) -> list[BetEvaluation]:
    """
    Analyze a single game and return all identified +EV bets.
    game: dict with home_team, away_team, game_id, game_date
    """
    evaluations = []
    home = game.get("home_team", "Home")
    away = game.get("away_team", "Away")
    game_id = game.get("id", f"{home}_vs_{away}")
    teams_str = f"{home} vs {away}"
    game_date = str(game.get("game_date", datetime.utcnow().date()))

    # Get ELO ratings
    elo = get_elo(sport)
    home_elo = elo.get(home)
    away_elo = elo.get(away)

    # Build stats dicts (real data would come from DB/fetcher)
    home_stats = _default_team_stats(sport, home, home_elo)
    away_stats = _default_team_stats(sport, away, away_elo)

    # Build feature vector for this matchup
    if sport == "NBA":
        features = build_nba_features(game, home_stats, away_stats)
    elif sport == "NFL":
        features = build_nfl_features(game, home_stats, away_stats)
    elif sport == "MLB":
        features = build_mlb_features(game, home_stats, away_stats)
    elif sport == "NHL":
        features = build_nhl_features(game, home_stats, away_stats)
    else:
        features = build_nba_features(game, home_stats, away_stats)

    # Win probability model
    ml_model = get_model(sport, "win_prob")
    ml_pred = ml_model.predict_game(features)

    # Away win probability (complement)
    away_prob = 1 - ml_pred.probability

    # Get odds (real or mock)
    if bookmaker_odds:
        # Find best available moneyline odds
        home_odds_list = []
        away_odds_list = []
        for bm in bookmaker_odds:
            h2h = bm.get("markets", {}).get("h2h", {})
            if home in h2h:
                home_odds_list.append(h2h[home]["price"])
            if away in h2h:
                away_odds_list.append(h2h[away]["price"])
        home_ml = max(home_odds_list, default=-110,
                      key=lambda x: (x/100 + 1 if x > 0 else 100/abs(x) + 1))
        away_ml = max(away_odds_list, default=110,
                      key=lambda x: (x/100 + 1 if x > 0 else 100/abs(x) + 1))
    else:
        mock = get_mock_odds(sport, home, away)
        home_ml = mock["home_ml"]
        away_ml = mock["away_ml"]

    # Evaluate home moneyline
    evaluations.append(evaluate_bet(
        sport=sport, game_id=game_id, bet_type="moneyline",
        outcome="home_win", description=f"{home} ML",
        model_prob=ml_pred.probability, model_confidence=ml_pred.confidence,
        american_odds=home_ml, teams=teams_str, game_date=game_date,
    ))

    # Evaluate away moneyline
    evaluations.append(evaluate_bet(
        sport=sport, game_id=game_id, bet_type="moneyline",
        outcome="away_win", description=f"{away} ML",
        model_prob=away_prob,
        model_confidence=ml_model.confidence_tier(away_prob),
        american_odds=away_ml, teams=teams_str, game_date=game_date,
    ))

    # Evaluate spreads
    spread_model = get_model(sport, "spread")
    mock = get_mock_odds(sport, home, away)
    spread = mock.get("home_spread", -3.0)
    home_cover_pred = spread_model.predict_spread(features, spread)

    evaluations.append(evaluate_bet(
        sport=sport, game_id=game_id, bet_type="spread",
        outcome="home_cover", description=f"{home} {spread:+.1f}",
        model_prob=home_cover_pred.probability,
        model_confidence=home_cover_pred.confidence,
        american_odds=mock.get("spread_odds", -110),
        teams=teams_str, game_date=game_date,
    ))

    # Evaluate totals
    total_model = get_model(sport, "totals")
    total_line = mock.get("total_line", 220.5)
    total_pred = total_model.predict_total(features, total_line)

    evaluations.append(evaluate_bet(
        sport=sport, game_id=game_id, bet_type="total",
        outcome=total_pred.outcome,
        description=f"{'Over' if total_pred.outcome == 'over' else 'Under'} {total_line}",
        model_prob=total_pred.probability,
        model_confidence=total_pred.confidence,
        american_odds=mock.get("over_odds", -110),
        teams=teams_str, game_date=game_date,
    ))

    return evaluations


def scan_today(
    sports: list[str] = None,
    ai_analysis: bool = True,
    bankroll: float = 1000.0,
    n_parlay_legs: int = 3,
    progress_cb=None,
) -> dict:
    """
    Full daily scan: fetch today's games, find +EV bets, build best parlays.
    Returns a comprehensive report dict.
    """
    sports = sports or ["NBA", "NFL", "MLB", "NHL"]
    all_evals: list[BetEvaluation] = []
    games_scanned = 0

    print(f"\n{'='*60}")
    print(f"  ParlayX Daily Scan — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    def _progress(msg: str):
        print(msg)
        if progress_cb:
            progress_cb(msg)

    for sport in sports:
        _progress(f"[{sport}] Fetching upcoming games...")
        games = fetch_upcoming_games(sport, days_ahead=1)
        if not games:
            today = datetime.utcnow().strftime("%Y%m%d")
            games = fetch_espn_scoreboard(sport, today)

        _progress(f"[{sport}] Found {len(games)} game(s) — analyzing...")
        games_scanned += len(games)

        for game in games[:10]:
            evals = analyze_game(sport, game)
            all_evals.extend(evals)

    # Screen for +EV bets
    ev_bets = screen_bets(all_evals)
    _progress(f"Screening complete — {len(ev_bets)} +EV bets from {len(all_evals)} evaluated")

    # Build optimal parlays
    best_parlays: list[ParlaySlip] = []
    if len(ev_bets) >= MIN_PARLAY_LEGS:
        _progress(f"Building optimal {n_parlay_legs}-leg parlays...")
        best_parlays = find_best_parlays(
            ev_bets,
            n_legs_range=range(MIN_PARLAY_LEGS, min(n_parlay_legs + 1, MAX_PARLAY_LEGS + 1)),
            top_n=3,
            bankroll=bankroll,
        )
        _progress(f"Built {len(best_parlays)} parlay option(s)")

    # AI analysis on best parlay
    ai_result = {}
    if ai_analysis and best_parlays:
        _progress("Running Claude AI analysis on top parlay...")
        advisor = get_advisor()
        ai_result = advisor.analyze_parlay(best_parlays[0])
        if best_parlays[0]:
            best_parlays[0].ai_analysis = ai_result.get("analysis", "")
            best_parlays[0].ai_confidence = ai_result.get("ai_confidence", "")

    return {
        "date": datetime.now().isoformat(),
        "sports_scanned": sports,
        "games_scanned": games_scanned,
        "total_bets_evaluated": len(all_evals),
        "ev_bets_found": len(ev_bets),
        "top_ev_bets": ev_bets[:10],
        "best_parlays": best_parlays,
        "ai_analysis": ai_result,
    }


def build_custom_parlay(
    legs_input: list[dict],
    bankroll: float = 1000.0,
    ai_analysis: bool = True,
) -> Optional[ParlaySlip]:
    """
    Build a parlay from user-specified legs.
    legs_input: [{"sport": "NBA", "description": "Lakers ML", "odds": -150, "model_prob": 0.60}]
    """
    legs = []
    for l in legs_input:
        ev = evaluate_bet(
            sport=l.get("sport", "NBA"),
            game_id=l.get("game_id", "custom"),
            bet_type=l.get("bet_type", "moneyline"),
            outcome=l.get("outcome", "home_win"),
            description=l.get("description", "Custom Bet"),
            model_prob=l.get("model_prob", 0.55),
            model_confidence="medium",
            american_odds=l.get("odds", -110),
            teams=l.get("teams", ""),
        )
        legs.append(ev)

    if not legs:
        return None

    slip = build_parlay(legs, bankroll=bankroll)

    if ai_analysis:
        advisor = get_advisor()
        ai_result = advisor.analyze_parlay(slip)
        slip.ai_analysis = ai_result.get("analysis", "")
        slip.ai_confidence = ai_result.get("ai_confidence", "")

    return slip
