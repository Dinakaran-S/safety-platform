"""
AGENT 1 — Sensor Fusion Agent

Responsibility: ingest raw per-zone sensor readings, maintain a rolling
shift-aware statistical baseline per sensor, and run unsupervised anomaly
detection (Isolation Forest) per sensor stream to output a continuous
anomaly score in [0, 1] -- NOT a fixed threshold.

Output contract (see AGENT_CONTRACTS.md):
    SensorAnomalyEvent(
        sensor_id, zone_id, sensor_type, timestamp, sim_hour,
        value, baseline_mean, baseline_std, z_score,
        anomaly_score,      # 0-1, from Isolation Forest decision function
        shift, handover,
    )
published onto the shared event bus for Agent 3 to consume.
"""
import collections
from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest

from generator.plant_model import ALL_SENSORS, SENSOR_BY_ID
from generator.sensor_stream import Reading, shift_for_hour, Shift


@dataclass
class SensorAnomalyEvent:
    sensor_id: str
    zone_id: str
    sensor_type: str
    timestamp: float
    sim_hour: float
    value: float
    baseline_mean: float
    baseline_std: float
    z_score: float
    anomaly_score: float  # 0 (normal) - 1 (highly anomalous)
    shift: str
    handover: bool
    model_trained: bool = False     # False while running on the cold-start z-score fallback
    training_pool_size: int = 0     # how many "clean" samples the live model was trained on


class _SensorState:
    """Per-sensor, per-shift rolling window + trained Isolation Forest model.

    Fix vs the original version: the model is trained on a *clean* pool of
    readings that were themselves scored as normal, not on the raw rolling
    window. This stops a sustained real anomaly from getting "voted in" as
    normal the next time the model retrains (self-poisoning). The anomaly
    score is also now calibrated against that same clean pool's score
    distribution (percentile rank) instead of an arbitrary fixed formula, so
    scores mean roughly the same thing across sensors of very different
    scales.
    """

    WINDOW = 300           # readings kept per shift bucket (for baseline stats)
    MIN_TRAIN = 30         # minimum clean samples before IF is trained
    RETRAIN_EVERY = 40     # retrain cadence (ticks) once trained
    TRUST_THRESHOLD = 0.4  # a reading only enters the training pool if its own score is below this

    def __init__(self, sensor_id: str):
        self.sensor_id = sensor_id
        # separate windows per shift -> models "seasonal/shift-aware" baseline
        self.windows: dict[str, collections.deque] = {
            s.value: collections.deque(maxlen=self.WINDOW) for s in Shift
        }
        # the "clean" pool: only readings that scored as normal get in here
        self.clean_windows: dict[str, collections.deque] = {
            s.value: collections.deque(maxlen=self.WINDOW) for s in Shift
        }
        self.models: dict[str, IsolationForest] = {}
        # sorted decision_function scores from the training pool, used to
        # convert a new raw score into a percentile-based anomaly score
        self.train_scores_sorted: dict[str, np.ndarray] = {}
        self.since_retrain: dict[str, int] = {s.value: 0 for s in Shift}

    def _maybe_train(self, shift: str):
        clean_window = self.clean_windows[shift]
        if len(clean_window) < self.MIN_TRAIN:
            return
        if shift not in self.models or self.since_retrain[shift] >= self.RETRAIN_EVERY:
            X = np.array(clean_window).reshape(-1, 1)
            model = IsolationForest(n_estimators=40, contamination=0.05, random_state=42, n_jobs=1)
            model.fit(X)
            self.models[shift] = model
            # cache this fit's score distribution for percentile calibration
            self.train_scores_sorted[shift] = np.sort(model.decision_function(X))
            self.since_retrain[shift] = 0

    def _percentile_anomaly_score(self, shift: str, raw: float) -> float:
        """Turn a raw decision_function value into a 0-1 anomaly score using
        this sensor's own training-pool score distribution, so a score of
        e.g. 0.8 means roughly the same thing (top ~20% most unusual) no
        matter what units/scale this particular sensor reads in."""
        scores = self.train_scores_sorted[shift]
        percentile = np.searchsorted(scores, raw) / len(scores)  # 0=most anomalous end, 1=most normal end
        return float(np.clip(1.0 - percentile, 0.0, 1.0))

    def update_and_score(self, value: float, shift: str) -> tuple[float, float, float, float, bool, int]:
        """Add value to the rolling window, return
        (mean, std, z_score, anomaly_score, model_trained, training_pool_size)."""
        window = self.windows[shift]
        window.append(value)
        self.since_retrain[shift] += 1

        if len(window) >= 2:
            mean = float(np.mean(window))
            std = float(np.std(window)) or 1e-6
        else:
            mean, std = value, 1e-6

        z = (value - mean) / std

        # score this reading against the CURRENT model (before deciding
        # whether it's clean enough to feed into the next training round)
        model_trained = shift in self.models
        if model_trained:
            raw = self.models[shift].decision_function(np.array([[value]]))[0]
            anomaly_score = self._percentile_anomaly_score(shift, raw)
        else:
            # cold start fallback: derive anomaly score from z-score until IF trains
            anomaly_score = float(np.clip((abs(z) - 2.0) / 6.0, 0.0, 1.0))

        # only let this reading into the training pool if it looks normal,
        # OR we're still bootstrapping and have no reliable score yet
        clean_window = self.clean_windows[shift]
        if anomaly_score < self.TRUST_THRESHOLD or len(clean_window) < self.MIN_TRAIN:
            clean_window.append(value)

        self._maybe_train(shift)

        return mean, std, z, anomaly_score, model_trained, len(clean_window)


