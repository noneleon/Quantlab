import { Link } from "react-router-dom";

import type { BacktestSummary, Dataset, Health, HealthReport, Strategy } from "../api";
import { ProvenanceBadge, StatusBadge } from "../components/Badges";
import { ErrorBox, Loading } from "../components/States";
import { dateTime, num, pct } from "../format";
import { useApi } from "../hooks";

const HEALTH_BADGE = { ok: "badge-ok", warn: "badge-warn", fail: "badge-bad" } as const;

/** OPS-001: read-only health checks; alerts on changes come from `quantlab paper supervise` / `health --alert`. */
export function HealthList({ report }: { report: HealthReport }) {
  return (
    <table>
      <thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
      <tbody>
        {report.checks.map((c) => (
          <tr key={c.name}>
            <td>{c.name}</td>
            <td><span className={`badge ${HEALTH_BADGE[c.status]}`}>{c.status}</span></td>
            <td>{c.detail}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Dashboard() {
  const health = useApi<Health>("/api/health");
  const datasets = useApi<Dataset[]>("/api/datasets");
  const strategies = useApi<Strategy[]>("/api/strategies");
  const backtests = useApi<BacktestSummary[]>("/api/backtests?limit=5");
  const checks = useApi<HealthReport>("/api/health/checks"); // never blocks the dashboard
  const error = datasets.error ?? strategies.error ?? backtests.error;
  if (error) return <ErrorBox error={error} />;
  if (datasets.loading || strategies.loading || backtests.loading) return <Loading />;
  return (
    <section>
      <h1>Dashboard</h1>
      <div className="cards">
        <div className="card"><span className="label">API</span><span className="value">{health.data?.status ?? "–"}</span>
          <span className="muted">QuantLab {health.data?.version ?? ""}</span></div>
        <div className="card"><span className="label">Datasets</span><span className="value">{datasets.data?.length ?? 0}</span>
          <Link to="/data">Market Data</Link></div>
        <div className="card"><span className="label">Strategies</span><span className="value">{strategies.data?.length ?? 0}</span>
          <Link to="/strategies">Strategy Library</Link></div>
        <div className="card"><span className="label">Trading mode</span><span className="value">paper</span>
          <span className="muted">live trading not available</span></div>
      </div>
      <h2>Health {checks.data ? <span className={`badge ${HEALTH_BADGE[checks.data.status]}`}>{checks.data.status}</span> : null}</h2>
      {checks.error ? <ErrorBox error={checks.error} /> : checks.data ? <HealthList report={checks.data} /> : <Loading />}
      <h2>Recent backtests</h2>
      {backtests.data && backtests.data.length > 0 ? (
        <table>
          <thead><tr><th>Run</th><th>Status</th><th>Data</th><th>Return</th><th>Sharpe</th><th>Max DD</th><th>Created</th></tr></thead>
          <tbody>
            {backtests.data.map((b) => (
              <tr key={b.id}>
                <td><Link to={`/backtests/${b.id}`}>{b.id}</Link></td>
                <td><StatusBadge status={b.status} /></td>
                <td><ProvenanceBadge provenance={b.data_provenance} /></td>
                <td className="num">{pct(b.total_return)}</td><td className="num">{num(b.sharpe)}</td>
                <td className="num">{pct(b.max_drawdown)}</td><td>{dateTime(b.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">No backtests yet. Run one with <code>quantlab backtest run &lt;strategy&gt;</code>.</p>
      )}
    </section>
  );
}
