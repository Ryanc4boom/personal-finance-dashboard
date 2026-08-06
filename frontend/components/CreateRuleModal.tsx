"use client";

import { useMemo, useState } from "react";
import { Wand2, X } from "lucide-react";
import { Overlay } from "@/components/SuggestionsModal";
import { createRule } from "@/lib/api";
import { formatCents } from "@/lib/format";
import type {
  CategoryNode,
  RuleApplyResult,
  RuleMatchType,
  Transaction,
} from "@/lib/types";

/**
 * Turn one transaction into a standing rule.
 *
 * The retroactive pass runs inside the create request, so "create and apply"
 * cannot half-succeed. Its result is reported honestly, including the count of
 * rows left alone because the user had categorised them by hand — a rule is
 * never allowed to quietly overwrite a human decision.
 */

const MATCH_LABELS: Record<RuleMatchType, string> = {
  EXACT_MERCHANT: "Merchant is exactly",
  DESCRIPTION_CONTAINS: "Description contains",
  AMOUNT_EQUALS: "Amount is exactly",
  AMOUNT_RANGE: "Amount is between",
  ACCOUNT_ID: "Account is",
};

interface Props {
  transaction: Transaction;
  categories: CategoryNode[];
  onClose: () => void;
  onApplied: () => void;
}

