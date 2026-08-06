export type AccountType =
  | "depository"
  | "credit"
  | "loan"
  | "investment"
  | "other";

/** Sign convention, mirrored from the backend enum: positive is money in. */
export type TransactionDirection = "INFLOW" | "OUTFLOW";

export interface Account {
  id: string;
  name: string;
  mask: string | null;
  type: AccountType;
  subtype: string | null;
  current_balance_cents: number | null;
  available_balance_cents: number | null;
  credit_limit_cents: number | null;
  is_active: boolean;
  institution_name: string | null;
}

export interface AccountSummary {
  depository_cash_cents: number;
  credit_liabilities_cents: number;
  net_cents: number;
  accounts: Account[];
}

export interface Transaction {
  id: string;
  account_id: string;
  provider_txn_id: string;
  /** Positive = money in, negative = money out. Normalised at ingestion. */
  amount_cents: number;
  direction: "INFLOW" | "OUTFLOW";
  date: string;
  posted_date: string | null;
  description_raw: string;
  merchant_name: string | null;
  is_pending: boolean;
  is_transfer: boolean;
  is_recurring: boolean;
  notes: string | null;
  tags: string[];
  excluded_from_budget: boolean;
  created_at: string;
  account_name: string | null;
  account_mask: string | null;

  // Phase 2 — normalisation and categorisation.
  normalized_key: string | null;
  merchant_id: string | null;
  category_id: string | null;
  /** Which layer decided the category. USER outranks every automated layer. */
  category_source: CategorySource | null;
  /** Plaid's own detailed slug, kept as the input to categorisation layer 3. */
  provider_category: string | null;
  transfer_pair_id: string | null;
  category_name: string | null;
  category_slug: string | null;
  merchant_display_name: string | null;
}

export type CategorySource =
  | "USER"
  | "RULE"
  | "MERCHANT"
  | "PROVIDER"
  | "UNCATEGORIZED";

export type CategoryKind = "INCOME" | "EXPENSE" | "TRANSFER";

export interface Category {
  id: string;
  slug: string;
  name: string;
  kind: CategoryKind;
  parent_id: string | null;
  icon: string | null;
  color: string | null;
  sort_order: number;
  is_system: boolean;
}

export interface CategoryNode extends Category {
  children: Category[];
}

export interface CategoryTree {
  parents: CategoryNode[];
  total: number;
}

export type RuleMatchType =
  | "EXACT_MERCHANT"
  | "DESCRIPTION_CONTAINS"
  | "AMOUNT_EQUALS"
  | "AMOUNT_RANGE"
  | "ACCOUNT_ID";

export interface Rule {
  id: string;
  match_type: RuleMatchType;
  match_value: string | null;
  amount_min_cents: number | null;
  amount_max_cents: number | null;
  target_category_id: string;
  priority: number;
  applies_retroactively: boolean;
  is_active: boolean;
  created_at: string;
  target_category_name: string | null;
  target_category_slug: string | null;
}

export interface RuleCreate {
  match_type: RuleMatchType;
  match_value?: string | null;
  amount_min_cents?: number | null;
  amount_max_cents?: number | null;
  target_category_id: string;
  priority?: number;
  applies_retroactively?: boolean;
}

export interface RuleApplyResult {
  matched: number;
  changed: number;
  skipped_user_categorized: number;
}

export interface RuleCreateResult {
  rule: Rule;
  /** Null when the rule was created without retroactive application. */
  applied: RuleApplyResult | null;
}

export interface RecategorizeResult {
  changed: number;
  skipped_user_categorized: number;
}

export interface TransferDetectResult {
  scanned: number;
  pairs_found: number;
  transactions_marked: number;
}

/** Only the user-owned fields; ingestion never overwrites these. */
export interface TransactionUpdate {
  category_id?: string;
  notes?: string | null;
  tags?: string[];
  excluded_from_budget?: boolean;
  is_transfer?: boolean;
}

export type PacingStatus =
  | "NO_LIMIT"
  | "ON_TRACK"
  | "AT_RISK"
  | "OVER_PACING"
  | "OVER_BUDGET";

export interface BudgetLine {
  budget_id: string | null;
  category_id: string;
  category_name: string;
  category_slug: string;
  parent_id: string | null;

  limit_cents: number;
  rollover_enabled: boolean;
  rollover_cents: number;
  /** limit + rollover — what the user can actually spend this period. */
  available_cents: number;
  spent_cents: number;
  remaining_cents: number;
  /** Run-rate forecast for the end of the period. */
  projected_cents: number;
  pacing_ratio_pct: number;
  status: PacingStatus;
  elapsed_days: number;
  total_days: number;
}

export interface BudgetReport {
  period_start: string;
  period_end: string;
  period: string;
  elapsed_days: number;
  total_days: number;
  total_limit_cents: number;
  total_spent_cents: number;
  total_projected_cents: number;
  lines: BudgetLine[];
}

export interface BudgetSuggestion {
  category_id: string;
  category_name: string;
  category_slug: string;
  /** Oldest month first. */
  monthly_spend_cents: number[];
  median_cents: number;
  suggested_limit_cents: number;
  current_limit_cents: number | null;
}

export interface SuggestionsResponse {
  months_analyzed: number;
  suggestions: BudgetSuggestion[];
}

export interface ApplySuggestionsResult {
  created: number;
  updated: number;
  skipped: number;
}

export interface TransactionPage {
  items: Transaction[];
  total: number;
  limit: number;
  offset: number;
}

