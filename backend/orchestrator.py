"""
Central orchestrator — V2.
Wires generator + all agents (1-6, equipment health, worker safety,
predictive simulation, decision validation) via the EventBus.
Maintains live state for API + WebSocket broadcast.
"""
import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from agents.agent1_sensor_fusion import SensorFusionAgent
from agents.agent2_operational_context import OperationalContextAgent
from agents.agent3_compound_risk import CompoundRiskCorrelationAgent
from agents.agent4_knowledge_graph import KnowledgeGraphAgent
from agents.agent5_incident_intelligence import IncidentIntelligenceAgent
from agents.agent6_response_orchestration import ResponseOrchestrationAgent
from agents.agent_equipment_health import EquipmentHealthAgent
from agents.agent_worker_safety import WorkerSafetyAgent
from agents.agent_predictive_simulation import PredictiveSimulationAgent
from agents.agent_decision_validation import DecisionValidationAgent
from backend import llm_client
from backend.event_bus import EventBus
from generator.context_generator import OperationalContextGenerator
from generator.equipment_worker_generator import EquipmentWorkerGenerator
from generator.plant_model import PLANT, ZONE_BY_ID
from generator.scenarios import SCENARIOS, stress_test_setup
from generator.sensor_stream import SensorStreamGenerator

import numpy as np

NAIVE_THRESHOLD_STD = 3.0


@dataclass
class AuditLogEntry:
    timestamp: float
    sim_hour: float
    agent: str
    summary: str
    detail: dict = field(default_factory=dict)


def _safe_dict(obj) -> dict:
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return dict(obj)


