"""Engine integration (ENG-*). Services use ``EngineAdapter``; ``create_engine_adapter`` builds one.

May import ``quantlab.core`` only (enforced by import-linter contracts in pyproject.toml).
"""

from __future__ import annotations

from quantlab.core.config import Settings
from quantlab.engine.base import EngineAdapter, EngineError


def create_engine_adapter(settings: Settings) -> EngineAdapter:
    lean = settings.engine.lean
    if lean.launcher == "native":
        from quantlab.engine.lean.native import LeanNativeAdapter

        return LeanNativeAdapter(settings.lean_native_dir, default_timeout_s=lean.timeout_s)
    if lean.image is None:
        raise EngineError(
            "engine.lean.image is not set. Pin the LEAN image by digest in quantlab.toml, e.g.\n"
            '  [engine.lean]\n  image = "quantconnect/lean@sha256:<digest>"\n'
            "(see docs/CONFIGURATION_REFERENCE.md for how to look up the digest)"
        )
    from quantlab.engine.lean.docker import LeanDockerAdapter

    return LeanDockerAdapter(
        lean.image,
        docker_command=lean.docker_command,
        network=lean.network,
        default_timeout_s=lean.timeout_s,
    )


__all__ = ["EngineAdapter", "EngineError", "create_engine_adapter"]
