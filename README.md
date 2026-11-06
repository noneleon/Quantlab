# QuantLab

A fully self-hosted, local-first quantitative trading platform built around the open-source
[QuantConnect LEAN](https://github.com/QuantConnect/Lean) engine — research, backtesting, optimization,
portfolio management, paper and live trading, all under the owner's control.

**Status:** Phase 0 (architecture & project memory). No platform code yet — see [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md).

## Start here
- [CLAUDE.md](CLAUDE.md) — rules for AI-assisted development sessions
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design
- [docs/ROADMAP.md](docs/ROADMAP.md) — phases and the first milestone (M1)
- [docs/FEATURE_MATRIX.md](docs/FEATURE_MATRIX.md) — QuantConnect → QuantLab parity
- [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) — feature registry

## Check your machine
```
python tools/env_check.py
```

## Tests
```
python -m unittest discover -s tests -v
```

Personal project. LEAN is © QuantConnect, Apache-2.0.
