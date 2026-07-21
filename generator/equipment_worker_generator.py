"""
Equipment and worker state generator.

Produces per-tick state for:
  - Equipment: valves, compressors, pumps, tanks (health score, wear, failure prob)
  - Workers: location, PPE status, time-in-zone, fatigue index
"""
import math
import random
import uuid
from dataclasses import dataclass, field
from typing import Optional

from generator.plant_model import ZONE_BY_ID


# ─────────────────────────────────────────────────────────────
# Equipment model
# ─────────────────────────────────────────────────────────────

EQUIPMENT_CATALOG = [
    {"id": "V-101", "name": "Feed Control Valve", "type": "valve", "zone": "Z1"},
    {"id": "V-102", "name": "Pressure Relief Valve", "type": "valve", "zone": "Z3"},
    {"id": "V-103", "name": "Isolation Valve A", "type": "valve", "zone": "Z4"},
    {"id": "V-104", "name": "Isolation Valve B", "type": "valve", "zone": "Z6"},
    {"id": "P-201", "name": "Compressor A", "type": "compressor", "zone": "Z1"},
    {"id": "P-202", "name": "Compressor B", "type": "compressor", "zone": "Z6"},
    {"id": "PU-301", "name": "Cooling Pump", "type": "pump", "zone": "Z7"},
    {"id": "PU-302", "name": "Feed Pump", "type": "pump", "zone": "Z4"},
    {"id": "T-401", "name": "Crude Storage Tank 1", "type": "tank", "zone": "Z2"},
    {"id": "T-402", "name": "Crude Storage Tank 2", "type": "tank", "zone": "Z2"},
    {"id": "HX-501", "name": "Heat Exchanger A", "type": "heat_exchanger", "zone": "Z4"},
    {"id": "HX-502", "name": "Heat Exchanger B", "type": "heat_exchanger", "zone": "Z6"},
]

# Baseline vibration (mm/s) and temperature (C) for a brand-new, healthy unit
# of each equipment type. Real condition-based-maintenance systems use
# exactly these two signal families (vibration + thermal) as the primary
# indirect indicators of mechanical wear.
BASE_TELEMETRY = {
    "valve":          {"vib": 1.5, "temp": 45},
    "compressor":     {"vib": 4.0, "temp": 70},
    "pump":           {"vib": 3.0, "temp": 55},
    "tank":           {"vib": 0.5, "temp": 40},
    "heat_exchanger": {"vib": 1.0, "temp": 85},
}


@dataclass
class EquipmentState:
    equipment_id: str
    name: str
    equipment_type: str
    zone_id: str
    vibration_mm_s: float        # raw telemetry -- what a real sensor would report
    temperature_c: float         # raw telemetry
    operating_hours: float       # raw telemetry (cumulative run time)
    true_wear_index: float       # 0-1 hidden ground truth -- NOT visible to Agent 7,
                                  # kept only so we can show "predicted vs actual" for validation


@dataclass
class WorkerState:
    worker_id: str
    name: str
    zone_id: str
    ppe_compliant: bool
    time_in_zone_minutes: float
    fatigue_index: float          # 0-1; rises over shift duration
    proximity_to_hazard: float    # 0-1
    exposure_risk: float          # 0-1 compound
    status: str                   # "safe"|"at_risk"|"danger"


