"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { usePlaidLink } from "react-plaid-link";
import { createLinkToken, setAccessToken, syncTransactions } from "@/lib/api";

interface Props {
  onLinked: () => void;
  onError: (message: string) => void;
}

export default function LinkAccountButton({ onLinked, onError }: Props) {
  const [linkToken, setLinkToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSuccess = useCallback(
    async (publicToken: string) => {
      setBusy(true);
      try {
        await setAccessToken(publicToken);
        onLinked();
      } catch (err) {
        onError(err instanceof Error ? err.message : "Failed to link account");
      } finally {
        setLinkToken(null);
        setBusy(false);
      }
    },
    [onLinked, onError],
  );

  const { open, ready } = usePlaidLink({ token: linkToken, onSuccess });

  // Plaid Link can only open once the SDK has consumed the token, so the click
  // handler fetches the token and this effect opens as soon as it is usable.
  useEffect(() => {
    if (linkToken && ready) open();
  }, [linkToken, ready, open]);

  async function handleLink() {
    setBusy(true);
    try {
      const { link_token } = await createLinkToken();
      setLinkToken(link_token);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not create link token");
    } finally {
      setBusy(false);
    }
  }

  async function handleSync() {
    setBusy(true);
    try {
      await syncTransactions();
      onLinked();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={handleSync}
        disabled={busy}
        className="flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 disabled:opacity-50"
      >
        <RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />
        Sync
      </button>
      <button
        type="button"
        onClick={handleLink}
        disabled={busy}
        className="flex items-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white transition hover:bg-slate-700 disabled:opacity-50"
      >
        <Plus className="h-4 w-4" />
        Link account
      </button>
    </div>
  );
}
