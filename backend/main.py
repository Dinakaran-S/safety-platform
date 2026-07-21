"""FastAPI backend V2 — REST + WebSocket"""
import asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import llm_client
from backend.orchestrator import Simulation
from generator.scenarios import SCENARIOS

app = FastAPI(title="SENTINEL — Industrial Safety Intelligence Platform")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

sim = Simulation(seed=42, tick_seconds=0.6, sim_seconds_per_tick=12.0)


@app.on_event("startup")
async def startup():
    sim.start()


# ── WebSocket ──────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    sim.add_ws_subscriber(ws)
    try:
        while True:
            await ws.receive_text()   # keep connection alive; we only push
    except (WebSocketDisconnect, Exception):
        sim.remove_ws_subscriber(ws)


# ── REST endpoints ─────────────────────────────────────────────────────────

@app.get("/api/state")
async def get_state():
    return sim.state_snapshot()


@app.get("/api/alerts")
async def get_alerts(limit: int = 50):
    return sim.alert_feed[:limit]


@app.get("/api/sensors")
async def get_sensors():
    return list(sim.latest_sensor_events.values())


@app.get("/api/workers")
async def get_workers():
    return sim.latest_workers


@app.get("/api/equipment")
async def get_equipment():
    return sim.latest_equipment


@app.get("/api/zone/{zone_id}/history")
async def zone_history(zone_id: str):
    from generator.plant_model import PLANT
    if zone_id not in {z.zone_id for z in PLANT}:
        raise HTTPException(404, "unknown zone")
    return sim.agent4.query_zone_history(zone_id)


@app.get("/api/graph/query")
async def graph_query(zone_id: str | None = None, tags: str | None = None):
    tag_list = tags.split(",") if tags else None
    out: dict = {"stats": sim.agent4.stats()}
    if zone_id:
        out["similar_incidents"] = sim.agent4.find_similar_incidents(zone_id, tags=tag_list)
    if tag_list:
        out["guidelines"] = sim.agent4.find_relevant_guidelines(tag_list)
    return out


@app.get("/api/graph/nodes")
async def graph_nodes():
    """Return all graph nodes for the KG visualisation panel."""
    nodes, edges = [], []
    for nid, data in sim.agent4.g.nodes(data=True):
        nodes.append({"id": nid, **{k: v for k, v in data.items() if isinstance(v, (str, int, float, bool))}})
    for u, v, data in sim.agent4.g.edges(data=True):
        edges.append({"source": u, "target": v, "relation": data.get("relation", "")})
    return {"nodes": nodes[:300], "edges": edges[:600]}


@app.get("/api/scenarios")
async def list_scenarios():
    return [{"id": s.scenario_id, "name": s.name, "description": s.description, "zone_id": s.zone_id}
            for s in SCENARIOS.values()]


@app.post("/api/scenarios/{scenario_id}/trigger")
async def trigger_scenario(scenario_id: str):
    if scenario_id not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    return sim.trigger_scenario(scenario_id)


@app.post("/api/stress-test/trigger")
async def trigger_stress_test(n_spikes: int = 50, seed: int = 0):
    return sim.trigger_stress_test(n_spikes=n_spikes, seed=seed)


@app.get("/api/comparison")
async def comparison(limit: int = 200):
    return list(sim.comparison_log)[-limit:]


@app.get("/api/audit-log")
async def audit_log(limit: int = 100):
    return [e.__dict__ for e in list(sim.audit_log)[-limit:]]


@app.get("/api/reports")
async def list_reports():
    return list(sim.reports.values())


@app.get("/api/reports/{report_id}")
async def get_report(report_id: str):
    if report_id not in sim.reports:
        raise HTTPException(404, "unknown report")
    return sim.reports[report_id]


@app.post("/api/sim/reset")
async def sim_reset():
    return await sim.reset()

@app.post("/api/sim/pause")
async def sim_pause():
    return sim.pause()

@app.post("/api/sim/resume")
async def sim_resume():
    return sim.resume()

@app.post("/api/sim/stop-scenario")
async def sim_stop_scenario():
    return sim.stop_current_scenario()

