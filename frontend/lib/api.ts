import type {
  AccountBreakdownRow,
  AccountSummary,
  Allocation,
  ApplySuggestionsResult,
  BackfillResult,
  BudgetReport,
  CategoryTree,
  DetectionResult,
  Forecast,
  ForecastHorizon,
  Goal,
  GoalCreate,
  GoalUpdate,
  GoalsReport,
  LedgerFilters,
  NetWorthHistory,
  NetWorthRange,
  Portfolio,
  RecategorizeResult,
  RecurringStream,
  RecurringStreamUpdate,
  ResearchReport,
  Rule,
  RuleApplyResult,
  RuleCreate,
  RuleCreateResult,
  StreamStatus,
  SubscriptionMetrics,
  SuggestionsResponse,
  TickerSearchResult,
  Transaction,
  TransactionDirection,
  TransactionPage,
  TransactionUpdate,
  TransferDetectResult,
  UpcomingRenewals,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Non-JSON error body — fall back to the status text.
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }

  // DELETEs answer 204; calling .json() on an empty body throws.
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function getAccountSummary(): Promise<AccountSummary> {
  return request<AccountSummary>("/api/v1/accounts");
}

export function getTransactions(
  filters: LedgerFilters,
  limit = 200,
  offset = 0,
): Promise<TransactionPage> {
  const params = new URLSearchParams();
  if (filters.search.trim()) params.set("search", filters.search.trim());
  if (filters.accountId) params.set("account_id", filters.accountId);
  if (filters.accountType) params.set("account_type", filters.accountType);
  if (filters.startDate) params.set("start_date", filters.startDate);
  if (filters.endDate) params.set("end_date", filters.endDate);
  if (filters.pending !== "all") {
    params.set("is_pending", String(filters.pending === "pending"));
  }
  if (filters.categoryId) params.set("category_id", filters.categoryId);
  if (filters.uncategorizedOnly) params.set("uncategorized", "true");
  params.set("limit", String(limit));
  params.set("offset", String(offset));

  return request<TransactionPage>(`/api/v1/transactions?${params}`);
}

export function createLinkToken(): Promise<{ link_token: string }> {
  return request("/api/v1/plaid/link-token", { method: "POST" });
}

export function setAccessToken(publicToken: string): Promise<unknown> {
  return request("/api/v1/plaid/set-access-token", {
    method: "POST",
    body: JSON.stringify({ public_token: publicToken }),
  });
}

