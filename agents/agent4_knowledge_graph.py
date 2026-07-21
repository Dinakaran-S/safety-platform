"""
AGENT 4 — Knowledge Graph / Memory Agent

Maintains a live networkx knowledge graph of entities: Zone, Sensor, Permit,
MaintenanceActivity, Shift, RiskEvent, HistoricalIncident, RegulatoryGuideline
-- with typed relationships (LOCATED_IN, ACTIVE_DURING, CONTRIBUTED_TO,
SIMILAR_TO).

Every RiskEvent produced by Agent 3 is written into the graph linked to its
contributing entities. The graph is pre-seeded with ~18 synthetic historical
near-miss/incident records and regulatory guideline excerpts (OISD-style),
each as its own node, so Agent 5 has real precedent to retrieve and cite.
"""
import json
from pathlib import Path

import networkx as nx

from agents.agent3_compound_risk import RiskEvent
from generator.plant_model import PLANT


SEED_DATA_PATH = Path(__file__).resolve().parents[1] / "knowledge_graph" / "seed_data.json"


class KnowledgeGraphAgent:
    """AGENT 4. Wraps a networkx.MultiDiGraph."""

    def __init__(self):
        self.g = nx.MultiDiGraph()
        self._seed_plant_topology()
        self._seed_historical_data()

    # ---------- seeding ----------

    def _seed_plant_topology(self):
        for zone in PLANT:
            self.g.add_node(zone.zone_id, type="Zone", name=zone.name,
                             zone_class=zone.zone_class.value, x=zone.x, y=zone.y,
                             base_risk_weight=zone.base_risk_weight)
            for sensor in zone.sensors:
                self.g.add_node(sensor.sensor_id, type="Sensor",
                                 sensor_type=sensor.sensor_type.value, unit=sensor.unit)
                self.g.add_edge(sensor.sensor_id, zone.zone_id, relation="LOCATED_IN")

    def _seed_historical_data(self):
        with open(SEED_DATA_PATH) as f:
            seed = json.load(f)

        for inc in seed["historical_incidents"]:
            self.g.add_node(inc["id"], type="HistoricalIncident", **{
                k: v for k, v in inc.items() if k != "id"
            })
            if inc.get("zone_id") in self.g:
                self.g.add_edge(inc["id"], inc["zone_id"], relation="LOCATED_IN")

        for reg in seed["regulatory_guidelines"]:
            self.g.add_node(reg["id"], type="RegulatoryGuideline", **{
                k: v for k, v in reg.items() if k != "id"
            })

        # SIMILAR_TO links between incidents sharing tags, computed once at seed time
        incidents = seed["historical_incidents"]
        for i, a in enumerate(incidents):
            for b in incidents[i + 1:]:
                shared = set(a.get("tags", [])) & set(b.get("tags", []))
                if shared:
                    self.g.add_edge(a["id"], b["id"], relation="SIMILAR_TO", shared_tags=list(shared))
                    self.g.add_edge(b["id"], a["id"], relation="SIMILAR_TO", shared_tags=list(shared))

    # ---------- writes ----------

    def write_risk_event(self, event: RiskEvent):
        node_id = event.event_id
        self.g.add_node(node_id, type="RiskEvent", severity=event.severity,
                         sim_hour=event.sim_hour, probability=event.calibrated_probability,
                         zone_id=event.zone_id)
        self.g.add_edge(node_id, event.zone_id, relation="LOCATED_IN")

        for permit_id in event.contributing_context.get("active_permit_ids", []):
            # permit nodes are created on the fly if not already present (lightweight,
            # demo-scale -- a production system would have Agent 2 register them directly)
            if permit_id not in self.g:
                self.g.add_node(permit_id, type="Permit")
            self.g.add_edge(permit_id, node_id, relation="CONTRIBUTED_TO")

        for sensor_event in event.contributing_sensor_events:
            sid = sensor_event["sensor_id"]
            if sensor_event["anomaly_score"] >= 0.5:
                self.g.add_edge(sid, node_id, relation="CONTRIBUTED_TO",
                                 anomaly_score=sensor_event["anomaly_score"])

    # ---------- queries ----------

    def find_similar_incidents(self, zone_id: str, tags: list[str] | None = None, limit: int = 3) -> list[dict]:
        """Query used by Agent 5: find historical incidents in/near this zone,
        optionally filtered/ranked by shared tags."""
        results = []
        for node_id, data in self.g.nodes(data=True):
            if data.get("type") != "HistoricalIncident":
                continue
            score = 0
            if data.get("zone_id") == zone_id:
                score += 2
            if tags:
                score += len(set(data.get("tags", [])) & set(tags))
            if score > 0:
                results.append((score, node_id, data))
        results.sort(key=lambda r: -r[0])
        return [{"id": r[1], **r[2]} for r in results[:limit]]

    def find_relevant_guidelines(self, tags: list[str], limit: int = 2) -> list[dict]:
        results = []
        for node_id, data in self.g.nodes(data=True):
            if data.get("type") != "RegulatoryGuideline":
                continue
            score = len(set(data.get("tags", [])) & set(tags))
            if score > 0:
                results.append((score, node_id, data))
        results.sort(key=lambda r: -r[0])
        return [{"id": r[1], **r[2]} for r in results[:limit]]

    def query_zone_history(self, zone_id: str) -> dict:
        """'show me all past events where hot-work permits co-occurred with gas
        anomalies in this zone' style query, generalized to: every RiskEvent and
        HistoricalIncident linked to this zone, with their contributing factors."""
        risk_events, incidents = [], []
        for node_id, data in self.g.nodes(data=True):
            if data.get("type") == "RiskEvent" and data.get("zone_id") == zone_id:
                contributors = [u for u, v, d in self.g.in_edges(node_id, data=True) if d.get("relation") == "CONTRIBUTED_TO"]
                risk_events.append({"id": node_id, **data, "contributors": contributors})
            if data.get("type") == "HistoricalIncident" and data.get("zone_id") == zone_id:
                incidents.append({"id": node_id, **data})
        return {"zone_id": zone_id, "risk_events": risk_events, "historical_incidents": incidents}

    def stats(self) -> dict:
        by_type = {}
        for _, data in self.g.nodes(data=True):
            t = data.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
        return {"total_nodes": self.g.number_of_nodes(), "total_edges": self.g.number_of_edges(), "by_type": by_type}

    def reset_session_nodes(self):
        """Remove all session-generated nodes (RiskEvent, Permit) added during the
        current run, but keep all seeded data (Zone, Sensor, HistoricalIncident,
        RegulatoryGuideline). Called on full simulation reset so the graph returns
        to its baseline seeded state and the frontend visualisation doesn't go blank."""
        session_types = {"RiskEvent", "Permit"}
        to_remove = [
            nid for nid, data in self.g.nodes(data=True)
            if data.get("type") in session_types
        ]
        self.g.remove_nodes_from(to_remove)


if __name__ == "__main__":
    kg = KnowledgeGraphAgent()
    print(kg.stats())
    print(kg.find_similar_incidents("Z3", tags=["hot_work", "confined_space"]))
