"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { AlertCircle, RefreshCw } from "lucide-react";
import NetWorthChart from "@/components/NetWorthChart";
import StatCard from "@/components/StatCard";
import { backfillNetWorth, getNetWorthHistory } from "@/lib/api";
import {
  formatCents,
  formatDate,
  formatSignedBps,
  formatSignedCents,
  titleCase,
} from "@/lib/format";
import {
  NET_WORTH_RANGES,
  type AccountBreakdownRow,
  type NetWorthHistory,
  type NetWorthRange,
} from "@/lib/types";

const BUCKET_LABELS: Record<AccountBreakdownRow["bucket"], string> = {
  cash: "Cash",
  investments: "Investments",
  other: "Other assets",
  credit: "Credit cards",
  loans: "Loans",
};

const BUCKET_ORDER: AccountBreakdownRow["bucket"][] = [
  "cash",
  "investments",
  "other",
  "credit",
  "loans",
];

export default function NetWorthPage() {
  const [range, setRange] = useState<NetWorthRange>("1Y");
  const [history, setHistory] = useState<NetWorthHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [rebuilding, setRebuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setHistory(await getNetWorthHistory(range));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load net worth");
    } finally {
      setLoading(false);
    }
  }, [range]);

  useEffect(() => {
    load();
  }, [load]);

  async function rebuild() {
    setRebuilding(true);
    try {
      await backfillNetWorth();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Backfill failed");
    } finally {
      setRebuilding(false);
    }
  }

  const assets = history?.accounts.filter((a) => !a.is_liability) ?? [];
  const liabilities = history?.accounts.filter((a) => a.is_liability) ?? [];

  const grouped = BUCKET_ORDER.map((bucket) => ({
    bucket,
    rows: history?.accounts.filter((a) => a.bucket === bucket) ?? [],
  })).filter((group) => group.rows.length > 0);

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Net worth</h1>
          <p className="text-sm text-slate-500">
            Everything you own minus everything you owe, reconstructed backwards
            through your ledger. Investment accounts are valued from their
            holdings, not their cash balance.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex rounded-md border border-slate-300 bg-white p-0.5">
            {NET_WORTH_RANGES.map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setRange(key)}
                className={`rounded px-2.5 py-1 text-sm font-medium transition ${
                  range === key
                    ? "bg-slate-900 text-white"
                    : "text-slate-500 hover:text-slate-900"
                }`}
              >
                {key}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={rebuild}
            disabled={rebuilding}
            title="Recompute stored daily snapshots from your current balances and ledger history"
            className="flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${rebuilding ? "animate-spin" : ""}`} />
            Rebuild
          </button>
        </div>
      </header>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {history && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Net worth"
              value={formatCents(history.current_net_worth_cents)}
              sub={history.end_date ? formatDate(history.end_date) : undefined}
            />
            <StatCard
              label="Total assets"
              value={formatCents(history.current_assets_cents)}
              sub={`${assets.length} ${assets.length === 1 ? "account" : "accounts"}`}
              tone="positive"
            />
            <StatCard
              label="Total liabilities"
              value={formatCents(history.current_liabilities_cents)}
              sub={`${liabilities.length} ${liabilities.length === 1 ? "account" : "accounts"}`}
              tone={history.current_liabilities_cents > 0 ? "alert" : "default"}
            />
            <StatCard
              label={`Change over ${history.range_key}`}
              value={
                history.change ? formatSignedCents(history.change.delta_cents) : "—"
              }
              sub={
                history.change?.delta_bps === null
                  ? "no percentage from a negative start"
                  : formatSignedBps(history.change?.delta_bps)
              }
              tone={
                history.change && history.change.delta_cents < 0 ? "alert" : "positive"
              }
            />
          </div>

          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="mb-3 text-sm font-semibold text-slate-900">
              History
            </h2>
            {loading ? (
              <div className="h-72 animate-pulse rounded bg-slate-100" />
            ) : (
              <NetWorthChart points={history.points} />
            )}
          </section>

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            <h2 className="border-b border-slate-200 px-5 py-3 text-sm font-semibold text-slate-900">
              Assets &amp; liabilities
            </h2>
            <table className="w-full text-sm">
              <tbody>
                {grouped.map(({ bucket, rows }) => {
                  const subtotal = rows.reduce((sum, r) => sum + r.balance_cents, 0);
                  return (
                    <Fragment key={bucket}>
                      <tr className="bg-slate-50">
                        <th className="px-5 py-1.5 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                          {BUCKET_LABELS[bucket]}
                        </th>
                        <td className="px-5 py-1.5 text-right text-xs font-medium tabular-nums text-slate-500">
                          {formatSignedCents(subtotal)}
                        </td>
                      </tr>
                      {rows.map((row) => (
                        <tr key={row.account_id} className="border-b border-slate-100">
                          <td className="px-5 py-2.5">
                            <span className="text-slate-700">{row.name}</span>
                            {row.mask && (
                              <span className="ml-1.5 text-slate-400">
                                ····{row.mask}
                              </span>
                            )}
                            <span className="ml-2 text-xs text-slate-400">
                              {titleCase(row.subtype ?? row.type)}
                            </span>
                          </td>
                          <td
                            className={`px-5 py-2.5 text-right font-medium tabular-nums ${
                              row.is_liability ? "text-rose-600" : "text-slate-900"
                            }`}
                          >
                            {formatCents(Math.abs(row.balance_cents))}
                          </td>
                        </tr>
                      ))}
                    </Fragment>
                  );
                })}
                <tr className="border-t-2 border-slate-200">
                  <th className="px-5 py-3 text-left text-sm font-semibold text-slate-900">
                    Net worth
                  </th>
                  <td className="px-5 py-3 text-right text-sm font-semibold tabular-nums text-slate-900">
                    {formatCents(history.current_net_worth_cents)}
                  </td>
                </tr>
              </tbody>
            </table>
          </section>
        </>
      )}
    </main>
  );
}
