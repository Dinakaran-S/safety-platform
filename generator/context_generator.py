"""
Generates operational context events: permit-to-work logs, maintenance activity,
and shift schedule state. Consumed by Agent 2 (Operational Context Agent).
"""
import random
import uuid
from dataclasses import dataclass, field
from enum import Enum

from generator.plant_model import ZONE_BY_ID
from generator.sensor_stream import shift_for_hour, is_handover_window


class PermitType(str, Enum):
    HOT_WORK = "hot_work"
    CONFINED_SPACE_ENTRY = "confined_space_entry"
    ELECTRICAL_ISOLATION = "electrical_isolation"
    LIFTING = "lifting"
    EXCAVATION = "excavation"
    ROUTINE_COLD_WORK = "routine_cold_work"


class MaintenanceType(str, Enum):
    PREVENTIVE = "preventive"
    BREAKDOWN = "breakdown"
    INSPECTION = "inspection"


# Inherent risk weight per permit type (used in context multiplier calc)
PERMIT_RISK_WEIGHT = {
    PermitType.HOT_WORK: 0.9,
    PermitType.CONFINED_SPACE_ENTRY: 0.85,
    PermitType.ELECTRICAL_ISOLATION: 0.4,
    PermitType.LIFTING: 0.35,
    PermitType.EXCAVATION: 0.3,
    PermitType.ROUTINE_COLD_WORK: 0.1,
}

# Compound combinations that are known to be especially dangerous together
# (used by Agent 2 to flag "elevated context" even before Agent 3 runs correlation)
DANGEROUS_PERMIT_COMBOS = {
    frozenset({PermitType.HOT_WORK, PermitType.CONFINED_SPACE_ENTRY}): 1.8,
}


@dataclass
class Permit:
    permit_id: str
    permit_type: PermitType
    zone_id: str
    issued_at_hour: float
    duration_hours: float
    active: bool = True

    def is_active_at(self, hour: float, day_offset: int = 0) -> bool:
        # simple same-day window check (demo-scale, not crossing midnight)
        return self.issued_at_hour <= hour <= self.issued_at_hour + self.duration_hours


@dataclass
class MaintenanceActivity:
    activity_id: str
    maintenance_type: MaintenanceType
    zone_id: str
    started_at_hour: float
    duration_hours: float

    def is_active_at(self, hour: float) -> bool:
        return self.started_at_hour <= hour <= self.started_at_hour + self.duration_hours


class OperationalContextGenerator:
    """
    Maintains the live state of permits/maintenance and exposes a snapshot
    per zone at a given simulated hour. Also supports scripted injection
    for demo scenarios.
    """

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.permits: list[Permit] = []
        self.maintenance: list[MaintenanceActivity] = []

    def issue_permit(self, zone_id: str, permit_type: PermitType, at_hour: float, duration_hours: float = 2.0) -> Permit:
        p = Permit(
            permit_id=f"P-{uuid.uuid4().hex[:6].upper()}",
            permit_type=permit_type,
            zone_id=zone_id,
            issued_at_hour=at_hour,
            duration_hours=duration_hours,
        )
        self.permits.append(p)
        return p

    def start_maintenance(self, zone_id: str, m_type: MaintenanceType, at_hour: float, duration_hours: float = 1.5) -> MaintenanceActivity:
        m = MaintenanceActivity(
            activity_id=f"M-{uuid.uuid4().hex[:6].upper()}",
            maintenance_type=m_type,
            zone_id=zone_id,
            started_at_hour=at_hour,
            duration_hours=duration_hours,
        )
        self.maintenance.append(m)
        return m

    def random_background_activity(self, hour: float, p_permit: float = 0.02, p_maint: float = 0.015):
        """Call once per tick to sprinkle low-rate background permits/maintenance
        across the plant, so the context layer isn't empty between scripted scenarios."""
        if self.rng.random() < p_permit:
            zone = self.rng.choice(list(ZONE_BY_ID.values()))
            ptype = self.rng.choices(
                list(PermitType),
                weights=[0.15, 0.1, 0.25, 0.2, 0.1, 0.2],
            )[0]
            self.issue_permit(zone.zone_id, ptype, hour, duration_hours=self.rng.uniform(0.5, 3.0))
        if self.rng.random() < p_maint:
            zone = self.rng.choice(list(ZONE_BY_ID.values()))
            mtype = self.rng.choice(list(MaintenanceType))
            self.start_maintenance(zone.zone_id, mtype, hour, duration_hours=self.rng.uniform(0.5, 2.0))

    def snapshot(self, hour: float) -> dict:
        """Return zone_id -> {active_permits, active_maintenance, shift, handover}."""
        out = {}
        shift = shift_for_hour(hour)
        handover = is_handover_window(hour)
        for zone_id in ZONE_BY_ID:
            active_permits = [p for p in self.permits if p.zone_id == zone_id and p.is_active_at(hour)]
            active_maint = [m for m in self.maintenance if m.zone_id == zone_id and m.is_active_at(hour)]
            out[zone_id] = {
                "zone_id": zone_id,
                "active_permits": active_permits,
                "active_maintenance": active_maint,
                "shift": shift.value,
                "handover": handover,
            }
        return out


if __name__ == "__main__":
    gen = OperationalContextGenerator(seed=1)
    gen.issue_permit("Z3", PermitType.HOT_WORK, at_hour=10.0, duration_hours=2.0)
    gen.issue_permit("Z3", PermitType.CONFINED_SPACE_ENTRY, at_hour=10.2, duration_hours=1.5)
    snap = gen.snapshot(10.5)
    print(snap["Z3"])
