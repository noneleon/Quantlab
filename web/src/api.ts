// QuantLab API client. The UI talks only to the local QuantLab API (same origin, DEC-0018).
//
// Token flow: `quantlab serve` prints http://127.0.0.1:<port>/#token=<token>. A URL fragment is never sent to the
// server, so the token does not reach access logs. On load the token is moved into sessionStorage (cleared when the
// tab closes) and removed from the address bar.

export const TOKEN_KEY = "quantlab.api-token";

export interface TokenStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** Extract `token` from a location hash like "#token=abc"; returns null if absent. */
export function parseTokenFromHash(hash: string): string | null {
  const params = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
  const token = params.get("token");
  return token && token.trim() ? token.trim() : null;
}

/** Move a token from the URL fragment into storage; returns the active token (fragment wins). */
export function captureToken(hash: string, storage: TokenStorage, clearHash: () => void): string | null {
  const fromHash = parseTokenFromHash(hash);
  if (fromHash) {
    storage.setItem(TOKEN_KEY, fromHash);
    clearHash();
    return fromHash;
  }
  return storage.getItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

export async function apiGet<T>(path: string, token: string | null, fetchImpl: Fetch = fetch): Promise<T> {
  if (!path.startsWith("/api/")) throw new Error(`refusing non-API path: ${path}`);
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetchImpl(path, { headers, credentials: "omit", cache: "no-store" });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

/** POST/PUT JSON to the API (strategy editor, UI-003). Same token handling and error mapping as apiGet. */
export async function apiSend<T>(
  method: "POST" | "PUT",
  path: string,
  body: unknown,
  token: string | null,
  fetchImpl: Fetch = fetch,
): Promise<T> {
  if (!path.startsWith("/api/")) throw new Error(`refusing non-API path: ${path}`);
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetchImpl(path, { method, headers, body: JSON.stringify(body), credentials: "omit", cache: "no-store" });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const parsed = (await response.json()) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

/** GET a non-JSON API resource (e.g. the HTML report) as text, with the same token handling. */
export async function apiGetText(path: string, token: string | null, fetchImpl: Fetch = fetch): Promise<string> {
  if (!path.startsWith("/api/")) throw new Error(`refusing non-API path: ${path}`);
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetchImpl(path, { headers, credentials: "omit", cache: "no-store" });
  if (!response.ok) throw new ApiError(response.status, response.statusText || `HTTP ${response.status}`);
  return response.text();
}

// ---- response types (mirror src/quantlab/api/app.py) ------------------------------------------------------

export interface Health {
  status: string;
  version: string;
}

export interface Strategy {
  id: string;
  name: string;
  path: string;
  entry_file: string;
  language: string;
  description: string;
  archived: boolean;
  /** Lifecycle stage (STR-004): research, backtested, validated, paper, live_candidate, retired. */
  stage: string;
}

export interface Dataset {
  name: string;
  asset_class: string;
  market: string;
  resolution: string;
  calendar: string;
  provenance: string;
  price_adjustment: string;
  source: string;
  license_terms: string;
  redistribution_allowed: boolean;
  versions: number;
  latest_version: string | null;
  latest_rows: number | null;
  latest_start: string | null;
  latest_end: string | null;
  latest_validation_ok: boolean | null;
}

export interface ValidationIssue {
  severity: "error" | "warning";
  check: string;
  symbol: string | null;
  count: number;
  message: string;
  examples: string[];
}

export interface DatasetVersion {
  version: string;
  created_at: string;
  rows: number;
  start: string;
  end: string;
  symbols: string[];
  conflict_policy: string;
  source: string;
  notes: string[];
  validation: { ok: boolean; issues: ValidationIssue[]; completeness: Record<string, number> };
}

export interface CorporateAction {
  symbol: string;
  ex_date: string;
  kind: "split" | "dividend";
  value: number;
}

export interface DatasetDetail extends Dataset {
  version_history: DatasetVersion[];
  corporate_actions: CorporateAction[];
}

export interface Bar {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

export interface BarsResponse {
  dataset: string;
  version: string;
  provenance: string;
  price_adjustment: string;
  symbol: string;
  truncated: boolean;
  bars: Bar[];
}

export interface BacktestSummary {
  id: string;
  strategy_id: string;
  status: string;
  created_at: string;
  finished_at: string | null;
  data_provenance: string;
  rerun_of: string | null;
  total_return: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
}

export interface CrossCheck {
  metric: string;
  engine_statistic: string;
  quantlab: number | null;
  engine: number | null;
  tolerance: number;
  status: "match" | "mismatch" | "unavailable";
}

export interface BacktestDetail extends BacktestSummary {
  parameters: Record<string, string>;
  engine_status: string | null;
  runtime_error: string | null;
  error: string | null;
  inputs_hash: string;
  result_hash: string | null;
  engine_statistics: Record<string, string> | null;
  metrics: Record<string, number | string | null> | null;
  cross_checks: CrossCheck[] | null;
  manifest: Record<string, unknown>;
}

export interface StrategyTemplate {
  name: string;
  description: string;
  parameters: Record<string, string>;
}

export interface StrategyFileInfo {
  path: string;
  size: number;
  sha256: string;
  editable: boolean;
}

export interface StrategyFileContent {
  path: string;
  content: string;
  sha256: string;
}

export interface SaveResult {
  path: string;
  sha256: string;
  created: boolean;
  syntax_error: string | null;
}

export type Point = [string, number | null];

export interface OptimizationSummaryRow {
  id: string;
  kind: "sweep" | "walk_forward";
  strategy: string;
  status: string;
  created_at: string;
  finished_at: string | null;
  objective: string | null;
}

export interface TrialView {
  seq: number;
  parameters: Record<string, string>;
  objective: number | null;
  run_id: string | null;
  metrics: Record<string, number | null>;
  objectives?: number[];
}

export interface OptimizationTrialRow {
  seq: number;
  window: number | null;
  segment: "ALL" | "IS" | "OOS";
  parameters: Record<string, string>;
  run_id: string | null;
  status: string;
  objective: number | null;
  metrics: Record<string, number | string | null>;
}

export interface WalkForwardWindow {
  window: number;
  is: [string, string];
  oos: [string, string];
  is_trials: number;
  status?: string;
  parameters?: Record<string, string>;
  is_objective?: number | null;
  oos_objective?: number | null;
  is_cagr?: number | null;
  oos_cagr?: number | null;
  is_run?: string | null;
  oos_run?: string | null;
}

export interface OptimizationDetail {
  id: string;
  kind: "sweep" | "walk_forward";
  strategy: string;
  status: string;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  spec: Record<string, unknown> & { space: Record<string, string[]>; objective: string };
  summary: {
    objective: string;
    warnings: string[];
    dataset_version?: string | null;
    // sweep
    trials?: number;
    method?: string;
    status_counts?: Record<string, number>;
    best?: TrialView | null;
    top?: TrialView[];
    pareto?: TrialView[] | null;
    overfitting?: {
      deflated_sharpe?: { probability: number; sharpe: number; sharpe_annual: number; expected_max_sharpe: number; trials: number; observations: number };
      pbo?: { pbo: number; splits: number; blocks: number; trials: number };
      deflated_sharpe_error?: string;
      pbo_error?: string;
    };
    sensitivity?: {
      parameters: string[];
      marginal: Record<string, { value: string; mean: number; min: number; max: number; trials: number }[]>;
      neighbourhood_ratio: number | null;
    };
    // walk-forward
    windows?: WalkForwardWindow[];
    oos_equity?: Point[];
    oos_metrics?: Record<string, number | null>;
    walk_forward_efficiency?: number | null;
    parameter_changes?: number;
    is_trials?: number;
    oos_runs?: number;
  } | null;
  trials: OptimizationTrialRow[];
}

export interface CompareResult {
  runs: { id: string; label: string; provenance: string | null; metrics: Record<string, number | string | null> }[];
  equity: { id: string; label: string; equity: Point[] }[];
  common_days: number;
  correlation: (number | null)[][] | null;
  conventions: string;
  warnings: string[];
}

export interface Percentiles {
  p5: number;
  p25: number;
  p50: number;
  p75: number;
  p95: number;
}

export interface RobustnessResult {
  run_id: string;
  warnings: string[];
  note: string;
  bootstrap?: { samples: number; mean_block: number; seed: number; total_return: Percentiles; sharpe_annual: Percentiles; max_drawdown: Percentiles; probability_of_loss: number };
  bootstrap_error?: string;
  trade_monte_carlo?: { samples: number; trades: number; max_drawdown_shuffled: Percentiles; net_pnl_resampled: Percentiles; probability_of_net_loss: number };
  trade_monte_carlo_error?: string;
}

export interface TradeStats {
  basis: string;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  average_win: number | null;
  average_loss: number | null;
  largest_win: number | null;
  largest_loss: number | null;
  average_holding_days: number | null;
  net_pnl: number;
}

export interface BenchmarkStats {
  name: string;
  common_days: number;
  strategy_return: number;
  benchmark_return: number;
  excess_return: number;
  beta: number | null;
  alpha_annual: number | null;
  correlation: number | null;
  tracking_error: number | null;
  information_ratio: number | null;
}

export interface EngineOrderOut {
  id: number;
  time: string | null;
  symbol: string;
  type: string;
  direction: string;
  quantity: number;
  price: number;
  status: string;
  tag: string;
}

/** GET /api/backtests/{id}/charts (src/quantlab/analytics/charts.py). */
export interface BacktestCharts {
  run_id: string;
  status: string;
  data_provenance: string;
  warnings: string[];
  orders: EngineOrderOut[];
  charts: {
    conventions: Record<string, string | number>;
    equity: Point[];
    drawdown: Point[];
    monthly_returns: { year: number; month: number; return: number }[];
    annual_returns: { year: number; return: number }[];
    rolling_sharpe: Point[];
    trades: Record<string, string | number>[];
    trade_stats: TradeStats;
    benchmark: { name: string; stats: BenchmarkStats; equity: Point[]; rebased_from: string } | null;
  };
}

/** Real data is only ever real_historical or live_recorded; everything else must be labelled. */
export function isRealProvenance(provenance: string): boolean {
  return provenance === "real_historical" || provenance === "live_recorded";
}

// ---- paper trading console (UI-006): read-only, except engaging the kill switch (DEC-0028) ----
export interface PaperOrderRow {
  id: string;
  client_order_key: string;
  symbol: string;
  side: string;
  quantity: number;
  order_type: string;
  limit_price: number | null;
  stop_price: number | null;
  time_in_force: string;
  mode: string;
  source: string;
  state: string;
  created_at: string;
}

export interface RiskCheck {
  check: string;
  ok: boolean;
  detail: string;
}

export interface PaperOrderDetail {
  order_id: string;
  events: { seq: number; state: string; detail: Record<string, unknown>; at: string }[];
  risk_decisions: { approved: boolean; checks: RiskCheck[]; limits_sha256: string | null; at: string }[];
  fills: { exec_id: string; quantity: number; price: number; commission: number; at: string; price_state: string }[];
}

export interface PositionRow {
  symbol: string;
  quantity: number;
  average_price: number;
  realized_pnl: number;
  mark: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
}

export interface PaperPortfolio {
  account: string;
  mode: string;
  fill_price_states: string[];
  warning: string | null;
  starting_cash: number;
  cash: number;
  commissions: number;
  realized_pnl: number;
  positions: PositionRow[];
}

export interface RiskStatus {
  kill_switch: { engaged: boolean; reason: string | null; at: string | null };
  limits: { valid: true; sha256: string; values: Record<string, unknown> } | { valid: false; error: string };
  decisions: { order_id: string; approved: boolean; failed: RiskCheck[]; at: string }[];
}

export interface EngageResult {
  engaged: boolean;
  cancelled: Record<string, string> | null;
  still_working: Record<string, string> | null;
  error: string | null;
}

export const ENGAGE_CONFIRMATION = "ENGAGE KILL SWITCH";

export interface SupervisorStatus {
  running: boolean;
  healthy: boolean;
  error?: string;
  heartbeat_age_s?: number | null;
  status: {
    account: string;
    heartbeat_at: string | null;
    ticks: number;
    last_reconcile_at: string | null;
    last_reconcile_clean: boolean | null;
    last_reconcile_error: string | null;
    last_equity: number | null;
    kill_switch_engaged_by_supervisor: string | null;
    errors: string[];
  } | null;
}
