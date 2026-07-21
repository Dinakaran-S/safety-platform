"""
Real LLM integration — shared by every AI-powered module in SENTINEL:

  - Agent 5 (Incident Intelligence) narrative generation  -> explain()
  - AI Safety Copilot free-text Q&A                        -> ask()

Two providers are supported, switchable from the dashboard's "AI Settings"
panel with zero restart:

  - Anthropic (Claude)  — env var ANTHROPIC_API_KEY
  - Groq                — env var GROQ_API_KEY

Configuration sources, in priority order:
  1. Runtime, set from the AI Settings panel (POST /api/settings/ai).
     Kept in memory only for this process — never written to disk.
  2. Environment / .env file (ANTHROPIC_API_KEY / GROQ_API_KEY).

If neither is set for the active provider, `is_configured()` returns False.
Agent 5 already wraps its LLM call in try/except and falls back to its
deterministic template narrative, so a missing/invalid key or an API outage
never breaks the app — it just runs template-only, exactly like before this
integration existed.
"""
import json
import os
import threading

MAX_TOKENS = int(os.environ.get("SENTINEL_LLM_MAX_TOKENS", "220"))

# ── Provider registry ────────────────────────────────────────────────────
# Cheap + fast defaults — Agent 5 can fire on every MEDIUM/HIGH risk event in
# a live-ticking simulation. Pick a bigger model from the AI Settings panel
# if you want richer prose and don't mind the extra latency/cost.
PROVIDER_INFO: dict[str, dict] = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "env_key": "ANTHROPIC_API_KEY",
        "key_placeholder": "sk-ant-...",
        "console_url": "console.anthropic.com",
        "default_model": "claude-haiku-4-5-20251001",
        "models": [
            {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5 — fastest, cheapest"},
            {"id": "claude-sonnet-5", "label": "Claude Sonnet 5 — balanced"},
            {"id": "claude-opus-4-8", "label": "Claude Opus 4.8 — highest quality"},
        ],
    },
    "groq": {
        "label": "Groq",
        "env_key": "GROQ_API_KEY",
        "key_placeholder": "gsk_...",
        "console_url": "console.groq.com/keys",
        "default_model": "llama-3.3-70b-versatile",
        "models": [
            {"id": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B Versatile — balanced"},
            {"id": "llama-3.1-8b-instant", "label": "Llama 3.1 8B Instant — fastest"},
            {"id": "openai/gpt-oss-120b", "label": "GPT-OSS 120B — highest quality"},
            {"id": "gemma2-9b-it", "label": "Gemma 2 9B IT"},
        ],
    },
}
DEFAULT_PROVIDER = "anthropic"

_lock = threading.Lock()
_clients: dict[tuple, object] = {}  # (provider, key) -> cached client/session

# Runtime overrides set via the AI Settings panel (POST /api/settings/ai).
# Per-provider so switching providers doesn't clobber a key you already
# entered for the other one.
_runtime_provider: str | None = None
_runtime_keys: dict[str, str] = {}
_runtime_models: dict[str, str] = {}


def list_providers() -> list[dict]:
    """Provider catalog for the AI Settings panel: id, label, model list,
    and whether an env var is already set for it (so the UI can say
    'using .env' even if the panel field is left blank)."""
    return [
        {
            "id": pid,
            "label": info["label"],
            "key_placeholder": info["key_placeholder"],
            "console_url": info["console_url"],
            "default_model": info["default_model"],
            "models": info["models"],
            "env_configured": bool(os.environ.get(info["env_key"])),
        }
        for pid, info in PROVIDER_INFO.items()
    ]


def current_provider() -> str:
    if _runtime_provider and _runtime_provider in PROVIDER_INFO:
        return _runtime_provider
    # Auto-detect: prefer whichever provider has an env var set.
    for pid, info in PROVIDER_INFO.items():
        if os.environ.get(info["env_key"]):
            return pid
    return DEFAULT_PROVIDER


def _env_api_key(provider: str) -> str | None:
    return os.environ.get(PROVIDER_INFO[provider]["env_key"]) or None