/** `null` on a field means "no constraint". */
export interface LedgerFilters {
  search: string;
  accountId: string | null;
  accountType: AccountType | null;
  startDate: string | null;
  endDate: string | null;
  pending: "all" | "pending" | "posted";
  categoryId: string | null;
  /** Restrict to the holding pen — the queue a user works through. */
  uncategorizedOnly: boolean;
}

export const EMPTY_FILTERS: LedgerFilters = {
  search: "",
  accountId: null,
  accountType: null,
  startDate: null,
  endDate: null,
  pending: "all",
  categoryId: null,
  uncategorizedOnly: false,
};

// --------------------------------------------------------------------------- //
// Phase 3 — recurring streams, subscriptions, cash flow forecast
// --------------------------------------------------------------------------- //

export type RecurrenceFrequency =
  | "WEEKLY"
  | "BIWEEKLY"
  | "MONTHLY"
  | "QUARTERLY"
  | "ANNUALLY";

export type StreamStatus = "ACTIVE" | "PAUSED" | "CANCELLED";

/** Whether the engine or the user last set a field. USER is never overwritten. */
export type StatusSource = "AUTO" | "USER";

export interface RecurringStream {
  id: string;
  merchant_id: string | null;
  category_id: string | null;
  category_name: string | null;
  normalized_key: string;
  display_name: string;
  frequency: RecurrenceFrequency;
  direction: TransactionDirection;
  /** The median baseline the forecast spends. Always positive. */
  expected_amount_cents: number;
  /** The most recent charge. Above the baseline by >5% means a price hike. */
  last_amount_cents: number;
  amount_variance_bps: number;
  first_date: string;
  last_date: string;
  next_expected_date: string;
  median_interval_days: number;
  occurrence_count: number;
  status: StreamStatus;
  status_source: StatusSource;
  is_subscription: boolean;
  is_subscription_locked: boolean;
  // Derived server-side so the two headline totals cannot disagree with the rows.
  monthly_cents: number;
  annual_cents: number;
  days_until_next: number;
}

export interface RecurringStreamUpdate {
  status?: StreamStatus;
  is_subscription?: boolean;
  category_id?: string | null;
  expected_amount_cents?: number;
}

export interface DetectionResult {
  scanned: number;
  groups_considered: number;
  streams_created: number;
  streams_updated: number;
  streams_unchanged: number;
  transactions_linked: number;
  stale_marked: number;
  warnings: string[];
}

export interface PriceChange {
  stream_id: string;
  display_name: string;
  baseline_cents: number;
  current_cents: number;
  delta_cents: number;
  delta_bps: number;
  /** What the rise costs over a year — the number worth ranking on. */
  annual_impact_cents: number;
  last_date: string;
}

export interface ForgottenStream {
  stream_id: string;
  display_name: string;
  expected_amount_cents: number;
  frequency: RecurrenceFrequency;
  last_date: string;
  next_expected_date: string;
  days_overdue: number;
  status: StreamStatus;
}

export interface SubscriptionMetrics {
  /** Every recurring outflow, rent included: what leaves no matter what. */
  recurring_monthly_cents: number;
  recurring_annual_cents: number;
  /** The cancellable subset — the half the user can actually act on. */
  subscription_monthly_cents: number;
  subscription_annual_cents: number;
  recurring_income_monthly_cents: number;
  active_subscription_count: number;
  active_recurring_count: number;
  paused_count: number;
  cancelled_count: number;
  price_hikes: PriceChange[];
  forgotten: ForgottenStream[];
}

export interface Renewal {
  stream_id: string;
  display_name: string;
  category_name: string | null;
  date: string;
  amount_cents: number;
  direction: TransactionDirection;
  frequency: RecurrenceFrequency;
  is_subscription: boolean;
}

export interface UpcomingRenewals {
  start_date: string;
  end_date: string;
  /** Outflows only — inflows would net this into meaninglessness. */
  total_cents: number;
  renewals: Renewal[];
}

export interface ForecastAccount {
  account_id: string;
  name: string;
  mask: string | null;
  balance_cents: number;
}

export interface ForecastEvent {
  date: string;
  stream_id: string;
  display_name: string;
  /** Signed, matching the ledger: positive is money in. */
  amount_cents: number;
  direction: TransactionDirection;
  frequency: RecurrenceFrequency;
  is_subscription: boolean;
}

export interface ForecastDay {
  date: string;
  opening_cents: number;
  inflow_cents: number;
  recurring_outflow_cents: number;
  variable_outflow_cents: number;
  closing_cents: number;
  below_threshold: boolean;
  events: ForecastEvent[];
}

export interface LowCashPoint {
  date: string;
  balance_cents: number;
  days_away: number;
  shortfall_cents: number;
}

export interface Forecast {
  start_date: string;
  end_date: string;
  horizon_days: number;
  starting_cash_cents: number;
  ending_cash_cents: number;
  month_end_cents: number;
  min_balance_cents: number;
  min_balance_date: string;
  threshold_cents: number;
  include_variable: boolean;
  total_inflow_cents: number;
  total_recurring_outflow_cents: number;
  total_variable_outflow_cents: number;
  accounts: ForecastAccount[];
  days: ForecastDay[];
  /** One entry per contiguous dip, at its worst day — not one per day. */
  low_points: LowCashPoint[];
}

export const FORECAST_HORIZONS = [30, 60, 90] as const;
export type ForecastHorizon = (typeof FORECAST_HORIZONS)[number];
