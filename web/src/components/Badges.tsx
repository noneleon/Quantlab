import { isRealProvenance } from "../api";

/** Provenance label; anything that is not real data is visibly marked "not a performance claim". */
export function ProvenanceBadge({ provenance }: { provenance: string }) {
  const real = isRealProvenance(provenance);
  return (
    <span className={`badge ${real ? "badge-real" : "badge-warn"}`} title={real ? "real market data" : "not real market data"}>
      {provenance}
      {real ? "" : " · not a performance claim"}
    </span>
  );
}

const STATUS_CLASS: Record<string, string> = {
  succeeded: "badge-ok",
  running: "badge-info",
  algorithm_error: "badge-bad",
  failed: "badge-bad",
  engine_error: "badge-bad",
  timed_out: "badge-bad",
  cancelled: "badge-muted",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${STATUS_CLASS[status] ?? "badge-muted"}`}>{status}</span>;
}
