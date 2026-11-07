import { lazy, Suspense } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

import { AuthHelp, Loading } from "./components/States";
import { TokenContext } from "./hooks";
import { MODULES } from "./modules";
import { BacktestDetailPage, BacktestList } from "./pages/Backtests";
import { Dashboard } from "./pages/Dashboard";
import { DatasetDetailPage, DatasetList } from "./pages/Datasets";
import { ModulePage } from "./pages/ModulePage";
import { StrategyList } from "./pages/Strategies";

// The editor (CodeMirror) is loaded on demand so the rest of the workstation stays small.
const EditorIndex = lazy(() => import("./pages/Editor").then((m) => ({ default: m.EditorIndex })));
const EditorPage = lazy(() => import("./pages/Editor").then((m) => ({ default: m.EditorPage })));

export function Sidebar() {
  return (
    <nav className="sidebar" aria-label="modules">
      <div className="brand">QuantLab</div>
      <div className="mode" title="Live trading is not available">PAPER MODE</div>
      <ul>
        {MODULES.map((m) => (
          <li key={m.id}>
            <NavLink to={m.route} end={m.route === "/"} className={m.status === "available" ? "" : "planned"}>
              {m.title}
              {m.status === "planned" ? <span className="soon">planned</span> : null}
              {m.status === "external" ? <span className="soon">CLI</span> : null}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function App({ token }: { token: string | null }) {
  return (
    <TokenContext.Provider value={token}>
      <div className="layout">
        <Sidebar />
        <main>
          {token === null ? <AuthHelp /> : (
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/data" element={<DatasetList />} />
              <Route path="/data/:name" element={<DatasetDetailPage />} />
              <Route path="/backtests" element={<BacktestList />} />
              <Route path="/backtests/:id" element={<BacktestDetailPage />} />
              <Route path="/strategies" element={<StrategyList />} />
              <Route path="/editor" element={<Suspense fallback={<Loading />}><EditorIndex /></Suspense>} />
              <Route path="/editor/:name" element={<Suspense fallback={<Loading />}><EditorPage /></Suspense>} />
              <Route path="/modules/:id" element={<ModulePage />} />
              <Route path="*" element={<p className="muted">Page not found.</p>} />
            </Routes>
          )}
        </main>
      </div>
    </TokenContext.Provider>
  );
}
