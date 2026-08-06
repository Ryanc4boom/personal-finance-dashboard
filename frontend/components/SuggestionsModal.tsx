"use client";

import { useEffect, useState } from "react";
import { Sparkles, X } from "lucide-react";
import { applySuggestions, getSuggestions } from "@/lib/api";
import { formatCents } from "@/lib/format";
import type { BudgetSuggestion } from "@/lib/types";

/**
 * Suggestions are previewed before they are applied. Silently rewriting limits
 * a user has tuned by hand is the one thing this feature must never do, so
 * overwriting is a separate, off-by-default checkbox and the existing limit is
 * shown next to the proposal.
 */

interface Props {
  onClose: () => void;
  onApplied: () => void;
}

export default function SuggestionsModal({ onClose, onApplied }: Props) {
  const [suggestions, setSuggestions] = useState<BudgetSuggestion[] | null>(null);
  const [months, setMonths] = useState(3);
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSuggestions()
      .then((res) => {
        setSuggestions(res.suggestions);
        setMonths(res.months_analyzed);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "Could not load suggestions"),
      );
  }, []);

  const conflicts = (suggestions ?? []).filter((s) => s.current_limit_cents !== null);

  async function apply() {
    setBusy(true);
    try {
      await applySuggestions(overwrite);
      onApplied();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not apply suggestions");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Overlay onClose={onClose}>
      <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold text-slate-900">
            <Sparkles className="h-4 w-4 text-slate-400" />
            Suggested limits
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            The median of your last {months} complete months per category. The median
            rather than the average, so one unusual month does not set your budget.
          </p>
        </div>
        <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-700">
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="max-h-96 overflow-y-auto">
        {error && <p className="px-5 py-4 text-sm text-rose-600">{error}</p>}

        {suggestions === null && !error && (
          <p className="px-5 py-8 text-center text-sm text-slate-400">Analysing history…</p>
        )}

        {suggestions !== null && suggestions.length === 0 && (
          <p className="px-5 py-8 text-center text-sm text-slate-500">
            Not enough complete months of spending yet. Suggestions ignore the
            current month on purpose — a half-finished month lowballs every limit.
          </p>
        )}

        {suggestions !== null && suggestions.length > 0 && (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-5 py-2 font-medium">Category</th>
                <th className="px-3 py-2 text-right font-medium">Monthly spend</th>
                <th className="px-3 py-2 text-right font-medium">Current</th>
                <th className="px-5 py-2 text-right font-medium">Suggested</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {suggestions.map((s) => (
                <tr key={s.category_id}>
                  <td className="px-5 py-2 font-medium text-slate-800">{s.category_name}</td>
                  <td className="px-3 py-2 text-right font-mono text-xs text-slate-400">
                    {s.monthly_spend_cents.map((c) => formatCents(c)).join(" · ")}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                    {s.current_limit_cents === null ? "—" : formatCents(s.current_limit_cents)}
                  </td>
                  <td className="px-5 py-2 text-right font-medium tabular-nums text-slate-900">
                    {formatCents(s.suggested_limit_cents)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-slate-50 px-5 py-3">
        <label className="flex items-center gap-2 text-xs text-slate-600">
          <input
            type="checkbox"
            checked={overwrite}
            onChange={(e) => setOverwrite(e.target.checked)}
            className="rounded border-slate-300"
          />
          Replace limits I have already set
          {conflicts.length > 0 && (
            <span className="text-slate-400">({conflicts.length} would change)</span>
          )}
        </label>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:text-slate-900"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={apply}
            disabled={busy || !suggestions?.length}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {busy ? "Applying…" : "Apply limits"}
          </button>
        </div>
      </footer>
    </Overlay>
  );
}

export function Overlay({
  children,
  onClose,
}: {
  children: React.ReactNode;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/40 p-4 pt-20"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl overflow-hidden rounded-xl bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}
