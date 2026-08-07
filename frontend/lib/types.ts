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

// --------------------------------------------------------------------------- //
// Phase 4 — investments, net worth, goals
// --------------------------------------------------------------------------- //

export const ASSET_CLASSES = [
  "US_EQUITY",
  "INTERNATIONAL_EQUITY",
  "FIXED_INCOME",
  "CRYPTO",
  "REAL_ESTATE",
  "CASH",
] as const;
export type AssetClass = (typeof ASSET_CLASSES)[number];

export type SecurityType =
  | "EQUITY"
  | "ETF"
  | "MUTUAL_FUND"
  | "CRYPTO"
  | "FIXED_INCOME"
  | "CASH_EQUIVALENT";

export interface PositionAccount {
  account_id: string;
  account_name: string;
  account_mask: string | null;
  account_subtype: string | null;
  /** Decimal string, never a number — see the note on Position.quantity. */
  quantity: string;
  value_cents: number;
  cost_basis_cents: number | null;
  as_of_date: string;
}

export interface Position {
  security_id: string;
  ticker_symbol: string | null;
  name: string;
  type: SecurityType;
  asset_class: AssetClass;
  is_cash_equivalent: boolean;
  /**
   * A decimal string. Share counts carry up to eight places and a JSON number
   * is a double, so parsing this into one would silently round crypto and
   * fractional-share positions.
   */
  quantity: string;
  price_cents: number | null;
  price_as_of: string | null;
  value_cents: number;
  /** Null means the custodian never reported a basis — not zero. */
  cost_basis_cents: number | null;
  gain_cents: number | null;
  gain_bps: number | null;
  day_change_cents: number | null;
  weight_bps: number;
  accounts: PositionAccount[];
}

export interface AllocationSlice {
  asset_class: AssetClass;
  value_cents: number;
  /** Apportioned so the slices sum to exactly 10000. */
  weight_bps: number;
  position_count: number;
}

export interface AccountAllocation {
  account_id: string;
  name: string;
  mask: string | null;
  subtype: string | null;
  value_cents: number;
  cost_basis_cents: number | null;
  gain_cents: number | null;
  gain_bps: number | null;
  day_change_cents: number | null;
  weight_bps: number;
  position_count: number;
  as_of_date: string | null;
  by_asset_class: AllocationSlice[];
}

export interface PortfolioSummary {
  as_of_date: string | null;
  total_value_cents: number;
  total_cost_basis_cents: number;
  /** Market value of only the positions that reported a basis. */
  cost_basis_value_cents: number;
  unrealized_gain_cents: number;
  unrealized_gain_bps: number | null;
  cost_basis_coverage_bps: number;
  day_change_cents: number;
  day_change_bps: number | null;
  day_change_missing_count: number;
  cash_value_cents: number;
  invested_value_cents: number;
  position_count: number;
  account_count: number;
}

export interface Portfolio {
  summary: PortfolioSummary;
  positions: Position[];
  by_asset_class: AllocationSlice[];
  by_account: AccountAllocation[];
}

export interface Allocation {
  as_of_date: string | null;
  total_value_cents: number;
  by_asset_class: AllocationSlice[];
  by_account: AccountAllocation[];
}

export const NET_WORTH_RANGES = ["1M", "3M", "6M", "1Y", "ALL"] as const;
export type NetWorthRange = (typeof NET_WORTH_RANGES)[number];

export interface NetWorthBreakdown {
  cash_cents?: number;
  investments_cents?: number;
  other_assets_cents?: number;
  credit_cents?: number;
  loans_cents?: number;
}

export interface NetWorthPoint {
  date: string;
  total_assets_cents: number;
  /** Positive magnitude; net worth is assets minus this. */
  total_liabilities_cents: number;
  net_worth_cents: number;
  source: "ACTUAL" | "RECONSTRUCTED";
  breakdown: NetWorthBreakdown;
}

export interface NetWorthChange {
  start_cents: number;
  end_cents: number;
  delta_cents: number;
  /** Withheld when the starting net worth was zero or negative. */
  delta_bps: number | null;
}

export interface AccountBreakdownRow {
  account_id: string;
  name: string;
  mask: string | null;
  type: string;
  subtype: string | null;
  bucket: "cash" | "investments" | "credit" | "loans" | "other";
  /** Signed: assets positive, liabilities negative. */
  balance_cents: number;
  is_liability: boolean;
}

export interface NetWorthHistory {
  range_key: NetWorthRange;
  start_date: string | null;
  end_date: string | null;
  current_assets_cents: number;
  current_liabilities_cents: number;
  current_net_worth_cents: number;
  change: NetWorthChange | null;
  points: NetWorthPoint[];
  accounts: AccountBreakdownRow[];
  breakdown: NetWorthBreakdown;
}

export interface BackfillResult {
  start_date: string | null;
  end_date: string | null;
  days_computed: number;
  snapshots_created: number;
  snapshots_updated: number;
  accounts_considered: number;
  warnings: string[];
}

export const GOAL_CATEGORIES = [
  "EMERGENCY_FUND",
  "HOUSE_DOWN_PAYMENT",
  "FIRE",
  "CAR_PURCHASE",
  "CUSTOM",
] as const;
export type GoalCategory = (typeof GOAL_CATEGORIES)[number];

export type GoalStatus =
  | "ON_TRACK"
  | "AT_RISK"
  | "OFF_TRACK"
  | "ACHIEVED"
  | "NO_DEADLINE";

export interface GoalAccountRef {
  account_id: string;
  name: string;
  mask: string | null;
  type: string;
  subtype: string | null;
  balance_cents: number;
  is_liability: boolean;
}

export interface Goal {
  id: string;
  name: string;
  category: GoalCategory;
  target_amount_cents: number;
  current_amount_cents: number;
  remaining_cents: number;
  /** Clamped to 0..10000 for the bar. */
  progress_bps: number;
  /** Unclamped, for the label. */
  raw_progress_bps: number;
  target_date: string | null;
  days_remaining: number | null;
  months_remaining: number | null;
  required_monthly_cents: number | null;
  observed_monthly_cents: number | null;
  /** False when the rate came from the stated plan, not measured movement. */
  observed_is_measured: boolean;
  projected_completion_date: string | null;
  projected_vs_target_days: number | null;
  status: GoalStatus;
  is_achieved: boolean;
  is_archived: boolean;
  monthly_contribution_cents: number | null;
  notes: string | null;
  linked_account_ids: string[];
  linked_accounts: GoalAccountRef[];
}

export interface GoalsSummary {
  goal_count: number;
  achieved_count: number;
  off_track_count: number;
  total_target_cents: number;
  total_current_cents: number;
  total_remaining_cents: number;
  total_progress_bps: number;
  total_required_monthly_cents: number;
}

export interface GoalsReport {
  summary: GoalsSummary;
  goals: Goal[];
}

export interface GoalCreate {
  name: string;
  category: GoalCategory;
  target_amount_cents: number;
  current_amount_cents?: number;
  target_date?: string | null;
  monthly_contribution_cents?: number | null;
  notes?: string | null;
  linked_account_ids?: string[];
}

/** Every field optional; `linked_account_ids` replaces the whole set. */
export type GoalUpdate = Partial<GoalCreate> & { is_archived?: boolean };
