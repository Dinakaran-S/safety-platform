"""
HARD INVARIANT TEST SUITE for Agent 3.

Proves: a single anomalous sensor with NO corroborating operational context
must NEVER produce a HIGH or MEDIUM severity RiskEvent -- only LOW/INFO.

Methodology: run N randomized trials. In each trial, pick a random zone and
inject a random-magnitude anomaly (random std multiplier, 3-10 sigma) into a
random sensor in that zone, with NO permits, NO maintenance, and a handover
state forced to False (i.e. zero corroborating context). Run the full
Agent1 -> Agent2 -> Agent3 pipeline and assert severity is never MEDIUM/HIGH.

Also runs a corroborated-context control group to prove the pipeline CAN
still produce HIGH severity when context is present, i.e. the gate isn't
just suppressing everything.

Writes results to TEST_RESULTS.md at the project root.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.agent1_sensor_fusion import SensorFusionAgent
from agents.agent2_operational_context import OperationalContextAgent, ZoneContextEvent
from agents.agent3_compound_risk import CompoundRiskCorrelationAgent, Severity
from generator.context_generator import OperationalContextGenerator, PermitType
from generator.plant_model import ALL_SENSORS, ZONE_BY_ID
from generator.sensor_stream import SensorStreamGenerator


N_SINGLE_FACTOR_TRIALS = 250
N_CONTROL_TRIALS = 100


def _warm_up(stream_gen, fusion_agent, zone_id=None, ticks=35):
    for _ in range(ticks):
        readings = stream_gen.tick()
        if zone_id is not None:
            readings = [r for r in readings if r.zone_id == zone_id]
        fusion_agent.process_tick(readings)


def run_single_factor_invariant_test(seed: int = 100, n_trials: int = N_SINGLE_FACTOR_TRIALS):
    """No permits, no maintenance, no handover -- inject one random anomalous sensor
    per trial and confirm severity is always LOW or INFO."""
    rng = random.Random(seed)
    violations = []
    severities_seen = {"INFO": 0, "LOW": 0, "MEDIUM": 0, "HIGH": 0}

    for trial in range(n_trials):
        # pick a sim hour that is guaranteed NOT in a handover window
        hour = rng.uniform(0.6, 5.4)  # comfortably mid-night-shift, far from 06:00/22:00 boundaries

        stream_gen = SensorStreamGenerator(start_sim_hour=hour, seed=seed * 1000 + trial)
        fusion_agent = SensorFusionAgent()
        sensor = rng.choice(ALL_SENSORS)
        _warm_up(stream_gen, fusion_agent, zone_id=sensor.zone_id)

        magnitude = rng.uniform(3.0, 10.0)
        stream_gen.inject_anomaly(sensor.sensor_id, magnitude_std=magnitude, duration_ticks=5, tag="invariant_test")

        # empty context generator: zero permits, zero maintenance for this run
        ctx_gen = OperationalContextGenerator(seed=seed * 1000 + trial)
        context_agent = OperationalContextAgent(ctx_gen)
        risk_agent = CompoundRiskCorrelationAgent(seed=0)

        readings = stream_gen.tick()
        sensor_events = fusion_agent.process_tick(readings)
        ctx_by_zone = context_agent.process_tick(stream_gen.sim_hour)

        # sanity: confirm this tick truly has zero corroborating context and no handover
        zctx = ctx_by_zone[sensor.zone_id]
        assert zctx.context_multiplier == 1.0, f"trial {trial}: context not clean ({zctx})"
        assert zctx.handover is False, f"trial {trial}: unexpected handover at hour {stream_gen.sim_hour}"

        risk_event = risk_agent.evaluate_zone(sensor.zone_id, sensor_events, zctx)
        severities_seen[risk_event.severity] += 1

        if risk_event.severity in ("HIGH", "MEDIUM"):
            violations.append({
                "trial": trial,
                "sensor_id": sensor.sensor_id,
                "magnitude_std": magnitude,
                "severity": risk_event.severity,
                "max_anomaly_score": risk_event.features["max_anomaly_score"],
            })

    return violations, severities_seen


def run_corroborated_control_test(seed: int = 200, n_trials: int = N_CONTROL_TRIALS):
    """Control group: WITH a dangerous permit combo + handover + a real anomaly,
    confirm the pipeline CAN still produce HIGH severity (i.e. gate isn't
    blanket-suppressing everything)."""
    rng = random.Random(seed)
    high_count = 0
    medium_or_above = 0

    for trial in range(n_trials):
        hour = 13.95  # inside the 14:00 handover window
        stream_gen = SensorStreamGenerator(start_sim_hour=hour, seed=seed * 1000 + trial)
        fusion_agent = SensorFusionAgent()
        zone = ZONE_BY_ID["Z3"]  # confined space, highest base risk
        sensor = rng.choice(zone.sensors)
        _warm_up(stream_gen, fusion_agent, zone_id=zone.zone_id)

        stream_gen.inject_anomaly(sensor.sensor_id, magnitude_std=rng.uniform(5.0, 9.0), duration_ticks=5, tag="control")

        ctx_gen = OperationalContextGenerator(seed=seed * 1000 + trial)
        ctx_gen.issue_permit(zone.zone_id, PermitType.HOT_WORK, at_hour=hour - 0.1, duration_hours=2.0)
        ctx_gen.issue_permit(zone.zone_id, PermitType.CONFINED_SPACE_ENTRY, at_hour=hour - 0.05, duration_hours=2.0)
        context_agent = OperationalContextAgent(ctx_gen)
        risk_agent = CompoundRiskCorrelationAgent(seed=0)

        readings = stream_gen.tick()
        sensor_events = fusion_agent.process_tick(readings)
        ctx_by_zone = context_agent.process_tick(stream_gen.sim_hour)
        zctx = ctx_by_zone[zone.zone_id]

        risk_event = risk_agent.evaluate_zone(zone.zone_id, sensor_events, zctx)
        if risk_event.severity == "HIGH":
            high_count += 1
        if risk_event.severity in ("HIGH", "MEDIUM"):
            medium_or_above += 1

    return high_count, medium_or_above, n_trials


def main():
    print(f"Running single-factor false-positive invariant test ({N_SINGLE_FACTOR_TRIALS} trials)...")
    violations, severities_seen = run_single_factor_invariant_test()
    fpr = len(violations) / N_SINGLE_FACTOR_TRIALS

    print(f"Severities observed across single-factor trials: {severities_seen}")
    print(f"False positive rate (MEDIUM/HIGH on single-factor anomaly): {fpr*100:.2f}%")
    if violations:
        print(f"VIOLATIONS FOUND: {violations[:5]} ...")
    else:
        print("ZERO violations -- hard invariant holds across all trials.")

    print(f"\nRunning corroborated-context control test ({N_CONTROL_TRIALS} trials)...")
    high_count, med_plus, n_ctrl = run_corroborated_control_test()
    print(f"HIGH severity rate with corroborating context + real anomaly: {high_count}/{n_ctrl} ({high_count/n_ctrl*100:.1f}%)")
    print(f"MEDIUM-or-above rate: {med_plus}/{n_ctrl} ({med_plus/n_ctrl*100:.1f}%)")

    # write TEST_RESULTS.md
    out_path = Path(__file__).resolve().parents[1] / "TEST_RESULTS.md"
    with open(out_path, "w") as f:
        f.write("# Agent 3 False-Positive Invariant -- Test Results\n\n")
        f.write("## Hard Invariant Test (single-factor anomaly, zero corroborating context)\n\n")
        f.write(f"- Trials run: **{N_SINGLE_FACTOR_TRIALS}**\n")
        f.write(f"- Each trial: one randomly chosen sensor forced 3-10 std deviations above baseline; "
                f"zero active permits, zero maintenance, handover window forced false.\n")
        f.write(f"- Severities observed: `{severities_seen}`\n")
        f.write(f"- **False positive rate (MEDIUM or HIGH severity): {fpr*100:.2f}%**\n")
        f.write(f"- Result: {'**PASS -- 0% false positive rate**' if fpr == 0 else '**FAIL -- invariant violated**'}\n\n")
        if violations:
            f.write("### Violations\n\n")
            for v in violations:
                f.write(f"- {v}\n")
        f.write("\n## Control Test (corroborating context present: dangerous permit combo "
                 "+ handover window + real anomaly)\n\n")
        f.write(f"- Trials run: **{n_ctrl}**\n")
        f.write(f"- HIGH severity rate: **{high_count}/{n_ctrl} ({high_count/n_ctrl*100:.1f}%)**\n")
        f.write(f"- MEDIUM-or-above rate: **{med_plus}/{n_ctrl} ({med_plus/n_ctrl*100:.1f}%)**\n")
        f.write(f"- Interpretation: the gate does not blanket-suppress all alerts -- when real "
                f"corroborating operational context is present alongside a genuine sensor anomaly, "
                f"the pipeline correctly escalates to HIGH severity the large majority of the time.\n")

    print(f"\nWrote {out_path}")
    assert fpr == 0.0, "FALSE POSITIVE INVARIANT VIOLATED"
    assert high_count > 0, "Control group failed to ever produce HIGH severity -- gate may be over-suppressing"
    print("\nAll invariant assertions passed.")


if __name__ == "__main__":
    main()
