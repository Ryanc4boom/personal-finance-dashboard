"use client";

import { useEffect, useState } from "react";
import { AlertCircle, Info, TrendingUp } from "lucide-react";
import AllocationDonut from "@/components/AllocationDonut";
import HoldingsTable from "@/components/HoldingsTable";
import StatCard from "@/components/StatCard";
import { getPortfolio } from "@/lib/api";
import {
  formatBps,
  formatCents,
  formatDate,
  formatSignedBps,
  formatSignedCents,
  titleCase,
} from "@/lib/format";
import type { Portfolio } from "@/lib/types";

export default function InvestmentsPage() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPortfolio()
      .then((data) => {
        setPortfolio(data);
        setError(null);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Could not load the portfolio"),
      )
      .finally(() => setLoading(false));
  }, []);

  const summary = portfolio?.summary;
  // Below this, the headline gain describes a minority of the portfolio and
  // presenting it without a caveat would overstate how much is actually known.
  const basisIncomplete = (summary?.cost_basis_coverage_bps ?? 10_000) < 9_900;

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Investments</h1>
        <p className="text-sm text-slate-500">
          Positions across every linked investment account, valued at the most
          recent snapshot each account reported.
          {summary?.as_of_date && ` As of ${formatDate(summary.as_of_date)}.`}
        </p>
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

      {portfolio && summary && summary.position_count === 0 && (
        <section className="flex flex-col items-center gap-2 rounded-xl border border-slate-200 bg-white py-16 text-center shadow-sm">
          <TrendingUp className="h-8 w-8 text-slate-300" />
          <p className="font-medium text-slate-600">No investment accounts yet</p>
          <p className="max-w-sm text-sm text-slate-400">
            Link a brokerage, IRA or 401(k) and holdings will appear here after
            the next sync.
          </p>
        </section>
      )}

      {portfolio && summary && summary.position_count > 0 && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Portfolio value"
              value={formatCents(summary.total_value_cents)}
              sub={`${summary.position_count} positions · ${summary.account_count} accounts`}
            />
            <StatCard
              label="Total cost basis"
              value={formatCents(summary.total_cost_basis_cents)}
              sub={
                basisIncomplete
                  ? `covers ${formatBps(summary.cost_basis_coverage_bps, 0)} of value`
                  : undefined
              }
            />
            <StatCard
              label="Unrealized gain"
              value={formatSignedCents(summary.unrealized_gain_cents)}
              sub={formatSignedBps(summary.unrealized_gain_bps)}
              tone={summary.unrealized_gain_cents < 0 ? "alert" : "positive"}
            />
            <StatCard
              label="Today"
              value={formatSignedCents(summary.day_change_cents)}
              sub={
                summary.day_change_missing_count > 0
                  ? `${summary.day_change_missing_count} without a prior close`
                  : formatSignedBps(summary.day_change_bps, 2)
              }
              tone={summary.day_change_cents < 0 ? "alert" : "positive"}
            />
          </div>

          {basisIncomplete && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800">
              <Info className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                Gain figures cover {formatBps(summary.cost_basis_coverage_bps, 0)} of
                the portfolio. The rest has no cost basis reported by the
                custodian and is excluded rather than counted as pure profit.
              </span>
            </div>
          )}

          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="mb-4 text-sm font-semibold text-slate-900">
              Asset allocation
            </h2>
            <AllocationDonut
              slices={portfolio.by_asset_class}
              totalCents={summary.total_value_cents}
            />
          </section>

          <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <h2 className="border-b border-slate-200 px-5 py-3 text-sm font-semibold text-slate-900">
              Accounts
            </h2>
            <div className="divide-y divide-slate-100">
              {portfolio.by_account.map((account) => (
                <div
                  key={account.account_id}
                  className="flex flex-wrap items-center gap-x-6 gap-y-1 px-5 py-3"
                >
                  <div className="min-w-48 flex-1">
                    <p className="text-sm font-medium text-slate-900">
                      {account.name}
                      {account.mask && (
                        <span className="ml-1.5 font-normal text-slate-400">
                          ····{account.mask}
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-slate-400">
                      {titleCase(account.subtype)} · {account.position_count}{" "}
                      {account.position_count === 1 ? "position" : "positions"}
                      {account.as_of_date && ` · ${formatDate(account.as_of_date)}`}
                    </p>
                  </div>

                  {/* Share of the portfolio, drawn rather than only stated:
                      the relative width is the point, not the number. */}
                  <div className="hidden h-1.5 w-32 overflow-hidden rounded-full bg-slate-100 sm:block">
                    <div
                      className="h-full rounded-full bg-slate-900"
                      style={{ width: `${account.weight_bps / 100}%` }}
                    />
                  </div>
                  <span className="w-12 text-right text-xs tabular-nums text-slate-400">
                    {formatBps(account.weight_bps, 0)}
                  </span>

                  <span
                    className={`w-28 text-right text-sm tabular-nums ${
                      (account.gain_cents ?? 0) < 0 ? "text-rose-600" : "text-emerald-600"
                    }`}
                  >
                    {account.gain_cents === null
                      ? "—"
                      : formatSignedCents(account.gain_cents)}
                  </span>
                  <span className="w-28 text-right text-sm font-medium tabular-nums text-slate-900">
                    {formatCents(account.value_cents)}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <h2 className="border-b border-slate-200 px-5 py-3 text-sm font-semibold text-slate-900">
              Holdings
            </h2>
            <HoldingsTable positions={portfolio.positions} />
          </section>
        </>
      )}
    </main>
  );
}
