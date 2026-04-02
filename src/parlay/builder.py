"""
Parlay builder — assembles, scores, and ranks parlay combinations
from a pool of +EV bet evaluations.
"""
import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional
import numpy as np

from src.analysis.ev_calculator import BetEvaluation, parlay_ev
from src.analysis.correlation import (
    analyze_correlations, correlation_penalty, suggest_uncorrelated_combo
)
from src.parlay.simulator import simulate_parlay, SimulationResult
from config import (
    MIN_PARLAY_LEGS, MAX_PARLAY_LEGS, MIN_LEG_PROBABILITY,
    MAX_CORRELATION, MAX_BANKROLL_PCT
)


@dataclass
class ParlaySlip:
    """A complete parlay recommendation."""
    legs: list[BetEvaluation]
    sim: SimulationResult

    # Scoring
    composite_score: float = 0.0
    grade: str = "F"

    # Parlay metrics
    combined_decimal: float = 1.0
    combined_american: float = 0.0
    model_win_prob: float = 0.0     # Product of model probs (naive, no correlation)
    simulated_win_prob: float = 0.0 # Monte Carlo with correlations
    ev_pct: float = 0.0
    correlation_score: float = 0.0  # Lower = better (less correlated)

    # Kelly stake
    recommended_stake_pct: float = 0.01
    recommended_stake_dollars: float = 10.0  # for $1000 bankroll

    # AI analysis (filled later)
    ai_analysis: str = ""
    ai_confidence: str = ""

    def summary_table(self) -> list[dict]:
        rows = []
        for i, leg in enumerate(self.legs, 1):
            rows.append({
                "Leg": i,
                "Bet": leg.description,
                "Odds": f"{int(leg.best_odds):+d}",
                "Model%": f"{leg.model_prob:.1%}",
                "EV%": f"{leg.ev_pct:+.1f}%",
                "Grade": leg.grade,
                "Confidence": leg.model_confidence.upper(),
            })
        return rows

    def brief(self) -> str:
        legs_str = " + ".join(f"{l.description} ({int(l.best_odds):+d})" for l in self.legs)
        return (
            f"[{self.grade}] {legs_str} | "
            f"Payout: {self.combined_american:+.0f} | "
            f"Win%: {self.simulated_win_prob:.1%} | "
            f"EV: {self.ev_pct:+.1f}% | "
            f"Stake: {self.recommended_stake_pct:.1%}"
        )


def _grade_parlay(
    ev_pct: float,
    sim_win_prob: float,
    n_legs: int,
    correlation_score: float,
    avg_confidence: float,
) -> tuple[float, str]:
    """
    Score a parlay on a 0-100 scale, then letter-grade it.
    Rewards: positive EV, reasonable win probability, low correlation.
    Penalizes: low confidence, extreme long shots.
    """
    score = 0.0

    # EV component (0-35 points)
    if ev_pct > 0:
        score += min(ev_pct * 3.5, 35)

    # Win probability component (0-25 points)
    # Sweet spot: 15-35% win prob for 2-4 leg parlays
    optimal_win = 0.25
    win_score = 25 * (1 - abs(sim_win_prob - optimal_win) / optimal_win)
    score += max(win_score, 0)

    # Correlation component (0-20 points) — lower correlation = more points
    score += (1 - correlation_score) * 20

    # Confidence component (0-20 points)
    score += avg_confidence * 20

    # Penalize too many legs (diminishing returns, higher variance)
    if n_legs > 4:
        score -= (n_legs - 4) * 8

    score = max(0, min(score, 100))

    if score >= 80:
        grade = "A+"
    elif score >= 70:
        grade = "A"
    elif score >= 60:
        grade = "B+"
    elif score >= 50:
        grade = "B"
    elif score >= 40:
        grade = "C"
    else:
        grade = "D"

    return score, grade


