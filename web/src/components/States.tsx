import { ApiError } from "../api";

export function Loading() {
  return <p className="muted">Loading…</p>;
}

/** Error display; 401/503 explain how to authenticate instead of showing a raw error. */
export function ErrorBox({ error }: { error: Error }) {
  if (error instanceof ApiError && (error.status === 401 || error.status === 503)) {
    return <AuthHelp detail={error.message} />;
  }
  return (
    <div className="error" role="alert">
      <strong>Request failed.</strong> {error.message}
    </div>
  );
}

export function AuthHelp({ detail }: { detail?: string }) {
  return (
    <div className="notice" role="alert">
      <h2>Authentication required</h2>
      <p>
        Open the link printed by <code>quantlab serve</code> (it contains <code>#token=…</code>), or show the token with{" "}
        <code>quantlab api token --show</code> and open <code>/#token=&lt;token&gt;</code>.
      </p>
      {detail ? <p className="muted">Server said: {detail}</p> : null}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="muted empty">{children}</p>;
}
