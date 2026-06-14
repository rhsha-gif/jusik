/**
 * Hand-curated aliases over the backend Pydantic schemas.
 *
 * The full machine-generated OpenAPI types live in `openapi.d.ts`
 * (regenerate with `npm run generate:api`). These aliases name the
 * subset of shapes the UI renders, so pages stay readable.
 */

export interface HealthResponse {
  status: string;
  live_trading_enabled: boolean;
  default_broker: string;
  data_mode: string;
  data_mode_safe: boolean;
}

export interface SmokeOrder {
  order_plan_id: string;
  status: string;
}

export interface SmokeResult {
  policy_id: string;
  broker: string;
  execution_mode: string;
  signals: number;
  portfolio_plan_id: string;
  orders: SmokeOrder[];
  fills: number;
  audit_events: number;
  report_id: string;
  live_trading_enabled: boolean;
}

export interface UserPolicy {
  policy_id: string;
  user_id: string;
  version: number;
  market: string;
  risk_profile: string;
  max_positions: number;
  max_position_weight: number;
  max_sector_weight: number;
  min_cash_weight: number;
  daily_loss_limit: number;
  monthly_loss_limit: number;
  max_daily_orders: number;
  max_daily_turnover: number;
  single_order_cash_limit: number;
  rebalance_frequency: string;
  execution_mode: string;
  allowed_order_types: string[];
  broker: string;
  preferred_themes: string[];
  preferred_sectors: string[];
  blocklist: string[];
  kill_switch_engaged: boolean;
  created_at: string;
}

export interface PolicyPreviewResponse {
  confirmed: boolean;
  policy: UserPolicy;
  policy_json: Record<string, unknown>;
}

export interface ParsePolicyRequest {
  text: string;
  user_id: string;
}

export interface Level12Request {
  policy_id?: string | null;
  text?: string;
  user_id?: string;
}

export type SignalActionValue =
  | "buy_ready"
  | "buy_wait"
  | "hold"
  | "trim"
  | "exit"
  | "watch"
  | "blocked";

export interface Signal {
  signal_id: string;
  strategy_id: string;
  recipe_version: string;
  symbol: string;
  ticker: string | null;
  signal_date: string;
  action: SignalActionValue;
  strength: number;
  technical_score: number | null;
  quant_score: number | null;
  target_weight_hint: number | null;
  stop_price_hint: number | null;
  take_profit_hint: number | null;
  reason_codes: string[];
  reason: string;
  generated_at: string;
  source: string;
}

export interface SignalBoardResponse {
  policy_id: string;
  signals: Signal[];
}

export interface CandidateUniverseItem {
  ticker: string;
  name: string;
  market: string;
  sector: string;
  theme_match: boolean;
  liquidity_pass: boolean;
  data_ready: boolean;
  block_reason: string | null;
  analyst_required: boolean;
}

export interface UniverseResponse {
  policy_id: string;
  candidates: CandidateUniverseItem[];
}

export interface AnalystReport {
  ticker: string;
  rating: "positive" | "neutral" | "caution" | "blocked";
  confidence: number;
  summary: string;
  investment_thesis: string[];
  catalysts: string[];
  financial_snapshot: Record<string, unknown>;
  valuation_view: string;
  technical_view: string;
  operation_view: string;
  watch_conditions: string[];
  data_as_of: string;
}

export interface AnalystResponse {
  policy_id: string;
  analyst_reports: AnalystReport[];
}

export interface RebalanceSuggestion {
  ticker: string;
  current_weight: number;
  target_weight_suggestion: number;
  cash_target: number;
  risk_reason: string;
  suggested_action: "buy" | "sell" | "hold" | "blocked";
}

export interface OperationReportSummary {
  level?: string;
  candidate_count?: number;
  analyst_report_count?: number;
  signal_count?: number;
  rebalance_suggestion_count?: number;
  supported_actions?: string[];
  order_submission_enabled?: boolean;
  broker?: string;
  execution_mode?: string;
  [key: string]: unknown;
}