def build_parlay(
    legs: list[BetEvaluation],
    bankroll: float = 1000.0,
    n_sims: int = 100_000,
) -> ParlaySlip:
    """
    Build and score a parlay from a list of bet legs.
    """
    # Validate
    legs = [l for l in legs if l.model_prob >= MIN_LEG_PROBABILITY]
    if not legs:
        raise ValueError("No legs meet minimum probability threshold")

    # Simulate
    sim = simulate_parlay(legs, n_sims=n_sims)

    # Correlation analysis
    corr_report = analyze_correlations(legs)
    corr_penalty = correlation_penalty(legs)

    # Combined metrics
    model_win_prob = math.prod(l.model_prob for l in legs)
    combined_decimal = math.prod(l.decimal_odds for l in legs)
    combined_american = (
        (combined_decimal - 1) * 100 if combined_decimal >= 2
        else -100 / (combined_decimal - 1)
    )

    # Kelly stake (use simulated win prob, more accurate)
    kelly_num = sim.win_probability * (combined_decimal - 1) - (1 - sim.win_probability)
    kelly_denom = combined_decimal - 1
    kelly = max(kelly_num / kelly_denom, 0) if kelly_denom > 0 else 0
    from config import KELLY_FRACTION
    recommended_stake = min(kelly * KELLY_FRACTION, MAX_BANKROLL_PCT)

    # Confidence tiers: map low=0.3, medium=0.6, high=0.9
    conf_map = {"low": 0.3, "medium": 0.6, "high": 0.9}
    avg_conf = np.mean([conf_map.get(l.model_confidence, 0.5) for l in legs])

    composite, grade = _grade_parlay(
        ev_pct=sim.ev_pct,
        sim_win_prob=sim.win_probability,
        n_legs=len(legs),
        correlation_score=corr_report.avg_correlation,
        avg_confidence=float(avg_conf),
    )

    return ParlaySlip(
        legs=legs,
        sim=sim,
        composite_score=composite,
        grade=grade,
        combined_decimal=combined_decimal,
        combined_american=combined_american,
        model_win_prob=model_win_prob,
        simulated_win_prob=sim.win_probability,
        ev_pct=sim.ev_pct,
        correlation_score=corr_report.avg_correlation,
        recommended_stake_pct=recommended_stake,
        recommended_stake_dollars=bankroll * recommended_stake,
    )


def find_best_parlays(
    ev_bets: list[BetEvaluation],
    n_legs_range: range = range(2, 5),
    top_n: int = 5,
    bankroll: float = 1000.0,
    max_combos: int = 500,
) -> list[ParlaySlip]:
    """
    Exhaustively search (up to max_combos) for the best parlay combinations
    from a pool of +EV bets.
    Returns top N parlays sorted by composite score.
    """
    all_slips: list[ParlaySlip] = []

    # Filter to high-quality candidates only
    candidates = [
        b for b in ev_bets
        if b.model_prob >= MIN_LEG_PROBABILITY and b.is_positive_ev
    ]
    candidates.sort(key=lambda b: b.ev_pct, reverse=True)
    # Cap candidates to keep search tractable
    candidates = candidates[:20]

    combos_tried = 0
    for n_legs in n_legs_range:
        if n_legs > len(candidates):
            continue
        for combo in combinations(candidates, n_legs):
            if combos_tried >= max_combos:
                break
            combos_tried += 1
            try:
                slip = build_parlay(list(combo), bankroll=bankroll, n_sims=50_000)
                if slip.ev_pct > -5:  # Only keep non-terrible parlays
                    all_slips.append(slip)
            except Exception:
                continue

    all_slips.sort(key=lambda s: s.composite_score, reverse=True)
    return all_slips[:top_n]


def quick_parlay(
    ev_bets: list[BetEvaluation],
    n_legs: int = 3,
    bankroll: float = 1000.0,
) -> Optional[ParlaySlip]:
    """
    Quickly build one optimal parlay using the greedy correlation-aware selector.
    Faster than exhaustive search for large bet pools.
    """
    legs = suggest_uncorrelated_combo(ev_bets, n_legs=n_legs, min_ev=0.0)
    if not legs:
        return None
    try:
        return build_parlay(legs, bankroll=bankroll)
    except Exception:
        return None
