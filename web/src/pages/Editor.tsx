import { useContext, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, apiGet, apiSend, type SaveResult, type Strategy, type StrategyFileContent, type StrategyFileInfo } from "../api";
import { CodeEditor } from "../components/CodeEditor";
import { Empty, ErrorBox, Loading } from "../components/States";
import { TokenContext, useApi } from "../hooks";

/** /editor: pick a strategy to edit. */
export function EditorIndex() {
  const { data, error, loading } = useApi<Strategy[]>("/api/strategies");
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;
  return (
    <section>
      <h1>Algorithm Editor</h1>
      <p className="muted">Edits change the strategy's files. Recorded backtests are unaffected: each run executes a snapshot taken when it started.</p>
      {data.length === 0 ? <Empty>No strategies. Create one from a template in the <Link to="/strategies">Strategy Library</Link>.</Empty> : (
        <ul>{data.map((s) => <li key={s.id}><Link to={`/editor/${encodeURIComponent(s.name)}`}>{s.name}</Link> <span className="muted">{s.description}</span></li>)}</ul>
      )}
    </section>
  );
}

interface OpenFile {
  path: string;
  sha256: string | null; // null: new, not yet saved
  saved: string;
  text: string;
  key: string;
}

/** /editor/:name — file list + editor with conflict-checked saves. */
export function EditorPage() {
  const name = useParams().name ?? "";
  const token = useContext(TokenContext);
  const base = `/api/strategies/${encodeURIComponent(name)}/files`;
  const [files, setFiles] = useState<StrategyFileInfo[] | null>(null);
  const [open, setOpen] = useState<OpenFile | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [newPath, setNewPath] = useState("");
  const [saving, setSaving] = useState(false);

  async function refreshFiles() {
    setFiles(await apiGet<StrategyFileInfo[]>(base, token));
  }

  async function load(path: string) {
    setError(null);
    setNotice(null);
    try {
      const f = await apiGet<StrategyFileContent>(`${base}/${path.split("/").map(encodeURIComponent).join("/")}`, token);
      setOpen({ path: f.path, sha256: f.sha256, saved: f.content, text: f.content, key: `${f.path}@${f.sha256}` });
    } catch (e) {
      setError(e as Error);
    }
  }

  useEffect(() => {
    refreshFiles()
      .then(() => load("main.py"))
      .catch((e: Error) => setError(e));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, token]);

  const dirty = open !== null && open.text !== open.saved;

  async function save() {
    if (!open || saving) return;
    setSaving(true);
    setError(null);
    try {
      const r = await apiSend<SaveResult>("PUT", `${base}/${open.path.split("/").map(encodeURIComponent).join("/")}`,
        { content: open.text, expected_sha256: open.sha256 }, token);
      setOpen({ ...open, sha256: r.sha256, saved: open.text });
      setNotice(r.syntax_error ? `Saved, but Python reports a syntax error (${r.syntax_error}).` : "Saved.");
      await refreshFiles();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setNotice(`Not saved: ${e.message}. Copy your changes, then reload the file.`);
      } else {
        setError(e as Error);
      }
    } finally {
      setSaving(false);
    }
  }

  function startNewFile() {
    const path = newPath.trim();
    if (!path) return;
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    setOpen({ path, sha256: null, saved: "", text: "", key: `new:${path}` });
    setNewPath("");
    setNotice("New file — not saved yet.");
  }

  if (!files && !error) return <Loading />;
  return (
    <section>
      <p><Link to="/editor">← Algorithm Editor</Link></p>
      <h1>{name}</h1>
      {error ? <ErrorBox error={error} /> : null}
      <div className="editor-layout">
        <nav className="file-list" aria-label="files">
          <ul>
            {(files ?? []).map((f) => (
              <li key={f.path}>
                {f.editable ? (
                  <button type="button" className={open?.path === f.path ? "active" : ""}
                    onClick={() => (!dirty || window.confirm("Discard unsaved changes?")) && load(f.path)}>{f.path}</button>
                ) : <span className="muted" title="not editable">{f.path}</span>}
              </li>
            ))}
          </ul>
          <form onSubmit={(e) => { e.preventDefault(); startNewFile(); }}>
            <input aria-label="new file path" placeholder="new file, e.g. alpha.py" value={newPath} onChange={(e) => setNewPath(e.target.value)} />
            <button type="submit">Add</button>
          </form>
        </nav>
        <div className="editor-main">
          {open ? (
            <>
              <div className="editor-toolbar">
                <code>{open.path}</code>
                <span className={dirty ? "badge-warn" : "muted"}>{dirty ? "unsaved changes" : "saved"}</span>
                <button type="button" onClick={save} disabled={!dirty || saving}>{saving ? "Saving…" : "Save (Ctrl+S)"}</button>
                <button type="button" onClick={() => load(open.path)} disabled={open.sha256 === null}>Reload</button>
              </div>
              {notice ? <p className="notice" role="status">{notice}</p> : null}
              <CodeEditor docKey={open.key} value={open.text} path={open.path}
                onChange={(text) => setOpen((o) => (o ? { ...o, text } : o))} onSave={save} />
            </>
          ) : <Empty>Select a file.</Empty>}
        </div>
      </div>
    </section>
  );
}
