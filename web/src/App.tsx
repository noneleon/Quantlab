import { NavLink, Route, Routes } from "react-router-dom";

import { AuthHelp } from "./components/States";
import { TokenContext } from "./hooks";
import { MODULES } from "./modules";
import { BacktestDetailPage, BacktestList } from "./pages/Backtests";
import { Dashboard } from "./pages/Dashboard";
import { DatasetDetailPage, DatasetList } from "./pages/Datasets";
import { ModulePage } from "./pages/ModulePage";
import { StrategyList } from "./pages/Strategies";

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
              <Route path="/modules/:id" element={<ModulePage />} />
              <Route path="*" element={<p className="muted">Page not found.</p>} />
            </Routes>
          )}
        </main>
      </div>
    </TokenContext.Provider>
  );
}
