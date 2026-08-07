"use client";

import Link from "next/link";
import { FileText, Landmark, Scale, TrendingUp, Wallet } from "lucide-react";
import TickerSearch from "@/components/TickerSearch";

const EXAMPLES = ["NVDA", "MSFT", "AAPL", "GOOGL", "COST", "JPM"];

const STAGES = [
  {
    icon: FileText,
    title: "Qualitative & Moat",
    body: "Item 1 and Item 1A of the latest 10-K for recurring versus one-off revenue, any single customer above 10% of revenue, and the directors-and-officers ownership total from the proxy.",
  },
  {
    icon: TrendingUp,
    title: "Income Statement & Growth",
    body: "Five-year revenue CAGR against a 10% bar, the direction of gross and operating margin, and whether the diluted share count is shrinking or being diluted.",
  },
  {
    icon: Scale,
    title: "Balance Sheet Health",
    body: "Cash and short-term investments against long-term debt, debt-to-equity below 1.5x, and operating income covering interest at least 5x.",
  },
  {
    icon: Wallet,
    title: "Cash Flow & Valuation",
    body: "Free cash flow against reported net income, return on equity above 15%, and the P/E and PEG the market is charging for the growth.",
  },
];

export default function ResearchPage() {
  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="text-center">
        <h1 className="text-3xl font-semibold tracking-tight text-slate-900">
          Stock research
        </h1>
        <p className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-slate-500">
          A four-stage fundamental scorecard built from the company&apos;s own SEC
          filings — annual XBRL facts, the latest 10-K and the latest proxy. No
          vendor summaries, and no number without the filing it came from.
        </p>
      </header>

      <div className="mt-8">
        <TickerSearch autoFocus />
        <div className="mt-3 flex flex-wrap items-center justify-center gap-2 text-sm">
          <span className="text-slate-400">Try</span>
          {EXAMPLES.map((ticker) => (
            <Link
              key={ticker}
              href={`/research/${ticker}`}
              className="rounded-md border border-slate-200 bg-white px-2.5 py-1 font-medium text-slate-700 transition hover:border-slate-900 hover:text-slate-900"
            >
              {ticker}
            </Link>
          ))}
        </div>
      </div>

      <div className="mt-10 grid gap-4 sm:grid-cols-2">
        {STAGES.map(({ icon: Icon, title, body }, index) => (
          <section
            key={title}
            className="rounded-xl border border-slate-200 bg-white p-5"
          >
            <div className="flex items-center gap-2.5">
              <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-900">
                <Icon className="h-4 w-4 text-white" />
              </span>
              <h2 className="text-sm font-semibold text-slate-900">
                {index + 1}. {title}
              </h2>
            </div>
            <p className="mt-2.5 text-sm leading-relaxed text-slate-500">
              {body}
            </p>
          </section>
        ))}
      </div>

      <p className="mt-8 flex items-start gap-2 rounded-lg bg-slate-50 px-4 py-3 text-xs leading-relaxed text-slate-500">
        <Landmark className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
        <span>
          Checks whose input a filer never tagged report as{" "}
          <strong className="font-medium text-slate-700">not available</strong>{" "}
          rather than passing. A screener that fills a gap with a guess produces
          a ratio indistinguishable from a real one, so this one leaves the cell
          empty and says why.
        </span>
      </p>
    </div>
  );
}
