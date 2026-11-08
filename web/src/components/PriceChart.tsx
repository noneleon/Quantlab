import { useMemo } from "react";

import type { Bar } from "../api";
import { TimeSeriesChart } from "./TimeSeriesChart";

/** Close-price line chart of catalog bars. */
export function PriceChart({ bars }: { bars: Bar[] }) {
  const series = useMemo(
    () => [{ label: "close", color: "#2563eb", points: bars.map((b): [string, number] => [b.ts, b.close]) }],
    [bars],
  );
  return <TimeSeriesChart series={series} label="close price chart" />;
}
