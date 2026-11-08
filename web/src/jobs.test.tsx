import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { Job } from "./api";
import { BacktestForm, JobsTable, OptimizationForm, parsePairs } from "./pages/Jobs";

const job = (over: Partial<Job>): Job => ({
  id: "j1", kind: "backtest", title: "backtest sma", params: {}, state: "running", created_at: "2026-11-08T10:00:00Z",
  started_at: "2026-11-08T10:00:01Z", finished_at: null, result_id: null, error: null, cancel_requested: false, ...over,
});

describe("jobs (CORE-007)", () => {
  it("parses key=value pairs and rejects malformed ones", () => {
    expect(parsePairs("fast=10, slow=30\nlookback = 5")).toEqual({ fast: "10", slow: "30", lookback: "5" });
    expect(parsePairs("  ")).toEqual({});
    expect(() => parsePairs("fast")).toThrow(/key=value/);
  });

  it("lists jobs with links to results, errors and cancel only for active jobs", () => {
    const jobs = [
      job({ id: "a" }),
      job({ id: "b", state: "succeeded", result_id: "RUN1", finished_at: "2026-11-08T10:01:00Z" }),
      job({ id: "c", kind: "optimization", title: "sweep sma", state: "succeeded", result_id: "OPT1" }),
      job({ id: "d", state: "failed", error: "engine not configured" }),
      job({ id: "e", state: "running", cancel_requested: true }),
    ];
    const html = renderToStaticMarkup(<MemoryRouter><JobsTable jobs={jobs} onCancel={() => {}} /></MemoryRouter>);
    expect(html).toContain('href="/backtests/RUN1"');
    expect(html).toContain('href="/optimizations/OPT1"');
    expect(html).toContain("engine not configured");
    expect(html).toContain("running · cancelling");
    expect((html.match(/>Cancel</g) ?? []).length).toBe(1);
  });

  it("forms offer the registered strategies and research runs only", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <BacktestForm strategies={["sma", "rsi"]} onQueued={() => {}} />
        <OptimizationForm strategies={["sma"]} onQueued={() => {}} />
      </MemoryRouter>,
    );
    expect(html).toContain("<option>rsi</option>");
    expect(html).toContain("Run backtest");
    expect(html).toContain("Run sweep");
    expect(html).not.toMatch(/deploy|order|kill/i);
  });
});
