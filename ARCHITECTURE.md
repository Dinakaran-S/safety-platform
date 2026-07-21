# Architecture

## Why multi-agent, not a monolith

Each agent owns one narrow responsibility, has its own internal state, and
talks to the others only through typed messages on a shared event bus — not
direct function calls. This means: (1) Agent 3's correlation logic can be
audited and tested in total isolation from sensor/context generation, (2)
new data sources can be added as new agents without touching existing ones,
and (3) the "decision audit log" is a natural byproduct of message-passing
rather than something bolted on afterward.

## The pipeline, in plain language

```
                         ┌─────────────────────┐
   raw sensor readings → │  AGENT 1            │
   (gas/pressure/temp/   │  Sensor Fusion       │ → per-sensor anomaly score (0-1),
   vibration, per zone)  │  rolling shift-aware  │   continuous, NOT a fixed threshold
                         │  baseline + Isolation │
                         │  Forest               │
                         └──────────┬───────────┘
                                    │
   permits / maintenance /         │
   shift schedule          ┌───────▼────────┐
        │                  │  AGENT 3        │
        └────────────────► │  Compound Risk  │
   ┌─────────────────┐     │  Correlation    │ → RiskEvent (severity, calibrated
   │  AGENT 2          │──►│  (the "brain")  │   probability, full provenance)
   │  Operational      │    │                 │
   │  Context           │    │  RULE LAYER     │
   │  context risk      │    │  (hard gate,    │
   │  multiplier per    │    │  auditable) +   │
   │  zone               │    │  LEARNED LAYER  │
   └─────────────────┘     │  (logistic       │
                            │  regression,     │
                            │  interpretable)  │
                            └───────┬──────────┘
                                    │ RiskEvent
                     ┌──────────────┼───────────────┐
                     ▼              ▼                ▼
            ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
            │  AGENT 4    │ │  AGENT 5      │ │  AGENT 6      │
            │  Knowledge  │ │  Incident      │ │  Response     │
            │  Graph /    │ │  Intelligence  │ │  Orchestration│
            │  Memory     │ │  (RAG, only on │ │  (on HIGH:    │
            │  every      │ │  HIGH sev.,    │ │  structured   │
            │  RiskEvent  │◄┤  grounded in   │ │  report +     │
            │  written in │ │  the graph,    │ │  ranked       │
            │  linked to  │ │  cites real    │ │  action       │
            │  contrib.   │ │  node IDs --   │ │  checklist)   │
            │  entities   │ │  never         │ └──────────────┘
            │             │ │  hallucinated) │
            └─────────────┘ └──────────────┘
```

## The hard invariant (the single most important property)

Agent 3's rule layer runs a gate **before** the learned layer is even
consulted:

```
if zone has no active permit AND no active maintenance:
    severity is capped at LOW/INFO, full stop.
    (the learned model is never even called)
```

This makes the "single sensor anomaly never escalates alone" property true
by construction, not by training. A shift-handover window alone does NOT
satisfy this gate either — handover is only an amplifier on top of genuine
operational context, never a substitute for it. See
[`AGENT_CONTRACTS.md`](AGENT_CONTRACTS.md) for the exact gate logic and
[`TEST_RESULTS.md`](TEST_RESULTS.md) for the proof.

## Event bus

An in-process `asyncio`-based pub/sub bus (`backend/event_bus.py`) — not
Kafka. At hackathon/single-process scale, a real broker buys nothing but
operational risk; the *message contracts* (documented in
`AGENT_CONTRACTS.md`) are what would port directly to Kafka/NATS/etc. in a
production deployment.

Topics: `sensor.readings`, `sensor.anomalies`, `context.zone`,
`risk.events`, `intelligence.briefing`, `incident.report`.

## Data flow per simulated tick

1. `SensorStreamGenerator.tick()` produces one `Reading` per sensor
   (shift-aware baseline + noise + any active injected anomaly).
2. Agent 1 scores each reading against its sensor-and-shift-specific rolling
   Isolation Forest model → `SensorAnomalyEvent`.
3. Agent 2 snapshots active permits/maintenance per zone → `ZoneContextEvent`.
4. Agent 3 evaluates every zone that produced a sensor event: gate check,
   then (if passed) the calibrated logistic regression → `RiskEvent`,
   published on `risk.events`.
5. Agent 4 writes the `RiskEvent` into the knowledge graph, linked to its
   contributing permits/sensors.
6. If severity is HIGH: Agent 5 queries the graph for the closest precedent
   + regulatory guideline, Agent 6 generates the structured incident report
   and ranked action checklist.
7. The orchestrator's in-memory state (alert feed, zone risk map, audit log,
   naive-vs-multi-agent comparison log) is updated and is what the FastAPI
   endpoints read from.

## Baseline-vs-compound comparison (`/api/comparison`)

A naive detector runs side-by-side on the exact same sensor stream: a fixed
3-sigma threshold against a frozen, non-shift-aware baseline captured once
at warm-up — this is what most plants effectively run today. The dashboard
strip directly visualizes how often it fires (noise) against how often the
multi-agent pipeline actually escalates severity (signal).
