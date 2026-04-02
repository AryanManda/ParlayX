"""
Correlation engine — detects and penalizes correlated parlay legs.
High-correlation parlays offer no diversification benefit and can
actually reduce expected value vs single bets.
"""
import numpy as np
from itertools import combinations
from dataclasses import dataclass
from src.analysis.ev_calculator import BetEvaluation
from config import MAX_CORRELATION


@dataclass
class CorrelationReport:
    leg_pairs: list[tuple[str, str, float]]  # (desc_a, desc_b, correlation)
    max_correlation: float
    avg_correlation: float
    is_acceptable: bool
    warnings: list[str]


# ─── Known correlation rules ──────────────────────────────────────────────────
# These are domain-knowledge rules for common parlay correlations.
# Positive = correlated (bad for parlays), Negative = anti-correlated.

SAME_GAME_CORRELATIONS = {
    # (bet_type_a, bet_type_b) → estimated correlation coefficient
    ("moneyline_home", "spread_home"): 0.85,      # Very correlated
    ("moneyline_home", "total_over"): 0.10,       # Slight positive
    ("moneyline_home", "total_under"): -0.10,     # Slight negative
    ("spread_home", "total_over"): 0.12,
    ("moneyline_away", "total_over"): 0.10,
    # NBA props
    ("prop_points", "prop_assists"): 0.45,        # Same player
    ("prop_points", "prop_rebounds"): 0.20,
    ("prop_assists", "prop_rebounds"): 0.15,
    # NFL props
    ("prop_pass_yds", "prop_rec_yds"): 0.55,      # QB + receiver
    ("prop_pass_yds", "prop_rush_yds"): -0.25,    # Pass/run game tradeoff
    ("prop_pass_tds", "prop_rec_yds"): 0.60,
}


def bet_type_key(bet: BetEvaluation) -> str:
    """Canonical key for correlation lookup."""
    bt = bet.bet_type.lower()
    out = bet.outcome.lower()
    if "moneyline" in bt or "ml" in bt:
        return f"moneyline_{'home' if 'home' in out else 'away'}"
    if "spread" in bt:
        return f"spread_{'home' if 'cover' in out else 'away'}"
    if "total" in bt:
        return f"total_{'over' if 'over' in out else 'under'}"
    if "prop" in bt or "player" in bt:
        prop_stat = bet.extra.get("prop_type", "unknown").replace(" ", "_")
        return f"prop_{prop_stat}"
    return bt


def same_team(bet_a: BetEvaluation, bet_b: BetEvaluation) -> bool:
    """Check if two bets involve the same team."""
    teams_a = {t.strip().lower() for t in bet_a.teams.split("vs")}
    teams_b = {t.strip().lower() for t in bet_b.teams.split("vs")}
    return bool(teams_a & teams_b)


def same_game(bet_a: BetEvaluation, bet_b: BetEvaluation) -> bool:
    """Check if two bets are from the same game."""
    return bet_a.game_id == bet_b.game_id and bet_a.game_id != ""


def same_player(bet_a: BetEvaluation, bet_b: BetEvaluation) -> bool:
    """Check if two prop bets are for the same player."""
    pa = bet_a.extra.get("player", "").lower()
    pb = bet_b.extra.get("player", "").lower()
    return pa != "" and pa == pb


