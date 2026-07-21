"""
AGENT 7 — Equipment Health Agent (predictive maintenance, real inference)

WHAT THIS AGENT DOES AND WHY IT'S "REAL AI":
Real condition-based-maintenance systems never get to measure wear
directly -- you can't stick a sensor inside a valve and read "62% worn
out". All you ever get is indirect symptoms: vibration, temperature,
how many hours it's been running. The job of the model is to learn the
relationship between those symptoms and the equipment's actual health,
and then predict health for equipment it's watching live.

That's exactly what this agent does:
  1. At startup, it builds a synthetic training set per equipment TYPE
     (valve, compressor, pump, tank, heat_exchanger): "if wear were X,
     what would vibration/temperature/operating_hours typically look
     like?" -- using the same physical relationship the simulator uses,
     the same way a real maintenance team would use historical
     failure records to build a training set.
  2. It fits a small RandomForestRegressor per equipment type on that
     synthetic data: features = (vibration, temperature, operating_hours)
     -> target = health_score (0-100).
  3. On every live tick, it feeds the equipment's CURRENT raw telemetry
     into that model and gets back a predicted health score. It never
     sees the generator's true_wear_index -- that field only exists so
     we can sanity-check the model's predictions, exactly like a
     validation set.

Failure probability, remaining-useful-life, and status bucket are then
derived from the PREDICTED health with simple, transparent formulas
(this part is intentionally deterministic -- same philosophy as Agent 3's
rule gate: let the model do the genuinely hard inference step, keep the
downstream business logic auditable).
"""
import math
import random

import numpy as np
from dataclasses import dataclass, field
from sklearn.ensemble import RandomForestRegressor

from generator.equipment_worker_generator import EquipmentState, BASE_TELEMETRY


@dataclass
class EquipmentHealthEvent:
    zone_id: str
    sim_hour: float
    equipment_states: list[dict]
    zone_equipment_risk: float       # 0-1, worst-case from equipment in zone
    critical_equipment: list[str]    # IDs of equipment in critical/offline state
    co_degradation_flag: bool        # multiple equipment degrading in same zone
    zone_notes: list[str]


def _synthesize_training_row(equipment_type: str, rng: random.Random):
    """One synthetic (features, label) pair for a given equipment type.

    Mirrors the generator's real physical relationship between wear and
    telemetry, but is built independently here -- this is the agent's own
    training data, standing in for what would normally be months of
    historical sensor + maintenance-log data in a real plant."""
    base = BASE_TELEMETRY[equipment_type]
    wear = rng.uniform(0.0, 0.99)
    zone_anom = rng.uniform(0.0, 1.0)  # equipment sees a range of operating conditions historically

    vibration = base["vib"] * (1 + 3.5 * wear) + rng.gauss(0, base["vib"] * 0.08)
    temperature = base["temp"] + wear * 35 + zone_anom * 6 + rng.gauss(0, 1.5)
    operating_hours = rng.uniform(0, 4000)

    health_label = max(0.0, min(100.0, 100 * (1 - wear) - zone_anom * 4))
    features = [max(0.0, vibration), temperature, operating_hours]
    return features, health_label