def current_api_key() -> str | None:
    provider = current_provider()
    return _runtime_keys.get(provider) or _env_api_key(provider)


def current_model() -> str:
    provider = current_provider()
    if provider in _runtime_models:
        return _runtime_models[provider]
    return PROVIDER_INFO[provider]["default_model"]


def key_source() -> str | None:
    """'runtime' if set from the AI Settings panel, 'env' if from .env/shell, else None."""
    provider = current_provider()
    if _runtime_keys.get(provider):
        return "runtime"
    if _env_api_key(provider):
        return "env"
    return None


def masked_key() -> str | None:
    key = current_api_key()
    if not key:
        return None
    if len(key) <= 10:
        return "•" * len(key)
    return f"{key[:7]}{'•' * 6}{key[-4:]}"


def is_configured() -> bool:
    """True if an API key is present for the active provider, from either source."""
    return bool(current_api_key())


def configure(provider: str, api_key: str | None, model: str | None = None) -> None:
    """Set the active provider from the AI Settings panel, and optionally a
    runtime API key / model for it. Passing api_key=None just switches the
    active provider/model without touching that provider's stored key (so
    it falls back to its env var if no runtime key was ever set for it)."""
    global _runtime_provider
    if provider not in PROVIDER_INFO:
        raise ValueError(f"Unknown provider '{provider}'")
    with _lock:
        _runtime_provider = provider
        if api_key:
            _runtime_keys[provider] = api_key
        if model:
            _runtime_models[provider] = model
        _clients.clear()


def clear_runtime_key() -> None:
    """Clear the panel-set key/model for the CURRENT provider only, and stop
    forcing that provider — falls back to auto-detect (env vars), if any."""
    global _runtime_provider
    with _lock:
        provider = current_provider()
        _runtime_keys.pop(provider, None)
        _runtime_models.pop(provider, None)
        _runtime_provider = None
        _clients.clear()


# ── Provider calls ───────────────────────────────────────────────────────

def _call_anthropic(key: str, model: str, prompt: str, max_tokens: int) -> str:
    with _lock:
        client = _clients.get(("anthropic", key))
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise RuntimeError(
                    "The 'anthropic' package is not installed. "
                    "Run: pip install anthropic --break-system-packages"
                ) from e
            client = anthropic.Anthropic(api_key=key)
            _clients[("anthropic", key)] = client

    response = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return " ".join(
        block.text.strip()
        for block in response.content
        if getattr(block, "type", None) == "text" and block.text.strip()
    )


