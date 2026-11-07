import { ColorType, LineSeries, createChart, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { Bar } from "../api";

/**
 * Close-price line chart (TradingView Lightweight Charts, Apache-2.0).
 * The built-in attribution logo injects an inline <style> that the strict CSP blocks (DEC-0018), so the logo is
 * disabled and the required NOTICE attribution + link is rendered as text below every chart instead.
 */
export function PriceChart({ bars }: { bars: Bar[] }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
    const chart = createChart(container.current, {
      height: 320,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: dark ? "#d4d4d8" : "#27272a",
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: dark ? "#27272a" : "#e4e4e7" } },
      timeScale: { timeVisible: true },
    });
    const series = chart.addSeries(LineSeries, { color: "#2563eb", lineWidth: 2 });
    series.setData(bars.map((b) => ({ time: (Date.parse(b.ts) / 1000) as UTCTimestamp, value: b.close })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars]);
  return (
    <figure className="chart-figure">
      <div ref={container} className="chart" aria-label="close price chart" />
      <figcaption className="muted attribution">
        Chart: TradingView Lightweight Charts™, Copyright (c) 2025 TradingView, Inc.{" "}
        <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">https://www.tradingview.com/</a>
      </figcaption>
    </figure>
  );
}