@app.get("/api/sim/status")
async def sim_status():
    return {
        "running": sim.running,
        "tick_count": sim.stream_gen.tick_count,
        "sim_hour": round(sim.stream_gen.sim_hour, 3),
        "plant_safety_score": sim.plant_safety_score,
        "active_permits": len(sim.ctx_gen.permits),
        "active_maintenance": len(sim.ctx_gen.maintenance),
        "active_anomalies": len(sim.stream_gen._active_anomalies),
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "tick_count": sim.stream_gen.tick_count,
            "plant_safety_score": sim.plant_safety_score,
            "llm_enabled": sim.llm_enabled,
            "llm_model": llm_client.current_model() if sim.llm_enabled else None}


# ── AI Settings ──────────────────────────────────────────────────────────
# One place to configure the API key(s) that power every AI module in the
# app (currently: Agent 5 incident narratives + the AI Safety Copilot).
# Setting/clearing a key here takes effect immediately, no restart needed.

class AiKeyRequest(BaseModel):
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None


@app.get("/api/settings/ai")
async def get_ai_settings():
    """Status for the dashboard's AI Settings panel: whether AI is enabled,
    which provider/key source is active (env vs. one set in the panel), the
    masked key, current model, the full provider catalog, and which modules
    that key powers."""
    return sim.ai_status()


@app.post("/api/settings/ai")
async def set_ai_settings(req: AiKeyRequest):
    """Set the active provider from the AI Settings panel, and optionally a
    runtime API key / model for it. This key is kept in memory only for
    this process — it is never written to disk. Overrides the provider's
    env var for as long as the process runs, or until cleared with
    DELETE /api/settings/ai. Leaving api_key blank just switches the active
    provider/model, relying on that provider's env var if one is set."""
    provider = req.provider or llm_client.current_provider()
    if provider not in llm_client.PROVIDER_INFO:
        raise HTTPException(400, f"Unknown provider '{provider}'. "
                                  f"Choose one of: {', '.join(llm_client.PROVIDER_INFO)}")
    api_key = req.api_key.strip() if req.api_key else None
    llm_client.configure(provider, api_key, req.model)
    sim.refresh_llm_status(log=True)
    return sim.ai_status()


@app.delete("/api/settings/ai")
async def clear_ai_settings():
    """Clear the runtime key set from the panel for the currently active
    provider. Falls back to that provider's env var if set, else AI modules
    revert to their template/rule-based fallback behavior."""
    llm_client.clear_runtime_key()
    sim.refresh_llm_status(log=True)
    return sim.ai_status()


@app.post("/api/settings/ai/test")
async def test_ai_settings():
    """Fires one tiny real API call against the currently configured key to
    confirm it actually works, without waiting for a live alert to find out."""
    ok, message = await asyncio.to_thread(llm_client.test_connection)
    return {"ok": ok, "message": message}


# ── AI Safety Copilot ────────────────────────────────────────────────────

class CopilotAskRequest(BaseModel):
    question: str


@app.post("/api/copilot/ask")
async def copilot_ask(req: CopilotAskRequest):
    """Free-text grounded Q&A backing the Command Interface's fallback for
    anything that isn't a recognized command. Only reachable when an AI
    key is configured; the frontend keeps using the rule-based command
    parser for everything else regardless."""
    if not req.question or not req.question.strip():
        raise HTTPException(400, "question cannot be blank")
    if not sim.llm_enabled:
        # Return 200 with a helpful guide instead of 503 so the UI renders gracefully
        provider = llm_client.PROVIDER_INFO[llm_client.current_provider()]["label"]
        env_key  = llm_client.PROVIDER_INFO[llm_client.current_provider()]["env_key"]
        return {
            "answer": (
                f"AI is not yet configured. To enable real AI responses: "
                f"open the AI Settings panel (top bar), enter your {provider} API key, "
                f"and click Save. Alternatively set the {env_key} environment variable "
                f"before starting the server. Until then, use the command keywords "
                f"(type 'help' to see them) — they work without any API key."
            ),
            "llm_generated": False,
            "powered_by": "template",
        }
    try:
        answer = await asyncio.to_thread(llm_client.ask, req.question, sim.copilot_context())
        return {"answer": answer, "llm_generated": True, "powered_by": llm_client.current_model()}
    except Exception as e:
        return {
            "answer": f"AI request failed ({e}). Check your API key in AI Settings.",
            "llm_generated": False,
            "powered_by": "error",
        }


# ── PDF Export endpoints ────────────────────────────────────────────────────

