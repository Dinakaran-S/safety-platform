"""
PREDICTIVE SIMULATION AGENT

The biggest differentiator: instead of showing current risk, this agent
simulates the FUTURE plant state at 5, 10, 15, and 30 minute horizons.

Model:
- If the current trajectory (anomaly trend + active context) continues
  unchanged, how does compound risk evolve?
- Provides "do-nothing" vs "recommended-action" comparison
- Estimates time to critical threshold
- Generates financial impact projections

Design: deliberately interpretable — every projection is built from
first-principles trajectory extrapolation, not a black-box model.
Judges can understand and verify every number.
"""
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from agents.agent3_compound_risk import RiskEvent


# ── Financial impact parameters (demo-scale for Indian industrial context) ──
PRODUCTION_LOSS_PER_HOUR_INR = 800_000   # ₹8 lakh/hr production loss
DOWNTIME_REPAIR_COST_INR = 2_500_000     # ₹25 lakh one-time repair if incident
REGULATORY_FINE_INR = 1_200_000          # ₹12 lakh estimated OISD fine
ENVIRONMENTAL_DAMAGE_INR = 500_000       # ₹5 lakh environmental remediation
WORKERS_AT_RISK_THRESHOLD = 0.6


@dataclass
class TimeHorizonProjection:
    minutes_ahead: int
    projected_probability: float
    projected_severity: str       # INFO / LOW / MEDIUM / HIGH / CRITICAL
    gas_spread_radius: float      # notional spread (0-1 of zone width)
    workers_potentially_exposed: int
    action_options: list[dict]    # [{"action": ..., "projected_prob_after": ...}]


@dataclass
class FinancialImpact:
    production_loss_inr: int
    repair_cost_inr: int
    regulatory_fine_inr: int
    environmental_damage_inr: int
    total_loss_inr: int
    downtime_days: float
    lives_at_risk: int
    loss_prevented_if_acted_now_inr: int


@dataclass
class PredictiveSimulationResult:
    zone_id: str
    sim_hour: float
    current_probability: float
    time_to_critical_minutes: Optional[float]      # None if already critical / never critical
    trajectory: str                                  # "stable" | "rising" | "falling"
    projections: list[TimeHorizonProjection]
    financial_impact: FinancialImpact
    recommended_action_summary: str


_HORIZONS = [5, 10, 15, 30]


