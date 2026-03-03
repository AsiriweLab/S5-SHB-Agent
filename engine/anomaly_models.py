"""
ML/DL Anomaly Detection Models (NEW POC8).

Provides an ensemble of anomaly detection models:
  1. Isolation Forest (sklearn) -- primary detector
  2. Local Outlier Factor (sklearn) -- secondary detector
  3. Autoencoder (PyTorch, optional) -- deep learning detector
  4. Statistical Baseline (Z-score + IQR) -- always available
"""

import time
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


@dataclass
class AnomalyResult:
    device_id: str
    device_type: str
    anomaly_score: float         # 0.0 (normal) to 1.0 (anomalous)
    is_anomaly: bool
    detectors_triggered: List[str] = field(default_factory=list)
    explanation: str = ""
    readings: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

# Numeric keys we extract from telemetry readings per device type (known-good mappings)
FEATURE_KEYS = {
    "thermostat": ["current_temp", "target_temp"],
    "smart_plug": ["power_watts", "voltage"],
    "hvac": ["current_temp", "target_temp", "humidity"],
    "smart_appliance": ["runtime_hours"],
    "smoke_sensor": ["smoke_level"],
    "gas_sensor": ["gas_level_ppm"],
}

# Metadata keys to skip when auto-detecting numeric features
_METADATA_KEYS = {"device_type", "device_name", "timestamp"}


