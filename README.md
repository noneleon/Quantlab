# QuantLab

A fully self-hosted, local-first quantitative trading platform built around the open-source
[QuantConnect LEAN](https://github.com/QuantConnect/Lean) engine — research, backtesting, optimization,
portfolio management, paper and live trading, all under the owner's control.

**Status:** Phase 1 code complete; Milestone M1 awaiting its first real LEAN run — see [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md).

## Start here
- [CLAUDE.md](CLAUDE.md) — rules for AI-assisted development sessions
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design
- [docs/ROADMAP.md](docs/ROADMAP.md) — phases and the first milestone (M1)
- [docs/FEATURE_MATRIX.md](docs/FEATURE_MATRIX.md) — QuantConnect → QuantLab parity
- [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) — feature registry

## Quick start (Milestone M1)
```
uv sync
uv run quantlab init
```
Pin the LEAN image in `quantlab.toml` (path printed by `init`; see docs/CONFIGURATION_REFERENCE.md), then:
```
uv run quantlab engine pull
uv run quantlab strategy add sma strategies/examples/sma_crossover
uv run quantlab backtest run sma
uv run quantlab backtest list
uv run quantlab serve        # local read-only API on 127.0.0.1:8765 (token: quantlab api token --show)
```
Runs on LEAN's bundled sample data are labelled `sample` — they are not performance claims.

## Your own data (Phase 2a)
```
uv run quantlab data create spy-daily --asset-class equity --market usa --resolution daily \
    --provenance real_historical --adjustment raw --source "my broker export"
uv run quantlab data import spy-daily ./SPY.csv --symbol SPY
uv run quantlab data show spy-daily
```
Imports are validated (bad batches are quarantined, never partially imported) and every import creates an immutable,
content-hashed version. Backtest on a pinned dataset version:
```
uv run quantlab strategy add sma-daily strategies/examples/sma_daily
uv run quantlab backtest run sma-daily --dataset spy-daily
```

## Research notebooks
```
uv sync --extra research
uv run quantlab research      # JupyterLab on 127.0.0.1, workspace QUANTLAB_HOME/notebooks
```
```python
from quantlab.research import Research
qr = Research.open()
closes = qr.closes("spy-daily")          # same dataset versions as backtests; provenance in closes.attrs
qr.distribution(closes["SPY"]), qr.metrics(closes["SPY"])
```

## Check your machine
```
python tools/env_check.py
```

## Development
```
uv python install 3.11
uv sync
uv run pytest
uv run ruff check . && uv run mypy
```

Personal project. LEAN is © QuantConnect, Apache-2.0.