def _call_groq(key: str, model: str, prompt: str, max_tokens: int) -> str:
    # Groq exposes an OpenAI-compatible chat completions endpoint, so a
    # plain HTTP call (via httpx, already installed as an anthropic-sdk
    # dependency) is all that's needed — no extra required package.
    with _lock:
        http = _clients.get(("groq", key))
        if http is None:
            import httpx
            http = httpx.Client(base_url="https://api.groq.com/openai/v1", timeout=30.0)
            _clients[("groq", key)] = http

    resp = http.post(
        "/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


def _call_model(prompt: str, max_tokens: int) -> str:
    provider = current_provider()
    key = current_api_key()
    if not key:
        raise RuntimeError(f"No API key configured for {PROVIDER_INFO[provider]['label']}")
    model = current_model()
    if provider == "anthropic":
        return _call_anthropic(key, model, prompt, max_tokens)
    if provider == "groq":
        return _call_groq(key, model, prompt, max_tokens)
    raise RuntimeError(f"Unsupported provider '{provider}'")


def test_connection() -> tuple[bool, str]:
    """Makes one tiny real API call to verify the currently configured
    provider/key actually works. Returns (ok, message). Used by the AI
    Settings panel's 'Test Connection' button — never called automatically
    during the sim."""
    if not is_configured():
        return False, f"No API key configured for {PROVIDER_INFO[current_provider()]['label']}."
    try:
        _call_model("Reply with the single word: OK", 8)
        return True, f"Connected successfully to {PROVIDER_INFO[current_provider()]['label']} (model={current_model()})."
    except Exception as e:
        return False, str(e)


# ── Agent 5: grounded incident narrative ────────────────────────────────────

def _build_explain_prompt(event, matched_incident, matched_guideline) -> str:
    ctx = event.contributing_context or {}
    sensor_summary = [
        {
            "sensor_type": se.get("sensor_type"),
            "anomaly_score": round(se.get("anomaly_score", 0), 2),
        }
        for se in (event.contributing_sensor_events or [])
    ]

    lines = [
        "You are the incident-intelligence module of an industrial safety "
        "monitoring system (SENTINEL). Write a short operator-facing briefing "
        "(2-4 sentences, plain text, no markdown) for the risk event below.",
        "",
        "Ground rules:",
        "- Only use facts given below. Never invent incident IDs, guideline IDs, "
        "dates, or numbers that are not present in the input.",
        "- If a historical precedent is given, cite it by its exact ID in brackets.",
        "- If a guideline is given, cite it by its exact ID in brackets.",
        "- If either is 'none found', say so plainly instead of fabricating one.",
        "- Be direct and actionable. This is read by a plant operator during a "
        "live alert, not a report reader — no preamble, no filler.",
        "",
        f"Zone: {event.zone_id}",
        f"Validated severity: {event.severity}",
        f"Calibrated probability: {event.calibrated_probability}",
        f"Contributing sensor readings: {json.dumps(sensor_summary)}",
        f"Operational context: {json.dumps(ctx)}",
    ]

    if matched_incident:
        lines.append(f"Matched historical precedent: {json.dumps(matched_incident)}")
    else:
        lines.append("Matched historical precedent: none found.")

    if matched_guideline:
        lines.append(f"Matched regulatory guideline: {json.dumps(matched_guideline)}")
    else:
        lines.append("Matched regulatory guideline: none found.")

    return "\n".join(lines)


def explain(event, matched_incident, matched_guideline) -> str:
    """
    Matches the `llm_explain_fn(event, matched_incident, matched_guideline)`
    hook IncidentIntelligenceAgent expects (agents/agent5_incident_intelligence.py).

    Synchronous / blocking (real network call). The orchestrator runs this in
    a worker thread (asyncio.to_thread) so it never stalls the sim loop or
    WebSocket broadcasts. Raises on any failure — agent5.brief() already
    wraps the call in try/except and falls back to the template narrative.
    """
    prompt = _build_explain_prompt(event, matched_incident, matched_guideline)
    text = _call_model(prompt, MAX_TOKENS)
    if not text:
        raise RuntimeError("LLM returned an empty narrative")
    return text


# ── AI Safety Copilot: free-text grounded Q&A ───────────────────────────────

def ask(question: str, plant_context: dict) -> str:
    """
    Free-text Q&A used by the Command Interface / Copilot when a typed
    question doesn't match any known command. Grounded in a snapshot of live
    plant state so the model reasons about THIS plant, not generic facts.
    Raises on failure — caller (main.py's /api/copilot/ask) catches and
    returns a plain error message instead of crashing the request.
    """
    prompt = (
        "You are the AI Safety Copilot embedded in SENTINEL, an industrial "
        "safety monitoring dashboard. Answer the operator's question using "
        "ONLY the live plant state JSON given below — do not invent zones, "
        "sensor readings, or events that aren't in it. If the data doesn't "
        "contain what's needed to answer, say so plainly. Keep the answer to "
        "2-5 sentences, plain text, no markdown, operator-facing tone.\n\n"
        f"Live plant state:\n{json.dumps(plant_context, default=str)[:6000]}\n\n"
        f"Operator question: {question}"
    )
    text = _call_model(prompt, 300)
    if not text:
        raise RuntimeError("LLM returned an empty answer")
    return text


if __name__ == "__main__":
    # Manual smoke test: python -m backend.llm_client
    if not is_configured():
        print(f"No API key configured for {PROVIDER_INFO[current_provider()]['label']}.")
        print(f"Export {PROVIDER_INFO[current_provider()]['env_key']}, e.g.:")
        print(f"  export {PROVIDER_INFO[current_provider()]['env_key']}=...")
        raise SystemExit(1)

    ok, msg = test_connection()
    print(f"test_connection(): ok={ok} — {msg}")
