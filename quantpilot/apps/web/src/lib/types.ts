/**
 * Hand-curated aliases over the backend Pydantic schemas.
 *
 * The full machine-generated OpenAPI types live in `openapi.d.ts`
 * (regenerate with `npm run generate:api`). These aliases name the
 * subset of shapes the UI renders, so pages stay readable.
 */
import type { components } from "./openapi";

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
  preferred_symbols: string[];
  preferred_themes: string[];
  preferred_sectors: string[];
  blocklist: string[];
  kill_switch_engaged: boolean;
  created_at: string;
}

export type OperatorReport = components["schemas"]["OperatorReport"];
export type OperatorRunRequest = components["schemas"]["OperatorRunRequest"];
export type OperatorRunResult = components["schemas"]["OperatorRunResult"];

export interface PolicyPreviewResponse {
  confirmed: boolean;
  policy: UserPolicy;
  policy_json: Record<string, unknown>;
}

export interface ParsePolicyRequest {
  text: string;
  user_id: string;
}

export interface IntentRunRequest {
  text: string;
  user_id?: string;
  create_order_proposals?: boolean;
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
  symbol_match: boolean;
  sector_match: boolean;
  theme_match: boolean;
  focus_match: boolean;
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
  focus?: IntentFocusSummary;
  missing_preferred_symbols?: string[];
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
  focus?: IntentFocusSummary;
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

export interface IntentFocusSummary {
  preferred_symbols: string[];
  preferred_sectors: string[];
  preferred_themes: string[];
  missing_preferred_symbols: string[];
}

export interface IntentRunResponse {
  intent: {
    text: string;
    user_id: string;
    data_mode: string;
  };
  capability_assessment: Record<string, boolean>;
  policy: UserPolicy;
  focus: IntentFocusSummary;
  diagnostics: {
    missing_preferred_symbols: string[];
    data_mode: string;
    [key: string]: unknown;
  };
  candidate_analysis: {
    universe: CandidateUniverseItem[];
    analyst_reports: AnalystReport[];
  };
  signals: Signal[];
  target_weights: Record<string, number>;
  rebalance: Level12RunResponse["rebalance"];
  portfolio_plan: Record<string, unknown>;
  order_proposals: Record<string, unknown>[];
  operation_report: OperationReport;
  safety: {
    order_submission_enabled: boolean;
    broker_order_count: number;
    fill_count: number;
    risk_checked_order_plan_ids: string[];
    live_trading_enabled: boolean;
    broker: string;
    execution_mode: string;
    data_mode: string;
  };
}
