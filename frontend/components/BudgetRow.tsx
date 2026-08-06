"use client";

import { useState } from "react";
import { Check, Pencil, Trash2, X } from "lucide-react";
import { formatCents } from "@/lib/format";
import type { BudgetLine, PacingStatus } from "@/lib/types";

/**
 * One category row. The bar is scaled to `available_cents` (limit + rollover),
 * not to the limit alone, because that is the number the user may actually
 * spend this period — scaling to the limit would paint a rollover-funded
 * category red while it is genuinely on track.
 */

const STATUS: Record<
  PacingStatus,
  { label: string; bar: string; chip: string; note: string }
> = {
  NO_LIMIT: {
    label: "No limit",
    bar: "bg-slate-300",
    chip: "bg-slate-100 text-slate-600 ring-slate-200",
    note: "",
  },
  ON_TRACK: {
    label: "On track",
    bar: "bg-emerald-500",
    chip: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    note: "",
  },
  AT_RISK: {
    label: "At risk",
    bar: "bg-amber-400",
    chip: "bg-amber-50 text-amber-700 ring-amber-200",
    note: "Pacing slightly above the limit",
  },
  OVER_PACING: {
    label: "Over pacing",
    bar: "bg-orange-500",
    chip: "bg-orange-50 text-orange-700 ring-orange-200",
    note: "At this rate the limit will be blown",
  },
  OVER_BUDGET: {
    label: "Over budget",
    bar: "bg-rose-500",
    chip: "bg-rose-50 text-rose-700 ring-rose-200",
    note: "Already spent past the limit",
  },
};

interface Props {
  line: BudgetLine;
  onSave: (categoryId: string, limitCents: number, rollover: boolean) => Promise<void>;
  onDelete: (categoryId: string) => Promise<void>;
  onDrillDown: (categoryId: string) => void;
}

export default function BudgetRow({ line, onSave, onDelete, onDrillDown }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState((line.limit_cents / 100).toFixed(2));
  const [rollover, setRollover] = useState(line.rollover_enabled);
  const [busy, setBusy] = useState(false);

  const tone = STATUS[line.status];
  const available = line.available_cents;
  const spentPct = available > 0 ? Math.min((line.spent_cents / available) * 100, 100) : 0;
  // Where spending "should" be today if it were perfectly even. This is the
  // reference the pacing forecast is measured against.
  const pacePct =
    line.total_days > 0 ? Math.min((line.elapsed_days / line.total_days) * 100, 100) : 0;
  const overspill = line.spent_cents > available && available > 0;

  async function save() {
    const parsed = Number(draft);
    if (!Number.isFinite(parsed) || parsed < 0) return;
    setBusy(true);
    try {
      await onSave(line.category_id, Math.round(parsed * 100), rollover);
      setEditing(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="border-b border-slate-100 px-5 py-4 last:border-b-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onDrillDown(line.category_id)}
            className="text-sm font-semibold text-slate-900 hover:underline"
          >
            {line.category_name}
          </button>
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tone.chip}`}
          >
            {tone.label}
          </span>
          {line.rollover_cents !== 0 && (
            <span className="text-xs text-slate-500">
              {line.rollover_cents > 0 ? "+" : ""}
              {formatCents(line.rollover_cents)} rolled over
            </span>
          )}
        </div>

        <div className="flex items-center gap-3 text-sm tabular-nums">
          <span className="font-medium text-slate-900">{formatCents(line.spent_cents)}</span>
          <span className="text-slate-400">of {formatCents(available)}</span>
          {!editing && (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="text-slate-400 transition hover:text-slate-700"
              aria-label={`Edit ${line.category_name} limit`}
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      <div className="mt-3">
        <div className="relative h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
          <div
            className={`h-full rounded-full transition-all ${tone.bar}`}
            style={{ width: `${spentPct}%` }}
          />
          {pacePct > 0 && pacePct < 100 && (
            <div
              className="absolute inset-y-0 w-px bg-slate-500/60"
              style={{ left: `${pacePct}%` }}
              title={`Day ${line.elapsed_days} of ${line.total_days}`}
            />
          )}
        </div>

        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
          <span>
            Projected{" "}
            <span
              className={
                line.status === "ON_TRACK" || line.status === "NO_LIMIT"
                  ? "font-medium text-slate-700"
                  : "font-medium text-orange-600"
              }
            >
              {formatCents(line.projected_cents)}
            </span>
            {line.pacing_ratio_pct > 0 && ` (${line.pacing_ratio_pct}% of limit)`}
          </span>
          <span>
            {line.remaining_cents >= 0
              ? `${formatCents(line.remaining_cents)} left`
              : `${formatCents(Math.abs(line.remaining_cents))} over`}
          </span>
          <span className="text-slate-400">
            Day {line.elapsed_days} of {line.total_days}
          </span>
          {overspill && <span className="text-rose-600">{tone.note}</span>}
          {!overspill && tone.note && <span className="text-amber-600">{tone.note}</span>}
        </div>
      </div>

      {editing && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg bg-slate-50 px-3 py-2">
          <label className="flex items-center gap-2 text-xs text-slate-600">
            Monthly limit
            <span className="flex items-center rounded-md border border-slate-300 bg-white px-2 py-1">
              <span className="text-slate-400">$</span>
              <input
                type="number"
                min="0"
                step="0.01"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                className="w-24 bg-transparent px-1 text-sm tabular-nums outline-none"
              />
            </span>
          </label>
          <label className="flex items-center gap-1.5 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={rollover}
              onChange={(e) => setRollover(e.target.checked)}
              className="rounded border-slate-300"
            />
            Roll unspent money forward
          </label>
          <div className="ml-auto flex items-center gap-1">
            <button
              type="button"
              onClick={save}
              disabled={busy}
              className="flex items-center gap-1 rounded-md bg-slate-900 px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50"
            >
              <Check className="h-3.5 w-3.5" /> Save
            </button>
            <button
              type="button"
              onClick={() => {
                setDraft((line.limit_cents / 100).toFixed(2));
                setRollover(line.rollover_enabled);
                setEditing(false);
              }}
              className="rounded-md px-2 py-1 text-xs text-slate-500 hover:text-slate-800"
            >
              <X className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => onDelete(line.category_id)}
              className="rounded-md px-2 py-1 text-xs text-rose-500 hover:text-rose-700"
              aria-label={`Remove ${line.category_name} budget`}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
