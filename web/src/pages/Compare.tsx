import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { CompareResult } from "../api";
import { ProvenanceBadge } from "../components/Badges";
import { ErrorBox, Loading } from "../components/States";
import { TimeSeriesChart, type ChartSeries } from "../components/TimeSeriesChart";
import { num, pct } from "../format";
import { useApi } from "../hooks";

const COLOURS = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#4b5563", "#ca8a04", "#db2777", "#65a30d"];

/** AN-004: /compare?runs=a,b,c */
export function ComparePage() {
  const [params] = useSearchParams();
  const runs = params.get("runs") ?? "";
  const { data, error, loading } = useApi<CompareResult>(runs ? `/api/compare?runs=${encodeURIComponent(runs)}` : null);
  const series = useMemo<ChartSeries[]>(
    () => (data ? data.equity.map((e, i) => ({ label: e.label, color: COLOURS[i % COLOURS.length] ?? "#2563eb", points: e.equity })) : []),
    [data],
  );
  if (!runs) return <section><h1>Compare runs</h1><p className="muted">Select two or more runs in <Link to="/backtests">Backtesting</Link>.</p></section>;
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return (
    <section>
      <p><Link to="/backtests">← Backtesting</Link></p>
      <h1>Compare runs</h1>
      {data.warnings.length > 0 ? <div className="warning" role="alert"><ul>{data.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div> : null}
      <p className="muted">{data.conventions}.</p>
      <TimeSeriesChart series={series} label="rebased equity of the compared runs" />
      <table>
        <thead><tr><th>Run</th><th>Data</th><th className="num">Return</th><th className="num">CAGR</th><th className="num">Volatility</th><th className="num">Sharpe</th><th className="num">Sortino</th><th className="num">Max DD</th><th>Period</th></tr></thead>
        <tbody>
          {data.runs.map((r) => (
            <tr key={r.id}><td><Link to={`/backtests/${r.id}`}>{r.label}</Link></td><td>{r.provenance ? <ProvenanceBadge provenance={r.provenance} /> : "–"}</td>
              <td className="num">{pct(r.metrics.total_return as number | null)}</td><td className="num">{pct(r.metrics.cagr as number | null)}</td>
              <td className="num">{pct(r.metrics.annual_volatility as number | null)}</td><td className="num">{num(r.metrics.sharpe as number | null)}</td>
              <td className="num">{num(r.metrics.sortino as number | null)}</td><td className="num">{pct(r.metrics.max_drawdown as number | null)}</td>
              <td>{String(r.metrics.start ?? "–")} → {String(r.metrics.end ?? "–")}</td></tr>
          ))}
        </tbody>
      </table>
      <h2>Correlation of daily returns ({data.common_days} common days)</h2>
      {data.correlation ? (
        <table className="narrow">
          <thead><tr><th />{data.runs.map((r, i) => <th key={r.id} className="num">#{i + 1}</th>)}</tr></thead>
          <tbody>{data.correlation.map((row, i) => <tr key={i}><th>#{i + 1}</th>{row.map((v, j) => <td key={j} className="num">{num(v)}</td>)}</tr>)}</tbody>
        </table>
      ) : <p className="muted">Fewer than 3 common days.</p>}
    </section>
  );
}
