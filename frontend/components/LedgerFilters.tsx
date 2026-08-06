"use client";

import { Search, X } from "lucide-react";
import {
  EMPTY_FILTERS,
  type Account,
  type CategoryNode,
  type LedgerFilters,
} from "@/lib/types";

interface Props {
  filters: LedgerFilters;
  accounts: Account[];
  categories: CategoryNode[];
  searchInput: string;
  onSearchInput: (value: string) => void;
  onChange: (patch: Partial<LedgerFilters>) => void;
  onReset: () => void;
}

export default function LedgerFiltersBar({
  filters,
  accounts,
  categories,
  searchInput,
  onSearchInput,
  onChange,
  onReset,
}: Props) {
  const isFiltered =
    JSON.stringify({ ...filters, search: "" }) !==
      JSON.stringify({ ...EMPTY_FILTERS, search: "" }) || filters.search !== "";

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="relative min-w-[240px] flex-1">
        <label htmlFor="ledger-search" className="mb-1 block text-xs font-medium text-slate-500">
          Search
        </label>
        <Search className="pointer-events-none absolute left-3 top-[34px] h-4 w-4 text-slate-400" />
        <input
          id="ledger-search"
          type="search"
          value={searchInput}
          onChange={(e) => onSearchInput(e.target.value)}
          placeholder="Filter by description or merchant…"
          className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
        />
      </div>

      <Field label="Account" htmlFor="filter-account">
        <select
          id="filter-account"
          value={filters.accountId ?? ""}
          onChange={(e) =>
            onChange({ accountId: e.target.value || null, accountType: null })
          }
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
        >
          <option value="">All accounts</option>
          {accounts.map((account) => (
            <option key={account.id} value={account.id}>
              {account.name}
              {account.mask ? ` ••${account.mask}` : ""}
            </option>
          ))}
        </select>
      </Field>

      <Field label="From" htmlFor="filter-start">
        <input
          id="filter-start"
          type="date"
          value={filters.startDate ?? ""}
          max={filters.endDate ?? undefined}
          onChange={(e) => onChange({ startDate: e.target.value || null })}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
        />
      </Field>

      <Field label="To" htmlFor="filter-end">
        <input
          id="filter-end"
          type="date"
          value={filters.endDate ?? ""}
          min={filters.startDate ?? undefined}
          onChange={(e) => onChange({ endDate: e.target.value || null })}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
        />
      </Field>

      <Field label="Status" htmlFor="filter-pending">
        <select
          id="filter-pending"
          value={filters.pending}
          onChange={(e) =>
            onChange({ pending: e.target.value as LedgerFilters["pending"] })
          }
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
        >
          <option value="all">All</option>
          <option value="pending">Pending only</option>
          <option value="posted">Posted only</option>
        </select>
      </Field>

      <Field label="Category" htmlFor="filter-category">
        <select
          id="filter-category"
          value={filters.categoryId ?? ""}
          onChange={(e) =>
            onChange({ categoryId: e.target.value || null, uncategorizedOnly: false })
          }
          className="max-w-[12rem] rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
        >
          <option value="">All categories</option>
          {categories.map((parent) => (
            <optgroup key={parent.id} label={parent.name}>
              <option value={parent.id}>{parent.name} (all)</option>
              {parent.children.map((child) => (
                <option key={child.id} value={child.id}>
                  {"\u00A0\u00A0"}
                  {child.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </Field>

      <label className="flex items-center gap-2 py-2 text-sm text-slate-600">
        <input
          type="checkbox"
          checked={filters.uncategorizedOnly}
          onChange={(e) =>
            onChange({ uncategorizedOnly: e.target.checked, categoryId: null })
          }
          className="rounded border-slate-300"
        />
        Needs a category
      </label>

      {isFiltered && (
        <button
          type="button"
          onClick={onReset}
          className="flex items-center gap-1 rounded-lg px-3 py-2 text-sm text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
        >
          <X className="h-4 w-4" />
          Clear
        </button>
      )}
    </div>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-xs font-medium text-slate-500">
        {label}
      </label>
      {children}
    </div>
  );
}
