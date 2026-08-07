"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Plus, Target } from "lucide-react";
import GoalModal from "@/components/GoalModal";
import StatCard from "@/components/StatCard";
import { getGoals, getNetWorthAccounts } from "@/lib/api";
import {
  formatBps,
  formatCents,
  formatDate,
  formatRelativeDays,
} from "@/lib/format";
import type {
  AccountBreakdownRow,
  Goal,
  GoalCategory,
  GoalStatus,
  GoalsReport,
} from "@/lib/types";

const CATEGORY_LABELS: Record<GoalCategory, string> = {
  EMERGENCY_FUND: "Emergency fund",
  HOUSE_DOWN_PAYMENT: "House down payment",
  FIRE: "Financial independence",
  CAR_PURCHASE: "Car / vehicle",
  CUSTOM: "Custom",
};

/**
 * Status drives the only colour on the card. A goal with no deadline is grey
 * rather than green: it cannot be behind, but it has not been judged either,
 * and painting it as passing would flatter every goal a user never dated.
 */
const STATUS_STYLES: Record<GoalStatus, { label: string; badge: string; bar: string }> = {
  ACHIEVED: {
    label: "Achieved",
    badge: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    bar: "bg-emerald-500",
  },
  ON_TRACK: {
    label: "On track",
    badge: "bg-teal-50 text-teal-700 ring-teal-200",
    bar: "bg-teal-500",
  },
  AT_RISK: {
    label: "At risk",
    badge: "bg-amber-50 text-amber-700 ring-amber-200",
    bar: "bg-amber-500",
  },
  OFF_TRACK: {
    label: "Off track",
    badge: "bg-rose-50 text-rose-700 ring-rose-200",
    bar: "bg-rose-500",
  },
  NO_DEADLINE: {
    label: "No deadline",
    badge: "bg-slate-100 text-slate-600 ring-slate-200",
    bar: "bg-slate-400",
  },
};

