"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Overlay } from "@/components/SuggestionsModal";

/**
 * A deliberate second step in front of an irreversible delete.
 *
 * Every delete this app exposes is a hard delete on the server — there is no
 * trash, no undo, and nothing that can be re-derived from Plaid. A single
 * trash-can click one row away from the row the user meant is enough to lose a
 * budget line or a savings goal permanently.
 *
 * The confirm button is deliberately *not* autofocused, and cancel is. A dialog
 * that opens with the destructive action under the return key is a dialog that
 * gets dismissed by muscle memory into the thing it was supposed to prevent.
 *
 * For actions in a heavier class — unlinking a bank, revoking a Plaid item,
 * deleting transaction history — a plain modal is not a high enough bar. Pass
 * `confirmPhrase` there so the user has to type the name back. No such endpoint
 * exists yet; see CLAUDE.md.
 */
interface Props {
  title: string;
  /** What is actually lost. Be concrete — "this goal" is not an answer. */
  body: React.ReactNode;
  confirmLabel: string;
  /** When set, the confirm button stays disabled until this is typed exactly. */
  confirmPhrase?: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}

export default function ConfirmDestructiveModal({
  title,
  body,
  confirmLabel,
  confirmPhrase,
  onConfirm,
  onClose,
}: Props) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  const armed = confirmPhrase === undefined || typed === confirmPhrase;

  async function confirm() {
    if (!armed || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      onClose();
    } catch (err) {
      // Stay open on failure. Closing here would leave the user believing the
      // delete succeeded when the row is still there.
      setError(err instanceof Error ? err.message : "That could not be deleted.");
      setBusy(false);
    }
  }

  return (
    <Overlay onClose={busy ? () => {} : onClose} maxWidth="max-w-md">
      <div role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
        <div className="flex items-start gap-3 px-5 py-4">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-600" />
          <div className="flex-1">
            <h2 id="confirm-title" className="text-sm font-semibold text-slate-900">
              {title}
            </h2>
            <div className="mt-1.5 text-sm text-slate-600">{body}</div>

            {confirmPhrase !== undefined && (
              <label className="mt-3 block">
                <span className="text-xs text-slate-500">
                  Type <span className="font-medium text-slate-700">{confirmPhrase}</span>{" "}
                  to confirm
                </span>
                <input
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  autoComplete="off"
                  className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                />
              </label>
            )}

            {error && (
              <p className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                {error}
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 px-5 py-3">
          <button
            ref={cancelRef}
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md px-3 py-1.5 text-sm text-slate-600 transition hover:text-slate-900 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={confirm}
            disabled={!armed || busy}
            className="rounded-md bg-rose-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-rose-700 disabled:opacity-40"
          >
            {busy ? "Deleting…" : confirmLabel}
          </button>
        </div>
      </div>
    </Overlay>
  );
}
