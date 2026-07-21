"""
AGENT 6 — Response Orchestration Agent (lightweight, rule-based)

On HIGH severity confirmation, generates:
  1. A structured, regulatory-format-styled preliminary incident report
     (auto-filled fields: zone, time, contributing factors, recommended
     immediate actions).
  2. A ranked action checklist (e.g. "evacuate Zone X", "suspend permit
     #HW-2291", "notify shift supervisor").

Outputs to dict/JSON (for the dashboard) and can render a flat-text version
suitable for a "downloadable PDF" stand-in (kept as plain text/JSON per the
no-required-external-deps constraint -- a real deployment would route this
through the pdf skill or a templating engine).

This agent does not integrate with any real notification/SCADA system --
output is explicitly framed as "ready for SCADA/alerting system integration."
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from agents.agent3_compound_risk import RiskEvent
from agents.agent5_incident_intelligence import IncidentIntelligenceBriefing
from generator.plant_model import ZONE_BY_ID


@dataclass
class ActionItem:
    rank: int
    action: str
    rationale: str


@dataclass
class IncidentReport:
    report_id: str
    generated_at: float
    zone_id: str
    zone_name: str
    severity: str
    calibrated_probability: float
    sim_hour: float
    estimated_lead_time_minutes: float | None
    contributing_factors: list[str]
    grounded_precedent: str | None
    action_checklist: list[ActionItem]
    sscada_integration_note: str = (
        "STRUCTURED OUTPUT READY FOR SCADA/ALERTING SYSTEM INTEGRATION -- "
        "this report and checklist are generated for dashboard display and "
        "downloadable export; no live notification or control-system write "
        "is performed by this prototype."
    )

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["action_checklist"] = [a.__dict__ for a in self.action_checklist]
        return d

    def to_text(self) -> str:
        lines = [
            "=" * 70,
            "PRELIMINARY INCIDENT REPORT".center(70),
            "=" * 70,
            f"Report ID:        {self.report_id}",
            f"Generated:        {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.generated_at))}",
            f"Zone:             {self.zone_id} -- {self.zone_name}",
            f"Severity:         {self.severity}",
            f"Calibrated P:     {self.calibrated_probability}",
            f"Sim hour:         {self.sim_hour}",
            f"Est. lead time:   {self.estimated_lead_time_minutes} minutes",
            "",
            "CONTRIBUTING FACTORS",
            "-" * 70,
        ]
        lines += [f"  - {f}" for f in self.contributing_factors]
        lines += ["", "GROUNDED PRECEDENT", "-" * 70]
        lines.append(f"  {self.grounded_precedent or 'No matching precedent retrieved.'}")
        lines += ["", "RECOMMENDED ACTIONS (RANKED)", "-" * 70]
        for a in self.action_checklist:
            lines.append(f"  [{a.rank}] {a.action}")
            lines.append(f"        rationale: {a.rationale}")
        lines += ["", self.sscada_integration_note, "=" * 70]
        return "\n".join(lines)


class ResponseOrchestrationAgent:
    """AGENT 6."""

    def _build_actions(self, event: RiskEvent) -> list[ActionItem]:
        actions = []
        rank = 1
        zone_name = ZONE_BY_ID[event.zone_id].name

        actions.append(ActionItem(rank, f"Evacuate non-essential personnel from {event.zone_id} ({zone_name})",
                                   "HIGH severity compound risk confirmed with corroborating operational context."))
        rank += 1

        for permit_id in event.contributing_context.get("active_permit_ids", []):
            actions.append(ActionItem(rank, f"Suspend permit {permit_id} immediately",
                                       "Active permit identified as a contributing factor to the compound risk."))
            rank += 1

        if event.contributing_context.get("combo_flag"):
            actions.append(ActionItem(rank, "Halt any concurrent hot-work / confined-space-entry operations in the zone",
                                       "Dangerous permit combination detected (hot work + confined space entry)."))
            rank += 1

        if event.contributing_context.get("handover"):
            actions.append(ActionItem(rank, "Notify both outgoing and incoming shift supervisors directly",
                                       "Event occurred during a shift handover window -- explicit re-validation required."))
            rank += 1
        else:
            actions.append(ActionItem(rank, "Notify shift supervisor",
                                       "Standard escalation for confirmed HIGH severity event."))
            rank += 1

        anomalous_types = {
            se["sensor_type"] for se in event.contributing_sensor_events
            if se.get("anomaly_score", 0) >= 0.5
        }
        gas_or_pressure_involved = bool(anomalous_types & {"gas_h2s", "gas_ch4", "pressure"})
        mechanical_involved = bool(anomalous_types & {"vibration", "temperature"})

        if gas_or_pressure_involved:
            actions.append(ActionItem(rank, "Dispatch gas-test / safety officer to verify atmosphere before re-entry",
                                       f"Sensor anomaly involved a gas or pressure-relevant reading ({', '.join(sorted(anomalous_types & {'gas_h2s', 'gas_ch4', 'pressure'}))}) requiring physical verification."))
            rank += 1
        if mechanical_involved:
            actions.append(ActionItem(rank, "Dispatch maintenance/mechanical inspector to verify equipment condition",
                                       f"Sensor anomaly involved a mechanical reading ({', '.join(sorted(anomalous_types & {'vibration', 'temperature'}))}) requiring physical inspection."))
            rank += 1

        actions.append(ActionItem(rank, "Log event and initiate formal incident investigation",
                                   "Regulatory and internal audit traceability requirement for HIGH severity events."))
        return actions

    def generate_report(self, event: RiskEvent, briefing: IncidentIntelligenceBriefing | None) -> IncidentReport:
        contributing_factors = list(event.rule_trace)
        for name, val in event.feature_attribution.items():
            if abs(val) > 0.01:
                contributing_factors.append(f"feature '{name}' contributed {val:+.3f} to the risk logit")

        grounded = briefing.narrative if briefing else None

        report = IncidentReport(
            report_id=f"IR-{event.event_id}",
            generated_at=time.time(),
            zone_id=event.zone_id,
            zone_name=ZONE_BY_ID[event.zone_id].name,
            severity=event.severity,
            calibrated_probability=event.calibrated_probability,
            sim_hour=event.sim_hour,
            estimated_lead_time_minutes=event.estimated_lead_time_minutes,
            contributing_factors=contributing_factors,
            grounded_precedent=grounded,
            action_checklist=self._build_actions(event),
        )
        return report

    def save_report(self, report: IncidentReport, out_dir: str) -> tuple[str, str]:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        json_path = f"{out_dir}/{report.report_id}.json"
        txt_path = f"{out_dir}/{report.report_id}.txt"
        with open(json_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2, default=str)
        with open(txt_path, "w") as f:
            f.write(report.to_text())
        return json_path, txt_path


if __name__ == "__main__":
    from agents.agent4_knowledge_graph import KnowledgeGraphAgent
    from agents.agent5_incident_intelligence import IncidentIntelligenceAgent

    fake_event = RiskEvent(
        event_id="RE-TEST-2", zone_id="Z3", sim_hour=14.0, timestamp=time.time(),
        severity="HIGH", calibrated_probability=0.93,
        features={"max_anomaly_score": 0.95}, feature_attribution={"max_anomaly_score": 4.1, "combo_flag": 1.2},
        contributing_sensor_events=[{"sensor_id": "Z3-gas_ch4-1", "sensor_type": "gas_ch4", "anomaly_score": 0.95}],
        contributing_context={"active_permit_types": ["hot_work", "confined_space_entry"],
                               "active_permit_ids": ["P-HW2291", "P-CSE0042"],
                               "combo_flag": True, "handover": True},
        gate_passed=True, gate_reason="ok", estimated_lead_time_minutes=9.5,
        rule_trace=["Collected 3 sensor readings for zone Z3; 1 exceeds anomaly threshold 0.5.",
                    "Corroborating operational context present -- learned layer evaluated.",
                    "Learned layer calibrated probability=0.930 -> severity=HIGH."],
    )
    kg = KnowledgeGraphAgent()
    a5 = IncidentIntelligenceAgent(kg)
    briefing = a5.brief(fake_event)

    a6 = ResponseOrchestrationAgent()
    report = a6.generate_report(fake_event, briefing)
    print(report.to_text())
