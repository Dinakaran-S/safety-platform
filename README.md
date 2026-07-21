# SENTINEL AI — Industrial Safety Intelligence Platform

> *"Traditional systems say: Gas level exceeded threshold. SENTINEL says: Gas concentration is rising while a Hot Work Permit is active, maintenance is ongoing, shift handover has begun, and the pattern matches a historical near-miss with 96% confidence. Estimated time to critical: 8 minutes. Recommended action: Suspend Permit HW-2291, evacuate Zones 2 and 3 immediately."*

## Quick Start

```bash
./start.sh
# or: uvicorn backend.main:app --reload --port 8000
```

Open **http://localhost:8000** — full command-center dashboard, no build step.

Run the automated false-positive proof:
```bash
python3 tests/test_false_positive_invariant.py
```

## Ten-Agent Architecture

| Agent | Responsibility |
|---|---|
| 1 — Sensor Fusion | Shift-aware rolling baselines + Isolation Forest |
| 2 — Operational Context | Permit/maintenance tracking, context multipliers |
| 3 — Compound Risk Correlation | Hard gate + logistic regression, feature attribution |
| 4 — Knowledge Graph | Live networkx graph, 17 seeded incidents, 5 regulatory excerpts |
| 5 — Incident Intelligence | RAG grounded in graph — cites real node IDs, never hallucinates |
| 6 — Response Orchestration | Structured incident reports + ranked action checklists |
| 7 — Equipment Health | Health scores, failure probability, co-degradation detection |
| 8 — Worker Safety | Exposure tracking, PPE compliance, evacuation priority |
| 9 — Predictive Simulation | Risk at +5/10/15/30 min, INR financial impact, time-to-critical |
| 10 — Decision Validation | Challenges Agent 3, downgrades weak evidence |

All agents communicate via async pub/sub event bus — no direct calls between agents.

## Core Invariant (Proven)

A single anomalous sensor with no corroborating operational context **never** produces MEDIUM or HIGH severity — enforced by a hard rule gate, proven at 0% false positive rate across 250 randomised trials. See TEST_RESULTS.md.

## Dashboard Highlights

- Live SVG digital twin with animated pipelines and worker dots
- Gas spread animation on HIGH severity events
- Agent pipeline visualization (watch each agent light up)
- Predictive simulation panel (+5/10/15/30 min risk trajectory)
- Financial impact in INR (production loss, fines, downtime)
- D3 force-directed knowledge graph (live, draggable)
- Naive vs SENTINEL comparison strip
- AI Safety Copilot (grounded Q&A)
- Click-through explainability drawer (rule trace, feature attribution, precedent)

## Key API Endpoints

GET  /api/state          — full plant snapshot
GET  /api/alerts         — alert feed with predictions + briefings
GET  /api/workers        — live worker exposure
GET  /api/comparison     — naive vs multi-agent log
GET  /api/graph/nodes    — knowledge graph for visualisation
POST /api/scenarios/{id}/trigger
POST /api/stress-test/trigger?n_spikes=50
WS   /ws                 — real-time push (tick + HIGH alerts)

## Structure

generator/ agents/ backend/ frontend/ knowledge_graph/ tests/
See ARCHITECTURE.md, AGENT_CONTRACTS.md, DEMO_SCRIPT.md, TEST_RESULTS.md
