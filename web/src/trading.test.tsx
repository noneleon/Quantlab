import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { PaperOrderDetail, PaperPortfolio, RiskStatus } from "./api";
import { EngageOutcome, OrderDetailView, OrdersView, PortfolioView, RiskView, SupervisorLine } from "./pages/Trading";

const risk = (engaged: boolean): RiskStatus => ({
  kill_switch: { engaged, reason: engaged ? "halt" : null, at: engaged ? "2026-11-09T15:00:00Z" : null },
  limits: { valid: true, sha256: "ab".repeat(32), values: { max_order_quantity: 100 } },
  decisions: [{ order_id: "01ORDERAAAAAAAA", approved: false, failed: [{ check: "kill switch", ok: false, detail: "engaged" }],
    at: "2026-11-09T15:00:00Z" }],
});

const render = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);

describe("trading console (UI-006)", () => {
  it("offers only an engage control, guarded by a reason and the typed phrase", () => {
    const html = render(<RiskView data={risk(false)} onEngaged={() => {}} />);
    expect(html).toContain("Engage kill switch");
    expect(html).toContain("type ENGAGE KILL SWITCH");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Engage kill switch/); // disabled until both are filled in
    expect(html.toLowerCase()).not.toContain(">disengage");
    expect(html).toContain("max_order_quantity");
  });

  it("when engaged there is no control at all; disengaging is a terminal command", () => {
    const html = render(<RiskView data={risk(true)} onEngaged={() => {}} />);
    expect(html).toContain("ENGAGED");
    expect(html).toContain("quantlab risk kill-switch disengage");
    expect(html).not.toMatch(/<(button|input|select|form)\b/);
  });

  it("an invalid limits file is shown as rejecting every order", () => {
    const data: RiskStatus = { ...risk(false), limits: { valid: false, error: "no risk limits file" } };
    expect(render(<RiskView data={data} onEngaged={() => {}} />)).toContain("Every order is rejected: no risk limits file");
  });

  it("engage outcome lists orders that may still be working", () => {
    const html = renderToStaticMarkup(
      <EngageOutcome result={{ engaged: true, cancelled: { "01AAAAAAAAAAAAA1": "ACCEPTED" }, still_working: { "01AAAAAAAAAAAAA1": "ACCEPTED" }, error: null }} />,
    );
    expect(html).toContain("May still be working");
    expect(html).toContain('class="warning"');
  });

  it("portfolio and orders are read-only and labelled paper / simulated", () => {
    const pf: PaperPortfolio = {
      account: "paper", mode: "paper", fill_price_states: ["SIMULATED"], warning: "paper trading: simulated executions",
      starting_cash: 100000, cash: 98999, commissions: 1, realized_pnl: 0,
      positions: [{ symbol: "SPY", quantity: 10, average_price: 100.1, realized_pnl: 0, mark: null, market_value: null, unrealized_pnl: null }],
    };
    const p = render(<PortfolioView data={pf} />);
    expect(p).toContain("Not a performance claim");
    expect(p).toContain("SPY");
    const o = render(<OrdersView rows={[{ id: "01ORDERAAAAAAAA", client_order_key: "k", symbol: "SPY", side: "BUY", quantity: 10,
      order_type: "MARKET", limit_price: null, stop_price: null, time_in_force: "DAY", mode: "paper", source: "cli",
      state: "FILLED", created_at: "2026-11-09T15:00:00Z" }]} />);
    expect(o).toContain("FILLED");
    for (const html of [p, o]) expect(html).not.toMatch(/<(button|input|select|form)\b/);
  });

  it("order detail shows every risk check and marks simulated fill prices", () => {
    const d: PaperOrderDetail = {
      order_id: "01ORDER",
      events: [{ seq: 1, state: "NEW", detail: {}, at: "2026-11-09T15:00:00Z" }, { seq: 2, state: "FILLED", detail: { exec_id: "E-1" }, at: "2026-11-09T15:00:01Z" }],
      risk_decisions: [{ approved: true, checks: [{ check: "spread", ok: true, detail: "2.0 bps" }], limits_sha256: "cd".repeat(32), at: "2026-11-09T15:00:00Z" }],
      fills: [{ exec_id: "E-1", quantity: 10, price: 100.1, commission: 1, at: "2026-11-09T15:00:01Z", price_state: "SIMULATED" }],
    };
    const html = render(<OrderDetailView data={d} />);
    expect(html).toContain("spread");
    expect(html).toContain("SIMULATED · simulated");
    expect(html).not.toMatch(/<(button|input|select|form)\b/);
  });

  it("supervisor status warns when it is not running or found differences", () => {
    const base = { account: "paper", heartbeat_at: "2026-11-09T15:00:00Z", ticks: 3, last_reconcile_at: "2026-11-09T15:00:00Z",
      last_reconcile_clean: true, last_reconcile_error: null, last_equity: 1, kill_switch_engaged_by_supervisor: null, errors: [] };
    expect(renderToStaticMarkup(<SupervisorLine data={{ running: true, healthy: true, status: base }} />)).toContain('class="notice"');
    const down = renderToStaticMarkup(<SupervisorLine data={{ running: false, healthy: false, status: { ...base, last_reconcile_clean: false } }} />);
    expect(down).toContain("NOT running");
    expect(down).toContain("DIFFERENCES");
    expect(down).toContain('class="warning"');
    const failing = renderToStaticMarkup(<SupervisorLine data={{ running: true, healthy: false,
      status: { ...base, last_reconcile_clean: null, last_reconcile_error: "ConnectionError()" } }} />);
    expect(failing).toContain("FAILED");
    expect(failing).toContain('class="warning"'); // running but unhealthy is never green
    expect(renderToStaticMarkup(<SupervisorLine data={{ running: false, healthy: false, status: null }} />)).toContain("quantlab paper supervise");
    expect(renderToStaticMarkup(<SupervisorLine data={{ running: false, healthy: false, status: null, error: "bad json" }} />)).toContain("unavailable");
  });

  it("deployments are read-only and label non-live prices and lost processes", async () => {
    const { DeploymentsView } = await import("./pages/Deployments");
    const rows = [
      { deployment: "d1", run: "20261108T100000Z", strategy: "s", tree_hash: "ab".repeat(32), state: "running", running: true,
        data_states: ["SIMULATED"], started_at: "2026-11-08T10:00:00Z", heartbeat_at: "2026-11-08T10:05:00Z" },
      { deployment: "d2", run: "20261107T100000Z", state: "lost", running: false, data_states: ["LIVE"] },
      { deployment: "d3", run: "20261108T110000Z", state: "running", running: true, paused: true, pause_reason: "review" },
    ];
    const html = renderToStaticMarkup(<MemoryRouter><DeploymentsView rows={rows} onShowLog={() => {}} /></MemoryRouter>);
    expect(html).toContain("SIMULATED · not a performance claim");
    expect(html).toContain("no heartbeat — process gone");
    expect(html).toContain("paused · new orders refused");
    expect(html).toContain("quantlab paper pause");
    expect(html).toContain("quantlab paper deploy");
    expect(html).not.toMatch(/<(input|select|form)\b/); // only "Log" buttons: nothing that starts or stops anything
    expect((html.match(/<button/g) ?? []).length).toBe(3);
  });
});