class EquipmentWorkerGenerator:
    """Produces per-tick equipment and worker states."""

    WORKER_NAMES = [
        "R. Sharma", "A. Patel", "K. Singh", "M. Reddy",
        "S. Kumar", "P. Nair", "D. Verma", "J. Iyer",
    ]

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self.tick = 0

        # initialise equipment wear (starts low, slowly increases)
        self._wear: dict[str, float] = {e["id"]: self.rng.uniform(0.02, 0.18) for e in EQUIPMENT_CATALOG}
        # active zone anomalies injected externally by orchestrator
        self._zone_anomaly: dict[str, float] = {z: 0.0 for z in ZONE_BY_ID}

        # stable worker assignments (change only on shift boundary)
        zones_with_work = ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Z8"]
        self._worker_zones: dict[str, str] = {
            w: self.rng.choice(zones_with_work) for w in self.WORKER_NAMES
        }
        self._worker_entry_tick: dict[str, int] = {w: 0 for w in self.WORKER_NAMES}
        self._ppe: dict[str, bool] = {w: self.rng.random() > 0.12 for w in self.WORKER_NAMES}

    def set_zone_anomaly(self, zone_id: str, score: float):
        self._zone_anomaly[zone_id] = score

    def equipment_tick(self, sim_hour: float) -> list[EquipmentState]:
        self.tick += 1
        states = []
        for eq in EQUIPMENT_CATALOG:
            eid = eq["id"]
            zone = eq["zone"]
            base = BASE_TELEMETRY[eq["type"]]

            # slowly degrade true wear (hidden), slightly faster if zone is anomalous
            drift = self.rng.gauss(0.0002, 0.00005)
            zone_anom = self._zone_anomaly.get(zone, 0.0)
            drift += zone_anom * 0.001
            self._wear[eid] = min(0.99, self._wear[eid] + drift)
            wear = self._wear[eid]

            # raw telemetry: noisy, indirect signals that CORRELATE with wear
            # but don't reveal it directly -- this is what a real sensor gives you
            vibration = base["vib"] * (1 + 3.5 * wear) + self.rng.gauss(0, base["vib"] * 0.08)
            temperature = base["temp"] + wear * 35 + zone_anom * 6 + self.rng.gauss(0, 1.5)
            operating_hours = round(self.tick * 0.1, 1)  # demo-scaled cumulative run time

            states.append(EquipmentState(
                equipment_id=eid, name=eq["name"], equipment_type=eq["type"],
                zone_id=zone,
                vibration_mm_s=round(max(0.0, vibration), 3),
                temperature_c=round(temperature, 2),
                operating_hours=operating_hours,
                true_wear_index=round(wear, 4),
            ))
        return states

    def worker_tick(self, sim_hour: float, high_risk_zones: set[str]) -> list[WorkerState]:
        # reshuffle all workers near shift boundaries (06, 14, 22)
        for boundary in (6.0, 14.0, 22.0):
            if abs((sim_hour % 24) - boundary) < 0.08:
                for w in self.WORKER_NAMES:
                    new_zone = self.rng.choice(["Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Z8"])
                    self._worker_zones[w] = new_zone
                    self._worker_entry_tick[w] = self.tick
                    self._ppe[w] = self.rng.random() > 0.12
                break

        # Mid-shift: small chance any worker moves zones (~every 5-10 min sim time)
        # This makes the digital twin feel alive between shift boundaries
        for w in self.WORKER_NAMES:
            if self.rng.random() < 0.004:   # ~0.4% per tick ≈ move every ~4 sim-minutes
                zones = ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Z8"]
                self._worker_zones[w] = self.rng.choice(zones)
                self._worker_entry_tick[w] = self.tick

        # shift progress 0-1 (within current 8h shift)
        shift_progress = ((sim_hour % 8) / 8.0)
        states = []
        for w in self.WORKER_NAMES:
            zone = self._worker_zones[w]
            time_in_zone = (self.tick - self._worker_entry_tick[w]) * 0.1  # minutes (demo-scaled)
            fatigue = min(1.0, shift_progress * 0.7 + time_in_zone / 480 * 0.3)
            prox = 1.0 if zone in high_risk_zones else self.rng.uniform(0.0, 0.2)
            ppe = self._ppe[w]
            zone_anom = self._zone_anomaly.get(zone, 0.0)
            exposure = min(1.0, prox * 0.6 + zone_anom * 0.3 + (0.0 if ppe else 0.15) + fatigue * 0.05)

            if exposure >= 0.7:
                status = "danger"
            elif exposure >= 0.35:
                status = "at_risk"
            else:
                status = "safe"

            states.append(WorkerState(
                worker_id=f"W-{w[:2].upper()}", name=w, zone_id=zone,
                ppe_compliant=ppe, time_in_zone_minutes=round(time_in_zone, 1),
                fatigue_index=round(fatigue, 3), proximity_to_hazard=round(prox, 3),
                exposure_risk=round(exposure, 3), status=status,
            ))
        return states


if __name__ == "__main__":
    gen = EquipmentWorkerGenerator(seed=1)
    eq = gen.equipment_tick(10.0)
    print("Equipment:", [(e.equipment_id, e.health_score, e.status) for e in eq[:4]])
    workers = gen.worker_tick(10.0, high_risk_zones={"Z3"})
    print("Workers:", [(w.name, w.zone_id, w.status) for w in workers[:4]])