def _numeric(val, default=0.0) -> float:
    """Safely convert a value to float."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _auto_detect_numeric_keys(readings: dict) -> list:
    """Extract keys with numeric values from readings, for unknown device types."""
    keys = []
    for k, v in sorted(readings.items()):
        if k in _METADATA_KEYS:
            continue
        try:
            float(v)
            keys.append(k)
        except (TypeError, ValueError):
            pass
    return keys


def extract_features(telemetry_list) -> Dict[str, np.ndarray]:
    """Extract numeric feature vectors per device from telemetry.

    Uses known feature keys for recognized device types, auto-detects
    numeric fields for unknown types (HES devices).

    Returns {device_id: np.array([feature1, feature2, ...])}
    """
    result = {}
    for t in telemetry_list:
        keys = FEATURE_KEYS.get(t.device_type)
        if not keys:
            keys = _auto_detect_numeric_keys(t.readings)
        if not keys:
            continue
        vec = [_numeric(t.readings.get(k)) for k in keys]
        result[t.device_id] = np.array(vec, dtype=np.float64)
    return result


# ---------------------------------------------------------------------------
# Statistical Baseline (always available)
# ---------------------------------------------------------------------------

class StatisticalBaseline:
    """Z-score and IQR-based anomaly detection."""

    def __init__(self, zscore_threshold: float = 2.5):
        self._zscore_threshold = zscore_threshold
        self._means: Dict[str, np.ndarray] = {}
        self._stds: Dict[str, np.ndarray] = {}
        self._trained = False

    def train(self, feature_history: Dict[str, List[np.ndarray]]):
        """Compute per-device mean/std from historical feature vectors."""
        for device_id, vectors in feature_history.items():
            if len(vectors) < 3:
                continue
            arr = np.stack(vectors)
            self._means[device_id] = arr.mean(axis=0)
            self._stds[device_id] = arr.std(axis=0, ddof=1)
            # Avoid division by zero
            self._stds[device_id] = np.where(
                self._stds[device_id] < 1e-10, 1.0, self._stds[device_id])
        self._trained = bool(self._means)

    def score(self, device_id: str, features: np.ndarray) -> Tuple[float, str]:
        """Return (max_zscore, explanation). Higher = more anomalous."""
        if not self._trained or device_id not in self._means:
            return 0.0, "no baseline"
        z = np.abs((features - self._means[device_id]) / self._stds[device_id])
        max_z = float(z.max())
        idx = int(z.argmax())
        explanation = f"z-score={max_z:.2f} on feature[{idx}]"
        return max_z, explanation

    @property
    def trained(self) -> bool:
        return self._trained


# ---------------------------------------------------------------------------
# Isolation Forest Detector (sklearn)
# ---------------------------------------------------------------------------

class IsolationForestDetector:
    """Scikit-learn Isolation Forest wrapper."""

    def __init__(self, contamination: float = 0.05,
                 score_threshold: float = -0.5):
        self._model = None
        self._scaler = None
        self._threshold = score_threshold
        self._contamination = contamination
        self._trained = False

    def train(self, all_vectors: List[np.ndarray]):
        """Train on concatenated normal feature vectors."""
        if len(all_vectors) < 10:
            return
        try:
            from sklearn.ensemble import IsolationForest
            from sklearn.preprocessing import StandardScaler

            X = np.stack(all_vectors)
            self._scaler = StandardScaler()
            X_scaled = self._scaler.fit_transform(X)

            self._model = IsolationForest(
                contamination=self._contamination,
                random_state=42,
                n_estimators=100,
            )
            self._model.fit(X_scaled)
            self._trained = True
        except ImportError:
            self._trained = False

    def score(self, features: np.ndarray) -> float:
        """Return anomaly score. Negative = anomalous."""
        if not self._trained:
            return 0.0
        X = self._scaler.transform(features.reshape(1, -1))
        return float(self._model.score_samples(X)[0])

    @property
    def trained(self) -> bool:
        return self._trained


# ---------------------------------------------------------------------------
# Local Outlier Factor Detector (sklearn)
# ---------------------------------------------------------------------------

class LOFDetector:
    """Scikit-learn Local Outlier Factor wrapper."""

    def __init__(self, n_neighbors: int = 5):
        self._model = None
        self._scaler = None
        self._n_neighbors = n_neighbors
        self._trained = False
        self._X_train = None

    def train(self, all_vectors: List[np.ndarray]):
        """Train (store reference data) for LOF novelty detection."""
        if len(all_vectors) < self._n_neighbors + 1:
            return
        try:
            from sklearn.neighbors import LocalOutlierFactor
            from sklearn.preprocessing import StandardScaler

            X = np.stack(all_vectors)
            self._scaler = StandardScaler()
            X_scaled = self._scaler.fit_transform(X)

            self._model = LocalOutlierFactor(
                n_neighbors=min(self._n_neighbors, len(X) - 1),
                novelty=True,
            )
            self._model.fit(X_scaled)
            self._trained = True
        except ImportError:
            self._trained = False

    def score(self, features: np.ndarray) -> float:
        """Return decision function value. Negative = outlier."""
        if not self._trained:
            return 0.0
        X = self._scaler.transform(features.reshape(1, -1))
        return float(self._model.decision_function(X)[0])

    @property
    def trained(self) -> bool:
        return self._trained


# ---------------------------------------------------------------------------
# Autoencoder Detector (PyTorch, optional)
# ---------------------------------------------------------------------------

class AutoencoderDetector:
    """PyTorch autoencoder for anomaly detection via reconstruction error."""

    def __init__(self, enabled: bool = False):
        self._enabled = enabled
        self._model = None
        self._scaler = None
        self._threshold = 0.0
        self._trained = False
        self._input_dim = 0

    def train(self, all_vectors: List[np.ndarray], epochs: int = 50,
              lr: float = 1e-3):
        """Train autoencoder on normal data."""
        if not self._enabled or len(all_vectors) < 10:
            return
        try:
            import torch
            import torch.nn as nn
            from sklearn.preprocessing import StandardScaler

            X = np.stack(all_vectors)
            self._input_dim = X.shape[1]
            self._scaler = StandardScaler()
            X_scaled = self._scaler.fit_transform(X)
            X_tensor = torch.FloatTensor(X_scaled)

            # Simple autoencoder architecture
            class AE(nn.Module):
                def __init__(self, d):
                    super().__init__()
                    self.encoder = nn.Sequential(
                        nn.Linear(d, 32), nn.ReLU(),
                        nn.Linear(32, 8),
                    )
                    self.decoder = nn.Sequential(
                        nn.Linear(8, 32), nn.ReLU(),
                        nn.Linear(32, d),
                    )

                def forward(self, x):
                    return self.decoder(self.encoder(x))

            model = AE(self._input_dim)
            optimizer = torch.optim.Adam(model.parameters(), lr=lr)
            criterion = nn.MSELoss()

            model.train()
            for _ in range(epochs):
                optimizer.zero_grad()
                output = model(X_tensor)
                loss = criterion(output, X_tensor)
                loss.backward()
                optimizer.step()

            # Compute threshold from training reconstruction errors
            model.eval()
            with torch.no_grad():
                recon = model(X_tensor)
                errors = ((recon - X_tensor) ** 2).mean(dim=1).numpy()
                self._threshold = float(np.percentile(errors, 95))

            self._model = model
            self._trained = True
        except ImportError:
            self._trained = False

    def score(self, features: np.ndarray) -> float:
        """Return reconstruction error. Higher = more anomalous."""
        if not self._trained:
            return 0.0
        try:
            import torch
            X = self._scaler.transform(features.reshape(1, -1))
            X_tensor = torch.FloatTensor(X)
            self._model.eval()
            with torch.no_grad():
                recon = self._model(X_tensor)
                error = float(((recon - X_tensor) ** 2).mean())
            return error
        except Exception:
            return 0.0

    @property
    def trained(self) -> bool:
        return self._trained


# ---------------------------------------------------------------------------
# Ensemble Model Suite
# ---------------------------------------------------------------------------

class AnomalyModelSuite:
    """Ensemble of ML/DL anomaly detection models."""

    def __init__(self, dl_enabled: bool = False,
                 iforest_threshold: float = -0.5,
                 zscore_threshold: float = 2.5):
        self.isolation_forest = IsolationForestDetector(
            score_threshold=iforest_threshold)
        self.lof = LOFDetector()
        self.autoencoder = AutoencoderDetector(enabled=dl_enabled)
        self.baseline = StatisticalBaseline(zscore_threshold=zscore_threshold)

        self._feature_history: Dict[str, List[np.ndarray]] = {}
        self._all_vectors: List[np.ndarray] = []
        self._iforest_threshold = iforest_threshold
        self._zscore_threshold = zscore_threshold
        self._trained = False

    def set_thresholds(self, zscore_threshold: float,
                       iforest_threshold: float) -> None:
        """Update detection thresholds at runtime (no retraining needed).

        These are decision boundaries, not model parameters, so they can
        be changed without invalidating trained models.
        """
        self._zscore_threshold = zscore_threshold
        self._iforest_threshold = iforest_threshold
        self.baseline._zscore_threshold = zscore_threshold
        self.isolation_forest._threshold = iforest_threshold

    def accumulate(self, telemetry_list):
        """Accumulate feature vectors from one round of telemetry."""
        features = extract_features(telemetry_list)
        for device_id, vec in features.items():
            self._feature_history.setdefault(device_id, []).append(vec)
            self._all_vectors.append(vec)

    def _pad_vectors(self, vectors: list) -> list:
        """Pad all vectors to the same length (max dim) with zeros."""
        if not vectors:
            return vectors
        max_dim = max(v.shape[0] for v in vectors)
        padded = []
        for v in vectors:
            if v.shape[0] < max_dim:
                v = np.pad(v, (0, max_dim - v.shape[0]))
            padded.append(v)
        self._max_dim = max_dim
        return padded

    def _pad_single(self, vec: np.ndarray) -> np.ndarray:
        """Pad a single vector to the training dimension."""
        max_dim = getattr(self, '_max_dim', vec.shape[0])
        if vec.shape[0] < max_dim:
            return np.pad(vec, (0, max_dim - vec.shape[0]))
        return vec[:max_dim]

    def train(self):
        """Train all models on accumulated data."""
        t0 = time.time()

        # Statistical baseline (per-device)
        self.baseline.train(self._feature_history)

        # Pad vectors to uniform dimension for pooled models
        padded = self._pad_vectors(self._all_vectors)

        # Isolation Forest + LOF (all vectors pooled, padded)
        self.isolation_forest.train(padded)
        self.lof.train(padded)

        # Autoencoder (optional)
        self.autoencoder.train(padded)

        self._trained = True
        elapsed = time.time() - t0
        return {
            "training_time_s": round(elapsed, 3),
            "total_samples": len(self._all_vectors),
            "devices_profiled": len(self._feature_history),
            "models_trained": self._list_trained_models(),
        }

    def detect(self, telemetry_list) -> List[AnomalyResult]:
        """Run all models on current telemetry, return per-device results."""
        features = extract_features(telemetry_list)
        results = []

        # Build readings lookup
        readings_by_device = {}
        for t in telemetry_list:
            readings_by_device[t.device_id] = {
                "type": t.device_type,
                "readings": dict(t.readings),
            }

        for device_id, vec in features.items():
            detectors_triggered = []
            explanations = []
            padded_vec = self._pad_single(vec)

            # 1. Statistical baseline (uses original per-device vec)
            zscore, z_exp = self.baseline.score(device_id, vec)
            if zscore > self._zscore_threshold:
                detectors_triggered.append("zscore")
                explanations.append(z_exp)

            # 2. Isolation Forest (uses padded vec)
            if_score = self.isolation_forest.score(padded_vec)
            if if_score < self._iforest_threshold:
                detectors_triggered.append("isolation_forest")
                explanations.append(f"IF score={if_score:.3f}")

            # 3. LOF (uses padded vec)
            lof_score = self.lof.score(padded_vec)
            if lof_score < -1.0:
                detectors_triggered.append("lof")
                explanations.append(f"LOF score={lof_score:.3f}")

            # 4. Autoencoder (uses padded vec)
            if self.autoencoder.trained:
                ae_score = self.autoencoder.score(padded_vec)
                if ae_score > self.autoencoder._threshold:
                    detectors_triggered.append("autoencoder")
                    explanations.append(
                        f"AE recon_error={ae_score:.4f} > "
                        f"threshold={self.autoencoder._threshold:.4f}")

            # Ensemble: anomaly if 2+ detectors agree, or IF alone with
            # very low score
            is_anomaly = (len(detectors_triggered) >= 2
                          or if_score < self._iforest_threshold * 1.5)

            # Normalize score to 0-1 range
            raw_score = max(
                zscore / (self._zscore_threshold * 2),
                abs(min(if_score, 0)) / abs(self._iforest_threshold),
            ) if self._trained else 0.0
            anomaly_score = min(raw_score, 1.0)

            dev_info = readings_by_device.get(device_id, {})
            results.append(AnomalyResult(
                device_id=device_id,
                device_type=dev_info.get("type", ""),
                anomaly_score=round(anomaly_score, 3),
                is_anomaly=is_anomaly and len(detectors_triggered) > 0,
                detectors_triggered=detectors_triggered,
                explanation="; ".join(explanations) if explanations else "normal",
                readings=dev_info.get("readings", {}),
            ))

        return results

    def _list_trained_models(self) -> List[str]:
        models = []
        if self.baseline.trained:
            models.append("statistical_baseline")
        if self.isolation_forest.trained:
            models.append("isolation_forest")
        if self.lof.trained:
            models.append("lof")
        if self.autoencoder.trained:
            models.append("autoencoder")
        return models

    @property
    def trained(self) -> bool:
        return self._trained

    def training_summary(self) -> dict:
        return {
            "trained": self._trained,
            "total_samples": len(self._all_vectors),
            "devices_profiled": len(self._feature_history),
            "models_ready": self._list_trained_models(),
        }