@app.get("/api/reports/{report_id}/pdf")
async def report_pdf(report_id: str):
    """Download a single incident report as a branded PDF."""
    from fastapi.responses import Response
    if report_id not in sim.reports:
        raise HTTPException(404, "unknown report")
    from backend.pdf_generator import generate_incident_report
    alert = next((a for a in sim.alert_feed if a.get("event_id","").endswith(report_id.replace("IR-",""))), None)
    pdf_bytes = generate_incident_report(sim.reports[report_id], alert)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.pdf"'}
    )


@app.get("/api/export/plant-status")
async def export_plant_status():
    """Export current plant status as a PDF snapshot."""
    from fastapi.responses import Response
    from backend.pdf_generator import generate_plant_status
    state   = sim.state_snapshot()
    workers = sim.latest_workers
    equip   = sim.latest_equipment
    pdf_bytes = generate_plant_status(state, sim.alert_feed[:50], workers, equip)
    fname = f"SENTINEL_PlantStatus_{int(__import__('time').time())}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )


@app.get("/api/export/alerts")
async def export_alerts(limit: int = 30):
    """Export the alert feed as a PDF."""
    from fastapi.responses import Response
    from backend.pdf_generator import generate_alert_feed
    pdf_bytes = generate_alert_feed(sim.alert_feed, limit=limit)
    fname = f"SENTINEL_Alerts_{int(__import__('time').time())}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )


@app.get("/api/export/audit-log")
async def export_audit_log(limit: int = 100):
    """Export the agent decision audit log as a PDF."""
    from fastapi.responses import Response
    from backend.pdf_generator import generate_audit_log
    entries = [e.__dict__ for e in list(sim.audit_log)[-limit:]]
    pdf_bytes = generate_audit_log(entries, limit=limit)
    fname = f"SENTINEL_AuditLog_{int(__import__('time').time())}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )


@app.get("/api/metrics")
async def metrics():
    """Agent latency stats, tick performance, scenario progress."""
    return {
        "tick_ms": sim.agent_latencies.get("total_tick_ms", 0),
        "tick_count": sim.stream_gen.tick_count,
        "active_scenario": sim.active_scenario,
        "safety_score_history": list(sim.safety_score_history)[-60:],
        "alert_feed_size": len(sim.alert_feed),
        "report_count": len(sim.reports),
        "kg_stats": sim.agent4.stats(),
        "active_permits": len(sim.ctx_gen.permits),
        "active_maintenance": len(sim.ctx_gen.maintenance),
        "active_anomalies": len(sim.stream_gen._active_anomalies),
    }


@app.get("/api/reports/{report_id}/download")
async def download_report(report_id: str):
    """Return a plain-text version of the incident report for download."""
    from fastapi.responses import PlainTextResponse
    if report_id not in sim.reports:
        raise HTTPException(404, "unknown report")
    # reconstruct text from dict
    r = sim.reports[report_id]
    lines = [
        "=" * 68,
        "SENTINEL AI — PRELIMINARY INCIDENT REPORT".center(68),
        "=" * 68,
        f"Report ID:         {r.get('report_id', '')}",
        f"Generated:         {r.get('generated_at', '')}",
        f"Zone:              {r.get('zone_id', '')} — {r.get('zone_name', '')}",
        f"Severity:          {r.get('severity', '')}",
        f"Calibrated P:      {r.get('calibrated_probability', '')}",
        f"Sim Hour:          {r.get('sim_hour', '')}",
        f"Est. Lead Time:    {r.get('estimated_lead_time_minutes', '—')} min",
        "",
        "CONTRIBUTING FACTORS",
        "-" * 68,
        *[f"  - {f}" for f in (r.get('contributing_factors') or [])],
        "",
        "GROUNDED PRECEDENT",
        "-" * 68,
        f"  {r.get('grounded_precedent') or 'No matching precedent retrieved.'}",
        "",
        "RECOMMENDED ACTIONS",
        "-" * 68,
        *[f"  [{a['rank']}] {a['action']}" for a in (r.get('action_checklist') or [])],
        "",
        "=" * 68,
        r.get('sscada_integration_note', ''),
        "=" * 68,
    ]
    return PlainTextResponse(
        "\n".join(lines),
        headers={"Content-Disposition": f'attachment; filename="{report_id}.txt"'}
    )


# ── Static frontend ────────────────────────────────────────────────────────
_frontend = Path(__file__).resolve().parents[1] / "frontend"
app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