export function syncTransactions(): Promise<{ results: unknown[] }> {
  return request("/api/v1/plaid/sync", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function updateTransaction(
  id: string,
  patch: TransactionUpdate,
): Promise<Transaction> {
  return request<Transaction>(`/api/v1/transactions/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function getCategories(): Promise<CategoryTree> {
  return request<CategoryTree>("/api/v1/categories");
}

export function getRules(): Promise<Rule[]> {
  return request<Rule[]>("/api/v1/rules");
}

export function createRule(payload: RuleCreate): Promise<RuleCreateResult> {
  return request<RuleCreateResult>("/api/v1/rules", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteRule(id: string): Promise<void> {
  return request<void>(`/api/v1/rules/${id}`, { method: "DELETE" });
}

export function applyRule(id: string, force = false): Promise<RuleApplyResult> {
  return request<RuleApplyResult>(
    `/api/v1/rules/${id}/apply?force=${force}`,
    { method: "POST" },
  );
}

export function recategorizeAll(
  options: { onlyUncategorized?: boolean; force?: boolean } = {},
): Promise<RecategorizeResult> {
  const params = new URLSearchParams({
    only_uncategorized: String(options.onlyUncategorized ?? false),
    force: String(options.force ?? false),
  });
  return request<RecategorizeResult>(`/api/v1/rules/recategorize?${params}`, {
    method: "POST",
  });
}

export function detectTransfers(): Promise<TransferDetectResult> {
  return request<TransferDetectResult>("/api/v1/transfers/detect", {
    method: "POST",
  });
}

/** `period` is the 'YYYY-MM' the period selector holds. */
export function getBudgets(period: string): Promise<BudgetReport> {
  return request<BudgetReport>(`/api/v1/budgets?period=${period}`);
}

export function upsertBudget(payload: {
  category_id: string;
  limit_cents: number;
  rollover_enabled?: boolean;
  is_active?: boolean;
}): Promise<unknown> {
  return request("/api/v1/budgets", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteBudget(categoryId: string): Promise<void> {
  return request<void>(`/api/v1/budgets/${categoryId}`, { method: "DELETE" });
}

export function getSuggestions(): Promise<SuggestionsResponse> {
  return request<SuggestionsResponse>("/api/v1/budgets/suggestions");
}

export function applySuggestions(
  overwrite = false,
): Promise<ApplySuggestionsResult> {
  return request<ApplySuggestionsResult>(
    `/api/v1/budgets/suggestions/apply?overwrite=${overwrite}`,
    { method: "POST" },
  );
}

// --------------------------------------------------------------------------- //
// Phase 3 — recurring streams, subscriptions, cash flow forecast
// --------------------------------------------------------------------------- //

export function getRecurringStreams(filters?: {
  status?: StreamStatus;
  isSubscription?: boolean;
  direction?: TransactionDirection;
}): Promise<RecurringStream[]> {
  const params = new URLSearchParams();
  if (filters?.status) params.set("status", filters.status);
  if (filters?.isSubscription !== undefined) {
    params.set("is_subscription", String(filters.isSubscription));
  }
  if (filters?.direction) params.set("direction", filters.direction);
  const query = params.toString();
  return request<RecurringStream[]>(
    `/api/v1/recurring${query ? `?${query}` : ""}`,
  );
}

/** Re-fits every stream from history. Idempotent; safe after every sync. */
export function detectRecurring(lookbackDays?: number): Promise<DetectionResult> {
  const query =
    lookbackDays === undefined ? "" : `?lookback_days=${lookbackDays}`;
  return request<DetectionResult>(`/api/v1/recurring/detect${query}`, {
    method: "POST",
  });
}

export function getSubscriptionMetrics(): Promise<SubscriptionMetrics> {
  return request<SubscriptionMetrics>("/api/v1/recurring/metrics");
}

export function getUpcomingRenewals(days = 30): Promise<UpcomingRenewals> {
  return request<UpcomingRenewals>(`/api/v1/recurring/upcoming?days=${days}`);
}

export function updateRecurringStream(
  id: string,
  patch: RecurringStreamUpdate,
): Promise<RecurringStream> {
  return request<RecurringStream>(`/api/v1/recurring/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function deleteRecurringStream(id: string): Promise<void> {
  return request<void>(`/api/v1/recurring/${id}`, { method: "DELETE" });
}

export function getForecast(options?: {
  horizonDays?: ForecastHorizon;
  thresholdCents?: number;
  includeVariable?: boolean;
}): Promise<Forecast> {
  const params = new URLSearchParams();
  params.set("horizon_days", String(options?.horizonDays ?? 30));
  if (options?.thresholdCents !== undefined) {
    params.set("threshold_cents", String(options.thresholdCents));
  }
  if (options?.includeVariable !== undefined) {
    params.set("include_variable", String(options.includeVariable));
  }
  return request<Forecast>(`/api/v1/forecast?${params}`);
}

// --------------------------------------------------------------------------- //
// Phase 4 — investments, net worth, goals
// --------------------------------------------------------------------------- //

export function getPortfolio(asOf?: string): Promise<Portfolio> {
  const query = asOf ? `?as_of=${asOf}` : "";
  return request<Portfolio>(`/api/v1/investments/holdings${query}`);
}

export function getAllocation(asOf?: string): Promise<Allocation> {
  const query = asOf ? `?as_of=${asOf}` : "";
  return request<Allocation>(`/api/v1/investments/allocation${query}`);
}

export function getNetWorthHistory(
  range: NetWorthRange = "1Y",
): Promise<NetWorthHistory> {
  return request<NetWorthHistory>(`/api/v1/net-worth/history?range=${range}`);
}

export function getNetWorthAccounts(): Promise<AccountBreakdownRow[]> {
  return request<AccountBreakdownRow[]>("/api/v1/net-worth/accounts");
}

/**
 * Recompute and persist historical snapshots. Explicit on purpose — reading the
 * chart never triggers it, so the write cost is always something the user asked
 * for rather than a side effect of a page load.
 */
export function backfillNetWorth(): Promise<BackfillResult> {
  return request<BackfillResult>("/api/v1/net-worth/backfill", {
    method: "POST",
  });
}

export function getGoals(includeArchived = false): Promise<GoalsReport> {
  return request<GoalsReport>(
    `/api/v1/goals?include_archived=${includeArchived}`,
  );
}

export function createGoal(payload: GoalCreate): Promise<Goal> {
  return request<Goal>("/api/v1/goals", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateGoal(id: string, patch: GoalUpdate): Promise<Goal> {
  return request<Goal>(`/api/v1/goals/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function deleteGoal(id: string): Promise<void> {
  return request<void>(`/api/v1/goals/${id}`, { method: "DELETE" });
}

// --------------------------------------------------------------------------- //
// Phase 5 — SEC research engine
// --------------------------------------------------------------------------- //

export function searchTickers(
  query: string,
  limit = 8,
): Promise<TickerSearchResult> {
  return request<TickerSearchResult>(
    `/api/v1/research/search?q=${encodeURIComponent(query)}&limit=${limit}`,
  );
}

/**
 * The full four-stage scorecard. Slow on a cold cache — it downloads a 10-K and
 * a proxy from EDGAR under SEC's rate limit — so callers should show a loading
 * state rather than assume this returns in a frame.
 *
 * `priceCents` overrides the resolved share price, which is what makes the
 * valuation stage answerable with no market-data key configured.
 */
export function getResearchReport(
  ticker: string,
  priceCents?: number,
): Promise<ResearchReport> {
  const query = priceCents ? `?price_cents=${priceCents}` : "";
  return request<ResearchReport>(
    `/api/v1/research/${encodeURIComponent(ticker)}${query}`,
  );
}
