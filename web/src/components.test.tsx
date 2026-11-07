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
      <MemoryRouter initialEntries={["/modules/risk"]}>
        <Routes><Route path="/modules/:id" element={<ModulePage />} /></Routes>
      </MemoryRouter>,
    );
    expect(html).toContain("Not available yet");
    expect(html).toContain("RISK-001");
    expect(html).not.toMatch(/<(button|input|select|form)\b/);
  });

  it("without a token the shell shows auth help and the paper-mode indicator", () => {
    const html = renderToStaticMarkup(<MemoryRouter><App token={null} /></MemoryRouter>);
    expect(html).toContain("Authentication required");
    expect(html).toContain("PAPER MODE");
    expect(html).toContain("Live Trading");
  });
});
