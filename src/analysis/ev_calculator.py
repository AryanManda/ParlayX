"""
Expected Value (EV) calculator.
Core betting math: compares model probability vs sportsbook implied probability
to find edges and calculate Kelly criterion stake sizing.
"""
import math
from dataclasses import dataclass, field
from typing import Optional
from src.data.processor import american_to_decimal, american_to_prob, remove_vig
from config import EV_THRESHOLD, KELLY_FRACTION, MAX_BANKROLL_PCT


@dataclass
class BetEvaluation:
    """Full evaluation of a single bet opportunity."""
    # Identity
    sport: str
    game_id: str
    bet_type: str           # moneyline, spread, total, prop
    outcome: str            # "home_win", "over", "player_points_over", etc.
    description: str        # Human-readable e.g. "Lakers ML"

    # Odds
    best_odds: float        # Best available American odds
    decimal_odds: float
    implied_prob: float     # Sportsbook's vig-free implied probability

    # Model
    model_prob: float       # Our predicted probability
    model_confidence: str   # low / medium / high

    # EV
    ev_pct: float           # (model_prob × decimal_odds - 1) × 100
    edge: float             # model_prob - implied_prob
    is_positive_ev: bool

    # Kelly sizing
    kelly_pct: float        # Full Kelly %
    recommended_pct: float  # Fractional Kelly (conservative)
    max_pct: float = MAX_BANKROLL_PCT

    # Line movement signal
    sharp_money: bool = False
    line_movement: float = 0.0

    # Metadata
    teams: str = ""
    game_date: str = ""
    bookmaker: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def grade(self) -> str:
        """Letter grade for this bet."""
        if self.ev_pct >= 8 and self.model_confidence == "high":
            return "A+"
        if self.ev_pct >= 5 and self.model_confidence in ("high", "medium"):
            return "A"
        if self.ev_pct >= 3:
            return "B"
        if self.ev_pct >= 1:
            return "C"
        return "F"

    def summary(self) -> str:
        sign = "+" if self.ev_pct >= 0 else ""
        return (
            f"{self.description} | "
            f"Odds: {int(self.best_odds):+d} | "
            f"Model: {self.model_prob:.1%} | "
            f"Implied: {self.implied_prob:.1%} | "
            f"EV: {sign}{self.ev_pct:.1f}% | "
            f"Grade: {self.grade}"
        )


def calculate_ev(model_prob: float, decimal_odds: float) -> float:
    """
    Expected value as a percentage of stake.
    EV% = (prob × decimal_odds - 1) × 100
    Positive means edge over the book.
    """
    return (model_prob * decimal_odds - 1) * 100


def kelly_criterion(prob: float, decimal_odds: float) -> float:
    """
    Full Kelly criterion stake as fraction of bankroll.
    f* = (p × b - q) / b   where b = decimal_odds - 1, q = 1 - p
    """
    b = decimal_odds - 1
    if b <= 0:
        return 0.0
    q = 1 - prob
    kelly = (prob * b - q) / b
    return max(kelly, 0.0)


def fractional_kelly(prob: float, decimal_odds: float, fraction: float = KELLY_FRACTION) -> float:
    """Conservative Kelly: use only a fraction of full Kelly."""
    return kelly_criterion(prob, decimal_odds) * fraction


