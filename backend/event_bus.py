"""
Lightweight in-process async pub/sub event bus connecting the six agents.

Deliberately NOT Kafka/real message broker -- an asyncio.Queue-per-topic is
plenty for a single-process hackathon demo and keeps the architecture story
clean without operational overhead. Message *contracts* are what matter for
the architecture slide; see AGENT_CONTRACTS.md.

Topics:
  "sensor.readings"   -> list[Reading]            (Generator -> Agent 1)
  "sensor.anomalies"  -> list[SensorAnomalyEvent]  (Agent 1 -> Agent 3)
  "context.zone"      -> dict[str, ZoneContextEvent] (Agent 2 -> Agent 3)
  "risk.events"       -> RiskEvent                 (Agent 3 -> Agent 4, 5, 6)
  "intelligence.briefing" -> IncidentIntelligenceBriefing (Agent 5 -> Agent 6, dashboard)
  "incident.report"   -> IncidentReport             (Agent 6 -> dashboard)
"""
import asyncio
from collections import defaultdict, deque
from typing import Any, Callable


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        self.max_history = 500

    def subscribe(self, topic: str, handler: Callable):
        self._subscribers[topic].append(handler)

    async def publish(self, topic: str, message: Any):
        self._history[topic].append(message)
        for handler in self._subscribers[topic]:
            result = handler(message)
            if asyncio.iscoroutine(result):
                await result

    def history(self, topic: str, limit: int | None = None) -> list[Any]:
        h = self._history.get(topic, [])
        return list(h)[-limit:] if limit else list(h)