def estimate_correlation(bet_a: BetEvaluation, bet_b: BetEvaluation) -> float:
    """
    Estimate correlation between two bet legs.
    Returns value in [-1, 1].
    0 = independent, 1 = perfectly correlated, -1 = perfectly anti-correlated.
    """
    # Same exact bet: perfect correlation
    if bet_a.game_id == bet_b.game_id and bet_a.outcome == bet_b.outcome:
        return 1.0

    # Different sports: assume independent
    if bet_a.sport != bet_b.sport:
        return 0.0

    # Same player props: use known correlations
    if same_player(bet_a, bet_b):
        key_a = bet_type_key(bet_a)
        key_b = bet_type_key(bet_b)
        corr = SAME_GAME_CORRELATIONS.get(
            (key_a, key_b),
            SAME_GAME_CORRELATIONS.get((key_b, key_a), 0.35)
        )
        return corr

    # Same game different bets
    if same_game(bet_a, bet_b):
        key_a = bet_type_key(bet_a)
        key_b = bet_type_key(bet_b)
        corr = SAME_GAME_CORRELATIONS.get(
            (key_a, key_b),
            SAME_GAME_CORRELATIONS.get((key_b, key_a), 0.15)
        )
        return corr

    # Same team, different games (e.g., team ML + player prop same team)
    if same_team(bet_a, bet_b):
        # Player prop + team result are mildly correlated
        if "prop" in bet_a.bet_type.lower() or "prop" in bet_b.bet_type.lower():
            return 0.20
        return 0.10

    # Different games, different sports/teams: minimal correlation
    return 0.02


def analyze_correlations(legs: list[BetEvaluation]) -> CorrelationReport:
    """
    Analyze all pairwise correlations in a parlay.
    Returns a report with warnings for high-correlation pairs.
    """
    if len(legs) < 2:
        return CorrelationReport([], 0.0, 0.0, True, [])

    pairs = []
    warnings = []

    for a, b in combinations(legs, 2):
        corr = estimate_correlation(a, b)
        pairs.append((a.description, b.description, corr))

        if corr > 0.7:
            warnings.append(
                f"HIGH CORRELATION ({corr:.2f}): '{a.description}' and '{b.description}' "
                f"are strongly linked — reduces diversification significantly."
            )
        elif corr > MAX_CORRELATION:
            warnings.append(
                f"Moderate correlation ({corr:.2f}): '{a.description}' and '{b.description}'."
            )

    correlations = [p[2] for p in pairs]
    max_corr = max(correlations) if correlations else 0.0
    avg_corr = sum(correlations) / len(correlations) if correlations else 0.0
    is_ok = max_corr <= MAX_CORRELATION

    return CorrelationReport(
        leg_pairs=pairs,
        max_correlation=max_corr,
        avg_correlation=avg_corr,
        is_acceptable=is_ok,
        warnings=warnings,
    )


def correlation_penalty(legs: list[BetEvaluation]) -> float:
    """
    Compute a correlation penalty factor [0, 1] to reduce parlay score.
    1.0 = no penalty (fully independent), 0.0 = maximum penalty.
    """
    if len(legs) < 2:
        return 1.0
    report = analyze_correlations(legs)
    # Penalty scales with average correlation
    penalty = 1.0 - (report.avg_correlation * 0.8)
    return max(penalty, 0.1)


def deduplicate_legs(legs: list[BetEvaluation]) -> list[BetEvaluation]:
    """Remove duplicate or near-duplicate legs (same game + same outcome)."""
    seen = set()
    unique = []
    for leg in legs:
        key = f"{leg.game_id}_{leg.outcome}"
        if key not in seen:
            seen.add(key)
            unique.append(leg)
    return unique


def suggest_uncorrelated_combo(
    available_bets: list[BetEvaluation],
    n_legs: int = 3,
    min_ev: float = 0.0,
) -> list[BetEvaluation]:
    """
    Greedily select the most +EV, least-correlated N-leg parlay combo
    from available bet evaluations.
    """
    candidates = [b for b in available_bets if b.ev_pct >= min_ev]
    candidates.sort(key=lambda b: b.ev_pct, reverse=True)

    if len(candidates) <= n_legs:
        return candidates

    selected = [candidates[0]]  # Start with best EV bet

    while len(selected) < n_legs and len(selected) < len(candidates):
        best_next = None
        best_score = -np.inf

        for candidate in candidates:
            if candidate in selected:
                continue
            # Score = EV - correlation penalty with already selected legs
            avg_corr = np.mean([
                estimate_correlation(candidate, s) for s in selected
            ])
            # Penalize high correlation, reward high EV
            score = candidate.ev_pct - (avg_corr * 5)
            if score > best_score:
                best_score = score
                best_next = candidate

        if best_next:
            selected.append(best_next)
        else:
            break

    return selected
