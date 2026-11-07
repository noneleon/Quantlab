import { useContext, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { apiSend, type Strategy, type StrategyTemplate } from "../api";
import { Empty, ErrorBox, Loading } from "../components/States";
import { TokenContext, useApi } from "../hooks";

export function NewFromTemplate({ templates }: { templates: StrategyTemplate[] }) {
  const token = useContext(TokenContext);
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [template, setTemplate] = useState(templates[0]?.name ?? "");
  const [error, setError] = useState<Error | null>(null);
  if (templates.length === 0) return null;
  async function create() {
    setError(null);
    try {
      const s = await apiSend<Strategy>("POST", "/api/strategies", { name: name.trim(), template }, token);
      navigate(`/editor/${encodeURIComponent(s.name)}`);
    } catch (e) {
      setError(e as Error);
    }
  }
  return (
    <>
      <h2>New strategy from a template</h2>
      <form className="inline-form" onSubmit={(e) => { e.preventDefault(); void create(); }}>
        <label>Template
          <select value={template} onChange={(e) => setTemplate(e.target.value)}>
            {templates.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
          </select>
        </label>
        <label>Name <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. my-momentum" pattern="[a-z0-9][a-z0-9_\-]*" required /></label>
        <button type="submit">Create</button>
      </form>
      <p className="muted">{templates.find((t) => t.name === template)?.description}</p>
      {error ? <ErrorBox error={error} /> : null}
    </>
  );
}

export function StrategyList() {
  const { data, error, loading } = useApi<Strategy[]>("/api/strategies?include_archived=true");
  const templates = useApi<StrategyTemplate[]>("/api/strategy-templates");
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return (
    <section>
      <h1>Strategy Library</h1>
      <p className="muted">Register existing folders with <code>quantlab strategy add &lt;name&gt; &lt;path&gt;</code>, or create one from a template below. Edit in the <Link to="/editor">Algorithm Editor</Link>.</p>
      {data.length === 0 ? <Empty>No strategies registered.</Empty> : (
        <table>
          <thead><tr><th>Name</th><th>Language</th><th>Entry</th><th>Path</th><th>Description</th><th>State</th></tr></thead>
          <tbody>
            {data.map((s) => (
              <tr key={s.id}>
                <td>{s.archived ? s.name : <Link to={`/editor/${encodeURIComponent(s.name)}`}>{s.name}</Link>}</td>
                <td>{s.language}</td><td>{s.entry_file}</td><td><code>{s.path}</code></td>
                <td>{s.description}</td><td>{s.archived ? "archived (read-only)" : "active"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {templates.data ? <NewFromTemplate templates={templates.data} /> : null}
    </section>
  );
}
