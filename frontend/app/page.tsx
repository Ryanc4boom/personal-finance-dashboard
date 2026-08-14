"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AlertCircle, RefreshCw } from "lucide-react";
import CreateRuleModal from "@/components/CreateRuleModal";
import LedgerFiltersBar from "@/components/LedgerFilters";
import LinkAccountButton from "@/components/LinkAccountButton";
import SummaryCards from "@/components/SummaryCards";
import TransactionTable from "@/components/TransactionTable";
import {
  detectTransfers,
  getAccountSummary,
  getCategories,
  getTransactions,
  recategorizeAll,
  updateTransaction,
} from "@/lib/api";
import {
  EMPTY_FILTERS,
  type AccountSummary,
  type AccountType,
  type CategoryNode,
  type LedgerFilters,
  type Transaction,
} from "@/lib/types";

/**
 * The independent things on this page that can fail. Each owns its own slot, so
 * one recovering never silently clears another's still-valid message.
 * "action" covers user-initiated writes — recategorizing, re-running the
 * pipeline, linking an account — which are mutually exclusive in practice.
 */
type ErrorSource = "summary" | "transactions" | "action";

// Fixed display order so a message cannot jump position as others come and go.
const ERROR_SOURCES: ErrorSource[] = ["summary", "transactions", "action"];

export default function LedgerPage() {
  return (
    <Suspense fallback={null}>
      <Ledger />
    </Suspense>
  );
}

