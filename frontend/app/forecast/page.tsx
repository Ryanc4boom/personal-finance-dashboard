"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, AlertTriangle, ShieldCheck, Wallet } from "lucide-react";
import BalanceChart from "@/components/BalanceChart";
import StatCard from "@/components/StatCard";
import { getForecast } from "@/lib/api";
import { formatCents, formatDate } from "@/lib/format";
import { FORECAST_HORIZONS, type Forecast, type ForecastHorizon } from "@/lib/types";

export default function ForecastPage() {
  const [horizon, setHorizon] = useState<ForecastHorizon>(30);
  const [includeVariable, setIncludeVariable] = useState(true);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setForecast(
        await getForecast({ horizonDays: horizon, includeVariable }),
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the forecast");
    } finally {
      setLoading(false);
    }
  }, [horizon, includeVariable]);

  useEffect(() => {
    load();
  }, [load]);

  const worst = forecast?.low_points[0] ?? null;

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Cash flow forecast</h1>
          <p className="text-sm text-slate-500">
            Today&rsquo;s cash, carried forward through the bills and income we
            expect. Card balances are excluded — they are settled by a payment
            that is already modelled.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex rounded-md border border-slate-300 bg-white p-0.5">
            {FORECAST_HORIZONS.map((days) => (
              <button
                key={days}
                type="button"
                onClick={() => setHorizon(days)}
                className={`rounded px-2.5 py-1 text-sm font-medium transition ${
                  horizon === days
                    ? "bg-slate-900 text-white"
                    : "text-slate-500 hover:text-slate-900"
                }`}
              >
                {days}d
              </button>
            ))}
          </div>
        </div>
      </header>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {forecast && (
        <>
          {worst ? (
            <section className="flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-500" />
              <div className="text-sm text-rose-900">
                <p className="font-semibold">
                  Projected to dip below {formatCents(forecast.threshold_cents)}{" "}
                  on {formatDate(worst.date)}
                </p>
                <p className="mt-0.5 text-rose-700">
                  Balance reaches {formatCents(worst.balance_cents)} in{" "}
                  {worst.days_away} days — {formatCents(worst.shortfall_cents)}{" "}
                  short of the floor.
                  {forecast.low_points.length > 1 &&
                    ` ${forecast.low_points.length - 1} further ${
                      forecast.low_points.length === 2 ? "dip" : "dips"
                    } after this one.`}
                </p>
              </div>
            </section>
          ) : (
            <section className="flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3">
              <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
              <div className="text-sm text-emerald-900">
                <p className="font-semibold">
                  Stays above {formatCents(forecast.threshold_cents)} for the
                  whole {forecast.horizon_days} days
                </p>
                <p className="mt-0.5 text-emerald-700">
                  Lowest point is {formatCents(forecast.min_balance_cents)} on{" "}
                  {formatDate(forecast.min_balance_date)}.
                </p>
              </div>
            </section>
          )}

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Cash today"
              value={formatCents(forecast.starting_cash_cents)}
              sub={`${forecast.accounts.length} cash ${
                forecast.accounts.length === 1 ? "account" : "accounts"
              }`}
            />
            <StatCard
              label="Projected month end"
              value={formatCents(forecast.month_end_cents)}
              tone={forecast.month_end_cents < forecast.threshold_cents ? "alert" : "default"}
            />
            <StatCard
              label={`In ${forecast.horizon_days} days`}
              value={formatCents(forecast.ending_cash_cents)}
              tone={
                forecast.ending_cash_cents > forecast.starting_cash_cents
                  ? "positive"
                  : "default"
              }
            />
            <StatCard
              label="Lowest point"
              value={formatCents(forecast.min_balance_cents)}
              sub={formatDate(forecast.min_balance_date)}
              tone={
                forecast.min_balance_cents < forecast.threshold_cents
                  ? "alert"
                  : "default"
              }
            />
          </div>

          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-slate-900">
                Projected daily balance
              </h2>
              <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-500">
                <input
                  type="checkbox"
                  checked={includeVariable}
                  onChange={(e) => setIncludeVariable(e.target.checked)}
                  className="rounded border-slate-300"
                />
                Include everyday spending
                <span
                  className="text-slate-400"
                  title="Spreads whatever is left of your budgets across the rest of the month. Off, the line shows committed bills and income only."
                >
                  (?)
                </span>
              </label>
            </div>

            {loading ? (
              <div className="h-64 animate-pulse rounded bg-slate-100" />
            ) : (
              <BalanceChart forecast={forecast} />
            )}

            <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-slate-100 pt-3 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-xs text-slate-500">Expected income</dt>
                <dd className="font-medium tabular-nums text-emerald-600">
                  +{formatCents(forecast.total_inflow_cents)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Bills &amp; subscriptions</dt>
                <dd className="font-medium tabular-nums text-slate-900">
                  −{formatCents(forecast.total_recurring_outflow_cents)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Everyday spending</dt>
                <dd className="font-medium tabular-nums text-slate-900">
                  {forecast.include_variable
                    ? `−${formatCents(forecast.total_variable_outflow_cents)}`
                    : "excluded"}
                </dd>
              </div>
            </dl>
          </section>

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            <h2 className="border-b border-slate-200 px-5 py-3 text-sm font-semibold text-slate-900">
              Cash accounts
            </h2>
            {forecast.accounts.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-12 text-center">
                <Wallet className="h-8 w-8 text-slate-300" />
                <p className="font-medium text-slate-600">No cash accounts</p>
                <p className="max-w-sm text-sm text-slate-400">
                  A forecast needs at least one depository account to start from.
                </p>
              </div>
            ) : (
              forecast.accounts.map((account) => (
                <div
                  key={account.account_id}
                  className="flex items-center justify-between border-b border-slate-100 px-5 py-3 last:border-b-0"
                >
                  <p className="text-sm text-slate-700">
                    {account.name}
                    {account.mask && (
                      <span className="ml-1.5 text-slate-400">
                        ····{account.mask}
                      </span>
                    )}
                  </p>
                  <p className="text-sm font-medium tabular-nums text-slate-900">
                    {formatCents(account.balance_cents)}
                  </p>
                </div>
              ))
            )}
          </section>
        </>
      )}
    </main>
  );
}
