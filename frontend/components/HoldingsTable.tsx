"use client";

import { Fragment, useMemo, useState } from "react";
import { ChevronRight, HelpCircle } from "lucide-react";
import { ASSET_CLASS_COLORS, ASSET_CLASS_LABELS } from "@/components/AllocationDonut";
import { formatCents, formatSignedBps, formatSignedCents, titleCase } from "@/lib/format";
import type { Position } from "@/lib/types";

type SortKey = "value" | "gain" | "ticker" | "day";

const HEADERS: { key: SortKey | null; label: string; align: "left" | "right" }[] = [
  { key: "ticker", label: "Ticker", align: "left" },
  { key: null, label: "Name", align: "left" },
  { key: null, label: "Quantity", align: "right" },
  { key: null, label: "Price", align: "right" },
  { key: "value", label: "Market value", align: "right" },
  { key: null, label: "Cost basis", align: "right" },
  { key: "gain", label: "Gain / loss", align: "right" },
  { key: "day", label: "1-day", align: "right" },
];

function toneFor(cents: number | null): string {
  if (cents === null) return "text-slate-400";
  if (cents > 0) return "text-emerald-600";
  if (cents < 0) return "text-rose-600";
  return "text-slate-500";
}

/**
 * Every position, with the accounts holding it collapsed underneath.
 *
 * The same security in a Roth and a taxable brokerage is *one* row here — that
 * is the portfolio-level view of the position — but the tax treatment of the
 * two lots is completely different, so the split is one click away rather than
 * hidden.
 *
 * A null cost basis renders as "not reported", never as $0. Showing zero would
 * report the whole position as gain, which is wrong in the direction that ends
 * up on a tax return.
 */
export default function HoldingsTable({ positions }: { positions: Position[] }) {
  const [sort, setSort] = useState<SortKey>("value");
  const [expanded, setExpanded] = useState<string | null>(null);

  const rows = useMemo(() => {
    const copy = [...positions];
    copy.sort((a, b) => {
      switch (sort) {
        case "ticker":
          return (a.ticker_symbol ?? a.name).localeCompare(b.ticker_symbol ?? b.name);
        case "gain":
          return (b.gain_cents ?? -Infinity) - (a.gain_cents ?? -Infinity);
        case "day":
          return (b.day_change_cents ?? -Infinity) - (a.day_change_cents ?? -Infinity);
        default:
          return b.value_cents - a.value_cents;
      }
    });
    return copy;
  }, [positions, sort]);

  if (positions.length === 0) {
    return (
      <p className="px-5 py-12 text-center text-sm text-slate-400">
        No holdings yet. Link a brokerage account to see positions here.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
            <th className="w-8" />
            {HEADERS.map(({ key, label, align }) => (
              <th
                key={label}
                className={`px-3 py-2 font-medium ${
                  align === "right" ? "text-right" : "text-left"
                } ${key ? "cursor-pointer select-none hover:text-slate-900" : ""} ${
                  key === sort ? "text-slate-900" : ""
                }`}
                onClick={key ? () => setSort(key) : undefined}
              >
                {label}
                {key === sort && " ↓"}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((position) => {
            const open = expanded === position.security_id;
            const split = position.accounts.length > 1;
            return (
              <Fragment key={position.security_id}>
                <tr
                  className={`border-b border-slate-100 ${
                    split ? "cursor-pointer hover:bg-slate-50" : ""
                  }`}
                  onClick={
                    split
                      ? () => setExpanded(open ? null : position.security_id)
                      : undefined
                  }
                >
                  <td className="pl-3">
                    {split && (
                      <ChevronRight
                        className={`h-4 w-4 text-slate-400 transition-transform ${
                          open ? "rotate-90" : ""
                        }`}
                      />
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="font-medium text-slate-900">
                      {position.ticker_symbol ?? "—"}
                    </span>
                  </td>
                  <td className="max-w-xs px-3 py-2.5">
                    <p className="truncate text-slate-700">{position.name}</p>
                    <span className="flex items-center gap-1.5 text-xs text-slate-400">
                      <span
                        className="h-1.5 w-1.5 rounded-sm"
                        style={{
                          background: ASSET_CLASS_COLORS[position.asset_class] ?? "#94a3b8",
                        }}
                      />
                      {ASSET_CLASS_LABELS[position.asset_class] ??
                        titleCase(position.asset_class)}
                      {split && ` · ${position.accounts.length} accounts`}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">
                    {position.quantity}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">
                    {formatCents(position.price_cents)}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums font-medium text-slate-900">
                    {formatCents(position.value_cents)}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">
                    {position.cost_basis_cents === null ? (
                      <span
                        className="inline-flex items-center gap-1 text-slate-400"
                        title="The custodian did not report a cost basis for this position — commonly the case after a transfer. It is excluded from the gain figures rather than assumed to be zero."
                      >
                        not reported
                        <HelpCircle className="h-3 w-3" />
                      </span>
                    ) : (
                      formatCents(position.cost_basis_cents)
                    )}
                  </td>
                  <td
                    className={`px-3 py-2.5 text-right tabular-nums font-medium ${toneFor(position.gain_cents)}`}
                  >
                    {position.gain_cents === null ? (
                      // Two different reasons land on a dash, and a gain column
                      // is the wrong place to leave the user guessing which:
                      // a cash equivalent has no return to report, whereas an
                      // unreported basis means the return is unknown.
                      <span
                        title={
                          position.cost_basis_cents === null
                            ? "No cost basis reported, so the gain cannot be computed."
                            : "Cash equivalents hold their value by construction and are excluded from return."
                        }
                      >
                        —
                      </span>
                    ) : (
                      <>
                        {formatSignedCents(position.gain_cents)}
                        <span className="ml-1 text-xs font-normal opacity-70">
                          {formatSignedBps(position.gain_bps)}
                        </span>
                      </>
                    )}
                  </td>
                  <td
                    className={`px-3 py-2.5 text-right tabular-nums ${toneFor(position.day_change_cents)}`}
                  >
                    {position.day_change_cents === null
                      ? "—"
                      : formatSignedCents(position.day_change_cents)}
                  </td>
                </tr>

                {open &&
                  position.accounts.map((lot) => (
                    <tr
                      key={lot.account_id}
                      className="border-b border-slate-100 bg-slate-50/60 text-xs"
                    >
                      <td />
                      <td className="px-3 py-1.5" />
                      <td className="px-3 py-1.5 text-slate-600">
                        {lot.account_name}
                        {lot.account_mask && (
                          <span className="ml-1 text-slate-400">····{lot.account_mask}</span>
                        )}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-slate-500">
                        {lot.quantity}
                      </td>
                      <td />
                      <td className="px-3 py-1.5 text-right tabular-nums text-slate-700">
                        {formatCents(lot.value_cents)}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-slate-500">
                        {lot.cost_basis_cents === null
                          ? "not reported"
                          : formatCents(lot.cost_basis_cents)}
                      </td>
                      <td colSpan={2} />
                    </tr>
                  ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