function GoalCard({ goal, onEdit }: { goal: Goal; onEdit: () => void }) {
  const style = STATUS_STYLES[goal.status];

  return (
    <button
      type="button"
      onClick={onEdit}
      className="flex w-full flex-col gap-3 rounded-xl border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:border-slate-300 hover:shadow"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-900">{goal.name}</p>
          <p className="text-xs text-slate-400">
            {CATEGORY_LABELS[goal.category]}
            {goal.linked_accounts.length > 0 &&
              ` · ${goal.linked_accounts.length} linked ${
                goal.linked_accounts.length === 1 ? "account" : "accounts"
              }`}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${style.badge}`}
        >
          {style.label}
        </span>
      </div>

      <div>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-lg font-semibold tabular-nums text-slate-900">
            {formatCents(goal.current_amount_cents)}
          </span>
          <span className="text-xs tabular-nums text-slate-400">
            of {formatCents(goal.target_amount_cents)}
          </span>
        </div>
        <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-100">
          <div
            className={`h-full rounded-full transition-all ${style.bar}`}
            style={{ width: `${goal.progress_bps / 100}%` }}
          />
        </div>
        <div className="mt-1 flex justify-between text-[11px] tabular-nums text-slate-400">
          {/* The raw figure, not the clamped bar width: a goal that is 140%
              funded should say so rather than read as an exact finish. */}
          <span>{formatBps(goal.raw_progress_bps, 0)}</span>
          <span>
            {goal.remaining_cents > 0
              ? `${formatCents(goal.remaining_cents)} to go`
              : "funded"}
          </span>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 border-t border-slate-100 pt-3 text-xs">
        <div>
          <dt className="text-slate-400">Target date</dt>
          <dd className="text-slate-700">
            {goal.target_date ? (
              <>
                {formatDate(goal.target_date)}
                {goal.days_remaining !== null && (
                  <span
                    className={`ml-1 ${
                      goal.days_remaining < 0 ? "text-rose-600" : "text-slate-400"
                    }`}
                  >
                    ({formatRelativeDays(goal.days_remaining)})
                  </span>
                )}
              </>
            ) : (
              <span className="text-slate-400">none set</span>
            )}
          </dd>
        </div>

        <div>
          <dt className="text-slate-400">Needed monthly</dt>
          <dd className="tabular-nums text-slate-700">
            {goal.required_monthly_cents === null
              ? "—"
              : formatCents(goal.required_monthly_cents)}
          </dd>
        </div>

        <div>
          <dt className="text-slate-400">
            {goal.observed_is_measured ? "Actual monthly" : "Planned monthly"}
          </dt>
          <dd
            className={`tabular-nums ${
              goal.required_monthly_cents !== null &&
              goal.observed_monthly_cents !== null &&
              goal.observed_monthly_cents < goal.required_monthly_cents
                ? "text-rose-600"
                : "text-slate-700"
            }`}
          >
            {goal.observed_monthly_cents === null
              ? "—"
              : formatCents(goal.observed_monthly_cents)}
          </dd>
        </div>

        <div>
          <dt className="text-slate-400">Projected finish</dt>
          <dd className="text-slate-700">
            {goal.projected_completion_date ? (
              <>
                {formatDate(goal.projected_completion_date)}
                {goal.projected_vs_target_days !== null &&
                  goal.projected_vs_target_days > 0 && (
                    <span className="ml-1 text-rose-600">
                      ({goal.projected_vs_target_days}d late)
                    </span>
                  )}
              </>
            ) : (
              <span className="text-slate-400">
                {goal.is_achieved ? "reached" : "not enough movement"}
              </span>
            )}
          </dd>
        </div>
      </dl>

      {!goal.observed_is_measured && !goal.is_achieved && (
        <p className="text-[11px] text-slate-400">
          Projection uses your stated plan — there is not yet enough balance
          history to measure what you actually save.
        </p>
      )}
    </button>
  );
}

export default function GoalsPage() {
  const [report, setReport] = useState<GoalsReport | null>(null);
  const [accounts, setAccounts] = useState<AccountBreakdownRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** `null` is closed; `{ goal: null }` is the create form. */
  const [editing, setEditing] = useState<{ goal: Goal | null } | null>(null);

  const load = useCallback(async () => {
    try {
      const [goals, rows] = await Promise.all([getGoals(), getNetWorthAccounts()]);
      setReport(goals);
      setAccounts(rows);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load goals");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const summary = report?.summary;

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Goals</h1>
          <p className="text-sm text-slate-500">
            What you are saving toward, scored against real balances rather than
            a number you last updated by hand.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setEditing({ goal: null })}
          className="flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-slate-800"
        >
          <Plus className="h-4 w-4" />
          New goal
        </button>
      </header>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {loading && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-20 animate-pulse rounded-xl bg-slate-100" />
          ))}
        </div>
      )}

      {summary && summary.goal_count > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Saved toward goals"
            value={formatCents(summary.total_current_cents)}
            sub={`${formatBps(summary.total_progress_bps, 0)} of ${formatCents(
              summary.total_target_cents,
            )}`}
          />
          <StatCard
            label="Still to fund"
            value={formatCents(summary.total_remaining_cents)}
            sub={`across ${summary.goal_count} ${
              summary.goal_count === 1 ? "goal" : "goals"
            }`}
          />
          <StatCard
            label="Required monthly"
            value={formatCents(summary.total_required_monthly_cents)}
            sub="to hit every dated goal on time"
          />
          <StatCard
            label="Off track"
            value={String(summary.off_track_count)}
            sub={`${summary.achieved_count} achieved`}
            tone={summary.off_track_count > 0 ? "alert" : "positive"}
          />
        </div>
      )}

      {report && report.goals.length === 0 && (
        <section className="flex flex-col items-center gap-2 rounded-xl border border-slate-200 bg-white py-16 text-center shadow-sm">
          <Target className="h-8 w-8 text-slate-300" />
          <p className="font-medium text-slate-600">No goals yet</p>
          <p className="max-w-sm text-sm text-slate-400">
            Create one and link the accounts that back it — progress then tracks
            your real balances instead of a figure you have to maintain.
          </p>
        </section>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        {report?.goals.map((goal) => (
          <GoalCard
            key={goal.id}
            goal={goal}
            onEdit={() => setEditing({ goal })}
          />
        ))}
      </div>

      {editing && (
        <GoalModal
          goal={editing.goal}
          accounts={accounts}
          onClose={() => setEditing(null)}
          onSaved={load}
        />
      )}
    </main>
  );
}
