"""Strategy templates (STR-005): manifests, syntax, pure helpers. NOT run on real LEAN here (KI-0050)."""

import importlib.util
import py_compile
import sys
from pathlib import Path

import pytest

from quantlab.strategies.registry import load_manifest

ROOT = Path(__file__).parents[3]
TEMPLATES = sorted(
    p for p in (ROOT / "strategies" / "templates").iterdir() if (p / "strategy.toml").is_file()
)
STUB = ROOT / "tests" / "fixtures" / "lean_stub"


def test_three_templates_exist():
    assert [p.name for p in TEMPLATES] == ["momentum_rotation", "pairs_zscore", "rsi_mean_reversion"]


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_manifest_and_syntax(template):
    manifest = load_manifest(template)
    assert manifest.language == "python" and manifest.description.startswith("Template:")
    assert {"start", "end"} <= set(manifest.parameters)
    py_compile.compile(str(template / manifest.entry_file), doraise=True)
    source = (template / manifest.entry_file).read_text()
    assert "quantlab_lean.configure(self)" in source and "except ImportError" in source
    assert "not a recommendation" in source


def test_pairs_hedge_ratio_and_zscore(monkeypatch):
    monkeypatch.syspath_prepend(str(STUB))
    sys.modules.pop("AlgorithmImports", None)
    spec = importlib.util.spec_from_file_location("pairs_tmpl", TEMPLATES[1] / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    beta, z = module.hedge_ratio_and_z(xs, [2 * x + (0.1 if i == 4 else 0.0) for i, x in enumerate(xs)])
    assert beta == pytest.approx(2.02) and z > 1.0  # last residual is the outlier
    assert module.hedge_ratio_and_z([1.0, 1.0], [2.0, 3.0]) == (None, None)
    sys.modules.pop("AlgorithmImports", None)
