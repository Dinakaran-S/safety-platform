"""
AGENT 3 — Compound Risk Correlation Agent (the core "brain")

Subscribes to Agent 1 (sensor anomaly scores) and Agent 2 (zone context) outputs.

Two-layer design:
  1. RULE LAYER (deterministic, auditable, fast) -- a hard gate that is evaluated
     FIRST and cannot be overridden by the learned layer:

        HARD INVARIANT: if a zone has NO corroborating operational context
        (context_multiplier <= 1.0, i.e. no active permit, no breakdown
        maintenance, no handover window) then severity is capped at LOW/INFO,
        no matter how high any single sensor's anomaly score is.

     This is the single most important credibility property of the system and
     is proven by tests/test_false_positive_invariant.py.

  2. LEARNED SCORING LAYER -- once the gate is passed (i.e. there IS
     corroborating context), a calibrated logistic regression combines
     engineered features (max anomaly score, count of anomalous sensors,
     context multiplier, combo_flag, handover) into a probability that is
     mapped to LOW/MEDIUM/HIGH severity. Coefficients are exposed directly
     for explainability (no SHAP dependency needed for a linear model --
     contribution = coefficient * feature_value, which is exact and exposed
     in RiskEvent.feature_attribution).

Output: RiskEvent with full provenance (contributing agent outputs, raw
values, feature attribution, calibrated probability, severity, estimated
lead time).
"""
import time
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from sklearn.linear_model import LogisticRegression

from agents.agent1_sensor_fusion import SensorAnomalyEvent
from agents.agent2_operational_context import ZoneContextEvent

FEATURE_NAMES = [
    "max_anomaly_score",
    "num_anomalous_sensors",
    "context_multiplier",
    "combo_flag",
    "handover",
    "compound_interaction",   # max_anomaly_score * (context_multiplier - 1.0) --
                              # lets the linear model actually represent "compound"
                              # risk (sensor anomaly AND elevated context together)
                              # instead of only approximating it via two separate
                              # linear terms.
]


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


ANOMALY_SCORE_THRESHOLD = 0.5  # a sensor counts as "anomalous" above this score


@dataclass
class RiskEvent:
    event_id: str
    zone_id: str
    sim_hour: float
    timestamp: float
    severity: str
    calibrated_probability: float
    features: dict
    feature_attribution: dict          # coefficient * value per feature
    contributing_sensor_events: list    # raw SensorAnomalyEvent dicts
    contributing_context: dict          # raw ZoneContextEvent dict
    gate_passed: bool                   # did it clear the hard invariant gate?
    gate_reason: str
    estimated_lead_time_minutes: float | None
    rule_trace: list = field(default_factory=list)


def _train_calibration_model(seed: int = 0) -> tuple[LogisticRegression, list[str]]:
    """
    Train the learned scoring layer on a synthetic labeled feature dataset.
    Labels simulate "this combination historically preceded an incident" (1)
    vs "this combination is benign" (0). This keeps the model interpretable
    (linear, 5 features, coefficients exposed) per the constraint to favor
    explainable logic over black-box ML.
    """
    rng = np.random.default_rng(seed)
    n = 4000
    X = np.zeros((n, len(FEATURE_NAMES)))
    y = np.zeros(n)

    for i in range(n):
        max_score = rng.uniform(0, 1)
        # num_anomalous_sensors must be logically consistent with max_score --
        # live, max_score is the max over a zone's sensors and num_anomalous_sensors
        # counts sensors >= ANOMALY_SCORE_THRESHOLD, so max_score >= threshold
        # IFF num_anomalous_sensors >= 1. The old version drew these independently,
        # producing ~10% of rows that can never occur live (e.g. max_score=0.9 with
        # zero anomalous sensors), which just added noise the model had to absorb.
        if max_score >= ANOMALY_SCORE_THRESHOLD:
            num_anom = rng.integers(1, 5)
        else:
            num_anom = 0
        ctx_mult = rng.uniform(1.0, 6.0)
        combo = rng.integers(0, 2)
        handover = rng.integers(0, 2)
        compound_interaction = max_score * (ctx_mult - 1.0)
        X[i] = [max_score, num_anom, ctx_mult, combo, handover, compound_interaction]

        # ground-truth synthetic incident rule: incidents happen when BOTH
        # sensor anomaly is real AND operational context is elevated --
        # this directly encodes the "compound risk" hypothesis we want the
        # model to learn, never from anomaly alone.
        incident_logit = (
            -6.0
            + 4.0 * max_score
            + 0.5 * num_anom
            + 1.1 * (ctx_mult - 1.0)
            + 1.5 * combo
            + 0.6 * handover
            + 3.0 * compound_interaction  # interaction term drives "compound"-ness
        )
        p = 1 / (1 + np.exp(-incident_logit))
        y[i] = rng.random() < p

    model = LogisticRegression(max_iter=1000)
    model.fit(X, y)
    return model, FEATURE_NAMES


