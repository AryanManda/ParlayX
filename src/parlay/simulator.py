"""
Monte Carlo simulator for parlay win probability.
Accounts for leg correlations, uncertainty in model probabilities,
and line shopping value.
"""
import math
import numpy as np
from dataclasses import dataclass
from src.analysis.ev_calculator import BetEvaluation
from src.analysis.correlation import estimate_correlation
from config import SIMULATIONS


@dataclass
class SimulationResult:
    win_probability: float       # Simulated win %
    ev_pct: float                # Expected value %
    combined_decimal_odds: float
    combined_american_odds: float
    roi_distribution: np.ndarray # Distribution of outcomes per $1 bet
    percentile_5: float          # 5th percentile outcome
    percentile_95: float         # 95th percentile outcome
    std_dev: float
    sharpe_ratio: float          # (mean_return - 0) / std_dev
    n_simulations: int


def _correlated_outcomes(
    probs: list[float],
    corr_matrix: np.ndarray,
    n_sims: int,
    rng: np.random.Generator
) -> np.ndarray:
    """
    Generate correlated Bernoulli outcomes using Gaussian copula.
    Returns (n_sims, n_legs) boolean array.
    """
    n = len(probs)
    if n == 1:
        return (rng.uniform(0, 1, (n_sims, 1)) < probs[0]).astype(int)

    # Convert probabilities to normal quantiles (Gaussian copula)
    from scipy.stats import norm
    quantiles = norm.ppf(np.clip(probs, 0.001, 0.999))

    # Cholesky decomposition of correlation matrix
    try:
        L = np.linalg.cholesky(corr_matrix)
    except np.linalg.LinAlgError:
        # Fall back to diagonal (independent) if matrix not PSD
        L = np.eye(n)

    # Generate correlated normals
    Z = rng.standard_normal((n_sims, n))
    corr_Z = Z @ L.T

    # Convert back to Bernoulli: leg_i wins if corr_Z[:, i] < quantile_i
    outcomes = np.zeros((n_sims, n), dtype=int)
    for i, q in enumerate(quantiles):
        outcomes[:, i] = (corr_Z[:, i] < q).astype(int)

    return outcomes


def build_correlation_matrix(legs: list[BetEvaluation]) -> np.ndarray:
    """Build pairwise correlation matrix for parlay legs."""
    n = len(legs)
    mat = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            c = estimate_correlation(legs[i], legs[j])
            mat[i, j] = c
            mat[j, i] = c

    # Ensure positive semi-definite (project to nearest PSD if needed)
    eigenvalues, eigenvectors = np.linalg.eigh(mat)
    eigenvalues = np.maximum(eigenvalues, 0.001)
    mat_psd = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T

    # Re-normalize diagonal to 1
    diag = np.sqrt(np.diag(mat_psd))
    mat_psd = mat_psd / np.outer(diag, diag)
    return mat_psd


def simulate_parlay(
    legs: list[BetEvaluation],
    n_sims: int = SIMULATIONS,
    seed: int = 42,
) -> SimulationResult:
    """
    Run Monte Carlo simulation for a parlay.
    Accounts for leg correlations via Gaussian copula.
    """
    if not legs:
        raise ValueError("No legs provided for simulation")

    rng = np.random.default_rng(seed)
    probs = [leg.model_prob for leg in legs]
    decimals = [leg.decimal_odds for leg in legs]

    # Combined true payout if all legs win
    combined_decimal = math.prod(decimals)

    # Build correlation matrix
    corr_matrix = build_correlation_matrix(legs)

    # Simulate correlated outcomes
    outcomes = _correlated_outcomes(probs, corr_matrix, n_sims, rng)

    # Parlay wins when ALL legs hit
    all_win = outcomes.all(axis=1)  # (n_sims,) boolean

    # ROI per $1 bet
    # Win: profit = combined_decimal - 1
    # Loss: profit = -1
    roi = np.where(all_win, combined_decimal - 1, -1.0)

    win_pct = float(all_win.mean())
    mean_roi = float(roi.mean())
    std_roi = float(roi.std())
    ev_pct = mean_roi * 100

    # Sharpe ratio (risk-adjusted return)
    sharpe = mean_roi / std_roi if std_roi > 0 else 0.0

    combined_american = (
        (combined_decimal - 1) * 100 if combined_decimal >= 2
        else -100 / (combined_decimal - 1)
    )

    return SimulationResult(
        win_probability=win_pct,
        ev_pct=ev_pct,
        combined_decimal_odds=combined_decimal,
        combined_american_odds=combined_american,
        roi_distribution=roi,
        percentile_5=float(np.percentile(roi, 5)),
        percentile_95=float(np.percentile(roi, 95)),
        std_dev=std_roi,
        sharpe_ratio=sharpe,
        n_simulations=n_sims,
    )


def simulate_bankroll_growth(
    parlays: list[dict],
    initial_bankroll: float = 1000.0,
    n_seasons: int = 100,
    bets_per_season: int = 50,
    seed: int = 42,
) -> dict:
    """
    Simulate long-term bankroll growth using the parlay strategy.
    parlays: list of {win_prob, decimal_odds, stake_pct}
    """
    rng = np.random.default_rng(seed)
    results = []

    for season in range(n_seasons):
        bankroll = initial_bankroll
        history = [bankroll]

        for _ in range(bets_per_season):
            if bankroll <= 0:
                break
            for parlay in parlays:
                stake = bankroll * parlay["stake_pct"]
                if rng.uniform() < parlay["win_prob"]:
                    bankroll += stake * (parlay["decimal_odds"] - 1)
                else:
                    bankroll -= stake
            history.append(max(bankroll, 0))

        results.append({
            "final_bankroll": bankroll,
            "max_bankroll": max(history),
            "min_bankroll": min(history),
            "roi_pct": (bankroll / initial_bankroll - 1) * 100,
        })

    final_bankrolls = [r["final_bankroll"] for r in results]
    return {
        "mean_final": float(np.mean(final_bankrolls)),
        "median_final": float(np.median(final_bankrolls)),
        "pct_profitable": float(sum(b > initial_bankroll for b in final_bankrolls) / n_seasons),
        "pct_ruin": float(sum(b <= 0 for b in final_bankrolls) / n_seasons),
        "mean_roi_pct": float(np.mean([(b / initial_bankroll - 1) * 100 for b in final_bankrolls])),
        "results": results[:10],  # First 10 for display
    }
