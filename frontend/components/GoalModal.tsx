"use client";

import { useMemo, useState } from "react";
import { Trash2, X } from "lucide-react";
import { Overlay } from "@/components/SuggestionsModal";
import { createGoal, deleteGoal, updateGoal } from "@/lib/api";
import { formatCents } from "@/lib/format";
import {
  GOAL_CATEGORIES,
  type AccountBreakdownRow,
  type Goal,
  type GoalCategory,
} from "@/lib/types";

const CATEGORY_LABELS: Record<GoalCategory, string> = {
  EMERGENCY_FUND: "Emergency fund",
  HOUSE_DOWN_PAYMENT: "House down payment",
  FIRE: "Financial independence",
  CAR_PURCHASE: "Car / vehicle",
  CUSTOM: "Custom",
};

/** "1250.50" -> 125050. Rejects anything that is not a finite number. */
function toCents(value: string): number | null {
  const parsed = Number(value.replace(/[$,\s]/g, ""));
  if (!Number.isFinite(parsed)) return null;
  return Math.round(parsed * 100);
}

function fromCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "";
  return (cents / 100).toFixed(2);
}

/**
 * Create or edit a goal.
 *
 * Linking accounts is the consequential control here, so it says what it does:
 * a linked goal reads its progress from live balances and the manual amount
 * field disappears entirely. Leaving both editable would let a user type a
 * number that the next render silently ignores.
 */
export default function GoalModal({
  goal,
  accounts,
  onClose,
  onSaved,
}: {
  /** Null creates; a goal edits. */
  goal: Goal | null;
  accounts: AccountBreakdownRow[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(goal?.name ?? "");
  const [category, setCategory] = useState<GoalCategory>(goal?.category ?? "CUSTOM");
  const [target, setTarget] = useState(fromCents(goal?.target_amount_cents) || "");
  const [manual, setManual] = useState(
    goal ? fromCents(goal.current_amount_cents) : "0.00",
  );
  const [targetDate, setTargetDate] = useState(goal?.target_date ?? "");
  const [contribution, setContribution] = useState(
    fromCents(goal?.monthly_contribution_cents),
  );
  const [notes, setNotes] = useState(goal?.notes ?? "");
  const [linked, setLinked] = useState<string[]>(goal?.linked_account_ids ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const linkedTotal = useMemo(
    () =>
      accounts
        .filter((a) => linked.includes(a.account_id))
        .reduce((sum, a) => sum + a.balance_cents, 0),
    [accounts, linked],
  );

  const targetCents = toCents(target);
  const valid = name.trim().length > 0 && targetCents !== null && targetCents > 0;

  function toggle(accountId: string) {
    setLinked((current) =>
      current.includes(accountId)
        ? current.filter((id) => id !== accountId)
        : [...current, accountId],
    );
  }

  async function save() {
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      const payload = {
        name: name.trim(),
        category,
        target_amount_cents: targetCents!,
        current_amount_cents: linked.length ? 0 : (toCents(manual) ?? 0),
        target_date: targetDate || null,
        monthly_contribution_cents: contribution ? toCents(contribution) : null,
        notes: notes.trim() || null,
        linked_account_ids: linked,
      };
      if (goal) await updateGoal(goal.id, payload);
      else await createGoal(payload);
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the goal");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!goal) return;
    setBusy(true);
    try {
      await deleteGoal(goal.id);
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the goal");
      setBusy(false);
    }
  }

  return (
    <Overlay onClose={onClose}>
      <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
        <h2 className="text-sm font-semibold text-slate-900">
          {goal ? "Edit goal" : "New goal"}
        </h2>
        <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-600">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="max-h-[70vh] space-y-4 overflow-y-auto px-5 py-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="text-xs font-medium text-slate-600">Name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Emergency fund"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            />
          </label>

          <label className="block">
            <span className="text-xs font-medium text-slate-600">Category</span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value as GoalCategory)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            >
              {GOAL_CATEGORIES.map((key) => (
                <option key={key} value={key}>
                  {CATEGORY_LABELS[key]}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs font-medium text-slate-600">Target amount</span>
            <input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              inputMode="decimal"
              placeholder="30000.00"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm tabular-nums"
            />
          </label>

          <label className="block">
            <span className="text-xs font-medium text-slate-600">
              Target date <span className="text-slate-400">(optional)</span>
            </span>
            <input
              type="date"
              value={targetDate}
              onChange={(e) => setTargetDate(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            />
          </label>

          <label className="block">
            <span className="text-xs font-medium text-slate-600">
              Planned monthly contribution
            </span>
            <input
              value={contribution}
              onChange={(e) => setContribution(e.target.value)}
              inputMode="decimal"
              placeholder="500.00"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm tabular-nums"
            />
            <span className="mt-0.5 block text-[11px] text-slate-400">
              Used for the projection only until there is enough history to
              measure what you actually save.
            </span>
          </label>

          {linked.length === 0 && (
            <label className="block">
              <span className="text-xs font-medium text-slate-600">
                Current amount
              </span>
              <input
                value={manual}
                onChange={(e) => setManual(e.target.value)}
                inputMode="decimal"
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm tabular-nums"
              />
              <span className="mt-0.5 block text-[11px] text-slate-400">
                Tracked by hand. Link an account below to have this read from a
                real balance instead.
              </span>
            </label>
          )}
        </div>

        <div>
          <p className="text-xs font-medium text-slate-600">Linked accounts</p>
          <p className="mb-2 text-[11px] text-slate-400">
            Progress is read live from whatever you link here. A linked loan
            counts debt retired, not the balance itself.
          </p>
          <div className="max-h-44 space-y-1 overflow-y-auto rounded-md border border-slate-200 p-1.5">
            {accounts.map((account) => (
              <label
                key={account.account_id}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-slate-50"
              >
                <input
                  type="checkbox"
                  checked={linked.includes(account.account_id)}
                  onChange={() => toggle(account.account_id)}
                  className="rounded border-slate-300"
                />
                <span className="flex-1 truncate text-slate-700">
                  {account.name}
                  {account.mask && (
                    <span className="ml-1 text-slate-400">····{account.mask}</span>
                  )}
                </span>
                <span
                  className={`tabular-nums ${
                    account.is_liability ? "text-rose-600" : "text-slate-500"
                  }`}
                >
                  {formatCents(Math.abs(account.balance_cents))}
                </span>
              </label>
            ))}
          </div>
          {linked.length > 0 && (
            <p className="mt-1.5 text-xs text-slate-500">
              Backing balance:{" "}
              <span className="font-medium tabular-nums text-slate-900">
                {formatCents(linkedTotal)}
              </span>
            </p>
          )}
        </div>

        <label className="block">
          <span className="text-xs font-medium text-slate-600">Notes</span>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          />
        </label>

        {error && (
          <p className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
            {error}
          </p>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-slate-200 px-5 py-3">
        {goal ? (
          <button
            type="button"
            onClick={remove}
            disabled={busy}
            className="flex items-center gap-1.5 text-sm text-rose-600 hover:text-rose-700 disabled:opacity-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete
          </button>
        ) : (
          <span />
        )}

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:text-slate-900"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={save}
            disabled={!valid || busy}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-slate-800 disabled:opacity-40"
          >
            {busy ? "Saving…" : goal ? "Save changes" : "Create goal"}
          </button>
        </div>
      </div>
    </Overlay>
  );
}
