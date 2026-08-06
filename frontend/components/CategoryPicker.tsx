"use client";

import type { CategoryNode, CategorySource } from "@/lib/types";

/**
 * Inline category dropdown for a ledger row.
 *
 * A native select is deliberate: the taxonomy is ~100 entries across 14 parents,
 * which optgroups render natively with keyboard type-ahead for free. The badge
 * next to it shows provenance, so the user can tell an automated guess from a
 * decision they made — the whole point of tracking `category_source`.
 */

const SOURCE_LABEL: Record<CategorySource, { text: string; className: string }> = {
  USER: { text: "You", className: "bg-slate-800 text-white" },
  RULE: { text: "Rule", className: "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200" },
  MERCHANT: {
    text: "Merchant",
    className: "bg-sky-50 text-sky-700 ring-1 ring-sky-200",
  },
  PROVIDER: {
    text: "Bank",
    className: "bg-slate-100 text-slate-600 ring-1 ring-slate-200",
  },
  UNCATEGORIZED: {
    text: "Unsorted",
    className: "bg-amber-50 text-amber-700 ring-1 ring-amber-200",
  },
};

interface Props {
  value: string | null;
  source: CategorySource | null;
  categories: CategoryNode[];
  disabled?: boolean;
  onChange: (categoryId: string) => void;
}

export default function CategoryPicker({
  value,
  source,
  categories,
  disabled,
  onChange,
}: Props) {
  return (
    <div className="flex items-center gap-1.5">
      <select
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => e.target.value && onChange(e.target.value)}
        className="max-w-[11rem] truncate rounded-md border border-transparent bg-transparent px-1.5 py-1 text-sm text-slate-700 transition hover:border-slate-300 hover:bg-white focus:border-slate-400 focus:bg-white focus:outline-none disabled:opacity-50"
      >
        {value === null && <option value="">Uncategorized</option>}
        {categories.map((parent) => (
          <optgroup key={parent.id} label={parent.name}>
            {/* The parent itself is selectable — plenty of spend belongs at the
                top level and forcing a child would be a false precision. */}
            <option value={parent.id}>{parent.name}</option>
            {parent.children.map((child) => (
              <option key={child.id} value={child.id}>
                {"\u00A0\u00A0"}
                {child.name}
              </option>
            ))}
          </optgroup>
        ))}
      </select>

      {source && (
        <span
          className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${SOURCE_LABEL[source].className}`}
          title={`Category set by: ${SOURCE_LABEL[source].text}`}
        >
          {SOURCE_LABEL[source].text}
        </span>
      )}
    </div>
  );
}
