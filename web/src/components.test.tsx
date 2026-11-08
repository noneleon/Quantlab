import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { ApiError } from "./api";
import { ProvenanceBadge } from "./components/Badges";
import { ErrorBox } from "./components/States";
import { ModulePage } from "./pages/ModulePage";

describe("components", () => {
  it("price chart renders the required TradingView attribution and link", async () => {
    const { PriceChart } = await import("./components/PriceChart");
    const html = renderToStaticMarkup(<PriceChart bars={[]} />);
    expect(html).toContain("TradingView Lightweight Charts");
    expect(html).toContain('href="https://www.tradingview.com/"');
  });

  it("labels non-real provenance as not a performance claim", () => {
    expect(renderToStaticMarkup(<ProvenanceBadge provenance="synthetic" />)).toContain("not a performance claim");
    expect(renderToStaticMarkup(<ProvenanceBadge provenance="real_historical" />)).not.toContain("not a performance");
  });

  it("explains authentication on 401 instead of a raw error", () => {
    const html = renderToStaticMarkup(<ErrorBox error={new ApiError(401, "invalid or missing token")} />);
    expect(html).toContain("Authentication required");
    expect(html).toContain("quantlab serve");
  });

  it("planned module pages contain no controls", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/modules/ml"]}>
        <Routes><Route path="/modules/:id" element={<ModulePage />} /></Routes>
      </MemoryRouter>,
    );
    expect(html).toContain("Not available yet");
    expect(html).toContain("ML-001");
    expect(html).not.toMatch(/<(button|input|select|form)\b/);
  });

  it("without a token the shell shows auth help and the paper-mode indicator", () => {
    const html = renderToStaticMarkup(<MemoryRouter><App token={null} /></MemoryRouter>);
    expect(html).toContain("Authentication required");
    expect(html).toContain("PAPER MODE");
    expect(html).toContain("Live Trading");
  });

  it("monthly returns grid shows each month and the annual return, coloured by sign", async () => {
    const { MonthlyReturns } = await import("./pages/BacktestCharts");
    const charts = {
      conventions: {}, equity: [], drawdown: [], rolling_sharpe: [], trades: [], benchmark: null,
      trade_stats: { basis: "", trades: 0, wins: 0, losses: 0, win_rate: null, profit_factor: null, expectancy: null,
        average_win: null, average_loss: null, largest_win: null, largest_loss: null, average_holding_days: null, net_pnl: 0 },
      monthly_returns: [{ year: 2024, month: 1, return: 0.1 }, { year: 2024, month: 2, return: -0.1 }],
      annual_returns: [{ year: 2024, return: -0.01 }],
    };
    const html = renderToStaticMarkup(<MonthlyReturns data={charts} />);
    expect(html).toContain("10.0");
    expect(html).toContain('class="num pos"');
    expect(html).toContain('class="num neg"');
    expect(html).toContain("-1.0");
  });

  it("new-from-template form lists templates and shows the selected description", async () => {
    const { NewFromTemplate } = await import("./pages/Strategies");
    const html = renderToStaticMarkup(
      <MemoryRouter><NewFromTemplate templates={[{ name: "pairs_zscore", description: "Template: pairs", parameters: {} }]} /></MemoryRouter>,
    );
    expect(html).toContain("pairs_zscore");
    expect(html).toContain("Template: pairs");
    expect(html).toContain("<form");
  });

  it("objective heatmap prints every value and leaves missing cells empty", async () => {
    const { Heatmap } = await import("./pages/Optimizations");
    const t = (fast: string, slow: string, objective: number | null, status = "ok") =>
      ({ seq: 0, window: null, segment: "ALL" as const, parameters: { fast, slow }, run_id: null, status, objective, metrics: {} });
    const html = renderToStaticMarkup(<Heatmap trials={[t("5", "50", 1.5), t("10", "50", 0.5), t("5", "100", null, "run_failed")]} x="fast" y="slow" />);
    expect(html).toContain("1.500");
    expect(html).toContain("0.500");
    expect(html).toContain("background-color:rgba(37, 99, 235, 0.870)");
    expect(html).toContain("–");
  });
});

