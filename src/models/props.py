"""
Player prop prediction model.
Predicts probability of a player going over/under a prop line
based on their recent performance, opponent defense, and context.
"""
import numpy as np
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

from src.models.base import ParlayModel, Prediction
from src.features.engineering import PropFeatures


class PlayerPropModel(ParlayModel):
    """
    Predicts player prop over/under probability.
    Uses a hybrid approach:
      1. Statistical baseline (normal/Poisson distribution over recent logs)
      2. ML adjustment (contextual factors: opponent, rest, home/away)
      3. Combined probability
    """

    name = "prop"

    def __init__(self, sport: str, prop_type: str):
        super().__init__()
        self.sport = sport
        self.prop_type = prop_type
        self.scaler = StandardScaler()
        self._stat_weight = 0.6   # weight for statistical distribution
        self._ml_weight = 0.4     # weight for ML model

    def build(self) -> None:
        if XGB_AVAILABLE:
            self.model = xgb.XGBClassifier(
                n_estimators=150, max_depth=3, learning_rate=0.08,
                subsample=0.8, colsample_bytree=0.8, min_child_weight=10,
                use_label_encoder=False, eval_metric="logloss", random_state=42
            )
        else:
            self.model = LogisticRegression(C=0.5, max_iter=500)

    def train(self, X: np.ndarray, y: np.ndarray) -> None:
        if len(X) < 100:
            return
        X_s = self.scaler.fit_transform(X)
        self.model.fit(X_s, y)
        self.is_trained = True

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self.is_trained:
            return np.column_stack([np.full(len(X), 0.5), np.full(len(X), 0.5)])
        X_s = self.scaler.transform(X)
        return self.model.predict_proba(X_s)

    def _stat_over_prob(self, recent_values: list[float], line: float) -> float:
        """
        Estimate over probability using statistical distribution of recent logs.
        Uses normal distribution fit for continuous stats (points, yards)
        and Poisson for count stats (goals, home runs).
        """
        if not recent_values or len(recent_values) < 3:
            return 0.5

        arr = np.array(recent_values, dtype=float)
        mu = float(arr.mean())
        sigma = float(arr.std()) if arr.std() > 0 else mu * 0.3

        count_stats = ["home_runs", "goals", "touchdowns", "blocks", "steals"]
        is_count = any(s in self.prop_type.lower() for s in count_stats)

        if is_count and mu > 0:
            lam = mu
            # P(X > line) = 1 - P(X <= floor(line))
            return float(1 - stats.poisson.cdf(int(line), lam))
        else:
            # Normal distribution: P(X > line)
            if sigma == 0:
                return 1.0 if mu > line else 0.0
            return float(1 - stats.norm.cdf(line, loc=mu, scale=sigma))

    def predict_prop(
        self,
        features: PropFeatures,
        recent_values: list[float] = None,
    ) -> Prediction:
        """Predict over/under probability for a player prop."""
        recent_values = recent_values or []

        # Statistical distribution probability
        stat_prob = self._stat_over_prob(recent_values, features.line)

        # ML contextual adjustment
        if self.is_trained:
            X = features.to_array().reshape(1, -1)
            ml_prob = float(self.predict_proba(X)[0, 1])
        else:
            # Heuristic: favor over when player's recent avg > line
            ml_prob = 0.6 if features.line_vs_avg > 0 else 0.4

        # Blend statistical and ML probabilities
        combined = self._stat_weight * stat_prob + self._ml_weight * ml_prob

        # Apply home/away adjustment
        ha_adj = 0.02 if features.is_home else -0.01
        combined = float(np.clip(combined + ha_adj, 0.05, 0.95))

        # Back-to-back penalty
        if features.is_back_to_back:
            # Players typically underperform on B2B
            combined = float(np.clip(combined - 0.03, 0.05, 0.95))

        # Opponent defense adjustment
        # opp_rank_vs_prop: 0=tough defense, 1=easy defense
        opp_adj = (features.opp_rank_vs_prop - 0.5) * 0.06
        combined = float(np.clip(combined + opp_adj, 0.05, 0.95))

        # Injury status penalty
        if features.injury_status > 0:
            combined = float(np.clip(combined - features.injury_status * 0.15, 0.05, 0.95))

        outcome = "over" if combined > 0.5 else "under"
        display_prob = combined if combined > 0.5 else 1 - combined

        return Prediction(
            outcome=f"{features.prop_type}_{outcome}",
            probability=combined,
            confidence=self.confidence_tier(combined),
            model_name=f"{self.sport}_prop_{self.prop_type}",
            features={
                "stat_prob": stat_prob,
                "ml_prob": ml_prob,
                "line": features.line,
                "season_avg": features.season_avg,
                "last5_avg": features.last5_avg,
                "hit_rate_over": features.hit_rate_over,
                "opp_rank": features.opp_rank_vs_prop,
            },
            metadata={
                "sport": self.sport,
                "player": features.player_name,
                "prop_type": self.prop_type,
                "line": features.line,
                "outcome": outcome,
                "display_prob": display_prob,
            }
        )


# ─── Prop Model Cache ─────────────────────────────────────────────────────────

_prop_cache: dict[str, PlayerPropModel] = {}


def get_prop_model(sport: str, prop_type: str) -> PlayerPropModel:
    key = f"{sport}_{prop_type}"
    if key not in _prop_cache:
        m = PlayerPropModel(sport, prop_type)
        m.build()
        m.load()  # Load if saved, else use heuristic fallback
        _prop_cache[key] = m
    return _prop_cache[key]


# ─── Quick prop evaluation ─────────────────────────────────────────────────────

def evaluate_prop(
    sport: str,
    player_name: str,
    prop_type: str,
    line: float,
    recent_game_logs: list[dict],
    is_home: bool = True,
    rest_days: float = 2.0,
    opp_defense_rank: float = 0.5,
    injury_status: float = 0.0,
) -> Prediction:
    """
    High-level function to evaluate a player prop.
    Handles feature building and model lookup internally.
    """
    from src.features.engineering import build_prop_features, _prop_stat_map

    stat_key = _prop_stat_map(prop_type)
    recent_values = [g.get(stat_key, 0) for g in recent_game_logs if g.get(stat_key) is not None]

    features = build_prop_features(
        player_stats=recent_game_logs,
        prop_type=prop_type,
        line=line,
        is_home=is_home,
        rest_days=rest_days,
        opp_defense_rank=opp_defense_rank,
    )
    features.player_name = player_name
    features.sport = sport
    features.injury_status = injury_status

    model = get_prop_model(sport, prop_type)
    pred = model.predict_prop(features, recent_values)
    pred.metadata["player"] = player_name
    return pred
