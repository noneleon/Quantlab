import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";

import type { OptimizationDetail, OptimizationSummaryRow, OptimizationTrialRow, TrialView } from "../api";
import { StatusBadge } from "../components/Badges";
import { Empty, ErrorBox, Loading } from "../components/States";
import { TimeSeriesChart } from "../components/TimeSeriesChart";
import { dateTime, num, pct } from "../format";
import { useApi } from "../hooks";

export function OptimizationList() {
  const { data, error, loading } = useApi<OptimizationSummaryRow[]>("/api/optimizations?limit=200");
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return (
    <section>
      <h1>Optimization</h1>
      <p className="muted">
        Start studies with <code>quantlab optimize sweep</code> or <code>quantlab optimize walk-forward</code>. Every trial is a recorded
        backtest; the best trial of many is an in-sample selection — read it together with the deflated Sharpe ratio and PBO.
      </p>
      {data.length === 0 ? <Empty>No optimizations yet.</Empty> : (
        <table>
          <thead><tr><th>Study</th><th>Kind</th><th>Strategy</th><th>Objective</th><th>Status</th><th>Created</th></tr></thead>
          <tbody>
            {data.map((o) => (
              <tr key={o.id}><td><Link to={`/optimizations/${o.id}`}>{o.id}</Link></td><td>{o.kind === "sweep" ? "sweep" : "walk-forward"}</td>
                <td>{o.strategy}</td><td><code>{o.objective}</code></td><td><StatusBadge status={o.status} /></td><td>{dateTime(o.created_at)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function params(p: Record<string, string> | undefined, keys?: string[]): string {
  if (!p) return "–";
  return Object.entries(p).filter(([k]) => !keys || keys.includes(k)).map(([k, v]) => `${k}=${v}`).join(", ") || "–";
}

function RunLink({ id }: { id: string | null | undefined }) {
  return id ? <Link to={`/backtests/${id}`}>{id.slice(-8)}</Link> : <>–</>;
}

/** Objective over two parameters (first two search dimensions). Colour = relative objective; the value is printed too. */
export function Heatmap({ trials, x, y }: { trials: OptimizationTrialRow[]; x: string; y: string }) {
  const cells = new Map<string, number>();
  for (const t of trials) if (t.objective !== null && t.status === "ok") cells.set(`${t.parameters[x]}|${t.parameters[y]}`, t.objective);
  const axis = (name: string) =>
    [...new Set(trials.map((t) => t.parameters[name] ?? ""))].sort((a, b) => Number(a) - Number(b) || a.localeCompare(b));
  const xs = axis(x);
  const ys = axis(y);
  const values = [...cells.values()];
  const lo = Math.min(...values), hi = Math.max(...values);
  const shade = (v: number) => (hi > lo ? (v - lo) / (hi - lo) : 0.5);
  return (
    <table className="heatmap" aria-label={`objective by ${x} and ${y}`}>
      <thead><tr><th>{y} \ {x}</th>{xs.map((v) => <th key={v} className="num">{v}</th>)}</tr></thead>
      <tbody>
        {ys.map((yv) => (
          <tr key={yv}><th>{yv}</th>
            {xs.map((xv) => {
              const v = cells.get(`${xv}|${yv}`);
              return v === undefined ? <td key={xv} className="num muted">–</td> : (
                <td key={xv} className="num heat" style={{ backgroundColor: `rgba(37, 99, 235, ${(0.12 + 0.75 * shade(v)).toFixed(3)})` }}>{num(v)}</td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TrialTable({ rows, keys }: { rows: TrialView[]; keys: string[] }) {
  return (
    <table>
      <thead><tr><th>#</th><th>Parameters</th><th className="num">Objective</th><th className="num">Return</th><th className="num">Sharpe</th><th className="num">Max DD</th><th className="num">Trades</th><th>Run</th></tr></thead>
      <tbody>
        {rows.map((t) => (
          <tr key={t.seq}><td>{t.seq}</td><td>{params(t.parameters, keys)}</td><td className="num">{t.objectives ? t.objectives.map((v) => num(v)).join(" / ") : num(t.objective)}</td>
            <td className="num">{pct(t.metrics.total_return)}</td><td className="num">{num(t.metrics.sharpe)}</td><td className="num">{pct(t.metrics.max_drawdown)}</td>
            <td className="num">{t.metrics.trades ?? "–"}</td><td><RunLink id={t.run_id} /></td></tr>
        ))}
      </tbody>
    </table>
  );
}

export function OptimizationDetailPage() {
  const id = useParams().id ?? "";
  const { data, error, loading } = useApi<OptimizationDetail>(`/api/optimizations/${encodeURIComponent(id)}`);
  const oos = useMemo(() => (data?.summary?.oos_equity ? [{ label: "out-of-sample (stitched)", color: "#2563eb", points: data.summary.oos_equity }] : []), [data]);
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  const s = data.summary;
  const keys = Object.keys(data.spec.space ?? {});
  const sweepTrials = data.trials.filter((t) => t.segment === "ALL");
  const of = s?.overfitting;
  return (
    <section>
      <p><Link to="/optimizations">← Optimization</Link></p>
      <h1>{data.kind === "sweep" ? "Sweep" : "Walk-forward"} {data.id} <StatusBadge status={data.status} /></h1>
      <dl className="facts">
        <dt>Strategy</dt><dd>{data.strategy}</dd>
        <dt>Objective</dt><dd><code>{data.spec.objective}</code> (maximised)</dd>
        <dt>Search space</dt><dd>{keys.map((k) => `${k}: ${(data.spec.space[k] ?? []).join(", ")}`).join(" · ")}</dd>
        <dt>Method</dt><dd>{String(data.spec.method ?? "grid")}</dd>
        <dt>Created</dt><dd>{dateTime(data.created_at)}</dd>
      </dl>
      {data.error ? <div className="error">Error: {data.error}</div> : null}
      {s && s.warnings.length > 0 ? <div className="warning" role="alert"><ul>{s.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div> : null}
      {s && data.kind === "sweep" ? (
        <>
          <h2>Result</h2>
          <p>{s.trials} trials · {Object.entries(s.status_counts ?? {}).map(([k, v]) => `${k}: ${v}`).join(", ")}</p>
          {s.best ? <p>Best (in-sample): <strong>{params(s.best.parameters, keys)}</strong> · objective {num(s.best.objective)} · run <RunLink id={s.best.run_id} /></p> : <Empty>No trial satisfied the objective and constraints.</Empty>}
          <h2>Overfitting checks</h2>
          <table className="narrow"><tbody>
            <tr><th>Deflated Sharpe: P(true Sharpe &gt; best of {of?.deflated_sharpe?.trials ?? "–"} noise trials)</th><td className="num">{of?.deflated_sharpe ? num(of.deflated_sharpe.probability) : of?.deflated_sharpe_error ?? "–"}</td></tr>
            <tr><th>Best Sharpe (annualised) / expected max of noise (per period)</th><td className="num">{of?.deflated_sharpe ? `${num(of.deflated_sharpe.sharpe_annual)} / ${num(of.deflated_sharpe.expected_max_sharpe, 4)}` : "–"}</td></tr>
            <tr><th>Probability of backtest overfitting (CSCV{of?.pbo ? `, ${of.pbo.splits} splits` : ""})</th><td className="num">{of?.pbo ? num(of.pbo.pbo) : of?.pbo_error ?? "–"}</td></tr>
            <tr><th>Neighbourhood ratio (≈1 plateau, ≪1 spike)</th><td className="num">{num(s.sensitivity?.neighbourhood_ratio ?? null)}</td></tr>
          </tbody></table>
          {keys[0] !== undefined && keys[1] !== undefined ? <><h2>Objective heatmap</h2><Heatmap trials={sweepTrials} x={keys[0]} y={keys[1]} /></> : null}
          {s.sensitivity ? (
            <>
              <h2>Sensitivity (mean objective per value)</h2>
              <div className="grid-tables">
                {s.sensitivity.parameters.map((p) => (
                  <table key={p} className="narrow"><thead><tr><th>{p}</th><th className="num">mean</th><th className="num">min</th><th className="num">max</th></tr></thead>
                    <tbody>{(s.sensitivity!.marginal[p] ?? []).map((m) => <tr key={m.value}><td>{m.value}</td><td className="num">{num(m.mean)}</td><td className="num">{num(m.min)}</td><td className="num">{num(m.max)}</td></tr>)}</tbody></table>
                ))}
              </div>
            </>
          ) : null}
          {s.pareto ? <><h2>Pareto front</h2><TrialTable rows={s.pareto} keys={keys} /></> : null}
          <h2>Top trials</h2>
          {s.top && s.top.length > 0 ? <TrialTable rows={s.top} keys={keys} /> : <Empty>None.</Empty>}
        </>
      ) : null}
      {s && data.kind === "walk_forward" ? (
        <>
          <h2>Out-of-sample result</h2>
          <p>{s.is_trials} in-sample trials · {s.oos_runs} out-of-sample runs · walk-forward efficiency {num(s.walk_forward_efficiency ?? null)} · parameter changes {s.parameter_changes ?? "–"}</p>
          {s.oos_metrics ? <p>Stitched OOS: return {pct(s.oos_metrics.total_return)} · CAGR {pct(s.oos_metrics.cagr)} · Sharpe {num(s.oos_metrics.sharpe)} · max drawdown {pct(s.oos_metrics.max_drawdown)}</p> : null}
          {oos.length ? <TimeSeriesChart series={oos} label="out-of-sample equity" height={260} /> : null}
          <h2>Windows</h2>
          <table>
            <thead><tr><th>#</th><th>In-sample</th><th>Out-of-sample</th><th>Chosen parameters</th><th className="num">IS objective</th><th className="num">OOS objective</th><th className="num">IS CAGR</th><th className="num">OOS CAGR</th><th>Runs</th></tr></thead>
            <tbody>
              {(s.windows ?? []).map((w) => (
                <tr key={w.window}><td>{w.window}</td><td>{w.is[0]} → {w.is[1]}</td><td>{w.oos[0]} → {w.oos[1]}</td><td>{w.parameters ? params(w.parameters) : w.status}</td>
                  <td className="num">{num(w.is_objective ?? null)}</td><td className="num">{num(w.oos_objective ?? null)}</td><td className="num">{pct(w.is_cagr)}</td><td className="num">{pct(w.oos_cagr)}</td>
                  <td>IS <RunLink id={w.is_run} /> · OOS <RunLink id={w.oos_run} /></td></tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
      <details>
        <summary>All trials ({data.trials.length})</summary>
        <table>
          <thead><tr><th>#</th><th>Window</th><th>Segment</th><th>Parameters</th><th>Status</th><th className="num">Objective</th><th>Run</th></tr></thead>
          <tbody>
            {data.trials.map((t) => (
              <tr key={t.seq}><td>{t.seq}</td><td>{t.window ?? "–"}</td><td>{t.segment}</td><td>{params(t.parameters)}</td><td>{t.status}</td><td className="num">{num(t.objective)}</td><td><RunLink id={t.run_id} /></td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}
