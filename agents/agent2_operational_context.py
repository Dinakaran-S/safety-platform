"""
AGENT 2 — Operational Context Agent

Responsibility: ingest permit-to-work logs, maintenance logs, shift schedules,
maintain real-time per-zone state, and compute a "context risk multiplier"
per zone. Hot-work + confined-space-type combinations carry a higher inherent
multiplier than routine electrical isolation, etc.

Output contract:
    ZoneContextEvent(
        zone_id, sim_hour, shift, handover,
        active_permit_types, active_maintenance_types,
        context_multiplier,   # >= 1.0, scales Agent 3's compound score
        combo_flag,           # bool: a known-dangerous permit combination is active
        contributing_notes,
    )
"""
from dataclasses import dataclass, field

from generator.context_generator import (
    DANGEROUS_PERMIT_COMBOS,
    PERMIT_RISK_WEIGHT,
    OperationalContextGenerator,
    PermitType,
)
from generator.plant_model import ZONE_BY_ID

HANDOVER_MULTIPLIER_BONUS = 0.25
MAINTENANCE_BREAKDOWN_BONUS = 0.3


@dataclass
class ZoneContextEvent:
    zone_id: str
    sim_hour: float
    shift: str
    handover: bool
    active_permit_types: list[str] = field(default_factory=list)
    active_permit_ids: list[str] = field(default_factory=list)
    active_maintenance_types: list[str] = field(default_factory=list)
    context_multiplier: float = 1.0
    combo_flag: bool = False
    has_operational_context: bool = False   # True iff a permit or maintenance activity is active (handover alone does NOT count)
    contributing_notes: list[str] = field(default_factory=list)


class OperationalContextAgent:
    """AGENT 2. Wraps an OperationalContextGenerator-compatible state source and
    emits a ZoneContextEvent per zone per tick."""

    def __init__(self, context_source: OperationalContextGenerator):
        self.source = context_source

    def _compute_multiplier(self, permit_types: list[PermitType], maint_types, handover: bool) -> tuple[float, bool, list[str]]:
        notes = []
        multiplier = 1.0

        # base contribution from each active permit's inherent risk weight
        for pt in permit_types:
            w = PERMIT_RISK_WEIGHT.get(pt, 0.1)
            multiplier += w
            notes.append(f"active permit type '{pt.value}' adds weight {w}")

        # dangerous combination bonus -- if more than one combo matches at once,
        # apply only the strongest one, not all of them multiplied together
        # (multiplying every match would double-count the same permits' risk
        # if two dangerous combos happen to share a permit type)
        combo_flag = False
        pset = frozenset(permit_types)
        matched_bonuses = []
        for combo, bonus in DANGEROUS_PERMIT_COMBOS.items():
            if combo.issubset(pset):
                matched_bonuses.append((combo, bonus))
        if matched_bonuses:
            combo, bonus = max(matched_bonuses, key=lambda cb: cb[1])
            multiplier *= bonus
            combo_flag = True
            notes.append(f"dangerous permit combination detected: {[c.value for c in combo]} -> x{bonus}"
                          + (f" (strongest of {len(matched_bonuses)} matching combos)" if len(matched_bonuses) > 1 else ""))

        # breakdown maintenance bonus (unplanned work is riskier than preventive)
        for m in maint_types:
            if m.value == "breakdown":
                multiplier += MAINTENANCE_BREAKDOWN_BONUS
                notes.append("breakdown maintenance in progress adds weight")

        if handover:
            multiplier += HANDOVER_MULTIPLIER_BONUS
            notes.append("shift handover window active, situational awareness risk elevated")

        return round(multiplier, 4), combo_flag, notes

    def process_tick(self, hour: float) -> dict[str, ZoneContextEvent]:
        snap = self.source.snapshot(hour)
        out = {}
        for zone_id, z in snap.items():
            permit_types = [p.permit_type for p in z["active_permits"]]
            maint_types = [m.maintenance_type for m in z["active_maintenance"]]
            multiplier, combo_flag, notes = self._compute_multiplier(permit_types, maint_types, z["handover"])
            out[zone_id] = ZoneContextEvent(
                zone_id=zone_id,
                sim_hour=round(hour, 3),
                shift=z["shift"],
                handover=z["handover"],
                active_permit_types=[p.value for p in permit_types],
                active_permit_ids=[p.permit_id for p in z["active_permits"]],
                active_maintenance_types=[m.value for m in maint_types],
                context_multiplier=multiplier,
                combo_flag=combo_flag,
                has_operational_context=bool(permit_types or maint_types),
                contributing_notes=notes,
            )
        return out


if __name__ == "__main__":
    gen = OperationalContextGenerator(seed=1)
    gen.issue_permit("Z3", PermitType.HOT_WORK, at_hour=10.0, duration_hours=2.0)
    gen.issue_permit("Z3", PermitType.CONFINED_SPACE_ENTRY, at_hour=10.2, duration_hours=1.5)
    agent = OperationalContextAgent(gen)
    ctx = agent.process_tick(10.5)
    print(ctx["Z3"])
    print(ctx["Z1"])