class SensorFusionAgent:
    """AGENT 1. Call `process_tick(readings)` once per generator tick."""

    def __init__(self):
        self._state: dict[str, _SensorState] = {s.sensor_id: _SensorState(s.sensor_id) for s in ALL_SENSORS}

    def process_reading(self, r: Reading) -> SensorAnomalyEvent:
        state = self._state[r.sensor_id]
        mean, std, z, score, model_trained, pool_size = state.update_and_score(r.value, r.shift)
        # handover windows inherently noisier -- dampen score slightly to avoid
        # flagging expected transient noise as a strong anomaly (still scores it,
        # just not maximal, since z-score-based fallback and IF both already
        # incorporate the wider handover std from the generator's own baseline).
        return SensorAnomalyEvent(
            sensor_id=r.sensor_id,
            zone_id=r.zone_id,
            sensor_type=r.sensor_type,
            timestamp=r.timestamp,
            sim_hour=r.sim_hour,
            value=r.value,
            baseline_mean=round(mean, 4),
            baseline_std=round(std, 4),
            z_score=round(z, 3),
            anomaly_score=round(score, 4),
            shift=r.shift,
            handover=r.handover,
            model_trained=model_trained,
            training_pool_size=pool_size,
        )

    def process_tick(self, readings: list[Reading]) -> list[SensorAnomalyEvent]:
        return [self.process_reading(r) for r in readings]


if __name__ == "__main__":
    from generator.sensor_stream import SensorStreamGenerator

    gen = SensorStreamGenerator(start_sim_hour=8.0, seed=7)
    agent = SensorFusionAgent()

    # warm up baselines
    for _ in range(60):
        agent.process_tick(gen.tick())

    # inject an anomaly and confirm score rises
    gen.inject_anomaly("Z3-gas_h2s-0", magnitude_std=7, duration_ticks=5, tag="demo")
    for i in range(6):
        events = agent.process_tick(gen.tick())
        ev = next(e for e in events if e.sensor_id == "Z3-gas_h2s-0")
        print(f"tick {i}: value={ev.value} z={ev.z_score} anomaly_score={ev.anomaly_score}")
