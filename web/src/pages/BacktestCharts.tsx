import { useContext, useMemo, useState } from "react";

import { apiGet, apiGetText, type BacktestCharts, type Percentiles, type RobustnessResult } from "../api";
import type { ChartSeries } from "../components/TimeSeriesChart";
import { TimeSeriesChart } from "../components/TimeSeriesChart";
import { Empty, ErrorBox, Loading } from "../components/States";
import { dateTime, num, pct } from "../format";
import { TokenContext, useApi } from "../hooks";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function query(benchmark: string, symbol: string): string {
  const q = new URLSearchParams();
  if (benchmark.trim()) q.set("benchmark", benchmark.trim());
  if (symbol.trim()) q.set("benchmark_symbol", symbol.trim());
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Monthly returns grid (year x month) with the annual return; values come from the API unchanged. */
export function MonthlyReturns({ data }: { data: BacktestCharts["charts"] }) {
  const years = [...new Set(data.monthly_returns.map((m) => m.year))].sort();
  const cell = (y: number, m: number) => data.monthly_returns.find((r) => r.year === y && r.month === m)?.return;
  return (
    <table className="monthly">
      <thead><tr><th />{MONTHS.map((m) => <th key={m} className="num">{m}</th>)}<th className="num">Year</th></tr></thead>
      <tbody>
        {years.map((y) => (
          <tr key={y}>
            <th>{y}</th>
            {MONTHS.map((_, i) => {
              const v = cell(y, i + 1);
              return <td key={i} className={`num ${v === undefined ? "" : v > 0 ? "pos" : v < 0 ? "neg" : ""}`}>{v === undefined ? "" : pct(v, 1)}</td>;
            })}
            <th className="num">{pct(data.annual_returns.find((a) => a.year === y)?.return, 1)}</th>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function BacktestChartsSection({ runId }: { runId: string }) {
  const token = useContext(TokenContext);
  const [benchmark, setBenchmark] = useState("");
  const [symbol, setSymbol] = useState("");
  const [applied, setApplied] = useState("");
  const [downloadError, setDownloadError] = useState<Error | null>(null);
  const { data, error, loading } = useApi<BacktestCharts>(`/api/backtests/${encodeURIComponent(runId)}/charts${applied}`);

  const equity = useMemo<ChartSeries[]>(() => {
    if (!data) return [];
    const s: ChartSeries[] = [{ label: "strategy", color: "#2563eb", points: data.charts.equity }];
    if (data.charts.benchmark) s.push({ label: `${data.charts.benchmark.name} (rebased)`, color: "#9ca3af", points: data.charts.benchmark.equity });
    return s;
  }, [data]);
  const drawdown = useMemo<ChartSeries[]>(() => (data ? [{ label: "drawdown", color: "#dc2626", points: data.charts.drawdown }] : []), [data]);

  async function download() {
    setDownloadError(null);
    try {
      const html = await apiGetText(`/api/backtests/${encodeURIComponent(runId)}/report${applied}`, token);
      const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `backtest-${runId}.html`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setDownloadError(e as Error);
    }
  }

  if (error) return <><h2>Charts</h2><ErrorBox error={error} /></>;
  if (loading || !data) return <Loading />;
  const ts = data.charts.trade_stats;
  const b = data.charts.benchmark;
  return (
    <>
      {data.warnings.length > 0 ? (
        <div className="warning" role="alert"><strong>Not a performance claim:</strong><ul>{data.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>
      ) : null}
      <form className="inline-form" onSubmit={(e) => { e.preventDefault(); setApplied(query(benchmark, symbol)); }}>
        <label>Benchmark dataset <input value={benchmark} onChange={(e) => setBenchmark(e.target.value)} placeholder="e.g. spy-daily" /></label>
        <label>Symbol <input value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="optional" /></label>
        <button type="submit">Apply</button>
        <button type="button" onClick={download}>Download report</button>
      </form>
      {downloadError ? <ErrorBox error={downloadError} /> : null}
      <h2>Equity</h2>
      <TimeSeriesChart series={equity} label="equity chart" />
      <h2>Drawdown</h2>
      <TimeSeriesChart series={drawdown} label="drawdown chart" height={200} percent />
      {b ? (
        <>
          <h2>Benchmark — {b.name}</h2>
          <table className="narrow"><tbody>
            <tr><th>Common days</th><td className="num">{b.stats.common_days}</td></tr>
            <tr><th>Strategy / benchmark return</th><td className="num">{pct(b.stats.strategy_return)} / {pct(b.stats.benchmark_return)}</td></tr>
            <tr><th>Excess return</th><td className="num">{pct(b.stats.excess_return)}</td></tr>
            <tr><th>Beta</th><td className="num">{num(b.stats.beta)}</td></tr>
            <tr><th>Alpha (annual)</th><td className="num">{pct(b.stats.alpha_annual)}</td></tr>
            <tr><th>Correlation</th><td className="num">{num(b.stats.correlation)}</td></tr>
            <tr><th>Tracking error</th><td className="num">{pct(b.stats.tracking_error)}</td></tr>
            <tr><th>Information ratio</th><td className="num">{num(b.stats.information_ratio)}</td></tr>
          </tbody></table>
        </>
      ) : null}
      <h2>Monthly returns</h2>
      <MonthlyReturns data={data.charts} />
      <h2>Trades</h2>
      <p className="muted">{ts.basis}</p>
      <table className="narrow"><tbody>
        <tr><th>Round trips</th><td className="num">{ts.trades}</td></tr>
        <tr><th>Win rate</th><td className="num">{pct(ts.win_rate)}</td></tr>
        <tr><th>Profit factor</th><td className="num">{num(ts.profit_factor)}</td></tr>
        <tr><th>Expectancy</th><td className="num">{num(ts.expectancy)}</td></tr>
        <tr><th>Net P&amp;L (gross of fees)</th><td className="num">{num(ts.net_pnl, 2)}</td></tr>
      </tbody></table>
      <RobustnessPanel runId={runId} />
      <h2>Orders</h2>
      {data.orders.length === 0 ? <Empty>No orders.</Empty> : (
        <table>
          <thead><tr><th>Id</th><th>Time</th><th>Symbol</th><th>Type</th><th>Direction</th><th className="num">Quantity</th><th className="num">Price</th><th>Status</th><th>Tag</th></tr></thead>
          <tbody>
            {data.orders.map((o) => (
              <tr key={o.id}><td>{o.id}</td><td>{dateTime(o.time)}</td><td>{o.symbol}</td><td>{o.type}</td><td>{o.direction}</td>
                <td className="num">{num(o.quantity, 2)}</td><td className="num">{num(o.price, 2)}</td><td>{o.status}</td><td>{o.tag}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function row(label: string, p: Percentiles, fmt: (v: number) => string) {
  return <tr><th>{label}</th><td className="num">{fmt(p.p5)}</td><td className="num">{fmt(p.p25)}</td><td className="num">{fmt(p.p50)}</td><td className="num">{fmt(p.p75)}</td><td className="num">{fmt(p.p95)}</td></tr>;
}

/** OPT-004: bootstrap + trade Monte Carlo on demand (seeded; descriptive, not a forecast). */
export function RobustnessPanel({ runId }: { runId: string }) {
  const token = useContext(TokenContext);
  const [result, setResult] = useState<RobustnessResult | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await apiGet<RobustnessResult>(`/api/backtests/${encodeURIComponent(runId)}/robustness?samples=500&seed=0`, token));
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  const b = result?.bootstrap;
  const mc = result?.trade_monte_carlo;
  return (
    <>
      <h2>Robustness</h2>
      <p className="muted">Stationary bootstrap of daily returns and trade-order Monte Carlo (500 paths, seed 0). Describes this history; not a forecast.</p>
      <button type="button" className="action" onClick={() => void run()} disabled={busy}>{busy ? "Resampling…" : "Run bootstrap + Monte Carlo"}</button>
      {error ? <ErrorBox error={error} /> : null}
      {result ? (
        <>
          {b ? (
            <table className="narrow"><thead><tr><th>Bootstrap</th><th className="num">p5</th><th className="num">p25</th><th className="num">p50</th><th className="num">p75</th><th className="num">p95</th></tr></thead>
              <tbody>{row("Total return", b.total_return, (v) => pct(v))}{row("Max drawdown", b.max_drawdown, (v) => pct(v))}{row("Sharpe (annual)", b.sharpe_annual, (v) => num(v))}
                <tr><th>P(loss)</th><td className="num" colSpan={5}>{pct(b.probability_of_loss)}</td></tr></tbody></table>
          ) : <p className="muted">Bootstrap: {result.bootstrap_error}</p>}
          {mc ? (
            <table className="narrow"><thead><tr><th>Trade Monte Carlo ({mc.trades} trades)</th><th className="num">p5</th><th className="num">p25</th><th className="num">p50</th><th className="num">p75</th><th className="num">p95</th></tr></thead>
              <tbody>{row("Max drawdown (shuffled)", mc.max_drawdown_shuffled, (v) => pct(v))}{row("Net P&L (resampled)", mc.net_pnl_resampled, (v) => num(v, 2))}</tbody></table>
          ) : <p className="muted">Trade Monte Carlo: {result.trade_monte_carlo_error}</p>}
        </>
      ) : null}
    </>
  );
}
