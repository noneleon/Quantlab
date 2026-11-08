"""Self-contained HTML backtest report (AN-003).

One HTML file: inline CSS and inline SVG charts, no scripts, no external requests, so it opens offline and can be
archived or printed to PDF from any browser (print stylesheet included). Every value shown comes from
``analytics.metrics`` / ``analytics.charts`` output passed in; this module only formats. All text is HTML-escaped.

Non-real data (synthetic, sample) is announced in a banner at the top and in the footer: such a report is never
a performance claim.
"""

from __future__ import annotations

import html
import math
from collections.abc import Mapping, Sequence
from typing import Any

PALETTE = {"strategy": "#2563eb", "benchmark": "#9ca3af", "drawdown": "#dc2626", "grid": "#e5e7eb"}
DASH = "\u2013"  # en dash for missing values
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

CSS = """
body{font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#111827;background:#fff;margin:0}
main{max-width:1000px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:28px 0 8px;border-bottom:1px solid #e5e7eb;padding-bottom:4px}
.muted{color:#6b7280} .banner{background:#fef3c7;border:1px solid #f59e0b;padding:10px 12px;border-radius:6px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums} td,th{padding:4px 8px;border-bottom:1px solid #f3f4f6;text-align:left}
td.num,th.num{text-align:right} .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:0 24px}
svg{width:100%;height:auto;display:block} .legend span{margin-right:16px} .sw{display:inline-block;width:10px;height:10px;margin-right:4px}
.pos{background:#dcfce7} .neg{background:#fee2e2} code{font-size:12px;word-break:break-all}
@media print{main{max-width:none} h2{break-after:avoid} table,svg{break-inside:avoid}}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _pct(v: float | None, digits: int = 2) -> str:
    return DASH if v is None or not math.isfinite(v) else f"{v * 100:.{digits}f} %"


def _num(v: float | None, digits: int = 2) -> str:
    return DASH if v is None or not math.isfinite(v) else f"{v:,.{digits}f}"


def line_chart(
    series: Sequence[tuple[str, Sequence[Sequence[Any]], str]],
    *,
    width: int = 960,
    height: int = 260,
    y_format: str = "num",
    fill_zero: bool = False,
) -> str:
    """Inline SVG line chart. ``series`` = [(label, [[iso_date, value], ...], colour)], shared x by date order."""
    dates = sorted({str(p[0]) for _, pts, _ in series for p in pts})
    values = [float(p[1]) for _, pts, _ in series for p in pts if p[1] is not None]
    if len(dates) < 2 or not values:
        return '<p class="muted">Not enough data to draw.</p>'
    lo, hi = min(values), max(values)
    if fill_zero:
        hi = max(hi, 0.0)
    if math.isclose(lo, hi):
        lo, hi = lo - 1, hi + 1
    pad_l, pad_r, pad_t, pad_b = 70, 10, 10, 24
    index = {d: i for i, d in enumerate(dates)}

    def x(d: str) -> float:
        return pad_l + (width - pad_l - pad_r) * index[d] / (len(dates) - 1)

    def y(v: float) -> float:
        return pad_t + (height - pad_t - pad_b) * (hi - v) / (hi - lo)

    def fmt(v: float) -> str:
        return f"{v * 100:.1f}%" if y_format == "pct" else f"{v:,.0f}"

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" xmlns="http://www.w3.org/2000/svg">']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        parts.append(
            f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{PALETTE["grid"]}"/>'
            f'<text x="{pad_l - 6}" y="{y(v) + 4:.1f}" font-size="11" text-anchor="end" fill="#6b7280">'
            f"{_e(fmt(v))}</text>"
        )
    for d, anchor in ((dates[0], "start"), (dates[len(dates) // 2], "middle"), (dates[-1], "end")):
        parts.append(
            f'<text x="{x(d):.1f}" y="{height - 6}" font-size="11" text-anchor="{anchor}" fill="#6b7280">{_e(d)}</text>'
        )
    for label, pts, colour in series:
        coords = " ".join(f"{x(str(p[0])):.1f},{y(float(p[1])):.1f}" for p in pts if p[1] is not None)
        if fill_zero and coords:
            first, last = str(pts[0][0]), str(pts[-1][0])
            parts.append(
                f'<polygon points="{x(first):.1f},{y(0):.1f} {coords} {x(last):.1f},{y(0):.1f}" '
                f'fill="{colour}" fill-opacity="0.15"/>'
            )
        parts.append(
            f'<polyline points="{coords}" fill="none" stroke="{colour}" stroke-width="1.5"><title>{_e(label)}</title></polyline>'
        )
    parts.append("</svg>")
    legend = "".join(
        f'<span><i class="sw" style="background:{c}"></i>{_e(label)}</span>' for label, _, c in series
    )
    return f'<div class="legend muted">{legend}</div>' + "".join(parts)


def _kv_table(rows: Sequence[tuple[str, str]]) -> str:
    body = "".join(f"<tr><td>{_e(k)}</td><td class='num'>{_e(v)}</td></tr>" for k, v in rows)
    return f"<table>{body}</table>"


def _monthly_table(monthly: Sequence[Mapping[str, Any]], annual: Sequence[Mapping[str, Any]]) -> str:
    by_year: dict[int, dict[int, float]] = {}
    for m in monthly:
        by_year.setdefault(int(m["year"]), {})[int(m["month"])] = float(m["return"])
    annual_map = {int(a["year"]): float(a["return"]) for a in annual}
    head = "".join(f"<th class='num'>{m}</th>" for m in MONTHS)
    rows = []
    for year in sorted(by_year):
        cells = []
        for month in range(1, 13):
            v = by_year[year].get(month)
            cls = "" if v is None else (" pos" if v > 0 else " neg" if v < 0 else "")
            cells.append(f"<td class='num{cls}'>{_pct(v, 1) if v is not None else ''}</td>")
        total = annual_map.get(year)
        rows.append(f"<tr><th>{year}</th>{''.join(cells)}<th class='num'>{_pct(total, 1)}</th></tr>")
    return f"<table><tr><th></th>{head}<th class='num'>Year</th></tr>{''.join(rows)}</table>"


def render_report(
    *,
    run: Mapping[str, Any],
    metrics: Mapping[str, Any] | None,
    charts: Mapping[str, Any],
    warnings: Sequence[str],
    engine_statistics: Mapping[str, str] | None,
    cross_checks: Sequence[Mapping[str, Any]] | None,
    generated_at: str,
    quantlab_version: str,
) -> str:
    """The report as one HTML document. ``run`` holds id, strategy, status, provenance, parameters, manifest."""
    is_real = run.get("data_provenance") in ("real_historical", "live_recorded")
    banner = ""
    if not is_real or warnings:
        items = "".join(f"<li>{_e(w)}</li>" for w in warnings) or "<li>non-real data</li>"
        banner = (
            '<div class="banner"><strong>Not a performance claim.</strong> '
            f"Data provenance: <strong>{_e(run.get('data_provenance'))}</strong>.<ul>{items}</ul></div>"
        )
    m = metrics or {}
    metric_rows = [
        ("Period", f"{m.get('start', DASH)} → {m.get('end', DASH)}"),
        ("Total return", _pct(m.get("total_return"))),
        ("CAGR", _pct(m.get("cagr"))),
        ("Annual volatility", _pct(m.get("annual_volatility"))),
        ("Sharpe", _num(m.get("sharpe"))),
        ("Sortino", _num(m.get("sortino"))),
        ("Calmar", _num(m.get("calmar"))),
        ("Max drawdown", _pct(m.get("max_drawdown"))),
        ("Max drawdown duration (days)", _e(m.get("max_drawdown_duration_days", DASH))),
        ("Start / end equity", f"{_num(m.get('start_equity'))} / {_num(m.get('end_equity'))}"),
    ]
    ts = charts.get("trade_stats") or {}
    trade_rows = [
        ("Round trips", _e(ts.get("trades"))),
        ("Win rate", _pct(ts.get("win_rate"))),
        ("Profit factor", _num(ts.get("profit_factor"))),
        ("Expectancy (per trade)", _num(ts.get("expectancy"))),
        ("Average win / loss", f"{_num(ts.get('average_win'))} / {_num(ts.get('average_loss'))}"),
        ("Largest win / loss", f"{_num(ts.get('largest_win'))} / {_num(ts.get('largest_loss'))}"),
        ("Average holding (days)", _num(ts.get("average_holding_days"))),
    ]
    equity_series: list[tuple[str, Sequence[Sequence[Any]], str]] = [
        ("Strategy equity", charts["equity"], PALETTE["strategy"])
    ]
    bench = charts.get("benchmark")
    bench_html = ""
    if bench:
        equity_series.append((f"Benchmark: {bench['name']} (rebased)", bench["equity"], PALETTE["benchmark"]))
        s = bench["stats"]
        src = bench.get("source") or {}
        bench_html = (
            f"<h2>Benchmark — {_e(bench['name'])}</h2>"
            f"<p class='muted'>{_e(src.get('adjustment', ''))}; dataset {_e(src.get('dataset', ''))} "
            f"version <code>{_e(str(src.get('version', ''))[:20])}</code>; provenance {_e(src.get('provenance', ''))}; "
            f"{_e(s['common_days'])} common days.</p>"
            + _kv_table(
                [
                    (
                        "Strategy / benchmark return",
                        f"{_pct(s['strategy_return'])} / {_pct(s['benchmark_return'])}",
                    ),
                    ("Excess return", _pct(s["excess_return"])),
                    ("Beta", _num(s["beta"], 3)),
                    ("Alpha (annual)", _pct(s["alpha_annual"])),
                    ("Correlation", _num(s["correlation"], 3)),
                    ("Tracking error", _pct(s["tracking_error"])),
                    ("Information ratio", _num(s["information_ratio"])),
                ]
            )
        )
    checks = ""
    if cross_checks:
        rows = "".join(
            f"<tr><td>{_e(c.get('metric'))}</td><td class='num'>{_e(c.get('quantlab'))}</td>"
            f"<td class='num'>{_e(c.get('engine'))}</td><td>{_e(c.get('status'))}</td></tr>"
            for c in cross_checks
        )
        checks = (
            "<h2>Cross-check against engine statistics</h2><table><tr><th>Metric</th>"
            f"<th class='num'>QuantLab</th><th class='num'>Engine</th><th>Status</th></tr>{rows}</table>"
        )
    engine_stats = ""
    if engine_statistics:
        engine_stats = "<h2>Engine statistics (as reported by the engine)</h2>" + _kv_table(
            sorted(engine_statistics.items())
        )
    trades = charts.get("trades") or []
    trade_table = ""
    if trades:
        rows = "".join(
            f"<tr><td>{_e(t['symbol'])}</td><td>{_e(t['direction'])}</td><td class='num'>{_num(t['quantity'], 0)}</td>"
            f"<td>{_e(t['entry_time'][:19])}</td><td>{_e(t['exit_time'][:19])}</td>"
            f"<td class='num'>{_num(t['entry_price'])}</td><td class='num'>{_num(t['exit_price'])}</td>"
            f"<td class='num'>{_num(t['pnl'])}</td></tr>"
            for t in trades[:500]
        )
        more = f"<p class='muted'>First 500 of {len(trades)} trades.</p>" if len(trades) > 500 else ""
        trade_table = (
            "<h2>Trades</h2><table><tr><th>Symbol</th><th>Side</th><th class='num'>Qty</th><th>Entry</th>"
            f"<th>Exit</th><th class='num'>Entry px</th><th class='num'>Exit px</th><th class='num'>P&amp;L</th></tr>{rows}</table>"
            + more
        )
    manifest = run.get("manifest") or {}
    strategy = manifest.get("strategy") or {}
    engine = (manifest.get("engine") or {}).get("identity") or {}
    params = run.get("parameters") or {}
    repro_rows = [
        ("Run id", _e(run.get("id"))),
        (
            "Strategy",
            f"{_e(strategy.get('name'))} ({_e(strategy.get('language'))}, {_e(strategy.get('entry_file'))})",
        ),
        ("Strategy tree hash", _e(strategy.get("tree_hash"))),
        ("Parameters", ", ".join(f"{k}={v}" for k, v in sorted(params.items())) or DASH),
        ("Data", _e((manifest.get("data") or {}).get("note"))),
        ("Data version", _e(run.get("data_version") or DASH)),
        ("Engine", ", ".join(f"{k}={v}" for k, v in sorted(engine.items())) or DASH),
        (
            "Execution models",
            ", ".join(f"{k}={v}" for k, v in sorted((manifest.get("execution_models") or {}).items()))
            or "not recorded (LEAN defaults)",
        ),
        ("Inputs hash", _e(run.get("inputs_hash"))),
        ("Result hash", _e(run.get("result_hash"))),
    ]
    conventions = charts.get("conventions") or {}
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backtest {_e(run.get("id"))}</title><style>{CSS}</style></head>
<body><main>
<h1>Backtest report — {_e(strategy.get("name") or run.get("strategy"))}</h1>
<p class="muted">Run {_e(run.get("id"))} · status {_e(run.get("status"))} · generated {_e(generated_at)} · QuantLab {_e(quantlab_version)}</p>
{banner}
<h2>Equity</h2>{line_chart(equity_series)}
<h2>Drawdown</h2>{line_chart([("Drawdown", charts["drawdown"], PALETTE["drawdown"])], height=180, y_format="pct", fill_zero=True)}
<div class="grid"><div><h2>Performance</h2>{_kv_table(metric_rows)}</div>
<div><h2>Trades summary</h2>{_kv_table(trade_rows)}<p class="muted">{_e(ts.get("basis", ""))}</p></div></div>
<h2>Monthly returns</h2>{_monthly_table(charts["monthly_returns"], charts["annual_returns"])}
<h2>Rolling Sharpe ({_e(conventions.get("rolling_window"))} days)</h2>{line_chart([("Rolling Sharpe", charts["rolling_sharpe"], PALETTE["strategy"])], height=160)}
{bench_html}{checks}{trade_table}{engine_stats}
<h2>Reproducibility</h2>{_kv_table(repro_rows)}
<p class="muted">Conventions: {_e(", ".join(f"{k}={v}" for k, v in sorted(conventions.items())))}.
Metrics are computed by QuantLab from the engine's equity curve; trades are reconstructed from filled orders
(gross of fees). {"" if is_real else "This report uses non-real data and is not a performance claim."}</p>
</main></body></html>
"""
