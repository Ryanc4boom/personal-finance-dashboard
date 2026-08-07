"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  AlertCircle,
  ArrowLeft,
  ExternalLink,
  Info,
  Loader2,
  Quote,
} from "lucide-react";
import FlagBadge, { STATUS_STYLES } from "@/components/FlagBadge";
import FundamentalsChart from "@/components/FundamentalsChart";
import ScorecardStage from "@/components/ScorecardStage";
import TickerSearch from "@/components/TickerSearch";
import { getResearchReport } from "@/lib/api";
import {
  formatBps,
  formatCents,
  formatCentsCompact,
  formatDate,
} from "@/lib/format";
import type {
  AnnualRow,
  CheckStatus,
  Evidence,
  ResearchReport,
} from "@/lib/types";

const SUMMARY_ORDER: CheckStatus[] = ["PASS", "WARN", "FAIL", "UNKNOWN"];

const PRICE_SOURCE_LABELS: Record<string, string> = {
  OVERRIDE: "your entered price",
  PORTFOLIO: "your portfolio's stored close",
  FINNHUB: "Finnhub",
};

export default function TickerResearchPage() {
  const params = useParams<{ ticker: string }>();
  const ticker = (params.ticker ?? "").toUpperCase();

  const [report, setReport] = useState<ResearchReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Committed override, in cents. Separate from the input text so a half-typed
  // price never triggers a fetch of a 30MB filing.
  const [priceOverride, setPriceOverride] = useState<number | null>(null);
  const [priceInput, setPriceInput] = useState("");

  const load = useCallback(
    (override: number | null) => {
      if (!ticker) return;
      setLoading(true);
      setError(null);
      getResearchReport(ticker, override ?? undefined)
        .then(setReport)
        .catch((err: Error) => {
          setReport(null);
          setError(err.message);
        })
        .finally(() => setLoading(false));
    },
    [ticker],
  );

  useEffect(() => {
    load(priceOverride);
  }, [load, priceOverride]);

  function applyPrice(event: React.FormEvent) {
    event.preventDefault();
    const dollars = Number(priceInput);
    setPriceOverride(
      Number.isFinite(dollars) && dollars > 0 ? Math.round(dollars * 100) : null,
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
        <Link
          href="/research"
          className="flex shrink-0 items-center gap-1.5 text-sm font-medium text-slate-500 transition hover:text-slate-900"
        >
          <ArrowLeft className="h-4 w-4" />
          Research
        </Link>
        <div className="flex-1">
          <TickerSearch placeholder="Look up another ticker" />
        </div>
      </div>

      {loading && (
        <div className="mt-16 flex flex-col items-center gap-3 text-slate-500">
          <Loader2 className="h-6 w-6 animate-spin" />
          <p className="text-sm">
            Reading {ticker}&apos;s XBRL facts, latest 10-K and proxy from SEC
            EDGAR.
          </p>
          <p className="text-xs text-slate-400">
            First run for a filer downloads the filings themselves; later ones
            are served from cache.
          </p>
        </div>
      )}

      {error && !loading && (
        <div className="mt-10 flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 px-5 py-4">
          <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-rose-500" />
          <div>
            <p className="text-sm font-medium text-rose-900">
              No report for {ticker}
            </p>
            <p className="mt-1 text-sm leading-relaxed text-rose-700">{error}</p>
          </div>
        </div>
      )}

      {report && !loading && (
        <>
          <header className="mt-8 flex flex-wrap items-end justify-between gap-4">
            <div>
              <h1 className="text-3xl font-semibold tracking-tight text-slate-900">
                {report.ticker}
              </h1>
              <p className="mt-1 text-sm text-slate-500">
                {report.company_name} · CIK {report.cik} · generated{" "}
                {formatDate(report.generated_at)}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {SUMMARY_ORDER.filter(
                (status) => (report.summary_counts[status] ?? 0) > 0,
              ).map((status) => (
                <FlagBadge
                  key={status}
                  status={status}
                  label={`${report.summary_counts[status]} ${STATUS_STYLES[status].label.toLowerCase()}`}
                />
              ))}
            </div>
          </header>

          <ValuationBar
            report={report}
            priceInput={priceInput}
            onPriceInput={setPriceInput}
            onApply={applyPrice}
          />

          {report.notes.length > 0 && (
            <ul className="mt-6 space-y-2">
              {report.notes.map((note) => (
                <li
                  key={note}
                  className="flex items-start gap-2.5 rounded-lg bg-slate-50 px-4 py-3 text-xs leading-relaxed text-slate-600"
                >
                  <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
                  {note}
                </li>
              ))}
            </ul>
          )}

          <div className="mt-6 space-y-4">
            {report.stages.map((stage) => (
              <ScorecardStage key={stage.key} stage={stage} />
            ))}
          </div>

          <section className="mt-8 rounded-xl border border-slate-200 bg-white p-5">
            <h2 className="text-base font-semibold text-slate-900">
              Six years of fundamentals
            </h2>
            <p className="mt-1 mb-4 text-sm text-slate-500">
              Straight from the filer&apos;s annual XBRL facts, taken from the
              most recent filing that restated each year.
            </p>
            <FundamentalsChart rows={report.annuals} />
            <AnnualTable report={report} />
          </section>

          <FilingEvidence report={report} />

          {report.sources.length > 0 && (
            <section className="mt-8">
              <h2 className="text-sm font-semibold text-slate-900">
                Primary documents
              </h2>
              <ul className="mt-2 space-y-1.5">
                {report.sources.map((source) => (
                  <li key={source.document_url}>
                    <a
                      href={source.document_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1.5 text-sm text-slate-600 underline decoration-slate-300 underline-offset-2 transition hover:text-slate-900"
                    >
                      {source.form} filed {formatDate(source.filed)}
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function ValuationBar({
  report,
  priceInput,
  onPriceInput,
  onApply,
}: {
  report: ResearchReport;
  priceInput: string;
  onPriceInput: (value: string) => void;
  onApply: (event: React.FormEvent) => void;
}) {
  const price = report.price;
  return (
    <section className="mt-6 rounded-xl border border-slate-200 bg-white">
      <div className="grid divide-y divide-slate-100 sm:grid-cols-4 sm:divide-x sm:divide-y-0">
        <Metric
          label="Share price"
          value={price ? formatCents(price.price_cents) : "—"}
          hint={
            price
              ? `from ${PRICE_SOURCE_LABELS[price.source] ?? price.source}${
                  price.is_stale_close && price.as_of
                    ? `, ${formatDate(price.as_of)}`
                    : ""
                }`
              : "no source configured"
          }
        />
        <Metric
          label="Market cap"
          value={
            report.market_cap_cents
              ? formatCentsCompact(report.market_cap_cents)
              : "—"
          }
          hint="price × latest diluted shares"
        />
        <Metric
          label="P/E"
          value={report.pe_ratio ?? "—"}
          hint="trailing full fiscal year"
        />
        <Metric
          label="PEG"
          value={report.peg_ratio ?? "—"}
          hint={
            report.eps_growth_bps === null
              ? "needs positive earnings growth"
              : `on realised EPS growth of ${formatBps(report.eps_growth_bps)}`
          }
        />
      </div>
      <form
        onSubmit={onApply}
        className="flex flex-wrap items-center gap-2 border-t border-slate-100 px-5 py-3"
      >
        <label htmlFor="price" className="text-xs text-slate-500">
          Value it at a different price
        </label>
        <div className="relative">
          <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-slate-400">
            $
          </span>
          <input
            id="price"
            inputMode="decimal"
            value={priceInput}
            onChange={(event) => onPriceInput(event.target.value)}
            placeholder="180.00"
            className="w-28 rounded-md border border-slate-300 py-1.5 pl-6 pr-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
          />
        </div>
        <button
          type="submit"
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-slate-700"
        >
          Revalue
        </button>
      </form>
    </section>
  );
}

function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="px-5 py-4">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
      </p>
      <p className="mt-1 text-xl font-semibold tabular-nums text-slate-900">
        {value}
      </p>
      <p className="mt-0.5 text-xs text-slate-400">{hint}</p>
    </div>
  );
}

/**
 * Money here is abbreviated, not exact.
 *
 * Six fiscal years of "$130,497,000,000.00" is 150 characters of digits per row
 * and no reader compares them that way — the question the table answers is
 * which direction the line is going. The exact figure for any single year is on
 * the chart's hover readout above.
 */
const money = (cents: number | null) =>
  cents === null ? "—" : formatCentsCompact(cents);

const COLUMNS: { label: string; pick: (r: AnnualRow) => string }[] = [
  { label: "Revenue", pick: (r) => money(r.revenue_cents) },
  { label: "Gross margin", pick: (r) => formatBps(r.gross_margin_bps) },
  { label: "Operating income", pick: (r) => money(r.operating_income_cents) },
  { label: "Net income", pick: (r) => money(r.net_income_cents) },
  { label: "Free cash flow", pick: (r) => money(r.free_cash_flow_cents) },
  { label: "Diluted EPS", pick: (r) => (r.diluted_eps ? `$${r.diluted_eps}` : "—") },
  {
    label: "Diluted shares",
    pick: (r) =>
      r.diluted_shares === null
        ? "—"
        : `${(r.diluted_shares / 1_000_000).toFixed(0)}M`,
  },
  { label: "Cash & ST investments", pick: (r) => money(r.cash_and_sti_cents) },
  { label: "Total debt", pick: (r) => money(r.total_debt_cents) },
  { label: "Equity", pick: (r) => money(r.equity_cents) },
];

function AnnualTable({ report }: { report: ResearchReport }) {
  return (
    <div className="mt-6 -mx-5 overflow-x-auto px-5">
      <table className="w-full min-w-[720px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left">
            <th className="py-2 pr-4 font-medium text-slate-500">Fiscal year</th>
            {report.annuals.map((row) => (
              <th
                key={row.fiscal_label}
                className="py-2 pl-4 text-right font-semibold text-slate-900"
              >
                {row.fiscal_label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {COLUMNS.map((column) => (
            <tr key={column.label} className="border-b border-slate-100">
              <td className="py-2 pr-4 whitespace-nowrap text-slate-500">
                {column.label}
              </td>
              {report.annuals.map((row) => (
                <td
                  key={row.fiscal_label}
                  className="py-2 pl-4 text-right tabular-nums text-slate-800"
                >
                  {column.pick(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The passages Stage 1's heuristics actually matched.
 *
 * Business model, customer concentration and insider ownership are read out of
 * prose and HTML tables, not tagged data — which means they can be wrong in
 * ways a number never is. Showing the sentence behind each verdict is the only
 * thing that makes them checkable rather than merely assertive.
 */
function FilingEvidence({ report }: { report: ResearchReport }) {
  const blocks: { title: string; summary: string; evidence: Evidence[] }[] = [];

  if (report.business_model) {
    const model = report.business_model;
    blocks.push({
      title: "Revenue durability",
      summary: `${model.classification} · ${model.confidence.toLowerCase()} confidence · ${model.recurring_score} recurring signals against ${model.transactional_score} one-off`,
      evidence: model.evidence,
    });
  }
  if (report.concentration) {
    const c = report.concentration;
    blocks.push({
      title: "Customer concentration",
      summary:
        c.max_customer_bps === null
          ? `${c.status} · ${c.confidence.toLowerCase()} confidence`
          : `Largest single customer ${formatBps(c.max_customer_bps)} of revenue · ${c.confidence.toLowerCase()} confidence`,
      evidence: c.evidence,
    });
  }
  if (report.insider) {
    const i = report.insider;
    blocks.push({
      title: "Insider & executive ownership",
      summary: [
        i.status === "BELOW_ONE_PERCENT"
          ? "Under 1%"
          : i.group_bps !== null
            ? formatBps(i.group_bps, 2)
            : "Not found",
        i.group_person_count ? `${i.group_person_count} people` : null,
        `${i.confidence.toLowerCase()} confidence`,
      ]
        .filter(Boolean)
        .join(" · "),
      evidence: i.evidence,
    });
  }

  const withEvidence = blocks.filter((block) => block.evidence.length > 0);
  if (withEvidence.length === 0) return null;

  return (
    <section className="mt-8">
      <h2 className="text-base font-semibold text-slate-900">
        What the filings actually said
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        Stage 1 reads prose and HTML tables rather than tagged data, so every
        verdict ships the passage it matched.
      </p>
      <div className="mt-4 space-y-4">
        {withEvidence.map((block) => (
          <div
            key={block.title}
            className="rounded-xl border border-slate-200 bg-white p-5"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-sm font-medium text-slate-900">
                {block.title}
              </h3>
              <p className="text-xs text-slate-500">{block.summary}</p>
            </div>
            <ul className="mt-3 space-y-2.5">
              {block.evidence.slice(0, 4).map((item, index) => (
                <li
                  key={`${item.matched}-${index}`}
                  className="flex gap-2.5 border-l-2 border-slate-200 pl-3"
                >
                  <Quote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-300" />
                  <p className="text-xs leading-relaxed text-slate-600">
                    {item.text}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
