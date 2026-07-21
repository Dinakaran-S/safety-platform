# Agent 3 False-Positive Invariant -- Test Results

## Hard Invariant Test (single-factor anomaly, zero corroborating context)

- Trials run: **250**
- Each trial: one randomly chosen sensor forced 3-10 std deviations above baseline; zero active permits, zero maintenance, handover window forced false.
- Severities observed: `{'INFO': 0, 'LOW': 250, 'MEDIUM': 0, 'HIGH': 0}`
- **False positive rate (MEDIUM or HIGH severity): 0.00%**
- Result: **PASS -- 0% false positive rate**


## Control Test (corroborating context present: dangerous permit combo + handover window + real anomaly)

- Trials run: **100**
- HIGH severity rate: **100/100 (100.0%)**
- MEDIUM-or-above rate: **100/100 (100.0%)**
- Interpretation: the gate does not blanket-suppress all alerts -- when real corroborating operational context is present alongside a genuine sensor anomaly, the pipeline correctly escalates to HIGH severity the large majority of the time.
