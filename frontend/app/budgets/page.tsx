"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, PiggyBank, Plus, Sparkles } from "lucide-react";
import BudgetRow from "@/components/BudgetRow";
import StatCard from "@/components/StatCard";
import SuggestionsModal from "@/components/SuggestionsModal";
import {
  deleteBudget,
  getBudgets,
  getCategories,
  upsertBudget,
} from "@/lib/api";
import { formatCents } from "@/lib/format";
import type { BudgetReport, CategoryNode } from "@/lib/types";

/** 'YYYY-MM' for a month offset back from today. */
function monthKey(offset: number): string {
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth() - offset, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(key: string): string {
  const [year, month] = key.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
  });
}

const PERIODS = Array.from({ length: 12 }, (_, i) => monthKey(i));

export default function BudgetsPage() {
  const router = useRouter();
  const [period, setPeriod] = useState(PERIODS[0]);
  const [report, setReport] = useState<BudgetReport | null>(null);
  const [parents, setParents] = useState<CategoryNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setReport(await getBudgets(period));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load budgets");
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    getCategories()
      .then((tree) => setParents(tree.parents))
      .catch(() => setParents([]));
  }, []);

  // Only top-level spending categories get budgets. Income and transfers are
  // not spending, and a per-child budget would double-count against its parent.
  const budgetable = useMemo(() => {
    const taken = new Set(report?.lines.map((l) => l.category_id) ?? []);
    return parents.filter((p) => p.kind === "EXPENSE" && !taken.has(p.id));
  }, [parents, report]);

  async function saveLimit(categoryId: string, limitCents: number, rollover: boolean) {
    await upsertBudget({
      category_id: categoryId,
      limit_cents: limitCents,
      rollover_enabled: rollover,
    });
    await load();
  }

  async function removeBudget(categoryId: string) {
    await deleteBudget(categoryId);
    await load();
  }

  function drillDown(categoryId: string) {
    // The ledger reads these back and filters to the category's whole subtree.
    const params = new URLSearchParams({
      category_id: categoryId,
      start_date: report!.period_start,
      end_date: report!.period_end,
    });
    router.push(`/?${params}`);
  }

  const isCurrentMonth = period === PERIODS[0];
  const overallPct =
    report && report.total_limit_cents > 0
      ? Math.round((report.total_spent_cents / report.total_limit_cents) * 100)
      : 0;

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Budgets</h1>
          <p className="text-sm text-slate-500">
            Transfers and card payments are excluded from spend, so a statement
            payment never shows up as an expense.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700"
          >
            {PERIODS.map((key) => (
              <option key={key} value={key}>
                {monthLabel(key)}
                {key === PERIODS[0] ? " (current)" : ""}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setShowSuggestions(true)}
            className="flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white"
          >
            <Sparkles className="h-4 w-4" />
            Apply suggested limits
          </button>
        </div>
      </header>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {report && (
        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard label="Budgeted" value={formatCents(report.total_limit_cents)} />
          <StatCard
            label="Spent"
            value={formatCents(report.total_spent_cents)}
            sub={report.total_limit_cents > 0 ? `${overallPct}% of budget` : undefined}
          />
          <StatCard
            label={isCurrentMonth ? "Projected at this pace" : "Period total"}
            value={formatCents(
              isCurrentMonth ? report.total_projected_cents : report.total_spent_cents,
            )}
            sub={
              isCurrentMonth
                ? `Day ${report.elapsed_days} of ${report.total_days}`
                : monthLabel(period)
            }
            tone={
              isCurrentMonth &&
              report.total_limit_cents > 0 &&
              report.total_projected_cents > report.total_limit_cents
                ? "alert"
                : "default"
            }
          />
        </div>
      )}

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        {loading && (
          <div className="space-y-3 p-5">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-14 animate-pulse rounded bg-slate-100" />
            ))}
          </div>
        )}

        {!loading && report?.lines.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-16 text-center">
            <PiggyBank className="h-8 w-8 text-slate-300" />
            <p className="font-medium text-slate-600">No budgets set for this period</p>
            <p className="max-w-sm text-sm text-slate-400">
              Add a category below, or let the suggester propose limits from your
              own trailing spend.
            </p>
          </div>
        )}

        {!loading &&
          report?.lines.map((line) => (
            <BudgetRow
              key={line.category_id}
              line={line}
              onSave={saveLimit}
              onDelete={removeBudget}
              onDrillDown={drillDown}
            />
          ))}
      </section>

      <div className="flex items-center gap-2">
        {adding ? (
          <select
            autoFocus
            defaultValue=""
            onChange={(e) => {
              if (e.target.value) saveLimit(e.target.value, 0, false);
              setAdding(false);
            }}
            onBlur={() => setAdding(false)}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
          >
            <option value="" disabled>
              Pick a category…
            </option>
            {budgetable.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        ) : (
          <button
            type="button"
            onClick={() => setAdding(true)}
            disabled={budgetable.length === 0}
            className="flex items-center gap-1.5 rounded-md border border-dashed border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:border-slate-400 hover:text-slate-900 disabled:opacity-40"
          >
            <Plus className="h-4 w-4" />
            Add a category budget
          </button>
        )}
      </div>

      {showSuggestions && (
        <SuggestionsModal onClose={() => setShowSuggestions(false)} onApplied={load} />
      )}
    </main>
  );
}
