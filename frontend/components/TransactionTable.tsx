"use client";

import { Clock, Inbox, Repeat, Wand2 } from "lucide-react";
import CategoryPicker from "@/components/CategoryPicker";
import { formatDate, formatSignedCents } from "@/lib/format";
import type { CategoryNode, Transaction } from "@/lib/types";

interface Props {
  transactions: Transaction[];
  categories: CategoryNode[];
  loading: boolean;
  total: number;
  /** Ids currently mid-PATCH, so the row's picker locks until it settles. */
  saving: Set<string>;
  onRecategorize: (txn: Transaction, categoryId: string) => void;
  onCreateRule: (txn: Transaction) => void;
}

export default function TransactionTable({
  transactions,
  categories,
  loading,
  total,
  saving,
  onRecategorize,
  onCreateRule,
}: Props) {
  if (loading) {
    return (
      <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-10 animate-pulse rounded bg-slate-100" />
        ))}
      </div>
    );
  }

  if (transactions.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-slate-300 bg-white py-16 text-center">
        <Inbox className="h-8 w-8 text-slate-300" />
        <p className="font-medium text-slate-600">No transactions match these filters</p>
        <p className="text-sm text-slate-400">
          Link an account and run a sync, or widen the filters above.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-visible rounded-xl border border-slate-200 bg-white shadow-sm">
      <table className="w-full text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-3 font-medium">Date</th>
            <th className="px-4 py-3 font-medium">Account</th>
            <th className="px-4 py-3 font-medium">Description</th>
            <th className="px-4 py-3 font-medium">Category</th>
            <th className="px-4 py-3 text-right font-medium">Amount</th>
            <th className="w-10 px-2 py-3" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {transactions.map((txn) => (
            <tr key={txn.id} className="group transition hover:bg-slate-50">
              <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                {formatDate(txn.date)}
              </td>
              <td className="whitespace-nowrap px-4 py-3">
                <span className="text-slate-700">{txn.account_name}</span>
                {txn.account_mask && (
                  <span className="ml-1 font-mono text-xs text-slate-400">
                    ••{txn.account_mask}
                  </span>
                )}
              </td>
              <td className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-slate-900">
                    {txn.merchant_display_name ?? txn.merchant_name ?? txn.description_raw}
                  </span>
                  {txn.is_pending && (
                    <Badge tone="amber" icon={<Clock className="h-3 w-3" />}>
                      Pending
                    </Badge>
                  )}
                  {txn.is_transfer && (
                    <Badge tone="slate" icon={<Repeat className="h-3 w-3" />}>
                      {txn.transfer_pair_id ? "Paired transfer" : "Transfer"}
                    </Badge>
                  )}
                  {txn.excluded_from_budget && !txn.is_transfer && (
                    <Badge tone="slate" icon={null}>
                      Off budget
                    </Badge>
                  )}
                </div>
                {txn.description_raw !== (txn.merchant_display_name ?? txn.merchant_name) && (
                  <p className="mt-0.5 truncate text-xs text-slate-400">
                    {txn.description_raw}
                  </p>
                )}
              </td>
              <td className="px-4 py-2">
                <CategoryPicker
                  value={txn.category_id}
                  source={txn.category_source}
                  categories={categories}
                  disabled={saving.has(txn.id)}
                  onChange={(categoryId) => onRecategorize(txn, categoryId)}
                />
              </td>
              <td
                className={`whitespace-nowrap px-4 py-3 text-right font-medium tabular-nums ${
                  txn.amount_cents > 0 ? "text-emerald-600" : "text-slate-900"
                }`}
              >
                {formatSignedCents(txn.amount_cents)}
              </td>
              <td className="px-2 py-3">
                <button
                  type="button"
                  onClick={() => onCreateRule(txn)}
                  title="Create a rule from this transaction"
                  className="rounded p-1 text-slate-300 opacity-0 transition group-hover:opacity-100 hover:bg-slate-200 hover:text-slate-700 focus:opacity-100"
                >
                  <Wand2 className="h-4 w-4" />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="border-t border-slate-200 bg-slate-50 px-4 py-2 text-xs text-slate-500">
        Showing {transactions.length} of {total} transactions
      </div>
    </div>
  );
}

function Badge({
  children,
  tone,
  icon,
}: {
  children: React.ReactNode;
  tone: "amber" | "slate";
  icon: React.ReactNode;
}) {
  const tones = {
    amber: "bg-amber-50 text-amber-700 ring-amber-200",
    slate: "bg-slate-100 text-slate-600 ring-slate-200",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tones[tone]}`}
    >
      {icon}
      {children}
    </span>
  );
}
