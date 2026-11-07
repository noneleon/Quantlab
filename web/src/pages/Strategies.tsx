import type { Strategy } from "../api";
import { Empty, ErrorBox, Loading } from "../components/States";
import { useApi } from "../hooks";

export function StrategyList() {
  const { data, error, loading } = useApi<Strategy[]>("/api/strategies?include_archived=true");
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return (
    <section>
      <h1>Strategy Library</h1>
      <p className="muted">Read-only. Register with <code>quantlab strategy add &lt;name&gt; &lt;path&gt;</code>; editing arrives with the Algorithm Editor (Phase 3).</p>
      {data.length === 0 ? <Empty>No strategies registered.</Empty> : (
        <table>
          <thead><tr><th>Name</th><th>Language</th><th>Entry</th><th>Path</th><th>Description</th><th>State</th></tr></thead>
          <tbody>
            {data.map((s) => (
              <tr key={s.id}><td>{s.name}</td><td>{s.language}</td><td>{s.entry_file}</td><td><code>{s.path}</code></td>
                <td>{s.description}</td><td>{s.archived ? "archived" : "active"}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
