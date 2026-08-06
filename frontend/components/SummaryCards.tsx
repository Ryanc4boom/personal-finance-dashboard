"use client";

import { CreditCard, Landmark, Scale, Wallet } from "lucide-react";
import { formatCents } from "@/lib/format";
import type { Account, AccountSummary, AccountType } from "@/lib/types";

interface Props {
  summary: AccountSummary | null;
  activeAccountId: string | null;
  activeAccountType: AccountType | null;
  onSelectAccount: (accountId: string) => void;
  onSelectType: (type: AccountType) => void;
}

/**
 * Traceability: every balance shown here is clickable and drives the table
 * filter, so a number on screen can always be traced to the rows behind it.
 */
export default function SummaryCards({
  summary,
  activeAccountId,
  activeAccountType,
  onSelectAccount,
  onSelectType,
}: Props) {
  if (!summary) {
    return (
      <div className="grid gap-4 sm:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-28 animate-pulse rounded-xl bg-white shadow-sm" />
        ))}
      </div>
    );
  }

  const cash = summary.accounts.filter((a) => a.type === "depository");
  const credit = summary.accounts.filter((a) => a.type === "credit");

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <TotalCard
          label="Depository Cash"
          value={summary.depository_cash_cents}
          count={cash.length}
          icon={<Landmark className="h-5 w-5" />}
          tone="cash"
          active={activeAccountType === "depository" && !activeAccountId}
          onClick={() => onSelectType("depository")}
        />
        <TotalCard
          label="Credit Card Liabilities"
          value={summary.credit_liabilities_cents}
          count={credit.length}
          icon={<CreditCard className="h-5 w-5" />}
          tone="credit"
          active={activeAccountType === "credit" && !activeAccountId}
          onClick={() => onSelectType("credit")}
        />
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center gap-2 text-slate-500">
            <Scale className="h-5 w-5" />
            <span className="text-sm font-medium">Net Position</span>
          </div>
          <p
            className={`mt-3 text-2xl font-semibold tabular-nums ${
              summary.net_cents < 0 ? "text-rose-600" : "text-slate-900"
            }`}
          >
            {formatCents(summary.net_cents)}
          </p>
          <p className="mt-1 text-xs text-slate-400">Cash less credit balances</p>
        </div>
      </div>

      {summary.accounts.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {summary.accounts.map((account) => (
            <AccountChip
              key={account.id}
              account={account}
              active={activeAccountId === account.id}
              onClick={() => onSelectAccount(account.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function TotalCard({
  label,
  value,
  count,
  icon,
  tone,
  active,
  onClick,
}: {
  label: string;
  value: number;
  count: number;
  icon: React.ReactNode;
  tone: "cash" | "credit";
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-xl border bg-white p-5 text-left shadow-sm transition hover:border-slate-400 hover:shadow ${
        active ? "border-slate-900 ring-1 ring-slate-900" : "border-slate-200"
      }`}
    >
      <div
        className={`flex items-center gap-2 ${
          tone === "cash" ? "text-emerald-600" : "text-rose-600"
        }`}
      >
        {icon}
        <span className="text-sm font-medium text-slate-500">{label}</span>
      </div>
      <p className="mt-3 text-2xl font-semibold tabular-nums text-slate-900">
        {formatCents(value)}
      </p>
      <p className="mt-1 text-xs text-slate-400">
        {count} {count === 1 ? "account" : "accounts"} · click to filter
      </p>
    </button>
  );
}

function AccountChip({
  account,
  active,
  onClick,
}: {
  account: Account;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-2 rounded-lg border bg-white px-3 py-2 text-left text-sm transition hover:border-slate-400 ${
        active ? "border-slate-900 ring-1 ring-slate-900" : "border-slate-200"
      }`}
    >
      <Wallet className="h-4 w-4 shrink-0 text-slate-400" />
      <span className="font-medium text-slate-700">{account.name}</span>
      {account.mask && (
        <span className="font-mono text-xs text-slate-400">••{account.mask}</span>
      )}
      <span className="tabular-nums text-slate-900">
        {formatCents(account.current_balance_cents)}
      </span>
    </button>
  );
}
