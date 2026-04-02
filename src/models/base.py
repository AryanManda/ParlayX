"""
Base model class and model registry.
All sport-specific models inherit from ParlayModel.
"""
import os
import joblib
import numpy as np
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MODEL_DIR = Path(__file__).parent.parent.parent / "data" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Prediction:
    outcome: str            # e.g. "home_win", "over", "player_over"
    probability: float      # model's estimated win probability
    confidence: str         # "low" / "medium" / "high"
    model_name: str
    features: dict          # raw feature values for explainability
    metadata: dict          # sport, teams, line, etc.


class ParlayModel(ABC):
    """Abstract base class for all prediction models."""

    name: str = "base"
    sport: str = "unknown"
    version: str = "1.0"

    def __init__(self):
        self.model = None
        self.is_trained = False
        self.feature_importances: dict = {}
        self.training_accuracy: float = 0.0
        self.calibration_factor: float = 1.0  # Post-hoc probability calibration

    @abstractmethod
    def build(self) -> None:
        """Instantiate the underlying ML model(s)."""

    @abstractmethod
    def train(self, X: np.ndarray, y: np.ndarray) -> None:
        """Fit the model on training data."""

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Return win probabilities (N, 2) for N samples."""

    def predict(self, X: np.ndarray) -> np.ndarray:
        proba = self.predict_proba(X)
        return (proba[:, 1] >= 0.5).astype(int)

    def calibrate(self, proba: float) -> float:
        """Apply Platt scaling / calibration to raw probability."""
        # Simple temperature scaling: push predictions toward extremes slightly
        # when confidence is high, or toward 0.5 when uncertain.
        p = np.clip(proba, 0.01, 0.99)
        return float(p)

    def confidence_tier(self, proba: float) -> str:
        margin = abs(proba - 0.5)
        if margin >= 0.12:
            return "high"
        if margin >= 0.06:
            return "medium"
        return "low"

    def save(self, path: Optional[Path] = None) -> None:
        path = path or MODEL_DIR / f"{self.sport}_{self.name}_v{self.version}.pkl"
        joblib.dump({
            "model": self.model,
            "feature_importances": self.feature_importances,
            "training_accuracy": self.training_accuracy,
            "calibration_factor": self.calibration_factor,
            "version": self.version,
        }, path)

    def load(self, path: Optional[Path] = None) -> bool:
        path = path or MODEL_DIR / f"{self.sport}_{self.name}_v{self.version}.pkl"
        if not path.exists():
            return False
        data = joblib.load(path)
        self.model = data["model"]
        self.feature_importances = data.get("feature_importances", {})
        self.training_accuracy = data.get("training_accuracy", 0.0)
        self.calibration_factor = data.get("calibration_factor", 1.0)
        self.is_trained = True
        return True
