import { ColorType, LineSeries, createChart, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";

export interface ChartSeries {
  label: string;
  /** [time, value]: time is an ISO date ("2024-01-31") or an ISO timestamp. */
  points: [string, number | null][];
  color: string;
}

function toTime(t: string): Time {
  return (t.length === 10 ? t : Math.floor(Date.parse(t) / 1000)) as Time;
}

/**
 * Line chart for one or more series (TradingView Lightweight Charts, Apache-2.0).
 * The built-in attribution logo injects an inline <style> that the strict CSP blocks (DEC-0018), so the logo is
 * disabled and the required NOTICE attribution + link is rendered as text below every chart instead.
 */
export function TimeSeriesChart({ series, height = 320, label, percent = false }: {
  series: ChartSeries[];
  height?: number;
  label: string;
  percent?: boolean;
}) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
    const chart = createChart(container.current, {
      height,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: dark ? "#d4d4d8" : "#27272a",
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: dark ? "#27272a" : "#e4e4e7" } },
      timeScale: { timeVisible: true },
      localization: percent ? { priceFormatter: (v: number) => `${(v * 100).toFixed(1)}%` } : undefined,
    });
    for (const s of series) {
      const line = chart.addSeries(LineSeries, { color: s.color, lineWidth: 2, title: s.label });
      line.setData(
        s.points.filter((p): p is [string, number] => p[1] !== null).map(([t, v]) => ({ time: toTime(t), value: v })),
      );
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, height, percent]);
  return (
    <figure className="chart-figure">
      <div ref={container} className="chart" aria-label={label} />
      <figcaption className="muted attribution">
        Chart: TradingView Lightweight Charts™, Copyright (c) 2025 TradingView, Inc.{" "}
        <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">https://www.tradingview.com/</a>
      </figcaption>
    </figure>
  );
}
