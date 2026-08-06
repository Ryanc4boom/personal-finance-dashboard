"use client";

import { CalendarClock } from "lucide-react";
import { formatCents, formatDateShort, parseISODate } from "@/lib/format";
import type { Renewal, UpcomingRenewals } from "@/lib/types";

/**
 * "What will hit my account", in order.
 *
 * Renewals are grouped by date rather than listed flat, because the question
 * this answers is about days, not about plans: three charges on the 14th is one
 * bad Tuesday, and a flat list hides that. A weekly stream legitimately appears
 * four or five times here — the backend expands occurrences, not streams.
 */
export default function RenewalTimeline({ data }: { data: UpcomingRenewals }) {
  if (data.renewals.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-14 text-center">
        <CalendarClock className="h-8 w-8 text-slate-300" />
        <p className="font-medium text-slate-600">Nothing due in this window</p>
        <p className="max-w-sm text-sm text-slate-400">
          No active stream has an expected charge before{" "}
          {formatDateShort(data.end_date)}.
        </p>
      </div>
    );
  }

  const byDate = new Map<string, Renewal[]>();
  for (const renewal of data.renewals) {
    const bucket = byDate.get(renewal.date);
    if (bucket) bucket.push(renewal);
    else byDate.set(renewal.date, [renewal]);
  }

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  return (
    <ol className="relative space-y-5 px-5 py-5">
      {/* The spine. Inset to sit under the centre of each date marker. */}
      <span
        aria-hidden
        className="absolute left-[4.75rem] top-2 bottom-2 w-px bg-slate-200"
      />

      {[...byDate.entries()].map(([date, renewals]) => {
        const dayTotal = renewals
          .filter((r) => r.direction === "OUTFLOW")
          .reduce((sum, r) => sum + r.amount_cents, 0);
        const daysAway = Math.round(
          (parseISODate(date).getTime() - today.getTime()) / 86_400_000,
        );
        const imminent = daysAway <= 7;

        return (
          <li key={date} className="relative flex gap-4">
            <div className="w-14 shrink-0 pt-0.5 text-right">
              <p className="text-sm font-medium text-slate-700">
                {formatDateShort(date)}
              </p>
              <p className="text-xs text-slate-400">
                {daysAway === 0 ? "today" : `${daysAway}d`}
              </p>
            </div>

            <span
              aria-hidden
              className={`relative z-10 mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ring-4 ring-white ${
                imminent ? "bg-slate-900" : "bg-slate-300"
              }`}
            />

            <div className="min-w-0 flex-1 space-y-1.5">
              {renewals.map((renewal) => (
                <div
                  key={`${renewal.stream_id}-${renewal.date}`}
                  className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-900">
                      {renewal.display_name}
                    </p>
                    <p className="truncate text-xs text-slate-500">
                      {renewal.category_name ?? "Uncategorised"}
                      {!renewal.is_subscription &&
                        renewal.direction === "OUTFLOW" &&
                        " · bill"}
                    </p>
                  </div>
                  <p
                    className={`shrink-0 text-sm font-semibold tabular-nums ${
                      renewal.direction === "INFLOW"
                        ? "text-emerald-600"
                        : "text-slate-900"
                    }`}
                  >
                    {renewal.direction === "INFLOW" ? "+" : "−"}
                    {formatCents(renewal.amount_cents)}
                  </p>
                </div>
              ))}
              {renewals.length > 1 && dayTotal > 0 && (
                <p className="text-right text-xs text-slate-400 tabular-nums">
                  {formatCents(dayTotal)} due this day
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
