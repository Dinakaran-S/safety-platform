# SENTINEL AI — Complete System Documentation

**Version 2.0 | Industrial Safety Intelligence Platform**

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Philosophy](#2-system-philosophy)
3. [Architecture Overview](#3-architecture-overview)
4. [Plant Model & Data Generator](#4-plant-model--data-generator)
5. [Agent Reference](#5-agent-reference)
6. [Event Bus & Message Contracts](#6-event-bus--message-contracts)
7. [Orchestrator](#7-orchestrator)
8. [Backend API Reference](#8-backend-api-reference)
9. [WebSocket Protocol](#9-websocket-protocol)
10. [Knowledge Graph](#10-knowledge-graph)
11. [Frontend Dashboard](#11-frontend-dashboard)
12. [Simulation Controls & Scenarios](#12-simulation-controls--scenarios)
13. [False-Positive Invariant & Testing](#13-false-positive-invariant--testing)
14. [Configuration & Tuning](#14-configuration--tuning)
15. [Deployment](#15-deployment)
16. [Strategic Roadmap](#16-strategic-roadmap)
17. [Glossary](#17-glossary)

---

## 1. Executive Summary

SENTINEL AI is a ten-agent compound risk intelligence platform for industrial facilities. It solves a specific, documented problem: real plants have working individual safety systems — gas detectors, SCADA, permit-to-work software, maintenance logs — but no intelligence layer that correlates them. That gap has caused fatal incidents where an entrapped gas trend, an active hot-work permit, and a shift handover all occurred simultaneously, each system unaware of the others.

SENTINEL becomes that correlation layer. It does not replace sensors or SCADA. It reads their outputs, understands the operational context around them, and identifies compound patterns that no individual system would flag.

**Core guarantee:** A single anomalous sensor reading with no corroborating operational context never produces a MEDIUM or HIGH severity alert. This is enforced by a hard rule-layer gate in Agent 3 — not by ML training — and proven by automated testing across 250 randomised trials at 0.00% false positive rate.

**Stack at a glance:**
- Backend: Python 3.12, FastAPI, asyncio, scikit-learn, networkx
- Frontend: Single-file vanilla JS + D3.js (no build step)
- Storage: In-memory (demo scale); all contracts written for Postgres/Redis swap-in
- Transport: REST + WebSocket real-time push
- Lines of code: ~4,450 across 21 source files

---

## 2. System Philosophy

### 2.1 Why multi-agent?

Each agent owns one narrow responsibility and has its own internal state. Agents communicate only through typed messages on a shared event bus — never through direct function calls. This means:

- Agent 3's correlation logic can be tested in complete isolation from data generation
- New data sources are new agents, not changes to existing code
- Every decision is a traceable message, not a buried function call
- An agent that crashes does not take down the system

### 2.2 Why no fixed thresholds?

Fixed thresholds create two failure modes simultaneously: they miss slow, contextual build-ups (false negatives that kill people) and they fire constantly on normal operational noise (false positives that train operators to ignore alarms). SENTINEL uses Isolation Forest per sensor per shift — a model that learns what "normal" looks like for that sensor during that shift, and scores deviations relative to that learned baseline.

### 2.3 Why a hard rule gate instead of full ML?

The compound risk model (Agent 3) uses logistic regression, not a deep learning model, for a deliberate reason: every risk score is decomposable into `coefficient × feature_value` per input feature. This is exact attribution, not an approximation. Judges, safety engineers, and regulators can verify every number.

But the most important property — a single anomalous sensor never escalates alone — is not left to the model. It is enforced by a hard gate that runs before the model is consulted. This makes the property provable by code inspection, not just empirically.

### 2.4 Why a single-file frontend?

No Node.js, no npm, no build step means the demo always works. The dashboard loads in any browser from the FastAPI backend with zero configuration. The tradeoff (no React, no TypeScript) is acceptable at demo scale and is explicitly noted in the roadmap.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    INDUSTRIAL PLANT                          │
│  Gas / Pressure / Temperature / Vibration / Smoke sensors   │
│  Permit-to-work system  │  Maintenance logs  │  Shift sched │
└────────────────────────────┬────────────────────────────────┘
                             │ raw readings + operational events
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    DATA GENERATOR LAYER                      │
│  SensorStreamGenerator  │  OperationalContextGenerator      │
│  EquipmentWorkerGenerator  │  ScenarioLibrary               │
└────────────────────────────┬────────────────────────────────┘
                             │ typed Reading / Permit / Equipment objects
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    ASYNC EVENT BUS                           │
│  asyncio pub/sub  │  topic-per-message-type                 │
│  immutable append-only history per topic                    │
└──┬────────────┬──────────────┬────────────────┬────────────┘
   │            │              │                │
   ▼            ▼              ▼                ▼
┌──────┐   ┌──────┐      ┌─────────┐    ┌──────────┐
│Agent1│   │Agent2│      │Agent 7  │    │Agent 8   │
│Sensor│   │Op.   │      │Equip.   │    │Worker    │
│Fusion│   │Contxt│      │Health   │    │Safety    │
└──┬───┘   └──┬───┘      └────┬────┘    └────┬─────┘
   │          │               │               │
   └──────────┴───────┬───────┴───────────────┘
                      │ SensorAnomalyEvent + ZoneContextEvent
                      ▼                + EquipmentHealthEvent
┌─────────────────────────────────────────────────────────────┐
│                AGENT 3 — COMPOUND RISK CORRELATION          │
│  ① Hard invariant gate (no context → cap at LOW/INFO)       │
│  ② Logistic regression (5 features, exposed coefficients)   │
│  ③ Severity mapping (p≥0.75→HIGH, ≥0.45→MEDIUM, etc.)     │
│  Outputs: RiskEvent with full provenance                    │
└────────────────────────┬────────────────────────────────────┘
                         │ RiskEvent published to bus
           ┌─────────────┼─────────────────────────────┐
           ▼             ▼             ▼                ▼
    ┌──────────┐  ┌──────────┐ ┌──────────┐   ┌──────────────┐
    │ Agent 4  │  │ Agent 9  │ │ Agent 10 │   │  Agent 5+6   │
    │Knowledge │  │Predictive│ │Decision  │   │Intel+Response│
    │  Graph   │  │  Sim     │ │Validation│   │(HIGH only)   │
    └────┬─────┘  └────┬─────┘ └────┬─────┘   └──────┬───────┘
         │             │             │                 │
         └─────────────┴─────────────┴────────┬────────┘
                                               │ enriched alert
                                               ▼
┌─────────────────────────────────────────────────────────────┐
│                ORCHESTRATOR / IN-MEMORY STATE               │
│  alert_feed  │  zone_risk  │  predictions  │  reports       │
│  safety_score_history  │  comparison_log  │  audit_log      │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP REST + WebSocket push
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    DASHBOARD (frontend)                      │
│  Command Center (dense grid)  │  Overview (scrollable)      │
│  Digital Twin  │  Alert Feed  │  Knowledge Graph (D3)       │
│  Predictive Sim  │  Copilot  │  Agent Pipeline Viz          │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 Data flow per tick (every 0.6 wall-clock seconds)

| Step | Component | Output |
|------|-----------|--------|
| 1 | `SensorStreamGenerator.tick()` | 28 `Reading` objects (one per sensor) |
| 2 | Agent 1 | 28 `SensorAnomalyEvent` objects with 0–1 anomaly scores |
| 3 | Agent 2 | 9 `ZoneContextEvent` objects with context multipliers |
| 4 | Agent 7 | 12 `EquipmentState` objects → 9 `EquipmentHealthEvent` objects |
| 5 | Agent 8 | 8 `WorkerState` objects → 9 `WorkerSafetyEvent` objects |
| 6 | Agent 3 | 9 `RiskEvent` objects published to event bus |
| 7 | Agent 4 | HIGH events written into knowledge graph |
| 8 | Agent 9 | `PredictiveSimulationResult` per zone with HIGH alert |
| 9 | Agent 10 | `ValidationResult` — may downgrade severity |
| 10 | Agents 5+6 | `IncidentIntelligenceBriefing` + `IncidentReport` (HIGH only) |
| 11 | Orchestrator | Updates in-memory state, broadcasts WS `tick` message |

---

## 4. Plant Model & Data Generator

### 4.1 Zone layout

Nine zones model a mid-size petrochemical processing facility. Coordinates are arbitrary plant-units used for the 2D SVG twin.

| ID | Name | Class | Base Risk | Sensors | Key Equipment |
|----|------|-------|-----------|---------|---------------|
| Z1 | Compressor House | process_unit | 0.7 | pressure, temperature, vibration, gas_ch4 | V-101, P-201 |
| Z2 | Crude Storage Tank Farm | storage | 0.6 | gas_ch4, temperature, pressure | T-401, T-402 |
| Z3 | Confined Vessel Bay | **confined_space** | **0.9** | gas_h2s, gas_ch4, temperature | V-102 |
| Z4 | Reactor Unit A | process_unit | 0.8 | pressure, temperature, vibration, gas_ch4 | V-103, PU-302, HX-501 |
| Z5 | Pipe Rack / Manifold | utility | 0.5 | pressure, temperature, vibration | — |
| Z6 | Reactor Unit B | process_unit | 0.8 | pressure, temperature, vibration, gas_ch4 | V-104, P-202, HX-502 |
| Z7 | Cooling Water Plant | utility | 0.3 | pressure, temperature, vibration | PU-301 |
| Z8 | Effluent Treatment | utility | 0.4 | pressure, temperature, vibration | — |
| Z9 | Main Control Room | control_room | 0.2 | temperature | — |

Total: **28 sensors**, **12 equipment items**.

Z3 (Confined Vessel Bay) is the highest inherent risk zone and the target of the `fatal_pattern` scenario.

### 4.2 Shift-aware sensor baselines

Sensor readings are not flat noise. Each sensor has a different "normal" distribution per shift:

| Shift | Hours | Mean multiplier | Std multiplier | Notes |
|-------|-------|----------------|----------------|-------|
| Day | 06:00–14:00 | 1.00× | 1.00× | Reference baseline |
| Evening | 14:00–22:00 | 1.02× | 1.10× | Slightly elevated activity |
| Night | 22:00–06:00 | 1.05× | 1.25× | Fewer staff, less proactive tuning |
| Handover window | ±20 min around 06, 14, 22 | — | **1.60×** | Transition noise |

**Why this matters:** Agent 1 maintains separate rolling windows per sensor per shift. A night-shift reading is scored against night-shift history, not a single global baseline. Without this, the false positive rate on handover windows would be unacceptably high.

**Diurnal drift:** Temperature sensors also have a sinusoidal drift component `2.0 × sin(2π × hour/24)`, adding realistic day/night thermal variation.

### 4.3 Sensor types and baseline parameters

| Type | Mean | Std | Unit |
|------|------|-----|------|
| gas_h2s | 2.0 | 0.6 | ppm |
| gas_ch4 | 8.0 | 2.0 | %LEL |
| pressure | 4.5 | 0.3 | bar |
| temperature | 45.0 | 3.0 | °C |
| vibration | 1.2 | 0.2 | mm/s |

### 4.4 Operational context events

Permits and maintenance are the corroborating context that allows a sensor anomaly to escalate.

**Permit types and inherent risk weights:**

| Permit type | Risk weight | Notes |
|------------|------------|-------|
| hot_work | 0.90 | Ignition source; highest individual risk |
| confined_space_entry | 0.85 | Asphyxiation / entrapment risk |
| electrical_isolation | 0.40 | Arc flash and lockout/tagout |
| lifting | 0.35 | Dropped load / structural |
| excavation | 0.30 | Ground disturbance near live lines |
| routine_cold_work | 0.10 | Low-risk scheduled maintenance |

**Dangerous combination:** hot_work AND confined_space_entry simultaneously in the same zone → **1.8× multiplicative bonus** on top of the additive weights. This models real regulatory guidance (OISD-105-3.2) that these two permit types together require a joint risk assessment.

**Context multiplier formula:**
```
multiplier = 1.0
           + Σ(permit_risk_weight for each active permit)
           + 0.30  (if breakdown maintenance active)
           + 0.25  (if handover window active)
           × 1.80  (if hot_work AND confined_space_entry both active)
```

Example: hot_work (0.9) + confined_space_entry (0.85) + handover (0.25) → raw 3.0 → ×1.8 combo → **5.4× multiplier**.

### 4.5 Equipment and worker generator

**Equipment items (12 total):**
Valves (V-101 to V-104), compressors (P-201, P-202), pumps (PU-301, PU-302), tanks (T-401, T-402), heat exchangers (HX-501, HX-502).

**Equipment health model:**
```
wear_index increases by ~0.0002 per tick
wear_index increases faster when zone has high anomaly score
health_score = 100 × (1 - wear_index) - zone_anomaly × 15
failure_probability = 1 / (1 + exp((health_score - 40) × 0.12))
```

**Worker states (8 workers):**
Each worker has zone assignment, PPE compliance flag (~12% non-compliant), fatigue index (rises over shift), and exposure risk computed as:
```
exposure_risk = proximity_to_hazard × 0.6
              + zone_anomaly_score × 0.3
              + 0.15 (if not PPE compliant)
              + fatigue_index × 0.05
```
Status: exposure ≥ 0.7 → `danger`; ≥ 0.35 → `at_risk`; else → `safe`.

Workers reshuffle zones at shift boundaries (06:00, 14:00, 22:00).
---

## 5. Agent Reference

Each agent is a Python class in `agents/`. Agents never import from each other directly — all coordination flows through the event bus or the orchestrator.

---

### Agent 1 — Sensor Fusion

**File:** `agents/agent1_sensor_fusion.py` (146 lines)

**Purpose:** Convert raw sensor readings into continuous 0–1 anomaly scores using shift-aware rolling baselines and Isolation Forest unsupervised anomaly detection. Never use fixed thresholds.

**Internal state per sensor per shift:**
- Rolling deque: max 300 readings
- `sklearn.ensemble.IsolationForest`: `n_estimators=40`, `contamination=0.05`
- Retrain trigger: every 40 ticks once ≥30 samples exist

**Isolation Forest score mapping:**
```python
raw_score = model.decision_function([[value]])[0]
# IF returns positive for inliers, negative for outliers
anomaly_score = clip((0.5 - raw_score) * 2.0, 0.0, 1.0)
```

**Cold-start fallback** (before 30 samples exist):
```python
anomaly_score = clip((abs(z_score) - 2.0) / 6.0, 0.0, 1.0)
```

**Output — `SensorAnomalyEvent`:**
```python
@dataclass
class SensorAnomalyEvent:
    sensor_id: str        # e.g. "Z3-gas_h2s-0"
    zone_id: str          # e.g. "Z3"
    sensor_type: str      # e.g. "gas_h2s"
    timestamp: float      # unix seconds
    sim_hour: float       # 0–24 simulated hour
    value: float          # raw reading
    baseline_mean: float  # rolling mean for this sensor+shift
    baseline_std: float   # rolling std for this sensor+shift
    z_score: float        # (value - mean) / std
    anomaly_score: float  # 0 (normal) → 1 (highly anomalous)
    shift: str            # "day" | "evening" | "night"
    handover: bool        # True if within ±20 min of shift boundary
```

---

### Agent 2 — Operational Context

**File:** `agents/agent2_operational_context.py` (115 lines)

**Purpose:** Snapshot active permits and maintenance per zone each tick. Compute a `context_multiplier` and `has_operational_context` flag that Agent 3 uses as its gate condition.

**Critical flag — `has_operational_context`:**
- `True` if: ≥1 active permit OR ≥1 active maintenance activity
- `False` if: handover window only, or no operational events at all

A shift handover window **alone does NOT** set this flag. Handover is a risk amplifier, not a substitute for genuine operational context. This is the design decision that keeps the hard gate meaningful.

**Output — `ZoneContextEvent`:**
```python
@dataclass
class ZoneContextEvent:
    zone_id: str
    sim_hour: float
    shift: str
    handover: bool
    active_permit_types: list[str]       # e.g. ["hot_work", "confined_space_entry"]
    active_permit_ids: list[str]         # e.g. ["P-8C705B", "P-14D404"]
    active_maintenance_types: list[str]
    context_multiplier: float            # ≥1.0
    combo_flag: bool                     # True if dangerous permit combo active
    has_operational_context: bool        # Gate flag for Agent 3
    contributing_notes: list[str]        # Human-readable audit trail
```

---

### Agent 3 — Compound Risk Correlation

**File:** `agents/agent3_compound_risk.py` (236 lines)

**Purpose:** The core "brain". Takes sensor anomaly events and zone context, applies a two-layer decision process, and outputs a `RiskEvent` with full provenance.

#### Layer 1 — Hard Invariant Gate

```python
def _gate(ctx: ZoneContextEvent) -> tuple[bool, str]:
    if not ctx.has_operational_context:
        return False, "No corroborating context — severity capped at LOW/INFO"
    return True, "Context present — learned layer evaluated"
```

If gate returns `False`, the logistic regression model is **never called**. Severity is immediately set to `LOW` (if anomaly score ≥ 0.5) or `INFO`. Probability is set to 0.0.

This is the guarantee that makes the 0% false positive rate provable by code inspection rather than just empirically.

#### Layer 2 — Calibrated Logistic Regression

Trained at startup on 4,000 synthetic samples. Five features:

| Feature | Description | Coefficient |
|---------|-------------|-------------|
| `max_anomaly_score` | Highest IF anomaly score across zone sensors | **+6.5026** |
| `num_anomalous_sensors` | Count of sensors with score ≥ 0.5 | +0.4413 |
| `context_multiplier` | Zone context multiplier from Agent 2 | +1.6987 |
| `combo_flag` | 1 if dangerous permit combination active | +1.2073 |
| `handover` | 1 if shift handover window active | +0.6305 |
| intercept | — | −8.1068 |

The training synthetic ground-truth label includes an **interaction term**:
```python
logit = -8.1068
      + 6.50 * max_anomaly_score
      + 0.44 * num_anomalous_sensors
      + 1.70 * (context_multiplier - 1.0)
      + 1.21 * combo_flag
      + 0.63 * handover
      + 3.00 * max_anomaly_score * (context_multiplier - 1.0)  # INTERACTION
```

The interaction term is the key: it forces the model to reward compound risk — a high anomaly score is only meaningful when context is elevated too.

**Severity thresholds:**

| Calibrated probability | Severity |
|----------------------|----------|
| ≥ 0.75 | HIGH |
| ≥ 0.45 | MEDIUM |
| ≥ 0.20 | LOW |
| < 0.20 | INFO |

**Feature attribution (explainability):**
Because the model is linear, `contribution = coefficient × feature_value` is exact. No SHAP approximation required. Every risk score is fully decomposable.

**Lead time estimate:**
```python
lead_time = max(2.0, 45.0 * (1.0 - probability) * (1.0 - max_score * 0.5))
```
Only computed for MEDIUM/HIGH severity. Unit: minutes to incident threshold.

**Output — `RiskEvent`:**
```python
@dataclass
class RiskEvent:
    event_id: str
    zone_id: str
    sim_hour: float
    timestamp: float
    severity: str                    # "INFO"|"LOW"|"MEDIUM"|"HIGH"
    calibrated_probability: float    # 0.0–1.0
    features: dict                   # raw feature values
    feature_attribution: dict        # coefficient × value per feature (exact)
    contributing_sensor_events: list # raw SensorAnomalyEvent dicts
    contributing_context: dict       # raw ZoneContextEvent dict
    gate_passed: bool
    gate_reason: str
    estimated_lead_time_minutes: float | None
    rule_trace: list[str]            # human-readable decision audit
```

---

### Agent 4 — Knowledge Graph / Memory

**File:** `agents/agent4_knowledge_graph.py` (147 lines)

**Purpose:** Maintain a live `networkx.MultiDiGraph` that accumulates plant knowledge across the session. Every HIGH-severity RiskEvent is written into the graph linked to its contributing entities. Pre-seeded at startup with 17 historical incidents and 5 regulatory excerpts.

**Graph schema:**

| Node type | Attributes | Count (at startup) |
|-----------|-----------|-------------------|
| Zone | zone_id, name, zone_class, x, y, base_risk_weight | 9 |
| Sensor | sensor_type, unit | 28 |
| HistoricalIncident | title, date, summary, tags, severity | 17 |
| RegulatoryGuideline | title, excerpt_summary, tags | 5 |
| RiskEvent | severity, probability, zone_id, sim_hour | 0 (grows live) |
| Permit | permit_id, permit_type | 0 (grows live) |

**Edge types:**
| Relation | From → To | Semantics |
|----------|-----------|-----------|
| LOCATED_IN | Sensor/RiskEvent/Incident → Zone | Spatial relationship |
| CONTRIBUTED_TO | Sensor/Permit → RiskEvent | Causal link |
| SIMILAR_TO | Incident ↔ Incident | Shared tags computed at seed time |

**Key queries (used by Agent 5):**
```python
# Find historical incidents matching zone + tags
find_similar_incidents(zone_id, tags, limit=3)
# → sorted by: zone_match(+2) + shared_tag_count(+1 each)

# Find relevant regulatory guidelines by tags
find_relevant_guidelines(tags, limit=2)

# Full zone history: all RiskEvents + Incidents linked to a zone
query_zone_history(zone_id)
```

**Pre-seeded historical incidents (selected):**

| ID | Type | Zone | Tags |
|----|------|------|------|
| INC-088 | **Fatal** | Z3 | hot_work, confined_space, shift_handover, fatal |
| NM-114 | Near-miss | Z3 | hot_work, confined_space, pressure, gas_ch4 |
| INC-110 | **Fatal** | Z3 | confined_space, shift_handover, fatal |
| NM-198 | Near-miss | Z3 | confined_space, shift_handover, gas_h2s |
| NM-141 | False alarm | Z3 | gas_h2s, sensor_fault, false_alarm |
| INC-095 | Recordable | Z2 | inspection, pressure, storage |
| NM-152 | Near-miss | Z1 | preventive_maintenance, pressure, vibration |
| INC-103 | Recordable | Z5 | excavation, pressure |

NM-141 ("single gas spike, no permit → sensor drift") is specifically included as a reference case for the no-escalation-without-context principle. Agent 5 will retrieve it if a single-sensor anomaly fires with no context.

---

### Agent 5 — Incident Intelligence

**File:** `agents/agent5_incident_intelligence.py` (127 lines)

**Purpose:** When a HIGH severity RiskEvent fires, query Agent 4's knowledge graph for the closest historical precedent and relevant regulatory guideline, then generate a grounded explanation that cites real node IDs.

**Only activates on HIGH severity.** Returns `None` for all other severities.

**Retrieval process:**
1. Derive query tags from the RiskEvent's permit types, sensor types, combo flag, handover flag
2. Call `kg.find_similar_incidents(zone_id, tags)` — returns ranked historical incidents
3. Call `kg.find_relevant_guidelines(tags)` — returns ranked regulatory excerpts
4. Generate narrative using actual node data fields

**Grounding guarantee:** The narrative function only references fields from the matched node dicts. It cannot invent an incident ID that doesn't exist as a real graph node. If no relevant node exists, the function says so.

**LLM upgrade hook:**
```python
# Default: template-based (zero external dependency, zero demo risk)
agent5 = IncidentIntelligenceAgent(kg)

# Upgraded: LLM explanation with automatic fallback
agent5 = IncidentIntelligenceAgent(kg, llm_explain_fn=my_fn)
# my_fn signature: (risk_event, matched_incident, matched_guideline) -> str
# Any exception in my_fn is caught; template used as fallback
```

**Output — `IncidentIntelligenceBriefing`:**
```python
@dataclass
class IncidentIntelligenceBriefing:
    risk_event_id: str
    zone_id: str
    matched_incident: dict | None      # full graph node dict, or None
    matched_guideline: dict | None     # full graph node dict, or None
    narrative: str                     # grounded explanation
    source_citation: list[str]         # actual node IDs cited — never invented
```

---

### Agent 6 — Response Orchestration

**File:** `agents/agent6_response_orchestration.py` (184 lines)

**Purpose:** Generate a structured, regulatory-format incident report and ranked action checklist for every HIGH severity event.

**Action checklist (deterministic, ranked):**
1. Evacuate non-essential personnel from the affected zone
2. Suspend each active permit by ID (one item per permit)
3. Halt concurrent hot-work / confined-space operations (if combo_flag)
4. Notify shift supervisors — both outgoing AND incoming if handover is active
5. Dispatch gas-test / safety officer for physical atmosphere verification
6. Log event and initiate formal incident investigation

**Output — `IncidentReport`:**
```python
@dataclass
class IncidentReport:
    report_id: str                   # e.g. "IR-RE-1782799173683-846"
    generated_at: float
    zone_id: str
    zone_name: str
    severity: str
    calibrated_probability: float
    sim_hour: float
    estimated_lead_time_minutes: float | None
    contributing_factors: list[str]  # rule_trace + feature attributions
    grounded_precedent: str | None   # Agent 5 narrative
    action_checklist: list[ActionItem]
    sscada_integration_note: str     # explicit "not yet wired" disclaimer
```

Reports are available as `.to_dict()` (JSON for dashboard) and `.to_text()` (plain text for download). The download endpoint (`GET /api/reports/{id}/download`) returns a formatted text file.

---

### Agent 7 — Equipment Health

**File:** `agents/agent_equipment_health.py` (58 lines)

**Purpose:** Convert raw equipment states into per-zone health intelligence and detect co-degradation patterns.

**Co-degradation flag:** True when ≥2 equipment items in the same zone are simultaneously degraded or critical. Used by Agent 10 as an upgrade signal.

**Output — `EquipmentHealthEvent`:**
```python
@dataclass
class EquipmentHealthEvent:
    zone_id: str
    sim_hour: float
    equipment_states: list[dict]       # raw EquipmentState dicts
    zone_equipment_risk: float         # 0–1 (worst failure prob in zone)
    critical_equipment: list[str]      # IDs in critical/offline state
    co_degradation_flag: bool
    zone_notes: list[str]
```

---

### Agent 8 — Worker Safety

**File:** `agents/agent_worker_safety.py` (54 lines)

**Purpose:** Compute per-zone worker exposure risk and flag evacuation requirements.

**Output — `WorkerSafetyEvent`:**
```python
@dataclass
class WorkerSafetyEvent:
    zone_id: str
    sim_hour: float
    workers_in_zone: list[dict]
    max_exposure_risk: float           # 0–1
    workers_at_risk: list[str]         # names
    ppe_violations: list[str]          # names without PPE
    evacuation_recommended: bool       # True if zone has HIGH risk + workers present
    zone_worker_risk: float            # 0–1 aggregate
```

---

### Agent 9 — Predictive Simulation

**File:** `agents/agent_predictive_simulation.py` (212 lines)

**Purpose:** Project compound risk forward in time at +5, +10, +15, and +30 minute horizons. Compute financial impact in INR. Estimate time-to-critical.

**Trend calculation:**
```python
# Keep rolling history of risk probabilities per zone (last 10 values)
slope_per_tick = (recent[-1] - recent[0]) / max(n-1, 1)
trend_per_minute = slope_per_tick * 10  # each tick ≈ 0.1 sim-min
```

**Projection formula:**
```python
acceleration = 1.0 + current_p * 0.8 * (context_multiplier - 1.0)
projected_p(t) = current_p + trend_per_minute * t * acceleration
projected_p = clip(projected_p, 0.0, 1.0)
```

Acceleration ensures risk compounds faster when context is elevated and current probability is already high.

**Financial impact constants (INR):**
| Line item | Value |
|-----------|-------|
| Production loss | ₹8,00,000 / hour |
| One-time repair cost | ₹25,00,000 |
| Regulatory fine (OISD) | ₹12,00,000 |
| Environmental remediation | ₹5,00,000 |
| Loss prevented (acting now) | 85% of total |

**Output — `PredictiveSimulationResult`:**
```python
@dataclass
class PredictiveSimulationResult:
    zone_id: str
    sim_hour: float
    current_probability: float
    time_to_critical_minutes: float | None  # None if trajectory is falling/stable
    trajectory: str                          # "rising" | "falling" | "stable"
    projections: list[TimeHorizonProjection] # one per horizon [5, 10, 15, 30]
    financial_impact: FinancialImpact
    recommended_action_summary: str
```

---

### Agent 10 — Decision Validation

**File:** `agents/agent_decision_validation.py` (136 lines)

**Purpose:** Independently challenge every RiskEvent from Agent 3. Count corroborating signals from multiple independent sources. Downgrade if evidence is thin; upgrade if overwhelming multi-source evidence is present.

**Corroboration signal sources:**
| Signal | Threshold |
|--------|-----------|
| Sensor anomaly | `max_anomaly_score ≥ 0.5` |
| Multiple sensors | `num_anomalous_sensors ≥ 2` |
| Dangerous permit combo | `combo_flag == True` |
| High context | `context_multiplier ≥ 2.5` |
| Shift handover | `handover == True` |
| Equipment risk | Zone equipment `failure_probability ≥ 0.4` |

**Required corroboration per severity:**
| Severity | Required signals |
|----------|----------------|
| HIGH | 3 |
| MEDIUM | 2 |
| LOW | 1 |
| INFO | 0 |

**Decision logic:**
```
if corroboration < required AND dissenting_factors ≥ 2:
    downgrade one severity level

elif corroboration ≥ required + 2 AND equipment.co_degradation_flag:
    upgrade one severity level

else:
    keep original severity
```

**The hard invariant is not bypassed by Agent 10:** A zero-context event cannot accumulate corroboration because `context_multiplier = 1.0` means neither `context_multiplier ≥ 2.5` nor `combo_flag` can be True.

**Output — `ValidationResult`:**
```python
@dataclass
class ValidationResult:
    original_event_id: str
    original_severity: str
    validated_severity: str           # may differ from original
    validation_confidence: float      # 0–1
    corroboration_count: int
    dissenting_factors: list[str]
    supporting_factors: list[str]
    upgraded: bool
    downgraded: bool
    validation_notes: str
```

---

## 6. Event Bus & Message Contracts

**File:** `backend/event_bus.py` (42 lines)

In-process `asyncio` pub/sub. Topics are strings; handlers can be sync or async. Message history is kept per topic (max 500 entries) for the audit log and replay.

```python
bus = EventBus()
bus.subscribe("risk.events", my_async_handler)
await bus.publish("risk.events", risk_event_object)
history = bus.history("risk.events", limit=50)
```

**Active topics:**

| Topic | Publisher | Subscribers | Message type |
|-------|-----------|------------|--------------|
| `sensor.readings` | Orchestrator | Agent 1 | `list[Reading]` |
| `sensor.anomalies` | Agent 1 | Agent 3 | `list[SensorAnomalyEvent]` |
| `context.zone` | Agent 2 | Agent 3 | `dict[str, ZoneContextEvent]` |
| `risk.events` | Agent 3 | Agents 4, 5, 6, 9, 10 | `RiskEvent` |
| `intelligence.briefing` | Agent 5 | Agent 6, dashboard | `IncidentIntelligenceBriefing` |
| `incident.report` | Agent 6 | Dashboard | `IncidentReport` |

**Upgrading to a real broker (production):**
The bus interface (`subscribe`, `publish`) maps directly to Kafka/NATS/Redis Streams. No agent code changes are required — only the `EventBus` class needs to be replaced with a broker-backed implementation.

---

## 7. Orchestrator

**File:** `backend/orchestrator.py` (436 lines)

The `Simulation` class wires generator + all agents + WebSocket broadcast. Runs as a continuous `asyncio` task.

### 7.1 Constructor parameters

```python
sim = Simulation(
    seed=42,                   # RNG seed for reproducibility
    tick_seconds=0.6,          # wall-clock interval between ticks
    sim_seconds_per_tick=12.0  # how many simulated seconds per tick
                               # (12s/tick → 1 sim-hour in 5 real minutes)
)
```

### 7.2 Key methods

| Method | Description |
|--------|-------------|
| `start()` | Begins the async tick loop as an `asyncio.Task` |
| `stop()` | Gracefully stops the loop |
| `trigger_scenario(id)` | Stops current scenario first, then injects new one |
| `stop_current_scenario()` | Clears anomalies + permits, keeps alert history |
| `reset()` | Full clean slate: history, reports, wear, score all reset |
| `pause()` | Sets `running = False` |
| `resume()` | Restarts the tick loop |
| `trigger_stress_test(n)` | Schedules N isolated anomalies with staggered timing |

### 7.3 Scenario isolation guarantee

`trigger_scenario` always calls `stop_current_scenario()` first:
```python
def trigger_scenario(self, scenario_id):
    self.stop_current_scenario()          # clear previous scenario state
    scenario.setup_fn(...)                 # inject new scenario
    self.active_scenario = {...}           # track for progress indicator
```

This prevents scenario stacking — a fundamental correctness property for demo reliability.

### 7.4 Safety score recovery

Safety score uses a **time-windowed lookback** (0.12 sim-hours ≈ last 10 real seconds) so it recovers naturally when an incident clears:

```python
recent = [a for a in alert_feed[:80]
          if abs(current_hour - a.sim_hour) < 0.12]
# Score computed only from recent alerts
# Smooth recovery: max +0.8 per tick upward
```

### 7.5 Safety score history

The orchestrator keeps a rolling 200-tick history of safety scores in `safety_score_history`. This powers the sparkline chart in the Overview sidebar and is exposed via `GET /api/metrics`.

---
---

## 8. Backend API Reference

**Base URL:** `http://localhost:8000`
**Auto-generated interactive docs:** `http://localhost:8000/docs`

All REST responses are JSON. All `limit` params default to 50 unless stated.

---

### Simulation State

#### `GET /api/health`
Quick liveness check.
```json
{
  "status": "ok",
  "tick_count": 142,
  "plant_safety_score": 87.3
}
```

#### `GET /api/state`
Full plant snapshot — use this for initial page load.
```json
{
  "sim_hour": 10.35,
  "tick_count": 142,
  "plant_safety_score": 87.3,
  "agent_status": {"SensorFusion": "idle", "CompoundRisk": "active", ...},
  "zones": [
    {
      "zone_id": "Z3",
      "name": "Confined Vessel Bay",
      "zone_class": "confined_space",
      "x": 50, "y": 80,
      "context": { ...ZoneContextEvent... },
      "risk":    { ...RiskEvent... },
      "equipment": { ...EquipmentHealthEvent... },
      "workers":   [ ...WorkerState dicts... ],
      "prediction": { ...PredictiveSimulationResult... }
    },
    ...  // 9 zones total
  ]
}
```

#### `GET /api/sim/status`
Lightweight simulation status (faster than /api/state).
```json
{
  "running": true,
  "tick_count": 142,
  "sim_hour": 10.35,
  "plant_safety_score": 87.3,
  "active_permits": 2,
  "active_maintenance": 0,
  "active_anomalies": 1
}
```

#### `GET /api/metrics`
Performance and knowledge graph metrics. Refreshed every tick.
```json
{
  "tick_ms": 45.2,
  "tick_count": 142,
  "active_scenario": {"id": "fatal_pattern", "name": "...", "zone_id": "Z3"},
  "safety_score_history": [96.0, 95.4, 87.3, ...],
  "alert_feed_size": 38,
  "report_count": 5,
  "kg_stats": {"total_nodes": 312, "total_edges": 624, "by_type": {...}},
  "active_permits": 2,
  "active_maintenance": 0,
  "active_anomalies": 1
}
```

---

### Alerts & Intelligence

#### `GET /api/alerts?limit=50`
Alert feed (most recent first). Each alert includes full provenance: validation, prediction, briefing, financial impact.

Key fields in each alert:
```json
{
  "event_id": "RE-1782799173683-846",
  "zone_id": "Z3",
  "severity": "HIGH",
  "calibrated_probability": 0.942,
  "estimated_lead_time_minutes": 8.5,
  "features": {
    "max_anomaly_score": 0.98,
    "num_anomalous_sensors": 2,
    "context_multiplier": 5.4,
    "combo_flag": 1.0,
    "handover": 1.0
  },
  "feature_attribution": {
    "max_anomaly_score": 6.38,
    "combo_flag": 1.21,
    "context_multiplier": 7.38,
    "handover": 0.63,
    "num_anomalous_sensors": 0.88
  },
  "gate_passed": true,
  "rule_trace": ["Collected 3 sensor readings...", "Context present...", "p=0.942 → HIGH"],
  "validation": {
    "validated_severity": "HIGH",
    "corroboration_count": 5,
    "downgraded": false,
    "dissenting_factors": []
  },
  "prediction": {
    "current_probability": 0.942,
    "time_to_critical_minutes": 8.0,
    "trajectory": "rising",
    "projections": [
      {"minutes_ahead": 5,  "projected_probability": 0.96, "projected_severity": "HIGH"},
      {"minutes_ahead": 10, "projected_probability": 0.98, "projected_severity": "CRITICAL"},
      ...
    ],
    "financial_impact": {
      "total_loss_inr": 7534162,
      "loss_prevented_if_acted_now_inr": 6404037,
      "downtime_days": 3.8,
      "lives_at_risk": 2
    }
  },
  "briefing": {
    "narrative": "Pattern matches precedent NM-114 (2023-07-02): ...",
    "source_citation": ["NM-114", "OISD-105-3.2"]
  }
}
```

#### `GET /api/sensors`
All 28 current sensor anomaly readings.

#### `GET /api/workers`
All 8 worker states with exposure risk and PPE status.

#### `GET /api/equipment`
Equipment health by zone. Each zone entry contains `equipment_states` array, `zone_equipment_risk`, `critical_equipment`, and `co_degradation_flag`.

---

### Knowledge Graph

#### `GET /api/graph/nodes`
All graph nodes and edges for the D3 visualisation. Capped at 300 nodes / 600 edges to keep payload manageable.

```json
{
  "nodes": [
    {"id": "Z3", "type": "Zone", "name": "Confined Vessel Bay", ...},
    {"id": "NM-114", "type": "HistoricalIncident", "title": "...", "summary": "..."},
    ...
  ],
  "edges": [
    {"source": "Z3-gas_h2s-0", "target": "Z3", "relation": "LOCATED_IN"},
    {"source": "NM-114", "target": "Z3", "relation": "LOCATED_IN"},
    ...
  ]
}
```

#### `GET /api/graph/query?zone_id=Z3&tags=hot_work,confined_space`
Targeted knowledge graph query. Returns matching incidents, guidelines, and overall stats.

#### `GET /api/zone/{zone_id}/history`
All historical incidents and live RiskEvents linked to a specific zone, with contributor lists.

---

### Reports

#### `GET /api/reports`
All auto-generated incident reports (array, newest first).

#### `GET /api/reports/{report_id}`
Single report as JSON.

#### `GET /api/reports/{report_id}/download`
Plain-text formatted report as a downloadable `.txt` file.
Response header: `Content-Disposition: attachment; filename="IR-....txt"`

---

### Comparison & Audit

#### `GET /api/comparison?limit=200`
Naive single-threshold detector vs SENTINEL comparison log. One entry per tick:
```json
[
  {
    "sim_hour": 10.35,
    "naive_fired": true,
    "multiagent_max_severity": "LOW",
    "plant_safety_score": 96.0
  },
  ...
]
```
This is the dataset that powers the comparison chart. Naive fires constantly; SENTINEL stays disciplined.

#### `GET /api/audit-log?limit=100`
Every agent decision logged in sequence:
```json
[
  {
    "timestamp": 1782799173.6,
    "sim_hour": 10.35,
    "agent": "Agent3",
    "summary": "HIGH risk event in Z3 (p=0.942)",
    "detail": {"event_id": "RE-..."}
  },
  ...
]
```

---

### Simulation Control

| Endpoint | Method | Effect |
|----------|--------|--------|
| `/api/sim/reset` | POST | Full reset: clears alerts, reports, permits, resets scores |
| `/api/sim/pause` | POST | Freezes simulation tick loop |
| `/api/sim/resume` | POST | Restarts simulation tick loop |
| `/api/sim/stop-scenario` | POST | Clears current scenario only (keeps alert history) |

---

### Scenarios & Testing

#### `GET /api/scenarios`
```json
[
  {
    "id": "fatal_pattern",
    "name": "Entrapped Gas + Hot Work + Handover (INC-088 pattern)",
    "description": "Recreates the historical fatal-incident pattern...",
    "zone_id": "Z3"
  },
  ...  // 4 scenarios total
]
```

#### `POST /api/scenarios/{scenario_id}/trigger`
Stops the current scenario first, then injects the new one.
- `fatal_pattern` — hot work + confined space entry + gas anomaly in Z3
- `breakdown_vibration` — breakdown maintenance + vibration spike in Z1
- `excavation_pressure` — excavation permit + pressure transient in Z5
- `reactor_combo` — hot work + pressure/temperature co-anomaly in Z6

#### `POST /api/stress-test/trigger?n_spikes=50`
Fires N isolated single-factor anomalies across random sensors with zero corroborating context. Anomalies are staggered (2 ticks apart). The alert feed should remain quiet — this is the live false-positive proof.

---

## 9. WebSocket Protocol

**Endpoint:** `ws://localhost:8000/ws`

Connect once; the server pushes events. Clients can send any text to keep the connection alive (heartbeat). The client-side reconnect timeout is 2 seconds.

### Message format

All messages are JSON with a `type` field:

```json
{"type": "tick",   "data": {...}}
{"type": "alert",  "data": {...}}
{"type": "system", "data": {...}}
```

### `tick` message (every ~0.6 seconds)

```json
{
  "type": "tick",
  "data": {
    "sim_hour": 10.35,
    "tick_count": 142,
    "plant_safety_score": 87.3,
    "zone_risks": {
      "Z3": {"severity": "HIGH", "probability": 0.942},
      "Z1": {"severity": "INFO", "probability": 0.0},
      ...
    },
    "agent_status": {
      "SensorFusion": "active",
      "CompoundRisk": "active",
      "KnowledgeGraph": "idle",
      ...
    },
    "tick_ms": 45.2,
    "active_scenario": {"id": "fatal_pattern", "name": "...", "zone_id": "Z3"},
    "scenario_ticks": 12
  }
}
```

### `alert` message (on MEDIUM or HIGH severity only)

Full alert payload — same structure as `/api/alerts` entries, including `validation`, `prediction`, `briefing`, and `financial_impact`.

### `system` message (on reset)

```json
{
  "type": "system",
  "data": {
    "message": "Simulation fully reset. Plant returned to healthy baseline.",
    "type": "reset"
  }
}
```

### Polling fallback

If WebSocket is unavailable, the frontend automatically falls back to polling:
- `/api/alerts` every 2.5 seconds
- `/api/workers` every 4 seconds
- `/api/comparison` every 3 seconds

---

## 10. Knowledge Graph

**File:** `knowledge_graph/seed_data.json`

The graph starts with 22 pre-seeded nodes (9 zones + 28 sensors + 17 incidents + 5 guidelines) and grows during each session as new RiskEvents are written into it.

### Adding historical incidents

Add to the `historical_incidents` array in `seed_data.json`:

```json
{
  "id": "NM-200",
  "title": "Near-miss: your description",
  "zone_id": "Z3",
  "date": "2024-01-15",
  "summary": "What happened and how it was resolved.",
  "tags": ["hot_work", "gas_ch4", "near_miss"],
  "severity": "near_miss"
}
```

**Tags must match permit types or sensor types** used in the system for Agent 5's retrieval to match them:
`hot_work`, `confined_space`, `shift_handover`, `gas_h2s`, `gas_ch4`, `pressure`, `temperature`, `vibration`, `breakdown_maintenance`, `preventive_maintenance`, `electrical_isolation`, `excavation`, `lifting`, `sensor_fault`, `false_alarm`, `near_miss`, `recordable`, `fatal`

### Adding regulatory guidelines

```json
{
  "id": "OISD-105-7.1",
  "title": "Your guideline title",
  "excerpt_summary": "The actual guidance text.",
  "tags": ["hot_work", "confined_space"]
}
```

### SIMILAR_TO edges

These are automatically computed at startup between historical incidents that share at least one tag. They allow the knowledge graph to surface related precedents even when the zone doesn't match exactly.

### Graph growth during a session

Each HIGH-severity RiskEvent adds:
- 1 `RiskEvent` node
- 1 `LOCATED_IN` edge to the zone
- N `CONTRIBUTED_TO` edges from anomalous sensors (score ≥ 0.5)
- M `CONTRIBUTED_TO` edges from active permits

After a scenario that produces 5 HIGH alerts, the graph grows by approximately 20–30 new nodes and edges. Judges literally watch the graph expand during the demo.

---

## 11. Frontend Dashboard

**File:** `frontend/index.html` (1,656 lines)

A single self-contained HTML file. No build step. No npm. No framework. Served directly by FastAPI as a static file. Opens in any modern browser.

**External dependency:** D3.js v7, loaded from `cdnjs.cloudflare.com`. If offline, the knowledge graph panel is empty; all other functionality works.

### 11.1 View modes

Two distinct views, toggled by buttons in the topbar:

**Command Center** (`⊞ Command`) — Dense grid layout:
```
┌──────────────────────────┬──────────────┬──────────────┐
│ Digital Twin (SVG)       │ Alert Feed   │ Knowledge    │
│ 9 zones + animated       │              │ Graph (D3)   │
│ pipelines + workers      │              │              │
├───────────┬──────────────┴──────────────┴──────────────┤
│ Agent     │ Comparison  │ Prediction   │ Copilot │ Ctrl│
│ Pipeline  │ Chart       │ +5/10/15/30m │         │     │
└───────────┴─────────────┴──────────────┴─────────┴─────┘
```

**Overview** (`≡ Overview`) — Scrollable card-based layout:
```
[Sidebar: Safety Score + Sparkline + Navigation]
[Main scroll]:
  Zone Status cards (3-column grid)
  Active Incident card (only when HIGH alert exists)
  Predictive Simulation (5-column probability display)
  Workers (row cards with exposure risk)
  Equipment Health (progress bars)
  Recent Events (timeline list)
```

### 11.2 Theme system

CSS custom properties on `body[data-theme]`. Toggle with the `◑` button in the topbar. Preference saved to `localStorage`.

**Dark theme** (default): Deep navy/black backgrounds, SCADA control room aesthetic.
**Light theme**: Clean white + steel grey, enterprise product feel.

Both themes apply to all panels, the SVG plant map, the D3 graph, the canvas comparison chart, and the drawer.

### 11.3 Digital Twin — SVG Plant

The plant map is built dynamically in JavaScript from the `ZONES` and `PIPES` arrays:

```javascript
const ZONES = [
  {id:'Z1', name:'Compressor House', x:80,  y:60,  w:110, h:70},
  {id:'Z3', name:'Confined Vessel',  x:400, y:60,  w:110, h:70},
  // ... 9 zones total
];
const PIPES = [
  ['Z1','Z2'], ['Z2','Z3'], ['Z1','Z4'], ...  // 12 pipeline connections
];
```

**Live zone coloring:** Updated on every `tick` WebSocket message. Zone background CSS class changes from `z-INFO` → `z-LOW` → `z-MEDIUM` → `z-HIGH` as severity escalates. HIGH zones pulse (CSS animation).

**Gas spread animation:** When a HIGH severity event fires, a growing translucent circle radiates out from the zone centre using SVG animation.

**Worker dots:** Small circles positioned within their assigned zone. Green = safe, amber = at_risk (blinks), red = danger (blinks faster).

### 11.4 Knowledge Graph (D3)

Force-directed layout using D3 v7. Node radius by type: Zone = 9px, HistoricalIncident = 7px, others = 5px. Drag-to-reposition. Hover to see node tooltip (title, type, summary excerpt, severity, date). Refreshed every 15 seconds to show new nodes added during incidents.

**Node colours:**
| Type | Colour |
|------|--------|
| Zone | Blue |
| Sensor | Green |
| HistoricalIncident | Red |
| RegulatoryGuideline | Purple |
| RiskEvent | Amber |
| Permit | Teal |

### 11.5 Explainability Drawer

Click any alert in the feed to open a side drawer with:
- Risk assessment (zone, severity, probability, lead time)
- Feature attribution bars (exact `coefficient × value` per feature, with visual bar proportional to magnitude)
- Full rule trace (step-by-step decision audit)
- Decision validation result (corroboration count, dissenting factors, upgrade/downgrade flag)
- Grounded historical precedent (Agent 5's narrative with source citation)
- Future projection bars (NOW / +5m / +10m / +15m / +30m)
- Financial impact grid (total exposure, prevented loss, downtime, workers at risk)
- Recommended action
- Report download and audit log buttons

### 11.6 Scenario Progress Banner

When a scenario is active, a fixed banner appears at the bottom of the screen showing:
- Scenario name and target zone
- Elapsed time (seconds)
- Progress bar (advances over time)
- One-click "Stop" button

Disappears automatically when the scenario is stopped or reset.

### 11.7 Safety Score Sparkline

In the Overview sidebar, a canvas sparkline shows the last 80 ticks of safety score history. The line is green for healthy scores and smoothly updates every tick via the safety score callback.

---

## 12. Simulation Controls & Scenarios

### 12.1 Simulation lifecycle

```
RESET → healthy baseline state
  ↓
START (automatic on server launch)
  ↓  ticking every 0.6s
TRIGGER SCENARIO → stop_current_scenario() first, then inject
  ↓  agents detect and escalate
PAUSE → tick loop frozen
RESUME → tick loop restarts
  ↓
STOP SCENARIO → clear injections + permits, keep history
  ↓
RESET → full clean slate
```

### 12.2 Scripted scenarios

| ID | Name | Zone | What's injected |
|----|------|------|----------------|
| `fatal_pattern` | Entrapped Gas + Hot Work + Handover | Z3 | HOT_WORK + CONFINED_SPACE_ENTRY permits, gas_ch4 anomaly 6.5σ for 20 ticks |
| `breakdown_vibration` | Breakdown Maintenance + Vibration | Z1 | BREAKDOWN maintenance, vibration anomaly 5.5σ for 15 ticks |
| `excavation_pressure` | Excavation Near Live Line | Z5 | EXCAVATION permit, pressure anomaly 5.0σ for 15 ticks |
| `reactor_combo` | Hot Work + Reactor Co-Anomaly | Z6 | HOT_WORK permit, pressure 5.5σ + temperature 4.5σ for 15 ticks |

### 12.3 Stress test

`POST /api/stress-test/trigger?n_spikes=50`

Schedules 50 single-factor anomalies across randomly selected sensors, staggered 2 ticks apart. No permits, no maintenance, no handover window. Every anomaly should produce only LOW or INFO severity — the live false-positive proof.

### 12.4 Adding a new scenario

In `generator/scenarios.py`:

```python
def _scenario_my_new_scenario(stream_gen, ctx_gen, start_hour):
    zone_id = "Z4"
    ctx_gen.issue_permit(zone_id, PermitType.ELECTRICAL_ISOLATION,
                         at_hour=start_hour - 0.1, duration_hours=2.0)
    from generator.plant_model import ZONE_BY_ID
    pressure_sensor = next(
        s for s in ZONE_BY_ID[zone_id].sensors if "pressure" in s.sensor_id
    )
    stream_gen.inject_anomaly(pressure_sensor.sensor_id,
                              magnitude_std=5.0, duration_ticks=15,
                              tag="my_scenario")

SCENARIOS["my_scenario"] = Scenario(
    "my_scenario",
    "My New Scenario",
    "Description of what this simulates.",
    "Z4",
    _scenario_my_new_scenario
)
```

Restart the server. The scenario appears in the dashboard controls and the command interface automatically.

---

## 13. False-Positive Invariant & Testing

This is the single most important credibility property of the platform.

### 13.1 The invariant

**Statement:** A single anomalous sensor reading, in a zone with no active permit and no active maintenance activity, must never produce a MEDIUM or HIGH severity alert, regardless of how extreme the anomaly magnitude is.

**Enforcement mechanism:**
```python
# In agents/agent3_compound_risk.py
def _gate(ctx: ZoneContextEvent) -> tuple[bool, str]:
    if not ctx.has_operational_context:
        # Model is NEVER called. Severity capped at LOW/INFO.
        return False, "No corroborating context"
    return True, "Context present — learned layer evaluated"
```

This is enforced by code logic, not by training. Changing the model or its training data cannot violate this invariant.

### 13.2 Running the test suite

```bash
python3 tests/test_false_positive_invariant.py
```

Expected output:
```
Running single-factor false-positive invariant test (250 trials)...
Severities observed: {'INFO': 0, 'LOW': 250, 'MEDIUM': 0, 'HIGH': 0}
False positive rate (MEDIUM/HIGH): 0.00%
ZERO violations — hard invariant holds across all trials.

Running corroborated-context control test (100 trials)...
HIGH severity rate: 100/100 (100.0%)

All invariant assertions passed.
```

### 13.3 Test methodology

**Single-factor test (250 trials):**
- Random sensor in a random zone
- Forced 3–10 standard deviations above its shift-aware baseline
- Zero active permits, zero maintenance activity
- Handover window confirmed False by choosing mid-shift hours (00:36–05:24)
- Full Agent 1 → Agent 2 → Agent 3 pipeline run
- Assert: severity is never MEDIUM or HIGH

**Control test (100 trials):**
- HOT_WORK + CONFINED_SPACE_ENTRY permits active in Z3
- Sensor forced 5–9 standard deviations above baseline during handover window
- Full pipeline run
- Assert: severity is HIGH every time (gate doesn't over-suppress)

Both tests must pass. The control group is as important as the single-factor test — it proves the gate is genuinely selective, not just silent.

### 13.4 Live stress test demo

During a presentation:
1. Click "Fire 50 Isolated Anomalies" (or type `stress 50` in the command interface)
2. Watch the Comparison strip: red bars appear at the top (naive detector fires) while the SENTINEL severity track stays flat
3. Point at False Positives metric: **0**
4. Type `alerts 10` in the command interface to confirm only LOW/INFO events

This is demoable in under 45 seconds and directly proves the invariant to a live audience.

---

## 14. Configuration & Tuning

### 14.1 Simulation speed

In `backend/main.py`:
```python
sim = Simulation(
    seed=42,
    tick_seconds=0.6,          # wall-clock interval between ticks
    sim_seconds_per_tick=12.0  # simulated time per tick
)
```

| `tick_seconds` | Feel | CPU impact |
|----------------|------|-----------|
| 0.3 | Very fast, smooth animations | High |
| 0.6 | Default — demo-optimal | Medium |
| 1.5 | Slower, lower CPU | Low |

### 14.2 Anomaly detection sensitivity

In `agents/agent1_sensor_fusion.py`:
```python
MIN_TRAIN = 30        # samples before IF model is trained
RETRAIN_EVERY = 40    # ticks between retraining
n_estimators = 40     # IF trees (more = more accurate, slower)
contamination = 0.05  # assumed fraction of anomalies in training data
```

### 14.3 Risk escalation thresholds

In `agents/agent3_compound_risk.py`:
```python
ANOMALY_SCORE_THRESHOLD = 0.5  # sensor counts as "anomalous" above this

# Severity thresholds
HIGH   = 0.75
MEDIUM = 0.45
LOW    = 0.20
```

Lowering `HIGH` to 0.65 produces more HIGH alerts (more sensitive). Raising it to 0.85 makes HIGH alerts rarer (more conservative). For a demo, the defaults work well because the scripted scenarios reliably hit p > 0.90.

### 14.4 Context multiplier weights

In `generator/context_generator.py`:
```python
PERMIT_RISK_WEIGHT = {
    PermitType.HOT_WORK: 0.9,
    PermitType.CONFINED_SPACE_ENTRY: 0.85,
    # ... etc
}
DANGEROUS_PERMIT_COMBOS = {
    frozenset({PermitType.HOT_WORK, PermitType.CONFINED_SPACE_ENTRY}): 1.8,
}
HANDOVER_MULTIPLIER_BONUS = 0.25
MAINTENANCE_BREAKDOWN_BONUS = 0.30
```

### 14.5 Financial impact parameters

In `agents/agent_predictive_simulation.py`:
```python
PRODUCTION_LOSS_PER_HOUR_INR = 800_000   # ₹8 lakh/hour
DOWNTIME_REPAIR_COST_INR     = 2_500_000  # ₹25 lakh one-time
REGULATORY_FINE_INR          = 1_200_000  # ₹12 lakh (OISD)
ENVIRONMENTAL_DAMAGE_INR     = 500_000    # ₹5 lakh remediation
```

Adjust these to match your target client's actual production economics for maximum business impact in the demo.

---

## 15. Deployment

### 15.1 Local (development)

```bash
# Unzip and enter directory
unzip safety-platform.zip && cd safety-platform

# Install dependencies
pip install -r requirements.txt

# Start (one command)
./start.sh
# or: uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# Open dashboard
open http://localhost:8000
```

### 15.2 Requirements

```
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
pydantic>=2.0.0
numpy>=1.26.0
scipy>=1.12.0
pandas>=2.2.0
scikit-learn>=1.4.0
networkx>=3.3
websockets>=12.0
```

Python 3.11 minimum; 3.12 recommended.

### 15.3 Docker (production-ready path)

Create `Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t sentinel-ai .
docker run -p 8000:8000 sentinel-ai
```

### 15.4 Windows notes

If uvicorn exits with "RuntimeError: Event loop is closed":
```bash
uvicorn backend.main:app --loop asyncio --port 8000
```

### 15.5 Offline operation

Everything except the D3 knowledge graph visualisation works offline. D3 is loaded from `cdnjs.cloudflare.com`. For fully offline operation, download D3 v7 and replace the CDN `<script>` tag in `frontend/index.html` with a local path.

---

## 16. Strategic Roadmap

The following roadmap is drawn from the architectural improvement analysis (v3.0) and is organised in three phases by impact and implementation complexity.

### Phase 1 — Make the system genuinely autonomous (Highest impact)

These additions transform SENTINEL from a correlation dashboard into an autonomous AI system that reasons, plans, and decides.

#### 16.1 Strategic Intelligence Engine ⭐⭐⭐⭐⭐

**What it is:** A "Chief Safety Officer" agent that receives all ten agent outputs and produces a unified **Situation Assessment → Decision → Action Plan → Business Impact** package. Without this, agents feel independent. With it, they become a coordinated intelligence system.

**Why it matters:** Currently the system produces ten independent outputs. The Strategic Intelligence Engine synthesises them into one coherent narrative that answers: "What is actually happening? What should we do about it? What is the business consequence?"

**Implementation sketch:**
```python
class StrategicIntelligenceEngine:
    def assess(self,
               risk_events: list[RiskEvent],
               predictions: dict[str, PredictiveSimulationResult],
               equipment_health: dict[str, EquipmentHealthEvent],
               worker_safety: dict[str, WorkerSafetyEvent],
               validation_results: dict[str, ValidationResult]) -> SituationAssessment:
        # 1. Rank zones by compound risk score
        # 2. Identify the dominant threat vector
        # 3. Evaluate 3 response strategies (shutdown / partial / monitor)
        # 4. Select optimal strategy by minimising expected loss
        # 5. Generate executive narrative
        pass
```

#### 16.2 Risk Propagation Engine ⭐⭐⭐⭐⭐

**What it is:** Uses the plant's pipeline topology (the same graph used in the SVG twin) to model how a risk in one zone can cascade to adjacent zones.

**Why it matters:** Currently each zone is assessed independently. A gas leak in Z3 affects Z2 and Z5 through connected pipelines — but the system doesn't model this. Risk propagation makes the system think like a plant engineer, not a collection of sensors.

**Implementation sketch:**
```python
def propagate_risk(source_zone: str, source_probability: float,
                   adjacency: dict[str, list[str]]) -> dict[str, float]:
    # BFS from source zone through pipeline graph
    # Each hop attenuates probability by 0.4
    # Returns {zone_id: cascaded_risk_score}
```

**Demo impact:** Trigger a scenario in Z3 → watch risk ripple across the plant map to Z2 and Z5, with attenuated but non-zero scores. Judges immediately understand "cascading failures" without explanation.

#### 16.3 Root Cause Analysis Engine

**What it is:** Works backward from an observed HIGH severity event through the causal chain to identify the root cause, not just the symptoms.

**Output example:**
```
Gas concentration HIGH
    ← Gas sensor anomaly (Z3-gas_ch4-1, score=0.98)
        ← Pressure relief valve V-102 degraded (health=43%)
            ← No scheduled maintenance in 847 hours (last: 2024-01-14)
                ← ROOT CAUSE: Maintenance schedule gap for V-102
```

**Implementation:** Reverse-traverse the knowledge graph from the RiskEvent node through `CONTRIBUTED_TO` edges and `SIMILAR_TO` incident links.

#### 16.4 Decision Simulator

**What it is:** Before generating recommendations, the system evaluates N response strategies side-by-side and selects the one that minimises expected loss.

**Output example:**
```
Strategy A — Full shutdown
  Cost: ₹45L production loss
  Risk reduction: 98%  →  Residual risk: 2%

Strategy B — Permit suspension only
  Cost: ₹8L downtime
  Risk reduction: 71%  →  Residual risk: 9%

Strategy C — Monitoring only
  Cost: ₹0
  Risk reduction: 0%   →  Residual risk: 87%

→ Recommended: Strategy B (optimal expected value)
```

#### 16.5 Business Intelligence Layer ⭐⭐⭐⭐⭐

**What it is:** A dedicated executive view showing the metrics that CEOs and plant managers actually care about: money saved, downtime avoided, compliance score, insurance risk impact, carbon emissions.

**Missing from current system:** The current financial impact is per-alert. The BI layer aggregates across the session and shows trends, not just instantaneous snapshots.

**New metrics to add:**
- Cumulative loss prevented (INR, session total)
- Compliance score (0–100, vs OISD/ISO 45001 checklist items)
- Insurance risk category (Low / Medium / High / Critical)
- Estimated carbon impact of detected incidents (CO₂ tonnes)
- Plant health index (composite of safety + mechanical + electrical + chemical + worker)

#### 16.6 Agent Communication Visualisation ⭐⭐⭐⭐⭐

**What it is:** An animated diagram showing which agent is sending what to which other agent, in real time. Each message is a traveling dot on an animated arrow.

**Why it matters:** This single feature communicates the multi-agent architecture better than any slide. Judges immediately understand "this is genuinely distributed intelligence, not a monolith with a fancy dashboard."

**Implementation:** Extend the existing agent pipeline panel with animated CSS transitions triggered by the WebSocket `tick` message's `agent_status` field. Each agent box lights up in sequence; a traveling dot animates along the connecting arrow between boxes.

---

### Phase 2 — Deepen intelligence (High impact)

#### 16.7 Dynamic Knowledge Graph (reasoning, not storage)

Upgrade the KG from a storage system to a reasoning system. Currently: nodes connect to zones. Target: nodes connect to everything relevant — previous accidents, safety regulations, adjacent equipment, emergency exits, gas line routing.

Then Agent 5 can answer: "The last time this sensor pattern occurred at this zone, it preceded NM-114, and gas reached the emergency exit in 8 minutes."

#### 16.8 Incident Memory System

Give workers and equipment individual memory:
```python
worker_memory["K. Singh"] = {
    "ppe_violations_this_week": 6,   # flagged
    "time_in_high_risk_zones_hrs": 14.2,
    "incidents_witnessed": ["NM-141"]
}
equipment_memory["V-102"] = {
    "days_since_last_maintenance": 847,
    "failure_history": ["2023-03-14 pressure drop"],
    "similar_to_incident": "INC-095"
}
```

Then SENTINEL says: "Worker K. Singh has removed PPE 6 times this week and is currently in Z3 during a HIGH severity event" — a specific, actionable, traceable statement.

#### 16.9 AI Replay Engine

A scrub bar that replays the entire session — plant map, knowledge graph, alert feed — frame by frame. The entire factory animates backwards and forwards.

Each replay frame is a snapshot of the in-memory state at a tick. The orchestrator already maintains `comparison_log` and `alert_feed` — the replay engine is a frontend feature that scrubs through them.

#### 16.10 AI Compliance Officer

A dedicated agent that checks every HIGH-severity event against a ruleset of OSHA / ISO 45001 / OISD requirements and automatically generates a compliance report with pass/fail items and specific regulation references.

---

### Phase 3 — Differentiation features (If time allows)

- **Natural language query system** — Full LLM integration in the copilot (currently template-based), with the knowledge graph as context
- **Self-learning layer** — After each incident, feed outcome back to adjust model weights (even simulated feedback shows the concept)
- **Emergency route optimiser** — "Route A: 35 sec (recommended). Route B: 51 sec. Route C: blocked."
- **3D Digital Twin** — Three.js scene with the plant in 3D; risk propagation as volumetric heat maps
- **Scenario generator** — Random compound scenarios beyond the 4 scripted ones
- **Plant health index** — Single composite score across Safety, Mechanical, Electrical, Chemical, Worker categories

---

### Architectural target state

```
Sensors
    ↓
AI Agents (current: 10 agents)
    ↓
AI Reasoning (Strategic Intelligence Engine)
    ↓
AI Planning (Decision Simulator)
    ↓
AI Decision (optimal strategy selection)
    ↓
AI Action (Response Orchestration → Human approval → Execute)
    ↓
Dashboard (last step, not the product)
```

---

## 17. Glossary

| Term | Definition |
|------|-----------|
| **Anomaly score** | 0–1 output of the Isolation Forest per sensor per tick. 0 = normal, 1 = highly anomalous. Not a fixed threshold — relative to the learned shift-specific baseline. |
| **Calibrated probability** | The logistic regression's output probability that a compound risk event will escalate to an incident if no action is taken. Used directly as the risk score shown to operators. |
| **Combo flag** | Boolean: True when both HOT_WORK and CONFINED_SPACE_ENTRY permits are active simultaneously in a zone. Triggers a 1.8× multiplicative bonus on the context multiplier. |
| **Context multiplier** | Per-zone risk amplifier ≥1.0 computed by Agent 2. Combines permit weights, maintenance type, and handover window state. A multiplier of 5.4× means the zone's operational context is 5.4× more hazardous than baseline. |
| **Co-degradation flag** | Agent 7 flag: True when ≥2 equipment items in the same zone are simultaneously degraded or critical. Used by Agent 10 as an upgrade signal. |
| **Compound risk** | Risk that emerges from the interaction of multiple independent factors (sensor anomaly + operational context) that no single factor would produce alone. The core concept the system is designed to detect. |
| **Corroboration count** | Agent 10's count of independent sources confirming a risk claim. HIGH requires ≥3; MEDIUM requires ≥2. A claim with insufficient corroboration is downgraded. |
| **Feature attribution** | For each RiskEvent, the exact contribution of each input feature to the risk score: `coefficient × feature_value`. Exact for a linear model — no approximation needed. |
| **Gate (hard invariant gate)** | The rule in Agent 3 that runs before the ML model: if no operational context is present, severity is capped at LOW/INFO regardless of sensor readings. |
| **has_operational_context** | Agent 2 flag: True only if ≥1 active permit OR ≥1 active maintenance activity is present. A handover window alone does NOT set this flag. |
| **Handover window** | ±20 simulated minutes around each shift boundary (06:00, 14:00, 22:00). Sensor noise is elevated (×1.6 std) and the context multiplier receives a +0.25 bonus during this period. |
| **Isolation Forest** | Unsupervised anomaly detection algorithm that isolates anomalies by randomly partitioning the feature space. Naturally produces a continuous anomaly score without requiring a labelled dataset. |
| **Knowledge graph** | NetworkX `MultiDiGraph` containing zones, sensors, permits, risk events, historical incidents, and regulatory guidelines, connected by typed relationships. Grows during each session. |
| **Lead time** | Estimated minutes until the current trajectory reaches an incident threshold. Only computed for MEDIUM/HIGH events. Formula: `max(2, 45 × (1-p) × (1 - max_score×0.5))`. |
| **Naive detector** | A fixed-threshold detector (3σ from a frozen, non-shift-aware baseline) run side-by-side on the same data for comparison. Represents what most plants actually run today. |
| **Plant safety score** | 0–100 composite score computed from recent (time-windowed) HIGH/MEDIUM alerts, equipment failure risk, and worker exposure. Recovers at +0.8/tick when conditions improve. |
| **RAG** | Retrieval-Augmented Generation — the approach used by Agent 5: retrieve relevant historical incidents and regulatory excerpts from the knowledge graph, then generate a narrative grounded in those retrieved documents. |
| **Risk event** | A structured output from Agent 3 containing zone, severity, calibrated probability, feature attribution, rule trace, and full provenance. Written into the knowledge graph and broadcast to all subscribers. |
| **Scenario isolation** | The property that triggering a new scenario always stops the current one first. Prevents permit and anomaly stacking that would inflate risk scores unrealistically. |
| **Shift-aware baseline** | Agent 1 maintains separate rolling windows per sensor per shift. A night-shift reading is scored against night-shift history, not a global average. |
| **Stress test** | A batch injection of N isolated single-factor anomalies across random sensors with zero corroborating context. Used to prove the false-positive invariant live during a demo. |
| **Validated severity** | The final severity after Agent 10's review, which may differ from Agent 3's original output if corroboration was insufficient or overwhelming. |
| **Z-score** | `(value - rolling_mean) / rolling_std` for the current sensor+shift. Used in the cold-start anomaly score fallback before the Isolation Forest has enough samples to train. |
| **Zone class** | One of: `process_unit`, `confined_space`, `storage`, `utility`, `control_room`. Determines which sensor types are deployed in a zone and its base inherent risk weight. |

---

*SENTINEL AI v2.0 — Industrial Safety Intelligence Platform*
*Generated from source: 4,450 lines across 21 files*
