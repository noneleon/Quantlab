// Paper trading console (UI-006). Everything here is read-only except *engaging* the kill switch, which can only
// reduce risk (DEC-0028). Orders, cancels, limits and disengaging stay in the CLI (CLAUDE.md §4).
import { useContext, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ENGAGE_CONFIRMATION, apiSend, type EngageResult, type PaperOrderDetail, type PaperOrderRow, type PaperPortfolio,
  type RiskStatus, type SupervisorStatus,
} from "../api";
import { Empty, ErrorBox, Loading } from "../components/States";
import { dateTime, num, shortHash } from "../format";
import { TokenContext, useApi } from "../hooks";

const STATE_CLASS: Record<string, string> = {
  FILLED: "badge-ok", ACCEPTED: "badge-info", SUBMITTED: "badge-info", PARTIALLY_FILLED: "badge-info",
  PENDING_SUBMIT: "badge-warn", PENDING_CANCEL: "badge-warn", SUBMIT_UNKNOWN: "badge-bad",
  RISK_REJECTED: "badge-bad", REJECTED: "badge-bad", CANCELLED: "badge-muted", EXPIRED: "badge-muted",
  NOT_PLACED: "badge-muted", NEW: "badge-muted",
};

export function OrderStateBadge({ state }: { state: string }) {
  return <span className={`badge ${STATE_CLASS[state] ?? "badge-muted"}`}>{state}</span>;
}

function CliHint({ children }: { children: React.ReactNode }) {
  return <p className="muted">{children}</p>;
}