class PredictiveSimulationAgent:
    """PREDICTIVE SIMULATION AGENT."""

    def __init__(self):
        # keep a short history of risk scores per zone to compute trend
        self._history: dict[str, list[float]] = {}

    def _trend(self, zone_id: str, current_p: float) -> float:
        """Returns probability change per minute (positive = rising risk)."""
        hist = self._history.setdefault(zone_id, deque(maxlen=20))
        hist.append(current_p)
        if len(hist) < 3:
            return 0.0
        # linear slope over recent window (simple diff)
        n = min(len(hist), 10)
        recent = hist[-n:]
        slope = (recent[-1] - recent[0]) / max(n - 1, 1)
        # slope is per-tick; convert to per-minute (each tick ≈ 0.1 sim-min in demo)
        return slope * 10

    def _project_probability(self, current_p: float, trend_per_min: float, minutes: int,
                              context_multiplier: float) -> float:
        """Extrapolate risk forward. Non-linear: risk acceleration increases with current_p."""
        acceleration = 1.0 + current_p * 0.8 * (context_multiplier - 1.0)
        projected = current_p + trend_per_min * minutes * acceleration
        # risk is bounded [0, 1] and has a ceiling effect near 1
        return float(min(1.0, max(0.0, projected)))

    def _severity_label(self, p: float) -> str:
        if p >= 0.90:
            return "CRITICAL"
        if p >= 0.75:
            return "HIGH"
        if p >= 0.45:
            return "MEDIUM"
        if p >= 0.20:
            return "LOW"
        return "INFO"

    def _time_to_critical(self, current_p: float, trend: float, ctx: float) -> Optional[float]:
        if current_p >= 0.90:
            return 0.0
        if trend <= 0.0:
            return None  # falling or stable, won't self-escalate
        # solve for t: project(t) = 0.90
        for t in range(1, 61):
            if self._project_probability(current_p, trend, t, ctx) >= 0.90:
                return float(t)
        return None

    def _action_options(self, zone_id: str, current_p: float, ctx_mult: float,
                        combo_flag: bool) -> list[dict]:
        # compute all options' projected outcomes FIRST, then decide the label --
        # never assume which mitigation wins, since that depends on ctx_mult/combo_flag
        p_after_permits = max(0.0, current_p - 0.4 * (ctx_mult / 3.0) - (0.15 if combo_flag else 0.0))
        p_after_evac = max(0.05, current_p - 0.15)
        p_do_nothing = min(1.0, current_p * 1.25)

        mitigations = [
            {"action": "Suspend all active permits + halt hot work",
             "projected_probability": round(p_after_permits, 3)},
            {"action": "Evacuate non-essential personnel only",
             "projected_probability": round(p_after_evac, 3)},
        ]
        # whichever mitigation actually achieves the lower projected risk gets
        # labeled "Recommended" -- the other is "Partial mitigation"
        mitigations.sort(key=lambda o: o["projected_probability"])
        mitigations[0]["label"] = "Recommended"
        mitigations[1]["label"] = "Partial mitigation"

        mitigations.append({
            "action": "No action taken",
            "projected_probability": round(p_do_nothing, 3),
            "label": "Do nothing",
        })
        return mitigations

    def _financial_impact(self, probability: float, n_workers: int) -> FinancialImpact:
        # Expected-value financial impact scaled by probability
        expected_hours = max(1.0, 8.0 * probability)
        prod_loss = int(PRODUCTION_LOSS_PER_HOUR_INR * expected_hours * probability)
        repair = int(REPAIR_COST_INR * probability) if (REPAIR_COST_INR := DOWNTIME_REPAIR_COST_INR) else 0
        fine = int(REGULATORY_FINE_INR * probability)
        env = int(ENVIRONMENTAL_DAMAGE_INR * probability)
        total = prod_loss + repair + fine + env
        downtime = round(expected_hours / 24.0, 1)
        lives = min(n_workers, int(n_workers * probability + 0.5))
        prevented = int(total * 0.85)  # if action taken now, ~85% of loss is avoidable
        return FinancialImpact(
            production_loss_inr=prod_loss, repair_cost_inr=repair,
            regulatory_fine_inr=fine, environmental_damage_inr=env,
            total_loss_inr=total, downtime_days=downtime,
            lives_at_risk=lives, loss_prevented_if_acted_now_inr=prevented,
        )

    def simulate(self, risk_event: RiskEvent, n_workers_in_zone: int = 2) -> PredictiveSimulationResult:
        zone_id = risk_event.zone_id
        current_p = risk_event.calibrated_probability
        ctx = risk_event.features.get("context_multiplier", 1.0)
        combo = bool(risk_event.features.get("combo_flag", 0))

        trend = self._trend(zone_id, current_p)
        if trend > 0.005:
            trajectory = "rising"
        elif trend < -0.005:
            trajectory = "falling"
        else:
            trajectory = "stable"

        projections = []
        for h in _HORIZONS:
            pp = self._project_probability(current_p, trend, h, ctx)
            spread = min(1.0, pp * 1.3 + h * 0.01)
            exposed = int(n_workers_in_zone * min(1.0, spread * 1.5))
            projections.append(TimeHorizonProjection(
                minutes_ahead=h, projected_probability=round(pp, 3),
                projected_severity=self._severity_label(pp),
                gas_spread_radius=round(spread, 3),
                workers_potentially_exposed=exposed,
                action_options=self._action_options(zone_id, pp, ctx, combo),
            ))

        ttc = self._time_to_critical(current_p, trend, ctx)
        fin = self._financial_impact(current_p, n_workers_in_zone)

        if current_p >= 0.75:
            action_summary = "IMMEDIATE ACTION: Evacuate zone, suspend all permits, isolate equipment."
        elif current_p >= 0.45:
            action_summary = "URGENT: Suspend active permits, increase ventilation, alert supervisor."
        elif current_p >= 0.20:
            action_summary = "MONITOR: Increased vigilance, verify permit status, standby evacuation."
        else:
            action_summary = "No action required. Continue monitoring."

        return PredictiveSimulationResult(
            zone_id=zone_id, sim_hour=risk_event.sim_hour,
            current_probability=current_p,
            time_to_critical_minutes=ttc,
            trajectory=trajectory,
            projections=projections,
            financial_impact=fin,
            recommended_action_summary=action_summary,
        )
