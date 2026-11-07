# LEAN result fixtures

`sample_result.json` is **hand-constructed (synthetic)**, not output from a real LEAN run. Its structure follows
LEAN's result handler / order enums and the Lean CLI's local-result tests (checked 2026-11-06); all numbers are
made up and must never appear in a performance report.

When a real LEAN run is available (KI-0002), add a recorded `<id>.json` next to it, labelled `recorded`,
with the image digest that produced it, and test the parser against both.

`aapl_factor_file.csv` is LEAN's real factor file for AAPL, copied verbatim from the QuantConnect LEAN repository
(Apache-2.0), `Data/equity/usa/factor_files/aapl.csv` at commit 06eb5cb3 (2026-11-05). It is used as a golden reference
for QuantLab's corporate-action factor computation (`tests/unit/data/test_corporate_actions.py`).