export interface OperationReport {
  report_id: string;
  user_id: string;
  policy_id: string;
  summary: OperationReportSummary;
  order_plan_ids: string[];
  fill_ids: string[];
  audit_event_count: number;
  live_trading_enabled: boolean;
  created_at: string;
}

export interface Level12RunResponse {
  policy: UserPolicy;
  strategy: Record<string, unknown>;
  universe: CandidateUniverseItem[];
  analyst_reports: AnalystReport[];
  signals: Signal[];
  rebalance: {
    policy_id: string;
    policy_version: number;
    portfolio_plan: Record<string, unknown>;
    suggestions: RebalanceSuggestion[];
    order_submission_enabled: boolean;
    created_at: string;
  };
  daily_report: OperationReport;
  order_submission_enabled: boolean;
}

/* ---------------------------------------------------------------------------
 * Level 5 Operator (fully-automated operator harness).
 *
 * Level 5 is NOT live trading. Runs default to `dry_run`; `mock_submit` and
 * `paper_submit` only ever touch the mock/paper broker. These aliases mirror
 * quantpilot/packages/core/operator/schemas.py verbatim so the operator page
 * and its JSON viewers stay faithful to the backend contract.
 * ------------------------------------------------------------------------- */

export type OperatorRunMode = "dry_run" | "mock_submit" | "paper_submit";

export type OperatorRunStatus = "completed" | "blocked" | "fallback" | "failed";

export type OperatorDecisionAction = "submit" | "block" | "fallback" | "noop";

export interface OperatorRunRequest {
  user_id: string;
  policy_id: string;
  requested_policy_version: number;
  run_mode: OperatorRunMode;
  requested_at: string;
  idempotency_key: string;
}

export interface FallbackDecision {
  from_level: 5;
  /** 4=guarded autopilot, 3=proposals, 2=suggestions, 0=no-op. */
  to_level: 4 | 3 | 2 | 0;
  reason_code: string;
  detail: string;
  order_submission_enabled: boolean;
}

export interface StrategySelectionDecision {
  selected_strategy_id: string | null;
  selected_version: string | null;
  eligible_strategy_ids: string[];
  /** Maps strategy_id -> rejection reason. */
  rejected: Record<string, string>;
  reason: string;
}

export interface OperatorDecision {
  decision_id: string;
  run_id: string;
  policy_id: string;
  policy_version: number;
  strategy_id: string | null;
  order_plan_id: string | null;
  action: OperatorDecisionAction;
  reason: string;
  risk_check_id: string | null;
  created_at: string;
}

export interface OperatorReport {
  report_id: string;
  run_id: string;
  user_id: string;
  policy_id: string;
  policy_version: number;
  started_at: string;
  completed_at: string;
  status: OperatorRunStatus;
  strategy_selection: StrategySelectionDecision;
  decisions: OperatorDecision[];
  fallback: FallbackDecision | null;
  order_plan_ids: string[];
  broker_order_ids: string[];
  risk_check_ids: string[];
  safety_flags: Record<string, boolean | string>;
  live_trading_enabled: boolean;
  audit_event_count: number;
}

export interface OperatorRunResult {
  run_id: string;
  status: OperatorRunStatus;
  submitted_order_plan_ids: string[];
  blocked_order_plan_ids: string[];
  fallback: FallbackDecision | null;
  report: OperatorReport;
}

/** Registry entry as returned inline by GET /api/operator/status. */
export interface OperatorRegistryEntry {
  strategy_id: string;
  version: string;
  status: string;
  allowed_execution_levels: string[];
  disabled_reason: string | null;
}

/** GET /api/operator/status returns dict[str, object] on the backend. */
export interface OperatorStatusResponse {
  live_trading_enabled: boolean;
  feature_flags: Record<string, boolean | string>;
  registry: OperatorRegistryEntry[];
  runs: number;
}

/** GET /api/operator/reports/latest returns dict[str, object]. */
export interface OperatorReportEnvelope {
  report: OperatorReport | null;
  text: string;
}
