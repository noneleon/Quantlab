// Workstation modules (master prompt §11) and their real availability (UI-008).
// Rule: a module is "available" only if a page backed by a real API exists. Everything else is listed honestly as
// planned, with the roadmap phase and feature IDs — never as a control that does nothing.

export type ModuleStatus = "available" | "external" | "planned";

export interface WorkstationModule {
  id: string;
  title: string;
  status: ModuleStatus;
  route: string;
  phase: string;
  features: string[];
  summary: string;
}

export const MODULES: WorkstationModule[] = [
  { id: "dashboard", title: "Dashboard", status: "available", route: "/", phase: "2", features: ["UI-001"],
    summary: "Platform health, datasets and recent backtests." },
  { id: "research", title: "Research", status: "external", route: "/modules/research", phase: "2",
    features: ["RES-001", "RES-002"],
    summary: "JupyterLab on 127.0.0.1 via `quantlab research` (optional extra); notebooks use quantlab.research." },
  { id: "editor", title: "Algorithm Editor", status: "available", route: "/editor", phase: "3",
    features: ["UI-003"], summary: "Edit strategy files in the browser (CodeMirror); saves are conflict-checked and audited." },
  { id: "strategies", title: "Strategy Library", status: "available", route: "/strategies", phase: "2",
    features: ["STR-001", "STR-005", "UI-003"], summary: "Registered strategies; create new ones from templates." },
  { id: "backtests", title: "Backtesting", status: "available", route: "/backtests", phase: "2",
    features: ["BT-003", "UI-002"], summary: "Backtest history with metrics, cross-checks and manifests (read-only)." },
  { id: "optimization", title: "Optimization", status: "available", route: "/optimizations", phase: "4",
    features: ["OPT-001", "OPT-003", "OPT-005", "UI-005"],
    summary: "Sweeps and walk-forward studies with overfitting statistics (start them with `quantlab optimize`)." },
  { id: "portfolio", title: "Portfolio", status: "planned", route: "/modules/portfolio", phase: "5",
    features: ["PORT-001", "PORT-002", "UI-006"], summary: "Broker-confirmed positions, P&L and exposure." },
  { id: "live", title: "Live Trading", status: "planned", route: "/modules/live", phase: "6",
    features: ["LIVE-003", "UI-006"], summary: "Disabled until all live safety gates pass and the owner activates it." },
  { id: "paper", title: "Paper Trading", status: "planned", route: "/modules/paper", phase: "5",
    features: ["LIVE-002", "BRK-002", "UI-006"], summary: "Paper deployments through the OMS and risk gate." },
  { id: "orders", title: "Order Management", status: "planned", route: "/modules/orders", phase: "5",
    features: ["OMS-002", "UI-006"], summary: "Order state machine, fills and reconciliation." },
  { id: "data", title: "Market Data", status: "available", route: "/data", phase: "2",
    features: ["DATA-003", "DATA-005", "UI-004"], summary: "Versioned datasets, validation and price charts (read-only)." },
  { id: "risk", title: "Risk Management", status: "planned", route: "/modules/risk", phase: "5",
    features: ["RISK-001", "RISK-004", "UI-006"], summary: "Central risk limits, decision log and kill switch." },
  { id: "analytics", title: "Performance Analytics", status: "planned", route: "/modules/analytics", phase: "3",
    features: ["AN-002", "AN-003"], summary: "Charts and downloadable reports (metrics today: Backtesting detail)." },
  { id: "ml", title: "Machine Learning", status: "planned", route: "/modules/ml", phase: "7",
    features: ["ML-001", "ML-004", "UI-007"], summary: "Features, time-series CV and model registry." },
  { id: "ai", title: "AI Assistant", status: "planned", route: "/modules/ai", phase: "8",
    features: ["AI-001", "AI-002"], summary: "Permissioned, audited assistants (no trading capabilities)." },
  { id: "monitoring", title: "Logs and Monitoring", status: "planned", route: "/modules/monitoring", phase: "5",
    features: ["OPS-001", "OPS-002"], summary: "Engine, broker and data feed status; alerts." },
  { id: "settings", title: "Settings", status: "planned", route: "/modules/settings", phase: "9",
    features: ["UI-007"], summary: "Configuration UI (today: QUANTLAB_HOME/quantlab.toml)." },
  { id: "projects", title: "Project Management", status: "planned", route: "/modules/projects", phase: "7",
    features: ["UI-007"], summary: "Workspaces and strategy lifecycle overview." },
];

export function moduleById(id: string): WorkstationModule | undefined {
  return MODULES.find((m) => m.id === id);
}