export default function CreateRuleModal({
  transaction,
  categories,
  onClose,
  onApplied,
}: Props) {
  const defaultMatchValue =
    transaction.merchant_display_name ??
    transaction.merchant_name ??
    transaction.description_raw;

  const [matchType, setMatchType] = useState<RuleMatchType>(
    transaction.merchant_display_name ? "EXACT_MERCHANT" : "DESCRIPTION_CONTAINS",
  );
  const [matchValue, setMatchValue] = useState(defaultMatchValue);
  const [minDollars, setMinDollars] = useState(
    (Math.abs(transaction.amount_cents) / 100).toFixed(2),
  );
  const [maxDollars, setMaxDollars] = useState(
    (Math.abs(transaction.amount_cents) / 100).toFixed(2),
  );
  const [categoryId, setCategoryId] = useState(transaction.category_id ?? "");
  const [priority, setPriority] = useState(100);
  const [retro, setRetro] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RuleApplyResult | null>(null);

  const isAmount = matchType === "AMOUNT_EQUALS" || matchType === "AMOUNT_RANGE";
  const isAccount = matchType === "ACCOUNT_ID";

  const categoryName = useMemo(() => {
    for (const parent of categories) {
      if (parent.id === categoryId) return parent.name;
      const child = parent.children.find((c) => c.id === categoryId);
      if (child) return `${parent.name} › ${child.name}`;
    }
    return null;
  }, [categories, categoryId]);

  const summary = (() => {
    if (!categoryName) return null;
    if (matchType === "AMOUNT_RANGE") {
      return `Anything between $${minDollars} and $${maxDollars} → ${categoryName}`;
    }
    if (matchType === "AMOUNT_EQUALS") return `Anything for $${minDollars} → ${categoryName}`;
    if (isAccount) return `Everything in ${transaction.account_name} → ${categoryName}`;
    return `${MATCH_LABELS[matchType]} "${matchValue}" → ${categoryName}`;
  })();

  async function submit() {
    if (!categoryId) {
      setError("Pick a category for the rule to assign");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await createRule({
        match_type: matchType,
        // Amount rules carry no text; sending the description would fail the
        // server's match-field check.
        match_value: isAmount
          ? null
          : isAccount
            ? transaction.account_id
            : matchValue.trim(),
        // Amounts are matched on the absolute value; sign is carried by direction.
        amount_min_cents: isAmount ? Math.round(Number(minDollars) * 100) : null,
        amount_max_cents:
          matchType === "AMOUNT_RANGE" ? Math.round(Number(maxDollars) * 100) : null,
        target_category_id: categoryId,
        priority,
        applies_retroactively: retro,
      });
      onApplied();
      if (res.applied) setResult(res.applied);
      else onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the rule");
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <Overlay onClose={onClose}>
        <div className="px-6 py-6">
          <h2 className="text-base font-semibold text-slate-900">Rule created</h2>
          <ul className="mt-3 space-y-1 text-sm text-slate-600">
            <li>
              <strong className="tabular-nums text-slate-900">{result.matched}</strong>{" "}
              past transactions matched
            </li>
            <li>
              <strong className="tabular-nums text-slate-900">{result.changed}</strong>{" "}
              recategorized
            </li>
            {result.skipped_user_categorized > 0 && (
              <li className="text-slate-500">
                <strong className="tabular-nums">
                  {result.skipped_user_categorized}
                </strong>{" "}
                left alone because you had already categorized them by hand
              </li>
            )}
          </ul>
          <button
            type="button"
            onClick={onClose}
            className="mt-5 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white"
          >
            Done
          </button>
        </div>
      </Overlay>
    );
  }

  return (
    <Overlay onClose={onClose}>
      <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold text-slate-900">
            <Wand2 className="h-4 w-4 text-slate-400" />
            Create a rule
          </h2>
          <p className="mt-0.5 truncate text-xs text-slate-500">
            From “{transaction.description_raw}” · {formatCents(transaction.amount_cents)}
          </p>
        </div>
        <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-700">
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="space-y-4 px-5 py-4">
        <Field label="When">
          <select
            value={matchType}
            onChange={(e) => setMatchType(e.target.value as RuleMatchType)}
            className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            {(Object.keys(MATCH_LABELS) as RuleMatchType[]).map((key) => (
              <option key={key} value={key}>
                {MATCH_LABELS[key]}
              </option>
            ))}
          </select>
        </Field>

        {!isAmount && !isAccount && (
          <Field label="Value">
            <input
              value={matchValue}
              onChange={(e) => setMatchValue(e.target.value)}
              className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
            {matchType === "EXACT_MERCHANT" && (
              <p className="mt-1 text-xs text-slate-400">
                Matched against the cleaned merchant name, so store numbers and
                “SQ *” prefixes do not need to be typed.
              </p>
            )}
          </Field>
        )}

        {isAccount && (
          <p className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">
            Applies to every transaction in{" "}
            <strong>{transaction.account_name}</strong>.
          </p>
        )}

        {isAmount && (
          <div className="flex gap-3">
            <Field label={matchType === "AMOUNT_RANGE" ? "From" : "Amount"}>
              <input
                type="number"
                step="0.01"
                min="0"
                value={minDollars}
                onChange={(e) => setMinDollars(e.target.value)}
                className="w-32 rounded-md border border-slate-300 px-2 py-1.5 text-sm tabular-nums"
              />
            </Field>
            {matchType === "AMOUNT_RANGE" && (
              <Field label="To">
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={maxDollars}
                  onChange={(e) => setMaxDollars(e.target.value)}
                  className="w-32 rounded-md border border-slate-300 px-2 py-1.5 text-sm tabular-nums"
                />
              </Field>
            )}
          </div>
        )}

        <Field label="Then categorize as">
          <select
            value={categoryId}
            onChange={(e) => setCategoryId(e.target.value)}
            className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">Pick a category…</option>
            {categories.map((parent) => (
              <optgroup key={parent.id} label={parent.name}>
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
        </Field>

        <div className="flex flex-wrap items-center gap-4">
          <Field label="Priority">
            <input
              type="number"
              min="0"
              max="10000"
              value={priority}
              onChange={(e) => setPriority(Number(e.target.value))}
              className="w-24 rounded-md border border-slate-300 px-2 py-1.5 text-sm tabular-nums"
            />
            <p className="mt-1 text-xs text-slate-400">Higher wins when rules overlap.</p>
          </Field>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={retro}
              onChange={(e) => setRetro(e.target.checked)}
              className="rounded border-slate-300"
            />
            Apply to past transactions too
          </label>
        </div>

        {summary && (
          <p className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">{summary}</p>
        )}
        {error && <p className="text-sm text-rose-600">{error}</p>}
      </div>

      <footer className="flex items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 px-5 py-3">
        <button
          type="button"
          onClick={onClose}
          className="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:text-slate-900"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={busy}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          {busy ? "Creating…" : retro ? "Create and apply" : "Create rule"}
        </button>
      </footer>
    </Overlay>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </label>
      {children}
    </div>
  );
}
