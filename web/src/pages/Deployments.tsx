// Paper deployments (LIVE-001, DEC-0033): read-only. Start with `quantlab paper deploy`, stop with Ctrl+C or
// --max-minutes there; engaging the kill switch (Risk page) blocks every new order of every deployment.
import { useState } from "react";

import type { DeploymentLog, DeploymentRow } from "../api";
import { Empty, ErrorBox, Loading } from "../components/States";
import { dateTime, shortHash } from "../format";
import { useApi } from "../hooks";

function StateBadge({ row }: { row: DeploymentRow }) {
  const cls = row.running ? "badge-ok" : row.state === "lost" ? "badge-bad" : "badge-muted";
  const badge = <span className={`badge ${cls}`}>{row.running ? "running" : row.state}</span>;
  if (!row.paused) return badge;
  return (
    <>
      {badge}{" "}
      <span className="badge badge-warn" title={row.pause_reason ?? ""}>paused · new orders refused</span>
    </>
  );
}

function DataBadge({ states }: { states?: string[] }) {
  if (!states || states.length === 0) return <span className="muted">–</span>;
  const clean = states.every((s) => s === "LIVE");
  return (
    <span className={`badge ${clean ? "badge-ok" : "badge-warn"}`} title="data states of the quotes the strategy received">
      {states.join(", ")}{clean ? "" : " · not a performance claim"}
    </span>
  );
}

export function DeploymentsView({ rows, onShowLog }: { rows: DeploymentRow[]; onShowLog: (r: DeploymentRow) => void }) {
  return (
    <section>
      <h1>Paper deployments</h1>
      <p className="muted">Read-only. Start a strategy promoted to <code>paper</code> with <code>quantlab paper deploy</code>; every
        order goes through the QuantLab risk gate to the paper broker. <code>quantlab paper pause</code> / <code>resume</code> stop or
        allow one deployment's new orders; the kill switch (Risk page) stops all new orders.</p>
      {rows.length === 0 ? <Empty>No deployments yet.</Empty> : (
        <table>
          <thead><tr><th>Deployment</th><th>Run</th><th>Strategy</th><th>Code</th><th>State</th><th>Prices</th><th>Started</th>
            <th>Last heartbeat</th><th>Ended</th><th></th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.deployment}/${r.run}`}>
                <td>{r.deployment}</td><td><code>{r.run}</code></td><td>{r.strategy ?? "–"}</td>
                <td><code>{shortHash(r.tree_hash)}</code></td><td><StateBadge row={r} /></td><td><DataBadge states={r.data_states} /></td>
                <td>{dateTime(r.started_at)}</td><td>{dateTime(r.heartbeat_at)}</td>
                <td>{r.state === "stopped" ? `${r.stopped_by ?? ""} (exit ${r.exit_code ?? "?"})` : r.state === "lost" ? "no heartbeat — process gone" : "–"}</td>
                <td><button type="button" className="action" onClick={() => onShowLog(r)}>Log</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function LogView({ row }: { row: DeploymentRow }) {
  const { data, error, loading } = useApi<DeploymentLog>(
    `/api/paper/deployments/${encodeURIComponent(row.deployment)}/${encodeURIComponent(row.run)}/log?lines=300`,
  );
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return (
    <section>
      <h2>LEAN log — {row.deployment} / {row.run} (last {data.lines.length} lines)</h2>
      <pre className="log">{data.lines.join("\n")}</pre>
    </section>
  );
}

export function DeploymentsPage() {
  const { data, error, loading } = useApi<DeploymentRow[]>("/api/paper/deployments");
  const [selected, setSelected] = useState<DeploymentRow | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return (
    <>
      <DeploymentsView rows={data} onShowLog={setSelected} />
      {selected ? <LogView row={selected} /> : null}
    </>
  );
}
