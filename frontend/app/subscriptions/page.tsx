"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CalendarClock,
  Repeat,
  RefreshCw,
  TrendingUp,
} from "lucide-react";
import RenewalTimeline from "@/components/RenewalTimeline";
import StatCard from "@/components/StatCard";
import SubscriptionRow from "@/components/SubscriptionRow";
import {
  detectRecurring,
  getRecurringStreams,
  getSubscriptionMetrics,
  getUpcomingRenewals,
  updateRecurringStream,
} from "@/lib/api";
import { formatCents, formatDate } from "@/lib/format";
import type {
  RecurringStream,
  StreamStatus,
  SubscriptionMetrics,
  UpcomingRenewals,
} from "@/lib/types";

type Tab = "subscriptions" | "bills" | "all";

export default function SubscriptionsPage() {
  const [streams, setStreams] = useState<RecurringStream[]>([]);
  const [metrics, setMetrics] = useState<SubscriptionMetrics | null>(null);
  const [renewals, setRenewals] = useState<UpcomingRenewals | null>(null);
  const [loading, setLoading] = useState(true);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("subscriptions");
  const [showCancelled, setShowCancelled] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, m, r] = await Promise.all([
        getRecurringStreams({ direction: "OUTFLOW" }),
        getSubscriptionMetrics(),
        getUpcomingRenewals(30),
      ]);
      setStreams(s);
      setMetrics(m);
      setRenewals(r);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load subscriptions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function rerunDetection() {
    setDetecting(true);
    try {
      await detectRecurring();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Detection failed");
    } finally {
      setDetecting(false);
    }
  }

  const setStatus = useCallback(
    async (id: string, status: StreamStatus) => {
      // Optimistic: the toggle is the whole interaction, and waiting a round trip
      // to redraw a pill makes it feel broken.
      setStreams((prev) =>
        prev.map((s) =>
          s.id === id ? { ...s, status, status_source: "USER" } : s,
        ),
      );
      try {
        await updateRecurringStream(id, { status });
      } finally {
        // Totals, renewals and the forgotten list all shift when a stream is
        // cancelled, so the authoritative numbers have to come back from the API.
        await load();
      }
    },
    [load],
  );

  const visible = useMemo(() => {
    return streams.filter((s) => {
      if (!showCancelled && s.status === "CANCELLED") return false;
      if (tab === "subscriptions") return s.is_subscription;
      if (tab === "bills") return !s.is_subscription;
      return true;
    });
  }, [streams, tab, showCancelled]);

  const cancelledCount = streams.filter((s) => s.status === "CANCELLED").length;

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Subscriptions</h1>
          <p className="text-sm text-slate-500">
            Detected from your own history — a charge counts as recurring only
            when both its timing and its amount are consistent.
          </p>
        </div>
        <button
          type="button"
          onClick={rerunDetection}
          disabled={detecting}
          className="flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${detecting ? "animate-spin" : ""}`} />
          {detecting ? "Scanning…" : "Re-scan history"}
        </button>
      </header>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {metrics && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Monthly commitments"
            value={formatCents(metrics.recurring_monthly_cents)}
            sub="Every recurring outflow, rent included"
          />
          <StatCard
            label="Annualized cost"
            value={formatCents(metrics.recurring_annual_cents)}
            sub="What the same commitments cost per year"
          />
          <StatCard
            label="Subscriptions only"
            value={formatCents(metrics.subscription_monthly_cents)}
            sub={`${formatCents(metrics.subscription_annual_cents)}/yr — the cancellable part`}
          />
          <StatCard
            label="Active subscriptions"
            value={String(metrics.active_subscription_count)}
            sub={
              metrics.paused_count + metrics.cancelled_count > 0
                ? `${metrics.paused_count} paused · ${metrics.cancelled_count} cancelled`
                : `${metrics.active_recurring_count} recurring streams in total`
            }
          />
        </div>
      )}

      {metrics && metrics.price_hikes.length > 0 && (
        <section className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-amber-900">
            <TrendingUp className="h-4 w-4" />
            Price {metrics.price_hikes.length === 1 ? "increase" : "increases"}
          </h2>
          <ul className="mt-2 space-y-1.5">
            {metrics.price_hikes.map((hike) => (
              <li
                key={hike.stream_id}
                className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm text-amber-900"
              >
                <span className="font-medium">{hike.display_name}</span>
                <span className="tabular-nums">
                  {formatCents(hike.baseline_cents)} →{" "}
                  <strong>{formatCents(hike.current_cents)}</strong>
                  <span className="ml-2 text-amber-700">
                    +{(hike.delta_bps / 100).toFixed(1)}% ·{" "}
                    {formatCents(hike.annual_impact_cents)}/yr more
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {metrics && metrics.forgotten.length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
            <CalendarClock className="h-4 w-4 text-slate-400" />
            Expected but never charged
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Either these were cancelled and we have not been told, or the charge
            is late. Worth a look.
          </p>
          <ul className="mt-2 space-y-1.5">
            {metrics.forgotten.map((f) => (
              <li
                key={f.stream_id}
                className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm"
              >
                <span className="font-medium text-slate-800">
                  {f.display_name}
                </span>
                <span className="tabular-nums text-slate-500">
                  {formatCents(f.expected_amount_cents)} due{" "}
                  {formatDate(f.next_expected_date)} · {f.days_overdue} days late
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-5 py-3">
          <div className="flex gap-1">
            {(
              [
                ["subscriptions", "Subscriptions"],
                ["bills", "Bills"],
                ["all", "All recurring"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setTab(key)}
                className={`rounded-md px-2.5 py-1 text-sm font-medium transition ${
                  tab === key
                    ? "bg-slate-900 text-white"
                    : "text-slate-500 hover:text-slate-900"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {cancelledCount > 0 && (
            <label className="flex items-center gap-2 text-xs text-slate-500">
              <input
                type="checkbox"
                checked={showCancelled}
                onChange={(e) => setShowCancelled(e.target.checked)}
                className="rounded border-slate-300"
              />
              Show {cancelledCount} cancelled
            </label>
          )}
        </div>

        {loading && (
          <div className="space-y-3 p-5">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-12 animate-pulse rounded bg-slate-100" />
            ))}
          </div>
        )}

        {!loading && visible.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-16 text-center">
            <Repeat className="h-8 w-8 text-slate-300" />
            <p className="font-medium text-slate-600">Nothing here yet</p>
            <p className="max-w-sm text-sm text-slate-400">
              Detection needs at least three charges at a steady interval and a
              steady amount before it will call something recurring.
            </p>
          </div>
        )}

        {!loading &&
          visible.map((stream) => (
            <SubscriptionRow
              key={stream.id}
              stream={stream}
              onSetStatus={setStatus}
            />
          ))}
      </section>

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-baseline justify-between border-b border-slate-200 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-900">
            Upcoming renewals · next 30 days
          </h2>
          {renewals && (
            <p className="text-sm tabular-nums text-slate-500">
              {formatCents(renewals.total_cents)} due
            </p>
          )}
        </div>
        {loading && (
          <div className="space-y-3 p-5">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-12 animate-pulse rounded bg-slate-100" />
            ))}
          </div>
        )}
        {!loading && renewals && <RenewalTimeline data={renewals} />}
      </section>
    </main>
  );
}