class Simulation:
    def __init__(self, seed: int = 42, tick_seconds: float = 0.6,
                 sim_seconds_per_tick: float = 12.0):
        self.bus = EventBus()

        # Generators
        self.stream_gen = SensorStreamGenerator(
            start_sim_hour=6.0, sim_seconds_per_tick=sim_seconds_per_tick, seed=seed)
        self.ctx_gen = OperationalContextGenerator(seed=seed)
        self.equip_worker_gen = EquipmentWorkerGenerator(seed=seed)

        # Agents
        self.agent1 = SensorFusionAgent()
        self.agent2 = OperationalContextAgent(self.ctx_gen)
        self.agent3 = CompoundRiskCorrelationAgent(seed=0)
        self.agent4 = KnowledgeGraphAgent()

        # Agent 5 real LLM narratives: wired in only if a provider (Anthropic or
        # Groq) is configured, either via env var or the AI Settings panel.
        # agent5's brief() already catches any exception from this call and
        # falls back to the deterministic template narrative, so a missing/
        # invalid key or an API outage never breaks the demo.
        self.agent5 = IncidentIntelligenceAgent(self.agent4, llm_explain_fn=None)
        self.refresh_llm_status(log=True)

        self.agent6 = ResponseOrchestrationAgent()
        self.agent_equip = EquipmentHealthAgent()
        self.agent_worker = WorkerSafetyAgent()
        self.agent_predict = PredictiveSimulationAgent()
        self.agent_validate = DecisionValidationAgent()

        self.tick_seconds = tick_seconds
        self.running = False
        self._task: asyncio.Task | None = None

        # Live state
        self.latest_sensor_events: dict[str, dict] = {}
        self.latest_context: dict[str, dict] = {}
        self.latest_zone_risk: dict[str, dict] = {}
        self.latest_equipment: dict[str, dict] = {}
        self.latest_workers: list[dict] = []
        self.latest_predictions: dict[str, dict] = {}
        self.alert_feed: list[dict] = []
        self.reports: dict[str, dict] = {}
        self.audit_log: deque = deque(maxlen=3000)
        self.comparison_log: deque = deque(maxlen=2000)
        self.plant_safety_score: float = 96.0
        self.agent_status: dict[str, str] = {
            "SensorFusion": "idle", "OperationalContext": "idle",
            "EquipmentHealth": "idle", "WorkerSafety": "idle",
            "CompoundRisk": "idle", "KnowledgeGraph": "idle",
            "IncidentIntelligence": "idle", "PredictiveSim": "idle",
            "DecisionValidation": "idle", "ResponseOrchestration": "idle",
        }

        # WebSocket subscriber set
        self._ws_subscribers: set = set()

        # stress test state
        self._stress_schedule: list[dict] = []
        self._stress_start_tick: int | None = None
        self._naive_baselines: dict[str, tuple[float, float]] = {}

        # agent latency tracking (rolling average ms per tick)
        self.agent_latencies: dict[str, float] = {k: 0.0 for k in [
            "SensorFusion", "OperationalContext", "EquipmentHealth",
            "WorkerSafety", "CompoundRisk", "total_tick_ms"
        ]}
        # scenario progress tracking
        self.active_scenario: dict | None = None
        self._scenario_tick_start: int = 0
        # safety score history (last 200 ticks)
        self.safety_score_history: deque = deque(maxlen=200)

        self._wire_bus()

    def _wire_bus(self):
        self.bus.subscribe("risk.events", self._on_risk_event)

    def refresh_llm_status(self, log: bool = False) -> None:
        """Re-check llm_client's configured key/model and rewire Agent 5's
        hook accordingly. Called at startup and again whenever the AI
        Settings panel changes the key (POST/DELETE /api/settings/ai) so
        changes take effect immediately, with no restart needed."""
        self.llm_enabled = llm_client.is_configured()
        self.agent5.llm_explain_fn = llm_client.explain if self.llm_enabled else None
        if log:
            if self.llm_enabled:
                print(f"[SENTINEL] AI (Agent 5 + Copilot): ENABLED — "
                      f"provider={llm_client.PROVIDER_INFO[llm_client.current_provider()]['label']}, "
                      f"model={llm_client.current_model()}, key source={llm_client.key_source()}")
            else:
                print("[SENTINEL] AI (Agent 5 + Copilot): disabled (no API key) — using template fallback. "
                      "Configure one in the dashboard's AI Settings panel or set ANTHROPIC_API_KEY / GROQ_API_KEY.")

    def ai_status(self) -> dict:
        """Snapshot for GET /api/settings/ai and the dashboard's AI Settings panel."""
        return {
            "enabled": self.llm_enabled,
            "configured": llm_client.is_configured(),
            "provider": llm_client.current_provider(),
            "key_source": llm_client.key_source(),
            "masked_key": llm_client.masked_key(),
            "model": llm_client.current_model(),
            "providers": llm_client.list_providers(),
            "modules": [
                {"name": "Agent 5 — Incident Intelligence", "detail": "Grounded incident narratives on MEDIUM/HIGH alerts",
                 "active": self.llm_enabled},
                {"name": "AI Safety Copilot", "detail": "Free-text Q&A in the Command Interface, grounded in live plant state",
                 "active": self.llm_enabled},
            ],
        }

    def copilot_context(self) -> dict:
        """Compact live-state snapshot handed to the Copilot as grounding context."""
        return {
            "sim_hour": round(self.stream_gen.sim_hour, 2),
            "plant_safety_score": round(self.plant_safety_score, 1),
            "zones": [
                {
                    "zone_id": z.zone_id, "name": z.name,
                    "severity": (self.latest_zone_risk.get(z.zone_id) or {}).get("severity", "INFO"),
                    "calibrated_probability": (self.latest_zone_risk.get(z.zone_id) or {}).get("calibrated_probability"),
                }
                for z in PLANT
            ],
            "recent_alerts": [
                {k: a.get(k) for k in ("event_id", "zone_id", "severity", "calibrated_probability", "sim_hour")}
                for a in self.alert_feed[:10]
            ],
            "workers": [
                {"name": w.get("name"), "zone_id": w.get("zone_id"), "status": w.get("status"),
                 "ppe_compliant": w.get("ppe_compliant")}
                for w in self.latest_workers
            ],
            "active_scenario": self.active_scenario,
        }

    def _log(self, agent: str, summary: str, detail: dict | None = None):
        entry = AuditLogEntry(
            timestamp=time.time(), sim_hour=self.stream_gen.sim_hour,
            agent=agent, summary=summary, detail=detail or {})
        self.audit_log.append(entry)

    async def _broadcast(self, event_type: str, payload: Any):
        if not self._ws_subscribers:
            return
        import json
        msg = json.dumps({"type": event_type, "data": payload}, default=str)
        dead = set()
        for ws in self._ws_subscribers:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        self._ws_subscribers -= dead

    def add_ws_subscriber(self, ws):
        self._ws_subscribers.add(ws)

    def remove_ws_subscriber(self, ws):
        self._ws_subscribers.discard(ws)

    async def _on_risk_event(self, event):
        # Agent 7 (Knowledge Graph)
        self.agent_status["KnowledgeGraph"] = "active"
        self.agent4.write_risk_event(event)

        # Predictive simulation
        self.agent_status["PredictiveSim"] = "active"
        n_workers = len([w for w in self.latest_workers if w.get("zone_id") == event.zone_id])
        sim_result = self.agent_predict.simulate(event, n_workers_in_zone=max(n_workers, 1))
        self.latest_predictions[event.zone_id] = asdict(sim_result)

        # Decision validation
        self.agent_status["DecisionValidation"] = "active"
        equip_health = {
            k: type("EH", (), v)() for k, v in self.latest_equipment.items()
        } if self.latest_equipment else None
        validation = self.agent_validate.validate(event, equip_health=equip_health)

        # Agent 5 (Incident Intelligence) — only on HIGH/MEDIUM after validation
        briefing = None
        if validation.validated_severity in ("HIGH", "MEDIUM"):
            self.agent_status["IncidentIntelligence"] = "active"
            # temporarily set severity to HIGH so Agent 5 runs
            _orig = event.severity
            event.severity = "HIGH"
            briefing = await asyncio.to_thread(self.agent5.brief, event)
            event.severity = _orig

        # Agent 6 — report on HIGH
        if validation.validated_severity == "HIGH":
            self.agent_status["ResponseOrchestration"] = "active"
            report = self.agent6.generate_report(event, briefing)
            self.reports[report.report_id] = report.to_dict()
            self._log("Agent6", f"Generated incident report {report.report_id}", {"report_id": report.report_id})

        # Write validated severity back so zone heatmap stays in sync with alert feed
        if event.zone_id in self.latest_zone_risk:
            self.latest_zone_risk[event.zone_id]["severity"] = validation.validated_severity
            self.latest_zone_risk[event.zone_id]["calibrated_probability"] = event.calibrated_probability

        alert_dict = dict(event.__dict__)
        alert_dict["severity"] = validation.validated_severity   # always expose validated
        alert_dict["validation"] = validation.__dict__
        alert_dict["prediction"] = self.latest_predictions.get(event.zone_id)
        if briefing:
            alert_dict["briefing"] = briefing.__dict__
        self.alert_feed.insert(0, alert_dict)
        self.alert_feed = self.alert_feed[:500]

        # Broadcast to WebSocket clients
        if validation.validated_severity in ("HIGH", "MEDIUM"):
            await self._broadcast("alert", alert_dict)
            self._log("Agent3", f"{validation.validated_severity} alert in {event.zone_id} (p={event.calibrated_probability})")

        # reset agent statuses
        for k in self.agent_status:
            self.agent_status[k] = "idle"

    def _freeze_naive_baselines(self):
        for sensor_id, state in self.agent1._state.items():
            all_vals = []
            for w in state.windows.values():
                all_vals.extend(w)
            if len(all_vals) >= 5:
                self._naive_baselines[sensor_id] = (
                    float(np.mean(all_vals)), float(np.std(all_vals)) or 1.0)

    def _run_naive_detector(self, readings) -> bool:
        for r in readings:
            b = self._naive_baselines.get(r.sensor_id)
            if b:
                mean, std = b
                if std > 0 and abs(r.value - mean) / std > NAIVE_THRESHOLD_STD:
                    return True
        return False

    def _compute_plant_safety_score(self) -> float:
        """0-100 plant safety score. Uses a rolling time window so score
        recovers naturally once an incident clears — not permanently penalised."""
        current_hour = self.stream_gen.sim_hour
        # Only count alerts from the last 0.1 sim-hours (~10 real seconds)
        recent = [
            a for a in self.alert_feed[:80]
            if abs(current_hour - (a.get("sim_hour") or current_hour)) < 0.12
        ]
        sev = lambda a: a.get("validation", {}).get("validated_severity") or a.get("severity", "INFO")
        high_count = sum(1 for a in recent if sev(a) == "HIGH")
        med_count  = sum(1 for a in recent if sev(a) == "MEDIUM")
        equip_risk = max((v.get("zone_equipment_risk", 0) for v in self.latest_equipment.values()), default=0)
        worker_risk = max((w.get("exposure_risk", 0) for w in self.latest_workers), default=0)
        raw = 100 - (high_count * 10) - (med_count * 4) - (equip_risk * 18) - (worker_risk * 12)
        raw = max(0.0, min(100.0, raw))
        # Smooth recovery: blend toward raw score, max +0.8 per tick upward
        if raw > self.plant_safety_score:
            self.plant_safety_score = min(raw, self.plant_safety_score + 0.8)
        else:
            self.plant_safety_score = raw
        return round(self.plant_safety_score, 1)

    async def tick(self):
        import time as _time
        _tick_start = _time.perf_counter()

        # ── apply pending stress schedule ──
        if self._stress_schedule and self._stress_start_tick is not None:
            elapsed = self.stream_gen.tick_count - self._stress_start_tick
            for item in self._stress_schedule:
                if item["tick_offset"] == elapsed:
                    self.stream_gen.inject_anomaly(
                        item["sensor_id"], magnitude_std=item["magnitude"],
                        duration_ticks=3, tag="stress_test")
            if elapsed > max((i["tick_offset"] for i in self._stress_schedule), default=0) + 5:
                self._stress_schedule = []
                self._stress_start_tick = None

        # ── Agent 1: Sensor Fusion ──
        self.agent_status["SensorFusion"] = "active"
        readings = self.stream_gen.tick()
        sensor_events = self.agent1.process_tick(readings)

        if not self._naive_baselines and self.stream_gen.tick_count == 60:
            self._freeze_naive_baselines()
        naive_fired = self._run_naive_detector(readings) if self._naive_baselines else False

        for ev in sensor_events:
            self.latest_sensor_events[ev.sensor_id] = ev.__dict__

        # ── Agent 2: Operational Context ──
        self.agent_status["OperationalContext"] = "active"
        ctx_gen = self.ctx_gen
        ctx_gen.random_background_activity(self.stream_gen.sim_hour)
        ctx_by_zone = self.agent2.process_tick(self.stream_gen.sim_hour)
        self.latest_context = {z: c.__dict__ for z, c in ctx_by_zone.items()}

        # ── Equipment Health ──
        self.agent_status["EquipmentHealth"] = "active"
        high_risk_zones = {z for z, r in self.latest_zone_risk.items() if r.get("severity") in ("HIGH", "MEDIUM")}
        for zone_id, score in {z: r.get("calibrated_probability", 0) for z, r in self.latest_zone_risk.items()}.items():
            self.equip_worker_gen.set_zone_anomaly(zone_id, score)
        equip_states = self.equip_worker_gen.equipment_tick(self.stream_gen.sim_hour)
        equip_events = self.agent_equip.process_tick(equip_states, self.stream_gen.sim_hour)
        self.latest_equipment = {k: v.__dict__ for k, v in equip_events.items()}

        # ── Worker Safety ──
        self.agent_status["WorkerSafety"] = "active"
        worker_states = self.equip_worker_gen.worker_tick(self.stream_gen.sim_hour, high_risk_zones)
        worker_events = self.agent_worker.process_tick(worker_states, self.stream_gen.sim_hour, high_risk_zones)
        self.latest_workers = [w.__dict__ for w in worker_states]

        # ── Agent 3: Compound Risk ──
        self.agent_status["CompoundRisk"] = "active"
        import uuid as _uuid
        risk_events = self.agent3.evaluate_tick(sensor_events, ctx_by_zone)
        for _ev in risk_events:
            if not hasattr(_ev, "correlation_id") or not _ev.correlation_id:
                object.__setattr__(_ev, "correlation_id", str(_uuid.uuid4())[:8])                     if hasattr(_ev, "__dataclass_fields__") else None

        max_severity = "INFO"
        sev_order = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        for ev in risk_events:
            self.latest_zone_risk[ev.zone_id] = ev.__dict__
            await self.bus.publish("risk.events", ev)
            if sev_order[ev.severity] > sev_order[max_severity]:
                max_severity = ev.severity

        # ── Plant safety score ──
        self.plant_safety_score = self._compute_plant_safety_score()
        self.safety_score_history.append(self.plant_safety_score)

        # ── Scenario progress ──
        scenario_ticks_elapsed = (
            self.stream_gen.tick_count - self._scenario_tick_start
            if self.active_scenario else 0
        )

        self.comparison_log.append({
            "sim_hour": round(self.stream_gen.sim_hour, 3),
            "naive_fired": naive_fired,
            "multiagent_max_severity": max_severity,
            "plant_safety_score": self.plant_safety_score,
        })
        # deque(maxlen=2000) auto-truncates

        # Broadcast live state every tick to WebSocket clients
        _tick_ms = round((_time.perf_counter() - _tick_start) * 1000, 1)
        self.agent_latencies["total_tick_ms"] = round(
            self.agent_latencies["total_tick_ms"] * 0.85 + _tick_ms * 0.15, 1
        )
        await self._broadcast("tick", {
            "sim_hour": round(self.stream_gen.sim_hour, 3),
            "tick_count": self.stream_gen.tick_count,
            "plant_safety_score": self.plant_safety_score,
            "zone_risks": {z: {"severity": r.get("severity"), "probability": r.get("calibrated_probability")}
                           for z, r in self.latest_zone_risk.items()},
            "agent_status": self.agent_status,
            "tick_ms": _tick_ms,
            "active_scenario": self.active_scenario,
            "scenario_ticks": scenario_ticks_elapsed,
        })

    async def _loop(self):
        self.running = True
        while self.running:
            await self.tick()
            await asyncio.sleep(self.tick_seconds)

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self):
        self.running = False

    def stop_current_scenario(self) -> dict:
        """Stop the currently running scenario: clear all injected anomalies
        and active permits/maintenance, but keep alert history intact."""
        self.stream_gen._active_anomalies.clear()
        self._stress_schedule.clear()
        self._stress_start_tick = None
        self.ctx_gen.permits.clear()
        self.ctx_gen.maintenance.clear()
        for z in self.equip_worker_gen._zone_anomaly:
            self.equip_worker_gen._zone_anomaly[z] = 0.0
        self.active_scenario = None
        self._log("Orchestrator", "Current scenario stopped — plant returning to baseline")
        return {"status": "stopped", "message": "Active scenario cleared. Plant returning to baseline."}

    async def reset(self) -> dict:
        """Full clean-slate reset: clears all state including alert history."""
        self.stop_current_scenario()
        self.alert_feed.clear()
        self.reports.clear()
        self.latest_zone_risk.clear()
        self.comparison_log.clear()
        self.audit_log.clear()
        self.comparison_log.clear()
        self.safety_score_history.clear()
        self.latest_predictions.clear()
        self.plant_safety_score = 96.0
        # Reset equipment wear to initial low values
        import random as _rng
        r = _rng.Random(42)
        for k in self.equip_worker_gen._wear:
            self.equip_worker_gen._wear[k] = r.uniform(0.02, 0.18)
        # Reset knowledge graph to seeded baseline (remove session RiskEvent/Permit nodes)
        self.agent4.reset_session_nodes()
        self._log("Orchestrator", "Full simulation reset to baseline")
        await self._broadcast("system", {
            "message": "Simulation fully reset. Plant returned to healthy baseline.",
            "type": "reset",
        })
        return {"status": "reset", "message": "Full reset complete."}

    def pause(self) -> dict:
        self.running = False
        self._log("Orchestrator", "Simulation paused")
        return {"status": "paused"}

    def resume(self) -> dict:
        if not self.running:
            self.start()
        self._log("Orchestrator", "Simulation resumed")
        return {"status": "running"}

    def trigger_scenario(self, scenario_id: str) -> dict:
        scenario = SCENARIOS.get(scenario_id)
        if not scenario:
            return {"error": f"unknown scenario '{scenario_id}'"}
        # Always stop whatever is running first — scenarios don't stack
        self.stop_current_scenario()
        scenario.setup_fn(self.stream_gen, self.ctx_gen, self.stream_gen.sim_hour)
        self.active_scenario = {"id": scenario_id, "name": scenario.name, "zone_id": scenario.zone_id}
        self._scenario_tick_start = self.stream_gen.tick_count
        self._log("Orchestrator", f"Triggered scenario '{scenario.name}'")
        return {"status": "triggered", "scenario": scenario.name, "zone_id": scenario.zone_id}

    def trigger_stress_test(self, n_spikes: int = 50, seed: int = 0) -> dict:
        self._stress_schedule = stress_test_setup(self.stream_gen, self.ctx_gen, n_spikes=n_spikes, seed=seed)
        self._stress_start_tick = self.stream_gen.tick_count
        self._log("Orchestrator", f"Stress test: {n_spikes} isolated anomalies")
        return {"status": "triggered", "n_spikes": n_spikes}

    def state_snapshot(self) -> dict:
        return {
            "sim_hour": round(self.stream_gen.sim_hour, 3),
            "tick_count": self.stream_gen.tick_count,
            "plant_safety_score": self.plant_safety_score,
            "agent_status": self.agent_status,
            "llm_enabled": self.llm_enabled,
            "zones": [
                {
                    "zone_id": z.zone_id, "name": z.name, "zone_class": z.zone_class.value,
                    "x": z.x, "y": z.y,
                    "context": self.latest_context.get(z.zone_id),
                    "risk": self.latest_zone_risk.get(z.zone_id),
                    "equipment": self.latest_equipment.get(z.zone_id),
                    "workers": [w for w in self.latest_workers if w.get("zone_id") == z.zone_id],
                    "prediction": self.latest_predictions.get(z.zone_id),
                }
                for z in PLANT
            ],
        }
