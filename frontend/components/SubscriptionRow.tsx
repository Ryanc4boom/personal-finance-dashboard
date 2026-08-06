"use client";

import { useState } from "react";
import { Ban, Play, Repeat, TrendingUp } from "lucide-react";
import { formatCents, formatDate, formatRelativeDays } from "@/lib/format";
import type { RecurringStream, StreamStatus } from "@/lib/types";

const FREQUENCY_LABEL: Record<string, string> = {
  WEEKLY: "Weekly",
  BIWEEKLY: "Every 2 weeks",
  MONTHLY: "Monthly",
  QUARTERLY: "Quarterly",
  ANNUALLY: "Annually",
};

function StatusPill({ stream }: { stream: RecurringStream }) {
  const styles: Record<StreamStatus, string> = {
    ACTIVE: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    PAUSED: "bg-amber-50 text-amber-700 ring-amber-200",
    CANCELLED: "bg-slate-100 text-slate-500 ring-slate-200",
  };
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${styles[stream.status]}`}
      // The provenance matters: an auto-paused stream is a question, a
      // user-cancelled one is a decision. Same pill, different meaning.
      title={
        stream.status_source === "USER"
          ? "Set by you — re-detection will not change it"
          : "Inferred from your transaction history"
      }
    >
      {stream.status[0] + stream.status.slice(1).toLowerCase()}
      {stream.status_source === "USER" && " ·"}
    </span>
  );
}

export default function SubscriptionRow({
  stream,
  onSetStatus,
}: {
  stream: RecurringStream;
  onSetStatus: (id: string, status: StreamStatus) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const cancelled = stream.status === "CANCELLED";
  // A price hike is the last charge sitting >5% above the historic baseline.
  const hiked = stream.last_amount_cents > stream.expected_amount_cents * 1.05;
  const overdue = stream.days_until_next < 0;

  async function toggle() {
    setBusy(true);
    try {
      await onSetStatus(stream.id, cancelled ? "ACTIVE" : "CANCELLED");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={`flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-100 px-5 py-3.5 last:border-b-0 ${
        cancelled ? "bg-slate-50/60" : "bg-white"
      }`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p
            className={`truncate font-medium ${
              cancelled ? "text-slate-400 line-through" : "text-slate-900"
            }`}
          >
            {stream.display_name}
          </p>
          {!stream.is_subscription && (
            <span
              className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-500"
              title="A recurring bill rather than a cancellable plan — counted in commitments, not in the subscription totals."
            >
              Bill
            </span>
          )}
          {hiked && !cancelled && (
            <span
              className="flex items-center gap-1 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 ring-1 ring-inset ring-amber-200"
              title={`Was ${formatCents(stream.expected_amount_cents)}, now ${formatCents(stream.last_amount_cents)}`}
            >
              <TrendingUp className="h-3 w-3" />
              Price up
            </span>
          )}
        </div>
        <p className="mt-0.5 truncate text-xs text-slate-500">
          {stream.category_name ?? "Uncategorised"}
          <span className="mx-1.5 text-slate-300">·</span>
          <span className="inline-flex items-center gap-1">
            <Repeat className="h-3 w-3" />
            {FREQUENCY_LABEL[stream.frequency] ?? stream.frequency}
          </span>
          <span className="mx-1.5 text-slate-300">·</span>
          {stream.occurrence_count} charges
        </p>
      </div>

      <div className="w-32 text-right">
        <p
          className={`font-semibold tabular-nums ${
            cancelled ? "text-slate-400" : "text-slate-900"
          }`}
        >
          {formatCents(stream.expected_amount_cents)}
        </p>
        <p className="text-xs text-slate-400 tabular-nums">
          {formatCents(stream.monthly_cents)}/mo
        </p>
      </div>

      <div className="w-36 text-right">
        {cancelled ? (
          <p className="text-xs text-slate-400">No longer charging</p>
        ) : (
          <>
            <p className="text-sm tabular-nums text-slate-700">
              {formatDate(stream.next_expected_date)}
            </p>
            <p
              className={`text-xs tabular-nums ${
                overdue ? "text-amber-600" : "text-slate-400"
              }`}
            >
              {overdue ? "overdue — " : ""}
              {formatRelativeDays(stream.days_until_next)}
            </p>
          </>
        )}
      </div>

      <div className="flex w-28 items-center justify-end gap-2">
        <StatusPill stream={stream} />
        <button
          type="button"
          onClick={toggle}
          disabled={busy}
          title={cancelled ? "Mark as active" : "Mark as cancelled"}
          className="rounded-md border border-slate-200 p-1.5 text-slate-500 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-40"
        >
          {cancelled ? (
            <Play className="h-3.5 w-3.5" />
          ) : (
            <Ban className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
    </div>
  );
}