export function PortfolioView({ data }: { data: PaperPortfolio }) {
  return (
    <section>
      <h1>Portfolio <span className="badge badge-warn">paper</span></h1>
      {data.warning ? <div className="warning" role="alert"><strong>Not a performance claim:</strong> {data.warning}</div> : null}
      <div className="cards">
        <div className="card"><span className="label">Account</span><span className="value">{data.account}</span></div>
        <div className="card"><span className="label">Cash</span><span className="value">{num(data.cash, 2)}</span></div>
        <div className="card"><span className="label">Starting cash</span><span className="value">{num(data.starting_cash, 2)}</span></div>
        <div className="card"><span className="label">Realized P&amp;L</span><span className="value">{num(data.realized_pnl, 2)}</span></div>
        <div className="card"><span className="label">Commissions</span><span className="value">{num(data.commissions, 2)}</span></div>
      </div>
      <h2>Positions (from confirmed fills)</h2>
      {data.positions.length === 0 ? <Empty>No positions.</Empty> : (
        <table>
          <thead><tr><th>Symbol</th><th className="num">Quantity</th><th className="num">Average price</th><th className="num">Realized P&amp;L</th></tr></thead>
          <tbody>
            {data.positions.map((p) => (
              <tr key={p.symbol}><td>{p.symbol}</td><td className="num">{num(p.quantity, 4)}</td>
                <td className="num">{num(p.average_price, 4)}</td><td className="num">{num(p.realized_pnl, 2)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      <CliHint>Positions are not marked here (marking needs live quotes): use <code>quantlab paper positions</code> for marked
        values and <code>quantlab paper reconcile</code> to compare with the broker.</CliHint>
    </section>
  );
}

export function PortfolioPage() {
  const { data, error, loading } = useApi<PaperPortfolio>("/api/paper/portfolio");
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return <PortfolioView data={data} />;
}

export function OrdersView({ rows }: { rows: PaperOrderRow[] }) {
  return (
    <section>
      <h1>Orders <span className="badge badge-warn">paper</span></h1>
      <CliHint>Read-only. Place and cancel paper orders with <code>quantlab paper order</code> / <code>quantlab paper cancel</code>;
        every order passes the central risk gate first.</CliHint>
      {rows.length === 0 ? <Empty>No orders yet.</Empty> : (
        <table>
          <thead><tr><th>Order</th><th>Symbol</th><th>Side</th><th className="num">Qty</th><th>Type</th><th className="num">Limit</th>
            <th className="num">Stop</th><th>TIF</th><th>State</th><th>Created</th></tr></thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.id}><td><Link to={`/orders/${o.id}`}>{o.id.slice(-8)}</Link></td><td>{o.symbol}</td><td>{o.side}</td>
                <td className="num">{num(o.quantity, 4)}</td><td>{o.order_type}</td><td className="num">{num(o.limit_price, 4)}</td>
                <td className="num">{num(o.stop_price, 4)}</td><td>{o.time_in_force}</td><td><OrderStateBadge state={o.state} /></td>
                <td>{dateTime(o.created_at)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function OrdersPage() {
  const { data, error, loading } = useApi<PaperOrderRow[]>("/api/paper/orders?limit=500");
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return <OrdersView rows={data} />;
}

export function OrderDetailView({ data }: { data: PaperOrderDetail }) {
  return (
    <section>
      <p><Link to="/orders">← Orders</Link></p>
      <h1>Order {data.order_id}</h1>
      <h2>State history (append-only)</h2>
      <table>
        <thead><tr><th>#</th><th>State</th><th>Detail</th><th>At</th></tr></thead>
        <tbody>
          {data.events.map((e) => (
            <tr key={e.seq}><td>{e.seq}</td><td><OrderStateBadge state={e.state} /></td>
              <td><code>{Object.keys(e.detail).length ? JSON.stringify(e.detail) : ""}</code></td><td>{dateTime(e.at)}</td></tr>
          ))}
        </tbody>
      </table>
      <h2>Risk decision</h2>
      {data.risk_decisions.map((d) => (
        <div key={d.at}>
          <p>{d.approved ? <span className="badge badge-ok">approved</span> : <span className="badge badge-bad">rejected</span>}{" "}
            limits sha256 <code>{shortHash(d.limits_sha256)}</code> · {dateTime(d.at)}</p>
          <table>
            <thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead>
            <tbody>
              {d.checks.map((c, i) => (
                <tr key={`${c.check}-${i}`}><td>{c.check}</td>
                  <td>{c.ok ? <span className="badge badge-ok">ok</span> : <span className="badge badge-bad">failed</span>}</td><td>{c.detail}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <h2>Fills (broker execution reports)</h2>
      {data.fills.length === 0 ? <Empty>No fills.</Empty> : (
        <table>
          <thead><tr><th>Exec id</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Commission</th><th>Price data</th><th>At</th></tr></thead>
          <tbody>
            {data.fills.map((f) => (
              <tr key={f.exec_id}><td><code>{f.exec_id}</code></td><td className="num">{num(f.quantity, 4)}</td><td className="num">{num(f.price, 4)}</td>
                <td className="num">{num(f.commission, 2)}</td>
                <td>{f.price_state === "LIVE" ? f.price_state : <span className="badge badge-warn">{f.price_state} · simulated</span>}</td>
                <td>{dateTime(f.at)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function OrderDetailPage() {
  const { id } = useParams();
  const { data, error, loading } = useApi<PaperOrderDetail>(id ? `/api/paper/orders/${encodeURIComponent(id)}` : null);
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return <OrderDetailView data={data} />;
}

/** Engage-only control: a reason and the typed phrase are required; there is deliberately no disengage control. */
export function EngageForm({ onEngaged }: { onEngaged: (r: EngageResult) => void }) {
  const token = useContext(TokenContext);
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = reason.trim() !== "" && confirm === ENGAGE_CONFIRMATION && !busy;
  async function engage() {
    setBusy(true);
    setError(null);
    try {
      onEngaged(await apiSend<EngageResult>("POST", "/api/risk/kill-switch/engage", { reason: reason.trim(), confirm }, token));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="notice" onSubmit={(e) => { e.preventDefault(); if (ready) void engage(); }}>
      <p><strong>Engage the kill switch</strong>: every new order is rejected and working paper orders are cancelled.
        Positions are not closed.</p>
      <input aria-label="reason" placeholder="reason (recorded in the audit log)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <input aria-label="confirmation" placeholder={`type ${ENGAGE_CONFIRMATION}`} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      <button type="submit" className="danger" disabled={!ready}>{busy ? "Engaging…" : "Engage kill switch"}</button>
      {error ? <div className="error" role="alert">{error}</div> : null}
    </form>
  );
}

export function EngageOutcome({ result }: { result: EngageResult }) {
  const still = Object.entries(result.still_working ?? {});
  return (
    <div className={result.error || still.length ? "warning" : "notice"} role="status">
      <strong>Kill switch engaged.</strong>
      {result.error ? <p>{result.error}. Run <code>quantlab paper reconcile</code>, then <code>quantlab paper cancel &lt;id&gt;</code> for
        each order still working (once the broker and market data are reachable).</p> : null}
      {result.cancelled && Object.keys(result.cancelled).length ? (
        <ul>{Object.entries(result.cancelled).map(([id, s]) => <li key={id}>{id.slice(-8)}: {s}</li>)}</ul>
      ) : null}
      {still.length ? (
        <p>May still be working — run <code>quantlab paper reconcile</code> and cancel by hand:{" "}
          {still.map(([id, s]) => `${id.slice(-8)} (${s})`).join(", ")}</p>
      ) : null}
    </div>
  );
}

export function RiskView({ data, onEngaged }: { data: RiskStatus; onEngaged: (r: EngageResult) => void }) {
  const ks = data.kill_switch;
  return (
    <section>
      <h1>Risk</h1>
      <div className={ks.engaged ? "error" : "notice"} role="status">
        Kill switch: <strong>{ks.engaged ? "ENGAGED" : "not engaged"}</strong>
        {ks.reason ? <> · {ks.reason}</> : null}{ks.at ? <> · {dateTime(ks.at)}</> : null}
      </div>
      {ks.engaged ? (
        <CliHint>Disengaging is only possible from an interactive terminal: <code>quantlab risk kill-switch disengage</code>.</CliHint>
      ) : <EngageForm onEngaged={onEngaged} />}
      <h2>Limits</h2>
      {data.limits.valid ? (
        <>
          <p className="muted">sha256 <code>{shortHash(data.limits.sha256)}</code> · edit the file and review with <code>quantlab risk show</code></p>
          <table>
            <tbody>{Object.entries(data.limits.values).map(([k, v]) => <tr key={k}><th>{k}</th><td>{JSON.stringify(v)}</td></tr>)}</tbody>
          </table>
        </>
      ) : <div className="error" role="alert">Every order is rejected: {data.limits.error}</div>}
      <h2>Recent decisions</h2>
      {data.decisions.length === 0 ? <Empty>No risk decisions yet.</Empty> : (
        <table>
          <thead><tr><th>Order</th><th>Decision</th><th>Failed checks</th><th>At</th></tr></thead>
          <tbody>
            {data.decisions.map((d) => (
              <tr key={`${d.order_id}-${d.at}`}><td><Link to={`/orders/${d.order_id}`}>{d.order_id.slice(-8)}</Link></td>
                <td>{d.approved ? <span className="badge badge-ok">approved</span> : <span className="badge badge-bad">rejected</span>}</td>
                <td>{d.failed.map((c) => `${c.check}: ${c.detail}`).join("; ")}</td><td>{dateTime(d.at)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function SupervisorLine({ data }: { data: SupervisorStatus }) {
  const st = data.status;
  if (!st) return <p className="muted">Supervisor: never started — run <code>quantlab paper supervise</code>.</p>;
  return (
    <div className={data.running && st.last_reconcile_clean !== false ? "notice" : "warning"} role="status">
      Supervisor: <strong>{data.running ? "running" : "NOT running"}</strong> · last heartbeat {dateTime(st.heartbeat_at)}
      {" "}· last reconcile {st.last_reconcile_clean === null ? "–" : st.last_reconcile_clean ? "clean" : "DIFFERENCES"} ({dateTime(st.last_reconcile_at)})
      {st.kill_switch_engaged_by_supervisor ? <> · engaged the kill switch at {dateTime(st.kill_switch_engaged_by_supervisor)}</> : null}
      {st.errors.length ? <> · last error: <code>{st.errors[st.errors.length - 1]}</code></> : null}
    </div>
  );
}

export function RiskPage() {
  const [refresh, setRefresh] = useState(0);
  const [outcome, setOutcome] = useState<EngageResult | null>(null);
  const { data, error, loading } = useApi<RiskStatus>(`/api/risk?limit=100&r=${refresh}`);
  const sup = useApi<SupervisorStatus>("/api/paper/supervisor");
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return (
    <>
      {outcome ? <EngageOutcome result={outcome} /> : null}
      {sup.data ? <SupervisorLine data={sup.data} /> : null}
      <RiskView data={data} onEngaged={(r) => { setOutcome(r); setRefresh((n) => n + 1); }} />
    </>
  );
}