def evaluate_bet(
    sport: str,
    game_id: str,
    bet_type: str,
    outcome: str,
    description: str,
    model_prob: float,
    model_confidence: str,
    american_odds: float,
    bookmaker: str = "best",
    opening_odds: float = None,
    public_bet_pct: float = None,
    teams: str = "",
    game_date: str = "",
    **extra,
) -> BetEvaluation:
    """
    Evaluate a single bet opportunity.
    Returns full BetEvaluation with EV, Kelly, and grade.
    """
    decimal = american_to_decimal(american_odds)
    raw_implied = american_to_prob(american_odds)

    # For a two-sided market, remove vig properly
    # (For simplicity, use raw implied for single-sided evaluation)
    implied_prob = raw_implied

    ev = calculate_ev(model_prob, decimal)
    edge = model_prob - implied_prob
    is_ev_pos = ev >= (EV_THRESHOLD * 100)

    kelly = fractional_kelly(model_prob, decimal)
    recommended = min(kelly, MAX_BANKROLL_PCT)

    # Line movement analysis
    sharp = False
    movement = 0.0
    if opening_odds is not None:
        from src.data.processor import detect_sharp_money
        sharp_data = detect_sharp_money(opening_odds, american_odds, public_bet_pct)
        sharp = sharp_data["sharp_money"]
        movement = sharp_data["movement"]

    return BetEvaluation(
        sport=sport,
        game_id=game_id,
        bet_type=bet_type,
        outcome=outcome,
        description=description,
        best_odds=american_odds,
        decimal_odds=decimal,
        implied_prob=implied_prob,
        model_prob=model_prob,
        model_confidence=model_confidence,
        ev_pct=ev,
        edge=edge,
        is_positive_ev=is_ev_pos,
        kelly_pct=kelly_criterion(model_prob, decimal),
        recommended_pct=recommended,
        sharp_money=sharp,
        line_movement=movement,
        bookmaker=bookmaker,
        teams=teams,
        game_date=game_date,
        extra=extra,
    )


def find_best_line(bookmaker_odds: list[dict], outcome: str) -> tuple[float, str]:
    """
    Find the best available odds for an outcome across bookmakers.
    Returns (best_american_odds, bookmaker_name).
    """
    best = (-10000, "none")
    for bm in bookmaker_odds:
        for market in bm.get("markets", {}).values():
            if outcome in market:
                price = market[outcome].get("price", -10000)
                if american_to_decimal(price) > american_to_decimal(best[0]):
                    best = (price, bm.get("bookmaker", "unknown"))
    return best


def screen_bets(evaluations: list[BetEvaluation]) -> list[BetEvaluation]:
    """
    Filter bet evaluations to only +EV opportunities,
    sorted by EV% descending.
    """
    ev_bets = [b for b in evaluations if b.is_positive_ev]
    ev_bets.sort(key=lambda b: b.ev_pct, reverse=True)
    return ev_bets


def parlay_ev(legs: list[BetEvaluation]) -> dict:
    """
    Calculate expected value for a parlay of independent legs.
    EV = (combined_prob × combined_odds - 1) × 100
    """
    if not legs:
        return {}

    combined_prob = math.prod(leg.model_prob for leg in legs)
    combined_decimal = math.prod(leg.decimal_odds for leg in legs)
    combined_american = (
        (combined_decimal - 1) * 100 if combined_decimal >= 2
        else -100 / (combined_decimal - 1)
    )

    ev = calculate_ev(combined_prob, combined_decimal)
    kelly = fractional_kelly(combined_prob, combined_decimal)

    return {
        "legs": len(legs),
        "combined_prob": combined_prob,
        "combined_decimal_odds": combined_decimal,
        "combined_american_odds": combined_american,
        "ev_pct": ev,
        "kelly_pct": kelly,
        "recommended_stake_pct": min(kelly, MAX_BANKROLL_PCT),
        "is_positive_ev": ev > 0,
    }


def vig_percentage(home_odds: float, away_odds: float) -> float:
    """Calculate the bookmaker's vig/juice percentage."""
    home_prob = american_to_prob(home_odds)
    away_prob = american_to_prob(away_odds)
    return (home_prob + away_prob - 1) * 100


def no_vig_odds(home_odds: float, away_odds: float) -> tuple[float, float]:
    """Calculate true no-vig odds for a two-outcome market."""
    home_prob = american_to_prob(home_odds)
    away_prob = american_to_prob(away_odds)
    true_home, true_away = remove_vig(home_prob, away_prob)

    # Convert back to American
    def prob_to_american(p: float) -> float:
        if p >= 0.5:
            return -(p / (1 - p)) * 100
        return ((1 - p) / p) * 100

    return prob_to_american(true_home), prob_to_american(true_away)


def closing_line_value(bet_odds: float, closing_odds: float) -> float:
    """
    Closing line value (CLV): measure of bet quality.
    Positive CLV means you got a better number than closing.
    CLV% = (bet_prob / closing_prob - 1) × 100
    """
    bet_prob = american_to_prob(bet_odds)
    closing_prob = american_to_prob(closing_odds)
    if closing_prob == 0:
        return 0.0
    return (bet_prob / closing_prob - 1) * 100
