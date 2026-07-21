# Agent Contracts

Precise input/output contract for each agent. All dataclasses live in their
respective `agents/agentN_*.py` module.

---

## Agent 1 — Sensor Fusion (`agents/agent1_sensor_fusion.py`)

**Input:** `Reading` (from `generator/sensor_stream.py`)
```
sensor_id, zone_id, sensor_type, timestamp, sim_hour, value, unit,
shift ("day"|"evening"|"night"), handover (bool),
injected_anomaly (bool, generator-internal only)
```

**Output:** `SensorAnomalyEvent`
```
sensor_id, zone_id, sensor_type, timestamp, sim_hour, value,
baseline_mean, baseline_std,    # rolling, per-sensor, per-shift
z_score,
anomaly_score,                  # 0-1, from Isolation Forest decision_function
                                 # (cold-start fallback: z-score-derived until
                                 # >=30 samples exist for that sensor+shift)
shift, handover
```

**Internal state:** one rolling deque (max 300) per sensor per shift bucket,
plus one `IsolationForest` per sensor per shift, retrained every 40 ticks
once >=30 samples exist. This is the "seasonal/shift-aware baseline" --
night-shift readings are scored against night-shift history, not a single
global distribution.

---

## Agent 2 — Operational Context (`agents/agent2_operational_context.py`)

**Input:** zone snapshot from `OperationalContextGenerator.snapshot(hour)`
```
zone_id, active_permits: list[Permit], active_maintenance: list[MaintenanceActivity],
shift, handover
```

**Output:** `ZoneContextEvent`
```
zone_id, sim_hour, shift, handover,
active_permit_types: list[str], active_permit_ids: list[str],
active_maintenance_types: list[str],
context_multiplier: float (>=1.0),
combo_flag: bool,                    # a known-dangerous permit combo is active
has_operational_context: bool,       # True iff >=1 active permit or maintenance activity
                                      # -- handover alone does NOT set this True
contributing_notes: list[str]
```

**Multiplier logic:** base 1.0 + sum of per-permit-type risk weights (hot
work 0.9, confined space entry 0.85, electrical isolation 0.4, lifting 0.35,
excavation 0.3, routine cold work 0.1) + 0.3 if breakdown maintenance active
+ 0.25 if handover window active, with a ×1.8 multiplicative bonus if the
hot-work + confined-space-entry combo is simultaneously active.

---

## Agent 3 — Compound Risk Correlation (`agents/agent3_compound_risk.py`)

**Input:** `list[SensorAnomalyEvent]` for a zone + that zone's `ZoneContextEvent`.

**Output:** `RiskEvent`
```
event_id, zone_id, sim_hour, timestamp,
severity: "INFO"|"LOW"|"MEDIUM"|"HIGH",
calibrated_probability: float,
features: dict (max_anomaly_score, num_anomalous_sensors, context_multiplier,
                 combo_flag, handover),
feature_attribution: dict           # coefficient * feature_value, exact (linear model)
contributing_sensor_events: list[dict],
contributing_context: dict,
gate_passed: bool,
gate_reason: str,
estimated_lead_time_minutes: float | None,
rule_trace: list[str]
```

**THE HARD GATE (evaluated first, always):**
```
if not ctx.has_operational_context:
    severity = LOW if max_anomaly_score >= 0.5 else INFO
    probability = 0.0
    # the learned model is never invoked
else:
    probability = logistic_regression(features)
    severity = HIGH if p>=0.75 else MEDIUM if p>=0.45 else LOW if p>=0.2 else INFO
```

**Learned layer:** `sklearn.LogisticRegression`, 5 features (listed above),
trained on a 4000-row synthetic dataset where ground-truth incident
probability is driven by an interaction term between sensor anomaly and
elevated context (`max_score * (context_multiplier - 1)`) -- i.e. the model
is trained to specifically reward *compound* risk, not anomaly alone.
Coefficients are exposed directly (`agent.model.coef_`); no SHAP dependency
needed since contribution = coefficient × value is exact for a linear model.

---

## Agent 4 — Knowledge Graph / Memory (`agents/agent4_knowledge_graph.py`)

**Input:** `RiskEvent` (written via `write_risk_event`); pre-seeded
`knowledge_graph/seed_data.json` (17 historical incidents, 5 regulatory
guideline excerpts) loaded at construction.

**Graph schema (networkx.MultiDiGraph):**
```
Nodes: Zone, Sensor, Permit, RiskEvent, HistoricalIncident, RegulatoryGuideline
Edges: LOCATED_IN (Sensor/RiskEvent/HistoricalIncident -> Zone)
       CONTRIBUTED_TO (Permit/Sensor -> RiskEvent)
       SIMILAR_TO (HistoricalIncident <-> HistoricalIncident, shared tags)
```

**Query methods used by Agent 5 / the API:**
```
find_similar_incidents(zone_id, tags, limit) -> list[dict]
find_relevant_guidelines(tags, limit) -> list[dict]
query_zone_history(zone_id) -> {risk_events, historical_incidents}
stats() -> {total_nodes, total_edges, by_type}
```

---

## Agent 5 — Incident Intelligence (`agents/agent5_incident_intelligence.py`)

**Input:** `RiskEvent` (only acts if `severity == "HIGH"`, else returns `None`).

**Output:** `IncidentIntelligenceBriefing`
```
risk_event_id, zone_id,
matched_incident: dict | None,      # full HistoricalIncident node, or None
matched_guideline: dict | None,     # full RegulatoryGuideline node, or None
narrative: str,                     # grounded explanation, cites real node IDs
source_citation: list[str]          # the actual node IDs cited -- never invented
```

**Grounding guarantee:** `matched_incident`/`matched_guideline` come
directly from `KnowledgeGraphAgent` query results, which only ever return
real graph nodes. The narrative generator (template-based by default) only
ever references fields pulled from those dicts -- it cannot hallucinate a
precedent that isn't a real node, by construction.

**LLM swap point:** `IncidentIntelligenceAgent(kg, llm_explain_fn=my_fn)`
where `my_fn(risk_event, matched_incident, matched_guideline) -> str`. Any
exception in `llm_explain_fn` is caught and falls back to the template
narrative -- a live demo cannot break on an LLM/API failure.

---

## Agent 6 — Response Orchestration (`agents/agent6_response_orchestration.py`)

**Input:** `RiskEvent` + optional `IncidentIntelligenceBriefing`.

**Output:** `IncidentReport`
```
report_id, generated_at, zone_id, zone_name, severity, calibrated_probability,
sim_hour, estimated_lead_time_minutes,
contributing_factors: list[str],       # rule_trace + feature attributions, human-readable
grounded_precedent: str | None,        # Agent 5's narrative, if any
action_checklist: list[ActionItem],    # ranked, each with a rationale
sscada_integration_note: str           # explicit "ready for integration, not yet wired" disclaimer
```

`to_dict()` / `to_text()` produce the JSON (dashboard) and flat-text
(downloadable-report stand-in) representations respectively.

**Action checklist generation logic (deterministic, ranked):** evacuate
zone → suspend each contributing active permit → halt concurrent hot-work/
confined-space-entry if combo_flag → notify shift supervisor(s) (both
outgoing+incoming if handover active) → dispatch gas-test/safety officer →
log + initiate formal investigation.
