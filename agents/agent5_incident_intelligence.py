"""
AGENT 5 — Incident Intelligence Agent (RAG, retrieval grounded in the graph)

On a HIGH severity RiskEvent, queries Agent 4's knowledge graph for the most
relevant historical precedent and regulatory guideline, then produces a short
grounded explanation that CITES its source node -- never a hallucinated
precedent, since every cited ID is guaranteed to exist as a real graph node.

Per the constraint "if using an LLM for Agent 5's explanation generation,
make it swappable/optional and have a template-based fallback so a demo never
breaks on API failure": this implementation defaults to the template-based
generator (zero external dependency, zero risk of demo failure) and exposes
an optional `llm_explain_fn` hook a caller can inject to upgrade to an LLM
call without changing any other code.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

from agents.agent3_compound_risk import RiskEvent
from agents.agent4_knowledge_graph import KnowledgeGraphAgent

MINIMUM_SEVERITY = "MEDIUM"  # Agent 5 fires on both MEDIUM and HIGH


@dataclass
class IncidentIntelligenceBriefing:
    risk_event_id: str
    zone_id: str
    matched_incident: Optional[dict]
    matched_guideline: Optional[dict]
    narrative: str
    source_citation: list[str] = field(default_factory=list)
    llm_generated: bool = False  # True if `narrative` came from the real LLM call,
                                  # False if it came from the template fallback
                                  # (no key configured, or the API call failed)


class IncidentIntelligenceAgent:
    """AGENT 5."""

    def __init__(self, kg: KnowledgeGraphAgent, llm_explain_fn: Optional[Callable] = None):
        self.kg = kg
        # Optional hook: a function(risk_event, matched_incident, matched_guideline) -> str
        # If provided, used in place of the template narrative. Left None by default
        # so the demo has zero external API dependency and cannot fail on stage.
        self.llm_explain_fn = llm_explain_fn

    def _derive_query_tags(self, event: RiskEvent) -> list[str]:
        tags = []
        ctx = event.contributing_context
        for ptype in ctx.get("active_permit_types", []):
            tags.append(ptype)
        if ctx.get("combo_flag"):
            tags.extend(["hot_work", "confined_space"])
        if ctx.get("handover"):
            tags.append("shift_handover")
        for se in event.contributing_sensor_events:
            if se["anomaly_score"] >= 0.5:
                tags.append(se["sensor_type"])
        return list(dict.fromkeys(tags))  # de-dup, preserve order

    def _template_narrative(self, event: RiskEvent, incident: Optional[dict], guideline: Optional[dict]) -> str:
        parts = []
        if incident:
            parts.append(
                f"Pattern matches precedent {incident['id']} ({incident.get('date', 'undated')}): "
                f"{incident.get('summary', incident.get('title', ''))}"
            )
        else:
            parts.append("No closely matching historical precedent found in the knowledge graph for this combination.")
        if guideline:
            parts.append(
                f"Relevant guidance [{guideline['id']}]: {guideline.get('excerpt_summary', '')}"
            )
        return " ".join(parts)

    def brief(self, event: RiskEvent) -> Optional[IncidentIntelligenceBriefing]:
        """Returns None for LOW/INFO. Fires on MEDIUM and HIGH.
        MEDIUM events get a shorter brief (1 precedent, no guideline) to avoid
        overwhelming operators with precedent text for every medium alert."""
        sev_order = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        if sev_order.get(event.severity, 0) < sev_order["MEDIUM"]:
            return None
        is_high = event.severity == "HIGH"

        tags = self._derive_query_tags(event)
        incidents = self.kg.find_similar_incidents(event.zone_id, tags=tags, limit=1)
        guidelines = self.kg.find_relevant_guidelines(tags=tags, limit=1) if is_high else []

        matched_incident = incidents[0] if incidents else None
        matched_guideline = guidelines[0] if guidelines else None

        used_llm = False
        if self.llm_explain_fn is not None:
            try:
                narrative = self.llm_explain_fn(event, matched_incident, matched_guideline)
                used_llm = True
            except Exception:
                # never let an external call break the demo -- fall back silently
                narrative = self._template_narrative(event, matched_incident, matched_guideline)
        else:
            narrative = self._template_narrative(event, matched_incident, matched_guideline)

        citations = []
        if matched_incident:
            citations.append(matched_incident["id"])
        if matched_guideline:
            citations.append(matched_guideline["id"])

        return IncidentIntelligenceBriefing(
            risk_event_id=event.event_id,
            zone_id=event.zone_id,
            matched_incident=matched_incident,
            matched_guideline=matched_guideline,
            narrative=narrative,
            source_citation=citations,
            llm_generated=used_llm,
        )


if __name__ == "__main__":
    # smoke test with a synthetic HIGH severity event shape
    kg = KnowledgeGraphAgent()
    agent5 = IncidentIntelligenceAgent(kg)

    fake_event = RiskEvent(
        event_id="RE-TEST-1", zone_id="Z3", sim_hour=14.0, timestamp=0.0,
        severity="HIGH", calibrated_probability=0.91,
        features={}, feature_attribution={},
        contributing_sensor_events=[{"sensor_id": "Z3-gas_ch4-1", "sensor_type": "gas_ch4", "anomaly_score": 0.9}],
        contributing_context={"active_permit_types": ["hot_work", "confined_space_entry"], "combo_flag": True, "handover": False, "active_permit_ids": []},
        gate_passed=True, gate_reason="ok", estimated_lead_time_minutes=12.0,
    )
    briefing = agent5.brief(fake_event)
    print(briefing.narrative)
    print("Citations:", briefing.source_citation)
