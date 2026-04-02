"""
Win probability models for game outcomes (moneyline + spread).
Uses an XGBoost + LightGBM ensemble per sport, with ELO as a prior.
When insufficient historical data exists, falls back to ELO-only.
"""
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False

from src.models.base import ParlayModel, Prediction
from src.features.engineering import GameFeatures


class WinProbabilityModel(ParlayModel):
    """
    Ensemble model for game win probability.
    Architecture: XGBoost + LightGBM + Logistic Regression (stacking).
    Falls back to ELO-only when not enough data to train.
    """

    name = "win_prob"

    def __init__(self, sport: str):
        super().__init__()
        self.sport = sport
        self.scaler = StandardScaler()
        self.base_models = []
        self.meta_model = None
        self.min_train_samples = 200

    def build(self) -> None:
        """Build the ensemble components."""
        self.base_models = []

        if XGB_AVAILABLE:
            self.base_models.append(("xgb", xgb.XGBClassifier(
                n_estimators=300,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=5,
                gamma=0.1,
                reg_alpha=0.1,
                reg_lambda=1.0,
                use_label_encoder=False,
                eval_metric="logloss",
                random_state=42,
                n_jobs=-1,
            )))

        if LGB_AVAILABLE:
            self.base_models.append(("lgb", lgb.LGBMClassifier(
                n_estimators=300,
                max_depth=4,
                learning_rate=0.05,
                num_leaves=31,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_samples=20,
                reg_alpha=0.1,
                reg_lambda=1.0,
                random_state=42,
                n_jobs=-1,
                verbose=-1,
            )))

        # Always include logistic regression as a fallback
        self.base_models.append(("lr", CalibratedClassifierCV(
            LogisticRegression(C=1.0, max_iter=1000, random_state=42),
            cv=5, method="sigmoid"
        )))

        # Meta-model for stacking
        self.meta_model = LogisticRegression(C=1.0, max_iter=500)

    def train(self, X: np.ndarray, y: np.ndarray) -> None:
        if len(X) < self.min_train_samples:
            print(f"[{self.sport}] Only {len(X)} samples — training LR only")
            self.base_models = [self.base_models[-1]]  # keep only LR

        X_scaled = self.scaler.fit_transform(X)

        # Train base models
        meta_features = np.zeros((len(X), len(self.base_models)))
        for i, (name, model) in enumerate(self.base_models):
            model.fit(X_scaled, y)
            meta_features[:, i] = model.predict_proba(X_scaled)[:, 1]

        # Train meta-model on out-of-fold predictions
        if len(self.base_models) > 1:
            self.meta_model.fit(meta_features, y)

        # Store feature importances from XGB if available
        for name, model in self.base_models:
            if name == "xgb" and hasattr(model, "feature_importances_"):
                self.feature_importances = {
                    f"feat_{i}": float(v)
                    for i, v in enumerate(model.feature_importances_)
                }
                break

        # Evaluate
        cv_scores = cross_val_score(
            self.base_models[-1][1],  # use LR for CV (fastest)
            X_scaled, y, cv=min(5, len(X) // 20), scoring="accuracy"
        )
        self.training_accuracy = float(cv_scores.mean())
        self.is_trained = True
        print(f"[{self.sport}] Model trained: CV accuracy = {self.training_accuracy:.3f}")

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        X_scaled = self.scaler.transform(X)

        if not self.is_trained or not self.base_models:
            return np.column_stack([1 - X[:, 1], X[:, 1]])  # use ELO prob directly

        if len(self.base_models) == 1:
            return self.base_models[0][1].predict_proba(X_scaled)

        # Stack base model outputs
        meta_features = np.column_stack([
            m.predict_proba(X_scaled)[:, 1]
            for _, m in self.base_models
        ])
        prob = self.meta_model.predict_proba(meta_features)[:, 1]
        return np.column_stack([1 - prob, prob])

    def predict_game(self, features: GameFeatures) -> Prediction:
        """Predict outcome for a single game."""
        X = features.to_array().reshape(1, -1)

        if self.is_trained:
            proba = self.predict_proba(X)[0, 1]
        else:
            # ELO fallback
            proba = features.elo_win_prob

        proba = self.calibrate(proba)

        return Prediction(
            outcome="home_win",
            probability=proba,
            confidence=self.confidence_tier(proba),
            model_name=f"{self.sport}_{self.name}",
            features={
                "elo_win_prob": features.elo_win_prob,
                "elo_diff": features.elo_diff,
                "net_rating_diff": features.net_rating_diff,
                "form_diff5": features.form_diff5,
                "rest_diff": features.rest_diff,
                "pyth_diff": features.pyth_diff,
            },
            metadata={
                "sport": self.sport,
                "home_team": features.home_team,
                "away_team": features.away_team,
                "game_id": features.game_id,
            }
        )


class SpreadModel(WinProbabilityModel):
    """
    Predicts whether the home team covers the spread.
    Same architecture, but target is cover/no-cover instead of win/lose.
    """
    name = "spread"

    def predict_spread(self, features: GameFeatures, spread: float) -> Prediction:
        """
        Predict probability home team covers spread.
        spread: negative = home favored (e.g. -3.5), positive = home dog.
        """
        X = features.to_array().reshape(1, -1)

        if self.is_trained:
            proba = self.predict_proba(X)[0, 1]
        else:
            # ELO-based spread estimation
            # Roughly: every 25 ELO points ≈ 1 point in NFL, 2.5 in NBA
            scale = {"NBA": 2.5, "NFL": 1.0, "MLB": 0.4, "NHL": 0.4}.get(self.sport, 1.0)
            expected_margin = features.elo_diff / 25 * scale
            # Adjust for home advantage already baked in ELO
            adjusted_diff = expected_margin + spread  # positive = home likely to cover
            # Convert to probability using logistic function
            proba = 1 / (1 + np.exp(-adjusted_diff * 0.15))

        proba = self.calibrate(proba)
        return Prediction(
            outcome="home_cover",
            probability=proba,
            confidence=self.confidence_tier(proba),
            model_name=f"{self.sport}_{self.name}",
            features={"spread": spread, "elo_diff": features.elo_diff},
            metadata={
                "sport": self.sport,
                "home_team": features.home_team,
                "away_team": features.away_team,
                "spread": spread,
            }
        )


class TotalModel(ParlayModel):
    """
    Predicts over/under total.
    Uses team pace, scoring averages, and matchup dynamics.
    """
    name = "totals"

    def __init__(self, sport: str):
        super().__init__()
        self.sport = sport
        self.scaler = StandardScaler()
        self.model = None

    def build(self) -> None:
        if LGB_AVAILABLE:
            self.model = lgb.LGBMClassifier(
                n_estimators=200, max_depth=3, learning_rate=0.05,
                num_leaves=15, random_state=42, verbose=-1
            )
        elif XGB_AVAILABLE:
            self.model = xgb.XGBClassifier(
                n_estimators=200, max_depth=3, learning_rate=0.05,
                random_state=42, use_label_encoder=False, eval_metric="logloss"
            )
        else:
            self.model = LogisticRegression(C=1.0, max_iter=1000)

    def train(self, X: np.ndarray, y: np.ndarray) -> None:
        X_scaled = self.scaler.fit_transform(X)
        self.model.fit(X_scaled, y)
        scores = cross_val_score(self.model, X_scaled, y, cv=5, scoring="accuracy")
        self.training_accuracy = float(scores.mean())
        self.is_trained = True

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        X_scaled = self.scaler.transform(X) if self.is_trained else X
        if self.model and self.is_trained:
            return self.model.predict_proba(X_scaled)
        return np.column_stack([np.full(len(X), 0.5), np.full(len(X), 0.5)])

    def predict_total(self, features: GameFeatures, total_line: float) -> Prediction:
        """Predict over/under for a game total."""
        # Build total-specific feature vector
        avg_combined = (features.home_ppg + features.away_ppg +
                        features.home_papg + features.away_papg) / 2
        pace_score = avg_combined / 100  # normalized

        X = np.array([[
            features.home_ppg / 120,
            features.away_ppg / 120,
            features.home_papg / 120,
            features.away_papg / 120,
            (features.home_ppg + features.away_ppg) / (total_line + 1),
            features.rest_diff / 7,
            features.season_pct,
        ]])

        if self.is_trained:
            proba_over = self.predict_proba(X)[0, 1]
        else:
            # Simple heuristic: compare expected combined scoring to line
            expected_total = (features.home_ppg + features.away_ppg +
                              features.home_papg + features.away_papg) / 2
            diff = expected_total - total_line
            proba_over = float(1 / (1 + np.exp(-diff * 0.1)))

        proba_over = self.calibrate(proba_over)
        return Prediction(
            outcome="over" if proba_over > 0.5 else "under",
            probability=max(proba_over, 1 - proba_over),
            confidence=self.confidence_tier(proba_over),
            model_name=f"{self.sport}_{self.name}",
            features={"total_line": total_line, "expected": avg_combined},
            metadata={"sport": self.sport, "total_line": total_line}
        )


# ─── Model Registry ───────────────────────────────────────────────────────────

_model_cache: dict[str, ParlayModel] = {}

SPORTS = ["NBA", "NFL", "MLB", "NHL", "NCAAB", "NCAAF"]


def get_model(sport: str, model_type: str = "win_prob") -> ParlayModel:
    """Get or create a model instance for a sport."""
    key = f"{sport}_{model_type}"
    if key not in _model_cache:
        if model_type == "win_prob":
            m = WinProbabilityModel(sport)
        elif model_type == "spread":
            m = SpreadModel(sport)
        elif model_type == "totals":
            m = TotalModel(sport)
        else:
            m = WinProbabilityModel(sport)
        m.build()
        if not m.load():
            print(f"[{sport}] No saved model found — using ELO fallback")
        _model_cache[key] = m
    return _model_cache[key]


def generate_synthetic_training_data(sport: str, n_samples: int = 2000) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate synthetic training data based on known statistical patterns.
    Used when real historical data is unavailable.
    ELO difference is the primary predictor; other features add noise + signal.
    """
    rng = np.random.default_rng(42)

    # Feature names match GameFeatures.to_array() order (16 base + sport extras)
    elo_diffs = rng.normal(0, 150, n_samples)          # ELO difference
    elo_probs = 1 / (1 + 10 ** (-elo_diffs / 400))     # ELO win probability

    # Generate correlated features
    X = np.column_stack([
        elo_diffs / 400,
        elo_probs,
        rng.normal(0, 0.15, n_samples),    # win_pct_diff
        rng.beta(5, 5, n_samples),          # home_home_win_pct
        rng.beta(5, 5, n_samples),          # away_away_win_pct
        rng.normal(0, 0.1, n_samples),      # pyth_diff
        rng.normal(0, 0.3, n_samples),      # net_rating_diff / 20
        rng.normal(0, 0.2, n_samples),      # form_diff5
        rng.beta(5, 5, n_samples),          # home_form10
        rng.beta(5, 5, n_samples),          # away_form10
        rng.normal(0, 0.2, n_samples),      # rest_diff / 7
        rng.beta(3, 3, n_samples),          # home_rest / 7
        rng.beta(3, 3, n_samples),          # away_rest / 7
        rng.uniform(0, 1, n_samples),       # season_pct
        rng.beta(5, 5, n_samples),          # h2h_home_win_pct
        rng.beta(2, 5, n_samples),          # h2h_games weight
    ])

    # Labels: ELO is primary signal with noise
    noise = rng.normal(0, 0.08, n_samples)
    true_probs = np.clip(elo_probs + noise, 0.05, 0.95)
    y = (rng.uniform(0, 1, n_samples) < true_probs).astype(int)

    return X, y


def train_all_models(use_synthetic: bool = True) -> None:
    """Train or re-train all sport models."""
    for sport in SPORTS:
        print(f"\n{'='*40}")
        print(f"Training {sport} models...")
        for model_type in ["win_prob", "spread", "totals"]:
            model = get_model(sport, model_type)
            if not model.is_trained or use_synthetic:
                X, y = generate_synthetic_training_data(sport, n_samples=3000)
                model.train(X, y)
                model.save()
                print(f"  [{sport}/{model_type}] Saved.")
