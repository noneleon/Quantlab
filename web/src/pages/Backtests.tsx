import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import type { BacktestDetail, BacktestSummary } from "../api";
import { ProvenanceBadge, StatusBadge } from "../components/Badges";
import { Empty, ErrorBox, Loading } from "../components/States";
import { dateTime, num, pct, shortHash } from "../format";
import { useApi } from "../hooks";
import { BacktestChartsSection } from "./BacktestCharts";

export function BacktestList() {
  const { data, error, loading } = useApi<BacktestSummary[]>("/api/backtests?limit=200");
  const [selected, setSelected] = useState<string[]>([]);
  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id].slice(-10)));
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return (
    <section>
      <h1>Backtesting</h1>
      <p className="muted">History is immutable. Run with <code>quantlab backtest run</code>; reproduce with <code>quantlab backtest rerun</code>.</p>
      <p>{selected.length >= 2 ? <Link to={`/compare?runs=${selected.join(",")}`}>Compare {selected.length} selected runs</Link> : <span className="muted">Select 2–10 succeeded runs to compare.</span>}</p>
      {data.length === 0 ? <Empty>No backtests yet.</Empty> : (
        <table>
          <thead><tr><th>Compare</th><th>Run</th><th>Status</th><th>Data</th><th className="num">Return</th><th className="num">Sharpe</th>
            <th className="num">Max DD</th><th>Created</th><th>Rerun of</th></tr></thead>
          <tbody>
            {data.map((b) => (
              <tr key={b.id}>
                <td><input type="checkbox" aria-label={`compare ${b.id}`} checked={selected.includes(b.id)} onChange={() => toggle(b.id)} disabled={b.status !== "succeeded"} /></td>
                <td><Link to={`/backtests/${b.id}`}>{b.id}</Link></td><td><StatusBadge status={b.status} /></td>
                <td><ProvenanceBadge provenance={b.data_provenance} /></td><td className="num">{pct(b.total_return)}</td>
                <td className="num">{num(b.sharpe)}</td><td className="num">{pct(b.max_drawdown)}</td>
                <td>{dateTime(b.created_at)}</td>
                <td>{b.rerun_of ? <Link to={`/backtests/${b.rerun_of}`}>{shortHash(b.rerun_of)}</Link> : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

const METRIC_ROWS: [string, string, (v: number | null) => string][] = [
  ["total_return", "Total return", (v) => pct(v)],
  ["cagr", "CAGR", (v) => pct(v)],
  ["annual_volatility", "Annual volatility", (v) => pct(v)],
  ["sharpe", "Sharpe", (v) => num(v)],
  ["sortino", "Sortino", (v) => num(v)],
  ["calmar", "Calmar", (v) => num(v)],
  ["max_drawdown", "Max drawdown", (v) => pct(v)],
  ["max_drawdown_duration_days", "Max DD duration (days)", (v) => (v === null ? "–" : String(v))],
];

export function BacktestDetailPage() {
  const id = useParams().id ?? "";
  const { data, error, loading } = useApi<BacktestDetail>(`/api/backtests/${encodeURIComponent(id)}`);
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  const metrics = data.metrics ?? {};
  return (
    <section>
      <p><Link to="/backtests">← Backtesting</Link></p>
      <h1>Run {data.id} <StatusBadge status={data.status} /></h1>
      <dl className="facts">
        <dt>Data</dt><dd><ProvenanceBadge provenance={data.data_provenance} /></dd>
        <dt>Parameters</dt><dd><code>{JSON.stringify(data.parameters)}</code></dd>
        <dt>Engine status</dt><dd>{data.engine_status ?? "–"}</dd>
        <dt>Inputs hash</dt><dd><code>{shortHash(data.inputs_hash, 20)}</code></dd>
        <dt>Result hash</dt><dd><code>{shortHash(data.result_hash, 20)}</code></dd>
        <dt>Created</dt><dd>{dateTime(data.created_at)}</dd>
      </dl>
      {data.error ? <div className="error">Error: {data.error}</div> : null}
      {data.runtime_error ? <div className="error">Runtime error: {data.runtime_error}</div> : null}
      {data.status === "succeeded" || data.status === "algorithm_error" ? <BacktestChartsSection runId={data.id} /> : null}
      <h2>Metrics (QuantLab)</h2>
      {data.metrics ? (
        <table className="narrow"><tbody>
          {METRIC_ROWS.map(([key, label, fmt]) => (
            <tr key={key}><th>{label}</th><td className="num">{fmt((metrics[key] as number | null | undefined) ?? null)}</td></tr>
          ))}
        </tbody></table>
      ) : <Empty>No metrics.</Empty>}
      <h2>Cross-check with engine statistics</h2>
      {data.cross_checks && data.cross_checks.length > 0 ? (
        <table>
          <thead><tr><th>Metric</th><th>Engine statistic</th><th className="num">QuantLab</th><th className="num">Engine</th><th>Status</th></tr></thead>
          <tbody>
            {data.cross_checks.map((c) => (
              <tr key={c.metric}><td>{c.metric}</td><td>{c.engine_statistic}</td><td className="num">{pct(c.quantlab, 3)}</td>
                <td className="num">{pct(c.engine, 3)}</td><td>{c.status}</td></tr>
            ))}
          </tbody>
        </table>
      ) : <Empty>No cross-checks.</Empty>}
      <h2>Engine statistics</h2>
      {data.engine_statistics ? (
        <table className="narrow"><tbody>
          {Object.entries(data.engine_statistics).map(([k, v]) => <tr key={k}><th>{k}</th><td className="num">{v}</td></tr>)}
        </tbody></table>
      ) : <Empty>None.</Empty>}
      <details>
        <summary>Reproducibility manifest</summary>
        <pre>{JSON.stringify(data.manifest, null, 2)}</pre>
      </details>
    </section>
  );
}
