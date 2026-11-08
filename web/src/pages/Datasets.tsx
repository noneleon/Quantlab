import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import type { BarsResponse, Dataset, DatasetDetail } from "../api";
import { ProvenanceBadge } from "../components/Badges";
import { PriceChart } from "../components/PriceChart";
import { Empty, ErrorBox, Loading } from "../components/States";
import { dateTime, pct, shortHash } from "../format";
import { useApi } from "../hooks";

export function DatasetList() {
  const { data, error, loading } = useApi<Dataset[]>("/api/datasets");
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return (
    <section>
      <h1>Market Data</h1>
      <p className="muted">Read-only. Create and import with <code>quantlab data create</code> / <code>quantlab data import</code>.</p>
      {data.length === 0 ? <Empty>No datasets yet.</Empty> : (
        <table>
          <thead><tr><th>Dataset</th><th>Type</th><th>Provenance</th><th>Adjustment</th><th>Latest version</th>
            <th className="num">Rows</th><th>Range</th><th>Validation</th></tr></thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.name}>
                <td><Link to={`/data/${encodeURIComponent(d.name)}`}>{d.name}</Link></td>
                <td>{d.asset_class}/{d.market}/{d.resolution}</td>
                <td><ProvenanceBadge provenance={d.provenance} /></td>
                <td>{d.price_adjustment}</td>
                <td><code>{shortHash(d.latest_version)}</code></td>
                <td className="num">{d.latest_rows ?? 0}</td>
                <td>{d.latest_start ? `${d.latest_start.slice(0, 10)} → ${d.latest_end?.slice(0, 10)}` : "–"}</td>
                <td>{d.latest_validation_ok === null ? "–" : d.latest_validation_ok ? "ok" : "errors"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function DatasetDetailPage() {
  const name = useParams().name ?? "";
  const { data, error, loading } = useApi<DatasetDetail>(`/api/datasets/${encodeURIComponent(name)}`);
  const latest = data?.version_history[data.version_history.length - 1];
  const symbols = latest?.symbols ?? [];
  const [chosen, setChosen] = useState<string | null>(null);
  const symbol = chosen ?? symbols[0] ?? null;
  const bars = useApi<BarsResponse>(
    symbol ? `/api/datasets/${encodeURIComponent(name)}/bars?symbol=${encodeURIComponent(symbol)}` : null,
  );
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return (
    <section>
      <p><Link to="/data">← Market Data</Link></p>
      <h1>{data.name} <ProvenanceBadge provenance={data.provenance} /></h1>
      <dl className="facts">
        <dt>Type</dt><dd>{data.asset_class}/{data.market}/{data.resolution} · calendar {data.calendar}</dd>
        <dt>Adjustment</dt><dd>{data.price_adjustment}</dd>
        <dt>Source</dt><dd>{data.source}</dd>
        <dt>Licence</dt><dd>{data.license_terms}{data.redistribution_allowed ? "" : " (no redistribution)"}</dd>
      </dl>
      {latest ? (
        <>
          <h2>Prices</h2>
          {symbols.length > 1 ? (
            <label>Symbol{" "}
              <select value={symbol ?? ""} onChange={(e) => setChosen(e.target.value)}>
                {symbols.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
          ) : null}
          {bars.error ? <ErrorBox error={bars.error} /> : bars.loading || !bars.data ? <Loading /> : (
            <>
              <PriceChart bars={bars.data.bars} />
              <p className="muted">{symbol} close · version <code>{shortHash(bars.data.version)}</code> ·
                {" "}{bars.data.bars.length} bars{bars.data.truncated ? " (truncated to the most recent)" : ""}</p>
            </>
          )}
          <h2>Latest validation</h2>
          <p>Completeness: {Object.entries(latest.validation.completeness).map(([s, c]) => `${s} ${pct(c, 1)}`).join(", ") || "–"}</p>
          {latest.validation.issues.length === 0 ? <Empty>No issues.</Empty> : (
            <table>
              <thead><tr><th>Severity</th><th>Check</th><th>Symbol</th><th className="num">Count</th><th>Message</th><th>Examples</th></tr></thead>
              <tbody>
                {latest.validation.issues.map((i, n) => (
                  <tr key={n}><td>{i.severity}</td><td>{i.check}</td><td>{i.symbol ?? "*"}</td><td className="num">{i.count}</td>
                    <td>{i.message}</td><td className="muted">{i.examples.slice(0, 3).join(", ")}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      ) : <Empty>No versions yet.</Empty>}
      <h2>Corporate actions</h2>
      {data.corporate_actions.length === 0 ? (
        <Empty>None recorded{data.price_adjustment === "raw" ? " (prices are raw; splits/dividends unadjusted)" : ""}.</Empty>
      ) : (
        <table className="narrow">
          <thead><tr><th>Symbol</th><th>Ex-date</th><th>Kind</th><th className="num">Value</th></tr></thead>
          <tbody>
            {data.corporate_actions.map((a) => (
              <tr key={`${a.symbol}-${a.ex_date}-${a.kind}`}>
                <td>{a.symbol}</td><td>{a.ex_date}</td><td>{a.kind}</td>
                <td className="num">{a.kind === "split" ? `${a.value}:1` : a.value.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2>Versions</h2>
      <table>
        <thead><tr><th>Version</th><th>Created</th><th className="num">Rows</th><th>Range</th><th>Symbols</th><th>Conflicts</th><th>Source</th></tr></thead>
        <tbody>
          {[...data.version_history].reverse().map((v) => (
            <tr key={v.version}><td><code>{shortHash(v.version)}</code></td><td>{dateTime(v.created_at)}</td>
              <td className="num">{v.rows}</td><td>{v.start.slice(0, 10)} → {v.end.slice(0, 10)}</td>
              <td>{v.symbols.join(", ")}</td><td>{v.conflict_policy}</td><td>{v.source}</td></tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
