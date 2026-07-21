"""
WORKER SAFETY AGENT

Converts worker states into safety intelligence:
- Identifies workers in high-risk zones
- Tracks PPE compliance, fatigue, time-in-zone
- Generates evacuation priorities when compound risk escalates
"""
from dataclasses import dataclass, field

from generator.equipment_worker_generator import WorkerState


@dataclass
class WorkerSafetyEvent:
    zone_id: str
    sim_hour: float
    workers_in_zone: list[dict]
    max_exposure_risk: float
    workers_at_risk: list[str]      # names of workers needing attention
    ppe_violations: list[str]       # names of workers without PPE
    evacuation_recommended: bool
    zone_worker_risk: float         # 0-1 aggregate risk from worker exposure


class WorkerSafetyAgent:
    """WORKER SAFETY AGENT."""

    def process_tick(self, worker_states: list[WorkerState], sim_hour: float,
                     high_severity_zones: set[str]) -> dict[str, WorkerSafetyEvent]:
        by_zone: dict[str, list[WorkerState]] = {}
        for w in worker_states:
            by_zone.setdefault(w.zone_id, []).append(w)

        out = {}
        for zone_id, workers in by_zone.items():
            max_exp = max((w.exposure_risk for w in workers), default=0.0)
            at_risk = [w.name for w in workers if w.status in ("at_risk", "danger")]
            ppe_viol = [w.name for w in workers if not w.ppe_compliant]
            evacuate = zone_id in high_severity_zones and bool(workers)

            # zone worker risk: max exposure amplified slightly by ppe violations
            zone_risk = min(1.0, max_exp + len(ppe_viol) * 0.05)

            out[zone_id] = WorkerSafetyEvent(
                zone_id=zone_id, sim_hour=sim_hour,
                workers_in_zone=[w.__dict__ for w in workers],
                max_exposure_risk=round(max_exp, 3),
                workers_at_risk=at_risk, ppe_violations=ppe_viol,
                evacuation_recommended=evacuate,
                zone_worker_risk=round(zone_risk, 3),
            )

        return out
