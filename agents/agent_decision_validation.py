"""
DECISION VALIDATION AGENT

Challenges every RiskEvent from Agent 3 before it reaches the dashboard.

Asks:
  - Is the evidence internally consistent?
  - Are enough independent sources corroborating?
  - Is the confidence above the threshold for this severity level?
  - Does equipment health support or contradict the risk claim?
  - Are there dissenting factors that should cause a downgrade?

Better to miss a weak signal than flood operators.
This is what makes the system trustworthy to engineers.
"""
from dataclasses import dataclass, field

from agents.agent3_compound_risk import RiskEvent, Severity
from agents.agent_equipment_health import EquipmentHealthEvent


@dataclass
class ValidationResult:
    original_event_id: str
    original_severity: str
    validated_severity: str
    validation_confidence: float        # how confident the validator is in its output
    corroboration_count: int            # number of independent contributing factors
    dissenting_factors: list[str]       # reasons the evidence is weaker than it appears
    supporting_factors: list[str]       # reasons the evidence is strong
    upgraded: bool
    downgraded: bool
    validation_notes: str


class DecisionValidationAgent:
    """DECISION VALIDATION AGENT. Wraps every RiskEvent output from Agent 3."""

    MIN_CORROBORATION = {
        "HIGH": 3,      # HIGH requires >=3 independent corroborating signals
        "MEDIUM": 2,    # MEDIUM requires >=2
        "LOW": 1,
        "INFO": 0,
    }

    CONFIDENCE_FLOOR = {
        "HIGH": 0.72,
        "MEDIUM": 0.42,
        "LOW": 0.18,
        "INFO": 0.0,
    }

    def validate(self, event: RiskEvent,
                 equip_health: dict[str, EquipmentHealthEvent] | None = None) -> ValidationResult:
        severity = event.severity
        p = event.calibrated_probability
        ctx = event.contributing_context

        dissenting = []
        supporting = []

        # count independent corroborating signals
        corroboration = 0
        if event.features.get("max_anomaly_score", 0) >= 0.5:
            corroboration += 1
            supporting.append(f"Sensor anomaly score {event.features['max_anomaly_score']:.2f}")
        if event.features.get("num_anomalous_sensors", 0) >= 2:
            corroboration += 1
            supporting.append(f"{event.features['num_anomalous_sensors']} sensors anomalous")
        if event.features.get("combo_flag", 0):
            corroboration += 1
            supporting.append("Dangerous permit combination confirmed")
        if event.features.get("context_multiplier", 1.0) >= 2.5:
            corroboration += 1
            supporting.append(f"High context multiplier {event.features['context_multiplier']:.2f}")
        if event.features.get("handover", 0):
            corroboration += 1
            supporting.append("Shift handover window active")

        # check equipment health corroboration / contradiction
        zone_equip = equip_health.get(event.zone_id) if equip_health else None
        if zone_equip:
            if zone_equip.zone_equipment_risk >= 0.4:
                corroboration += 1
                supporting.append(f"Equipment risk in zone elevated (p={zone_equip.zone_equipment_risk:.2f})")
            elif zone_equip.zone_equipment_risk < 0.1 and severity == "HIGH":
                dissenting.append("Equipment health is good — contradicts HIGH severity claim.")

        # check for weak evidence patterns
        if event.features.get("num_anomalous_sensors", 0) == 1 and severity == "HIGH":
            dissenting.append("Only one sensor anomalous — single-sensor basis for HIGH is weak.")
        if p < self.CONFIDENCE_FLOOR.get(severity, 0):
            dissenting.append(f"Calibrated probability {p:.2f} is below floor for {severity}.")

        # determine if upgrade/downgrade warranted
        required_corr = self.MIN_CORROBORATION.get(severity, 0)
        downgraded = False
        upgraded = False
        new_severity = severity

        if corroboration < required_corr and len(dissenting) >= 2:
            # downgrade one level
            order = ["INFO", "LOW", "MEDIUM", "HIGH"]
            idx = order.index(severity)
            new_severity = order[max(0, idx - 1)]
            downgraded = new_severity != severity
            if downgraded:
                dissenting.append(f"Insufficient corroboration ({corroboration}/{required_corr}) — downgraded to {new_severity}.")
        elif corroboration >= required_corr + 2 and zone_equip and zone_equip.co_degradation_flag:
            # upgrade one level if overwhelming evidence
            order = ["INFO", "LOW", "MEDIUM", "HIGH"]
            idx = order.index(severity)
            new_severity = order[min(len(order) - 1, idx + 1)]
            upgraded = new_severity != severity

        # validation confidence: higher when corroboration is strong, lower when dissenting
        val_confidence = min(1.0, (corroboration / max(required_corr, 1)) * p - len(dissenting) * 0.05)
        val_confidence = max(0.1, round(val_confidence, 3))

        notes = f"Validated {severity}→{new_severity}. Corroboration: {corroboration}/{required_corr}. "
        notes += "Downgraded: weak evidence. " if downgraded else ""
        notes += "Upgraded: overwhelming multi-source evidence. " if upgraded else ""
        notes += f"Dissenting factors: {len(dissenting)}. Supporting factors: {len(supporting)}."

        return ValidationResult(
            original_event_id=event.event_id,
            original_severity=severity,
            validated_severity=new_severity,
            validation_confidence=val_confidence,
            corroboration_count=corroboration,
            dissenting_factors=dissenting,
            supporting_factors=supporting,
            upgraded=upgraded,
            downgraded=downgraded,
            validation_notes=notes,
        )