function Ledger() {
  // The budget dashboard drills in by pushing query params; honour them on
  // first render so a category row lands on exactly its own transactions.
  const searchParams = useSearchParams();

  const [summary, setSummary] = useState<AccountSummary | null>(null);
  const [categories, setCategories] = useState<CategoryNode[]>([]);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  // Keyed by source rather than a single string. The panes load independently,
  // so a failed account-summary request and a successful transaction fetch race
  // on mount — sharing one slot let whichever resolved last erase the other's
  // message, usually within a few hundred milliseconds of it appearing.
  const [errors, setErrors] = useState<Partial<Record<ErrorSource, string>>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState<Set<string>>(new Set());
  const [ruleTarget, setRuleTarget] = useState<Transaction | null>(null);
  const [busy, setBusy] = useState(false);

  const [filters, setFilters] = useState<LedgerFilters>(() => ({
    ...EMPTY_FILTERS,
    categoryId: searchParams.get("category_id"),
    startDate: searchParams.get("start_date"),
    endDate: searchParams.get("end_date"),
  }));
  // Kept separate from `filters.search` so typing stays responsive while the
  // request that actually hits the API is debounced.
  const [searchInput, setSearchInput] = useState("");

  useEffect(() => {
    const timer = setTimeout(
      () => setFilters((f) => (f.search === searchInput ? f : { ...f, search: searchInput })),
      300,
    );
    return () => clearTimeout(timer);
  }, [searchInput]);

  const reportError = useCallback((source: ErrorSource, message: string | null) => {
    setErrors((prev) => {
      if (message === null) {
        if (prev[source] === undefined) return prev;
        const next = { ...prev };
        delete next[source];
        return next;
      }
      return prev[source] === message ? prev : { ...prev, [source]: message };
    });
  }, []);

  const loadSummary = useCallback(async () => {
    try {
      setSummary(await getAccountSummary());
      reportError("summary", null);
    } catch (err) {
      reportError("summary", err instanceof Error ? err.message : "Could not load accounts");
    }
  }, [reportError]);

  useEffect(() => {
    loadSummary();
    getCategories()
      .then((tree) => setCategories(tree.parents))
      .catch(() => setCategories([]));
  }, [loadSummary]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    getTransactions(filters)
      .then((page) => {
        if (cancelled) return;
        setTransactions(page.items);
        setTotal(page.total);
        reportError("transactions", null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        reportError(
          "transactions",
          err instanceof Error ? err.message : "Could not load transactions",
        );
        setTransactions([]);
        setTotal(0);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    // Filters can change faster than requests resolve; ignore stale responses so
    // the table always reflects the filters currently on screen.
    return () => {
      cancelled = true;
    };
  }, [filters, reportError]);

  function patchFilters(patch: Partial<LedgerFilters>) {
    setFilters((f) => ({ ...f, ...patch }));
  }

  /** Traceability: a balance card drills straight into its own rows. */
  function selectAccount(accountId: string) {
    setFilters((f) => ({
      ...f,
      accountId: f.accountId === accountId ? null : accountId,
      accountType: null,
    }));
  }

  function selectType(type: AccountType) {
    setFilters((f) => ({
      ...f,
      accountType: f.accountType === type && !f.accountId ? null : type,
      accountId: null,
    }));
  }

  function reset() {
    setSearchInput("");
    setFilters(EMPTY_FILTERS);
  }

  function refresh() {
    loadSummary();
    setFilters((f) => ({ ...f }));
  }

  /**
   * Recategorize one row. The response is patched in place rather than
   * refetching the page: a refetch under a "needs a category" filter would make
   * the row the user just fixed vanish mid-click, which reads as data loss.
   */
  async function recategorize(txn: Transaction, categoryId: string) {
    setSaving((s) => new Set(s).add(txn.id));
    try {
      const updated = await updateTransaction(txn.id, { category_id: categoryId });
      setTransactions((rows) => rows.map((r) => (r.id === txn.id ? updated : r)));
      reportError("action", null);
    } catch (err) {
      reportError("action", err instanceof Error ? err.message : "Could not update the category");
    } finally {
      setSaving((s) => {
        const next = new Set(s);
        next.delete(txn.id);
        return next;
      });
    }
  }

  async function runMaintenance() {
    setBusy(true);
    setNotice(null);
    reportError("action", null);
    try {
      const transfers = await detectTransfers();
      const recat = await recategorizeAll();
      setNotice(
        `${transfers.pairs_found} transfer pair(s) matched · ${recat.changed} transaction(s) recategorized` +
          (recat.skipped_user_categorized > 0
            ? ` · ${recat.skipped_user_categorized} of your own picks kept`
            : ""),
      );
      refresh();
    } catch (err) {
      reportError("action", err instanceof Error ? err.message : "Could not re-run the pipeline");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Transaction Ledger</h1>
          <p className="text-sm text-slate-500">
            Every balance below is clickable and filters the table to its own transactions.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={runMaintenance}
            disabled={busy}
            title="Re-pair transfers and re-run the categorization rules"
            className="flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />
            Re-run rules
          </button>
          <LinkAccountButton
            onLinked={refresh}
            onError={(message) => reportError("action", message)}
          />
        </div>
      </header>

      {ERROR_SOURCES.some((source) => errors[source]) && (
        <div className="space-y-3">
          {ERROR_SOURCES.filter((source) => errors[source]).map((source) => (
            <div
              key={source}
              className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{errors[source]}</span>
            </div>
          ))}
        </div>
      )}

      {notice && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
          {notice}
        </div>
      )}

      <SummaryCards
        summary={summary}
        activeAccountId={filters.accountId}
        activeAccountType={filters.accountType}
        onSelectAccount={selectAccount}
        onSelectType={selectType}
      />

      <LedgerFiltersBar
        filters={filters}
        accounts={summary?.accounts ?? []}
        categories={categories}
        searchInput={searchInput}
        onSearchInput={setSearchInput}
        onChange={patchFilters}
        onReset={reset}
      />

      <TransactionTable
        transactions={transactions}
        categories={categories}
        loading={loading}
        total={total}
        saving={saving}
        onRecategorize={recategorize}
        onCreateRule={setRuleTarget}
      />

      {ruleTarget && (
        <CreateRuleModal
          transaction={ruleTarget}
          categories={categories}
          onClose={() => setRuleTarget(null)}
          onApplied={refresh}
        />
      )}
    </main>
  );
}
