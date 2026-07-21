# SENTINEL AI — Demo Script (5 minutes)

**Setup**: `./start.sh`, browser at http://localhost:8000, let run ~30s for baselines.

---

## SCENE 1 — HEALTHY PLANT (30s)

Dashboard opens. Everything green. Animated pipelines flowing. Workers moving. Machines running.

> "This is a normal industrial facility operating under continuous AI surveillance.
> Every sensor, every permit, every worker — all connected through ten specialised AI agents."

Point at the Agent Pipeline panel — all boxes lit green, processing each tick.

---

## SCENE 2 — THE FALSE POSITIVE PROOF (45s)

Click **"Fire 50 Isolated Anomalies"**.

> "I'm injecting 50 random sensor spikes — 3 to 9 standard deviations above baseline
> — across every zone, with zero active permits and zero maintenance context."

Watch: alert feed stays quiet. Comparison strip shows red bars (naive fires) vs flat bottom (SENTINEL stays disciplined).

> "A traditional fixed-threshold system would generate dozens of alarms right now.
> SENTINEL understands that an isolated spike with no operational context is noise, not danger.
> Zero false positives. Proven across 250 automated trials."

Point at False Positives metric in top bar: **0**.

---

## SCENE 3 — THE INCIDENT BUILDS (60s)

Click **"Entrapped Gas + Hot Work + Handover (INC-088 pattern)"**.

> "Now I'm recreating the exact pattern behind a real fatal incident from our knowledge base."

Watch Zone 3 (Confined Vessel Bay) on the plant map:
- Background slowly shifts from dark to amber to red
- Gas spread animation emanates from the zone
- Workers in Z3 turn from green to red dots
- Knowledge Graph expands — new RiskEvent node connects to the Permit nodes and Sensor nodes

> "No single event triggered anything. But when the gas anomaly combines with the
> hot-work permit and confined-space-entry permit simultaneously — that's the compound
> pattern that has killed people."

---

## SCENE 4 — THE PREDICTION (45s)

Zone 3 hits HIGH. A red blinking badge appears in the alert feed.

Point at the Predictive Simulation panel:

> "SENTINEL isn't just showing us current danger. It's showing us the future."

| NOW | +5 min | +10 min | +15 min | +30 min |
|---|---|---|---|---|
| 94% | ~96% | ~98% | CRITICAL | CRITICAL |

> "If nothing changes, this zone reaches CRITICAL in approximately 8 minutes.
> Six workers are potentially exposed. Expected financial impact: ₹3.8 crore."

Point at the Financial Impact in the top bar.

---

## SCENE 5 — AI THINKING (30s)

Point at Agent Pipeline panel — boxes lighting up in sequence:

> "Watch the agents working. Sensor Fusion detected the anomaly. Operational Context
> confirmed active permits. Equipment Health flagged co-degradation. Worker Safety
> identified exposed personnel. Compound Risk correlated all of it. Knowledge Graph
> stored it. Decision Validation challenged the finding — confirmed. Incident Intelligence
> retrieved a historical precedent. Predictive Simulation projected the future.
> Response Orchestration generated the action plan."

All ten agents, under 500ms.

---

## SCENE 6 — EXPLAINABILITY (45s)

Click the HIGH alert. The detail drawer slides open.

> "Every number is traceable."

Point at Feature Attribution bars:
- `max_anomaly_score: +4.1` → sensor evidence
- `combo_flag: +1.2` → dangerous permit combination
- `context_multiplier: +3.3` → compounded by active work

Point at Grounded Historical Precedent:
> "SENTINEL retrieved precedent NM-114 and INC-088 from its knowledge graph.
> These are real synthetic records — the system cannot invent a citation that
> doesn't exist as an actual node."

Point at Decision Validation:
> "Even Agent 3's own decision was independently challenged. Corroboration count:
> 5 out of 3 required signals. Confidence: high. No downgrade warranted."

---

## SCENE 7 — RESPONSE & IMPACT (30s)

> "Recommended actions, generated instantly and ranked by urgency:"

1. Evacuate non-essential personnel from Zone 3
2. Suspend Permit P-XXXXXX (hot work)
3. Suspend Permit P-XXXXXX (confined space entry)
4. Halt all concurrent hot-work / confined-space operations
5. Notify both outgoing and incoming shift supervisors
6. Dispatch gas-test officer for physical verification
7. Log for formal incident investigation

> "This report is fully structured and downloadable — ready for SCADA/alerting system integration."

---

## SCENE 8 — AI COPILOT (20s)

Type in the Copilot: *"Why is Z3 dangerous?"*

> "The AI Copilot answers in natural language, grounded in live plant data and the
> knowledge graph. It cites sources. It doesn't guess."

---

## CLOSE (15s)

> "SENTINEL doesn't just detect danger. It understands the factory.
> It reasons across permits, maintenance, sensors, workers, and 17 historical incidents
> — all at once, in real time, with every decision fully explainable and auditable.
> That's the intelligence layer industrial safety has been missing."

Silence. Let judges absorb.

---

## Common Judge Questions

**"Why not just use rules?"** Rules can't learn shift-aware baselines or catch unknown combinations. SENTINEL uses rules for the hard invariant (the gate) and ML for calibrated probability — best of both.

**"How is this different from existing SCADA?"** SCADA monitors sensors in isolation. SENTINEL correlates sensor anomalies with permit state, maintenance activity, worker locations, equipment health, and 17 historical precedents simultaneously.

**"Can it hallucinate a precedent?"** No — Agent 5 only cites node IDs that physically exist in the knowledge graph. If no relevant node exists, it says so.

**"What's the false positive rate?"** 0.00% on single-factor anomalies, proven by automated test suite (250 trials). The control group confirms it can still escalate correctly — 100% HIGH when evidence is genuinely compound.

**"How does it scale?"** Event bus and agent interfaces are designed to replace asyncio queues with Kafka/NATS with no agent code changes. Knowledge graph nodes are additive. Sensor count scales linearly.
