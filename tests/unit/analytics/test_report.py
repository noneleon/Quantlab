"""HTML report rendering (AN-003): escaping, banners, no scripts. Synthetic values."""

from datetime import UTC, datetime, timedelta

from quantlab.analytics.charts import chart_datasets
from quantlab.analytics.report import line_chart, render_report


def render(provenance, warnings=(), name="<b>x</b>"):
    equity = [(datetime(2024, 1, 1, 20, tzinfo=UTC) + timedelta(days=i), 100.0 + i) for i in range(70)]
    return render_report(
        run={
            "id": "R1",
            "status": "succeeded",
            "data_provenance": provenance,
            "parameters": {"p": "<i>"},
            "manifest": {"strategy": {"name": name}},
        },
        metrics={"total_return": 0.69},
        charts=chart_datasets(equity),
        warnings=list(warnings),
        engine_statistics={"Sharpe Ratio": "<script>"},
        cross_checks=None,
        generated_at="2026-11-07T00:00:00+00:00",
        quantlab_version="0.0.0",
    )


def test_everything_is_escaped_and_scriptless():
    out = render("synthetic")
    assert "<b>x</b>" not in out and "&lt;b&gt;x&lt;/b&gt;" in out
    assert "<script" not in out and "&lt;script&gt;" in out and "p=&lt;i&gt;" in out
    assert "Not a performance claim" in out and "69.00 %" in out


def test_real_data_without_warnings_has_no_banner():
    out = render("real_historical", name="SMA")
    assert "Not a performance claim" not in out and "non-real data" not in out
    assert "Not a performance claim" in render(
        "real_historical", warnings=["benchmark 'b' is synthetic data"]
    )


def test_line_chart_needs_two_points():
    assert "Not enough data" in line_chart([("a", [["2024-01-01", 1.0]], "#000")])
    assert line_chart([("a", [["2024-01-01", 1.0], ["2024-01-02", 1.0]], "#000")]).count("<polyline") == 1