class CompoundRiskCorrelationAgent:
    """AGENT 3."""

    def __init__(self, seed: int = 0):
        self.model, self.feature_names = _train_calibration_model(seed)
        self._event_counter = 0

    def _gate(self, ctx: ZoneContextEvent) -> tuple[bool, str]:
        """HARD INVARIANT gate. Returns (passed, reason).

        Note: a shift-handover window ALONE does not satisfy the gate -- it is
        a risk amplifier on top of genuine operational context (an active
        permit or breakdown maintenance), not a substitute for it. This keeps
        the gate's spirit intact: routine handovers must not, by themselves,
        turn an otherwise-single-factor sensor anomaly into a corroborated one.
        """
        if not ctx.has_operational_context:
            return False, (
                "No corroborating operational context (no active permit, no "
                "breakdown/preventive maintenance activity) -- severity hard-capped "
                "at LOW/INFO regardless of sensor anomaly score or handover state."
            )
        return True, "Corroborating operational context present (active permit or maintenance) -- learned layer evaluated."

    def _severity_from_probability(self, p: float) -> Severity:
        if p >= 0.75:
            return Severity.HIGH
        if p >= 0.45:
            return Severity.MEDIUM
        if p >= 0.2:
            return Severity.LOW
        return Severity.INFO

    def evaluate_zone(
        self,
        zone_id: str,
        sensor_events: list[SensorAnomalyEvent],
        ctx: ZoneContextEvent,
    ) -> RiskEvent:
        self._event_counter += 1
        zone_sensor_events = [e for e in sensor_events if e.zone_id == zone_id]
        anomalous = [e for e in zone_sensor_events if e.anomaly_score >= ANOMALY_SCORE_THRESHOLD]
        max_score = max((e.anomaly_score for e in zone_sensor_events), default=0.0)

        rule_trace = [
            f"Collected {len(zone_sensor_events)} sensor readings for zone {zone_id}; "
            f"{len(anomalous)} exceed anomaly threshold {ANOMALY_SCORE_THRESHOLD}.",
        ]

        gate_passed, gate_reason = self._gate(ctx)
        rule_trace.append(gate_reason)

        features = {
            "max_anomaly_score": max_score,
            "num_anomalous_sensors": len(anomalous),
            "context_multiplier": ctx.context_multiplier,
            "combo_flag": float(ctx.combo_flag),
            "handover": float(ctx.handover),
            "compound_interaction": max_score * (ctx.context_multiplier - 1.0),
        }

        if not gate_passed:
            # HARD INVARIANT enforced here -- no matter what the model would say,
            # we never even ask it. This makes the invariant provable by
            # construction, not just "usually true after training".
            severity = Severity.LOW if max_score >= ANOMALY_SCORE_THRESHOLD else Severity.INFO
            probability = 0.0
            attribution = {k: 0.0 for k in FEATURE_NAMES}
        else:
            X = np.array([[features[f] for f in FEATURE_NAMES]])
            probability = float(self.model.predict_proba(X)[0][1])
            severity = self._severity_from_probability(probability)
            coefs = self.model.coef_[0]
            attribution = {
                name: round(float(coefs[idx] * features[name]), 4)
                for idx, name in enumerate(FEATURE_NAMES)
            }
            rule_trace.append(
                f"Learned layer calibrated probability={probability:.3f} -> severity={severity.value}."
            )

        # crude lead-time estimate: higher probability + higher max_score => less time
        lead_time = None
        if severity in (Severity.MEDIUM, Severity.HIGH):
            lead_time = round(max(2.0, 45.0 * (1.0 - probability) * (1.0 - max_score * 0.5)), 1)

        return RiskEvent(
            event_id=f"RE-{int(time.time()*1000)}-{self._event_counter}",
            zone_id=zone_id,
            sim_hour=ctx.sim_hour,
            timestamp=time.time(),
            severity=severity.value,
            calibrated_probability=round(probability, 4),
            features=features,
            feature_attribution=attribution,
            contributing_sensor_events=[e.__dict__ for e in zone_sensor_events],
            contributing_context=ctx.__dict__,
            gate_passed=gate_passed,
            gate_reason=gate_reason,
            estimated_lead_time_minutes=lead_time,
            rule_trace=rule_trace,
        )

    def evaluate_tick(
        self,
        sensor_events: list[SensorAnomalyEvent],
        ctx_by_zone: dict[str, ZoneContextEvent],
    ) -> list[RiskEvent]:
        zones = {e.zone_id for e in sensor_events}
        return [self.evaluate_zone(z, sensor_events, ctx_by_zone[z]) for z in zones if z in ctx_by_zone]


if __name__ == "__main__":
    agent = CompoundRiskCorrelationAgent(seed=0)
    print("Trained. Coefficients:")
    for name, c in zip(FEATURE_NAMES, agent.model.coef_[0]):
        print(f"  {name}: {c:.4f}")
    print(f"  intercept: {agent.model.intercept_[0]:.4f}")