class EquipmentHealthAgent:
    """AGENT 7 — trains one small regressor per equipment type at startup,
    then does genuine live inference from telemetry every tick."""

    N_TRAIN_SAMPLES = 600

    def __init__(self, seed: int = 11):
        rng = random.Random(seed)
        self.models: dict[str, RandomForestRegressor] = {}
        for equipment_type in BASE_TELEMETRY:
            X, y = [], []
            for _ in range(self.N_TRAIN_SAMPLES):
                feats, label = _synthesize_training_row(equipment_type, rng)
                X.append(feats)
                y.append(label)
            model = RandomForestRegressor(
                n_estimators=60, max_depth=6, random_state=seed, n_jobs=1
            )
            model.fit(np.array(X), np.array(y))
            self.models[equipment_type] = model

    def _predict_health(self, eq: EquipmentState) -> tuple[float, float]:
        """Returns (predicted_health_0_100, confidence_0_1).

        Confidence comes from how much the individual trees in the forest
        agree with each other -- if 60 trees trained on different subsets
        of the data all land close to the same answer, we trust it more
        than if they're scattered."""
        model = self.models.get(eq.equipment_type)
        if model is None:
            return 100.0, 0.0
        feats = np.array([[eq.vibration_mm_s, eq.temperature_c, eq.operating_hours]])
        tree_preds = np.array([t.predict(feats)[0] for t in model.estimators_])
        health = float(np.clip(tree_preds.mean(), 0.0, 100.0))
        disagreement = float(tree_preds.std())
        confidence = float(np.clip(1.0 - disagreement / 25.0, 0.0, 1.0))
        return health, confidence

    @staticmethod
    def _failure_probability(health: float) -> float:
        # logistic: near 0 when healthy, climbs sharply below health=40
        return 1 / (1 + math.exp((health - 40) * 0.12))

    @staticmethod
    def _remaining_useful_life_hrs(health: float):
        wear_equiv = max(0.001, (100.0 - health) / 100.0)
        if wear_equiv >= 0.95:
            return 0.0
        return round((1.0 - wear_equiv) / wear_equiv * 200, 1)

    @staticmethod
    def _status_from_health(health: float) -> str:
        if health >= 75:
            return "healthy"
        elif health >= 50:
            return "degraded"
        elif health >= 25:
            return "critical"
        return "offline"

    def process_tick(self, equipment_states, sim_hour: float):
        by_zone = {}
        for eq in equipment_states:
            by_zone.setdefault(eq.zone_id, []).append(eq)

        out = {}
        for zone_id, eqs in by_zone.items():
            enriched = []
            for eq in eqs:
                health, confidence = self._predict_health(eq)
                fp = self._failure_probability(health)
                rul = self._remaining_useful_life_hrs(health)
                status = self._status_from_health(health)

                d = dict(eq.__dict__)  # raw telemetry + true_wear_index (kept for validation only)
                d.update({
                    "health_score": round(health, 1),
                    "failure_probability": round(fp, 4),
                    "remaining_useful_life_hrs": rul,
                    "status": status,
                    "health_confidence": round(confidence, 3),
                })
                enriched.append(d)

            critical = [e["equipment_id"] for e in enriched if e["status"] in ("critical", "offline")]
            degraded = [e["equipment_id"] for e in enriched if e["status"] == "degraded"]
            co_flag = len(critical) + len(degraded) >= 2

            zone_risk = max((e["failure_probability"] for e in enriched), default=0.0)
            notes = []
            if critical:
                notes.append(f"Equipment in critical/offline state: {critical}")
            if co_flag:
                notes.append("Multiple equipment items degrading simultaneously — elevated zone equipment risk.")
            if zone_risk > 0.5:
                notes.append(f"Zone equipment failure probability exceeds 50% (p={zone_risk:.2f}).")

            out[zone_id] = EquipmentHealthEvent(
                zone_id=zone_id, sim_hour=sim_hour,
                equipment_states=enriched,
                zone_equipment_risk=round(zone_risk, 4),
                critical_equipment=critical,
                co_degradation_flag=co_flag,
                zone_notes=notes,
            )
        return out


if __name__ == "__main__":
    from generator.equipment_worker_generator import EquipmentWorkerGenerator

    gen = EquipmentWorkerGenerator(seed=3)
    agent = EquipmentHealthAgent()

    for tick in range(0, 400, 50):
        states = None
        for _ in range(50):
            states = gen.equipment_tick(10.0)
        events = agent.process_tick(states, sim_hour=10.0)
        eq = events["Z1"].equipment_states[0]
        print(f"tick~{tick}: true_wear={eq['true_wear_index']:.3f} "
              f"predicted_health={eq['health_score']:.1f} confidence={eq['health_confidence']:.2f} "
              f"status={eq['status']}")
