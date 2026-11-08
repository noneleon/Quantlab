// Background jobs (CORE-007, DEC-0036): start backtests and optimizations of your strategies, follow them and
// cancel them. Research only: nothing on this page can trade or deploy.
import { useContext, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { apiGet, apiSend, type Job, type Strategy } from "../api";
import { ErrorBox, Loading } from "../components/States";
import { dateTime } from "../format";
import { TokenContext, useApi } from "../hooks";

/** "key=value" per line (or comma separated) → object. Throws on a malformed entry. */
export function parsePairs(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split(/[\n,]/)) {
    const item = raw.trim();
    if (!item) continue;
    const at = item.indexOf("=");
    if (at <= 0) throw new Error(`expected key=value, got "${item}"`);
    out[item.slice(0, at).trim()] = item.slice(at + 1).trim();
  }
  return out;
}

function lines(text: string): string[] {
  return text.split("\n").map((s) => s.trim()).filter(Boolean);
}

function useSubmit(onQueued: (job: Job) => void) {
  const token = useContext(TokenContext);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(path: string, body: () => unknown) {
    setBusy(true);
    setError(null);
    try {
      onQueued(await apiSend<Job>("POST", path, body(), token));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, submit };
}

export function BacktestForm({ strategies, onQueued }: { strategies: string[]; onQueued: (job: Job) => void }) {
  const [strategy, setStrategy] = useState(strategies[0] ?? "");
  const [params, setParams] = useState("");
  const [dataset, setDataset] = useState("");
  const [models, setModels] = useState("");
  const { busy, error, submit } = useSubmit(onQueued);
  return (
    <form className="card-form" onSubmit={(e) => {
      e.preventDefault();
      void submit("/api/jobs/backtest", () => ({
        strategy, parameters: parsePairs(params), dataset: dataset.trim() || null,
        models: models.trim() ? parsePairs(models) : null,
      }));
    }}>
      <h2>Run a backtest</h2>
      <label>Strategy <select aria-label="strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
        {strategies.map((s) => <option key={s}>{s}</option>)}</select></label>
      <label>Parameters <input aria-label="parameters" placeholder="fast=10, slow=30 (default: strategy.toml)" value={params} onChange={(e) => setParams(e.target.value)} /></label>
      <label>Dataset <input aria-label="dataset" placeholder="empty: LEAN sample data" value={dataset} onChange={(e) => setDataset(e.target.value)} /></label>
      <label>Execution models <input aria-label="models" placeholder="e.g. slippage=constant, slippage_percent=0.001" value={models} onChange={(e) => setModels(e.target.value)} /></label>
      <button type="submit" disabled={busy || !strategy}>{busy ? "Starting…" : "Run backtest"}</button>
      {error ? <div className="error" role="alert">{error}</div> : null}
    </form>
  );
}

export function OptimizationForm({ strategies, onQueued }: { strategies: string[]; onQueued: (job: Job) => void }) {
  const [strategy, setStrategy] = useState(strategies[0] ?? "");
  const [space, setSpace] = useState("fast=5,10,20\nslow=30,50");
  const [objective, setObjective] = useState("sharpe");
  const [constraints, setConstraints] = useState("");
  const [method, setMethod] = useState<"grid" | "random" | "tpe">("grid");
  const [samples, setSamples] = useState("20");
  const [parallel, setParallel] = useState("1");
  const [dataset, setDataset] = useState("");
  const [walk, setWalk] = useState(false);
  const [wf, setWf] = useState({ start: "", end: "", train_days: "365", test_days: "90" });
  const { busy, error, submit } = useSubmit(onQueued);
  function body() {
    const base = {
      strategy, space: lines(space), objective, constraints: lines(constraints), method,
      samples: method === "grid" ? null : Number(samples), parallel: Number(parallel), dataset: dataset.trim() || null,
    };
    return walk ? { ...base, ...wf, train_days: Number(wf.train_days), test_days: Number(wf.test_days) } : base;
  }
  return (
    <form className="card-form" onSubmit={(e) => { e.preventDefault(); void submit(walk ? "/api/jobs/walk-forward" : "/api/jobs/sweep", body); }}>
      <h2>Run an optimization</h2>
      <label>Strategy <select aria-label="optimization strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
        {strategies.map((s) => <option key={s}>{s}</option>)}</select></label>
      <label>Search space (one parameter per line) <textarea aria-label="space" rows={3} value={space} onChange={(e) => setSpace(e.target.value)} /></label>
      <label>Objective <input aria-label="objective" value={objective} onChange={(e) => setObjective(e.target.value)} /></label>
      <label>Constraints (one per line) <textarea aria-label="constraints" rows={2} placeholder="max_drawdown < 0.2" value={constraints} onChange={(e) => setConstraints(e.target.value)} /></label>
      <label>Method <select aria-label="method" value={method} onChange={(e) => setMethod(e.target.value as "grid" | "random" | "tpe")}>
        <option value="grid">grid</option><option value="random">random</option><option value="tpe">TPE (Optuna)</option></select></label>
      {method !== "grid" ? <label>Samples <input aria-label="samples" type="number" min={1} value={samples} onChange={(e) => setSamples(e.target.value)} /></label> : null}
      <label>Parallel trials <input aria-label="parallel" type="number" min={1} max={8} value={parallel} onChange={(e) => setParallel(e.target.value)} /></label>
      <label>Dataset <input aria-label="optimization dataset" placeholder="empty: LEAN sample data" value={dataset} onChange={(e) => setDataset(e.target.value)} /></label>
      <label className="inline"><input type="checkbox" aria-label="walk-forward" checked={walk} onChange={(e) => setWalk(e.target.checked)} /> Walk-forward</label>
      {walk ? (
        <div className="row">
          {(["start", "end", "train_days", "test_days"] as const).map((k) => (
            <label key={k}>{k.replace("_", " ")} <input aria-label={k} value={wf[k]} placeholder={k.endsWith("days") ? "" : "YYYY-MM-DD"}
              onChange={(e) => setWf({ ...wf, [k]: e.target.value })} /></label>
          ))}
        </div>
      ) : null}
      <button type="submit" disabled={busy || !strategy}>{busy ? "Starting…" : walk ? "Run walk-forward" : "Run sweep"}</button>
      {error ? <div className="error" role="alert">{error}</div> : null}
    </form>
  );
}

const ACTIVE = new Set(["queued", "running"]);

export function JobsTable({ jobs, onCancel }: { jobs: Job[]; onCancel: (id: string) => void }) {
  if (jobs.length === 0) return <p className="muted">No jobs since the server started.</p>;
  return (
    <table>
      <thead><tr><th>Job</th><th>State</th><th>Created</th><th>Finished</th><th>Result</th><th /></tr></thead>
      <tbody>
        {jobs.map((j) => (
          <tr key={j.id}>
            <td>{j.title}</td>
            <td><span className={`badge ${j.state === "succeeded" ? "badge-ok" : j.state === "failed" ? "badge-bad" : "badge-muted"}`}>
              {j.state}{j.cancel_requested && ACTIVE.has(j.state) ? " · cancelling" : ""}</span></td>
            <td>{dateTime(j.created_at)}</td>
            <td>{j.finished_at ? dateTime(j.finished_at) : "–"}</td>
            <td>{j.result_id ? <Link to={j.kind === "backtest" ? `/backtests/${j.result_id}` : `/optimizations/${j.result_id}`}>{j.result_id}</Link>
              : j.error ? <span className="error-text">{j.error}</span> : "–"}</td>
            <td>{ACTIVE.has(j.state) && !j.cancel_requested
              ? <button type="button" className="action" onClick={() => onCancel(j.id)}>Cancel</button> : null}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function JobsPage() {
  const token = useContext(TokenContext);
  const strategies = useApi<Strategy[]>("/api/strategies");
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let stop = false;
    apiGet<Job[]>("/api/jobs", token).then((j) => !stop && setJobs(j)).catch((e: Error) => !stop && setError(e));
    return () => { stop = true; };
  }, [token, tick]);
  const active = (jobs ?? []).some((j) => ACTIVE.has(j.state));
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setTick((t) => t + 1), 2000);
    return () => clearInterval(timer);
  }, [active]);
  if (strategies.error) return <ErrorBox error={strategies.error} />;
  if (strategies.loading || !strategies.data) return <Loading />;
  const names = strategies.data.map((s) => s.name);
  const refresh = () => setTick((t) => t + 1);
  async function cancel(id: string) {
    try { await apiSend("POST", `/api/jobs/${id}/cancel`, {}, token); } catch (e) { setError(e as Error); }
    refresh();
  }
  return (
    <section>
      <h1>Jobs</h1>
      <p className="muted">Backtests and optimizations started here run in the QuantLab server, one at a time by default
        (<code>[server] max_jobs</code>). Results are stored like CLI runs; the job list itself is kept until the server stops.</p>
      {names.length === 0 ? <p className="muted">Add a strategy first (Strategy Library).</p> : (
        <div className="forms">
          <BacktestForm strategies={names} onQueued={refresh} />
          <OptimizationForm strategies={names} onQueued={refresh} />
        </div>
      )}
      <h2>Recent jobs</h2>
      {error ? <ErrorBox error={error} /> : jobs === null ? <Loading /> : <JobsTable jobs={jobs} onCancel={(id) => void cancel(id)} />}
    </section>
  );
}
