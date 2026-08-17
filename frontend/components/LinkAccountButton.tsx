"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { usePlaidLink } from "react-plaid-link";
import type { PlaidLinkOnExit, PlaidLinkOnEvent } from "react-plaid-link";
import { createLinkToken, setAccessToken, syncTransactions } from "@/lib/api";

interface Props {
  onLinked: () => void;
  onError: (message: string) => void;
}

// OAuth banks (Chase, Capital One, Fidelity...) send the browser to the bank's
// own site and back, which unmounts this component and loses React state. Plaid
// requires the *same* link token to resume, so it has to outlive the navigation
// in storage rather than memory. sessionStorage over localStorage: a token is
// single-use and expires in hours, so it should not linger in another tab.
const OAUTH_TOKEN_KEY = "plaid:oauth_link_token";

export default function LinkAccountButton({ onLinked, onError }: Props) {
  const [linkToken, setLinkToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Set only when resuming an OAuth handoff. Passing it on an ordinary open()
  // makes Plaid try to resume a flow that never started, so it stays undefined
  // in the normal path.
  const [receivedRedirectUri, setReceivedRedirectUri] = useState<string | undefined>(undefined);

  // Held in a ref so the resume effect below runs exactly once on mount. As a
  // plain dependency, a parent re-render that reallocates onError would re-fire
  // the effect and reopen Link on top of itself.
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  // Kept local instead of raised through onError. Failures here can happen
  // during mount, and the page clears its shared error banner when the
  // transaction list finishes loading — so a message reported that early is
  // wiped a few hundred milliseconds later, exactly when the user is reading it.
  const [linkError, setLinkError] = useState<string | null>(null);

  // Clears the OAuth breadcrumbs. Both the stored token and the oauth_state_id
  // are single-use, so anything that ends a Link session has to drop them or a
  // refresh replays a handoff that Plaid has already spent.
  const clearOAuthState = useCallback(() => {
    window.sessionStorage.removeItem(OAUTH_TOKEN_KEY);
    if (new URLSearchParams(window.location.search).has("oauth_state_id")) {
      window.history.replaceState({}, "", window.location.pathname);
    }
    setReceivedRedirectUri(undefined);
    setLinkToken(null);
  }, []);

  const onSuccess = useCallback(
    async (publicToken: string) => {
      setBusy(true);
      try {
        await setAccessToken(publicToken);
        onLinked();
      } catch (err) {
        onError(err instanceof Error ? err.message : "Failed to link account");
      } finally {
        clearOAuthState();
        setBusy(false);
      }
    },
    [onLinked, onError, clearOAuthState],
  );

  // Plaid reports OAuth failures, bank-side outages and plain user cancellation
  // through onExit, never onSuccess. Without this handler every one of those is
  // silent: Link closes, no item is created, and the UI just keeps showing zero
  // accounts with nothing to explain it. An exit is also the end of the session,
  // so the single-use OAuth breadcrumbs have to be dropped here too.
  const onExit = useCallback<PlaidLinkOnExit>(
    (err, metadata) => {
      if (err) {
        // display_message is Plaid's user-facing wording and is often null for
        // institution errors; error_code is always present and is the string
        // worth quoting when digging through Plaid's docs or dashboard logs.
        const detail = err.display_message || err.error_message || "";
        setLinkError(
          `Bank connection failed${detail ? `: ${detail}` : "."} (${err.error_code})` +
            (metadata?.institution?.name ? ` — ${metadata.institution.name}` : ""),
        );
        console.error("[plaid] exit with error", { err, metadata });
      } else {
        // No error means the user closed Link deliberately. Not worth an alarming
        // banner, but it still ends the session and still needs the cleanup.
        console.debug("[plaid] exit without error", metadata);
      }
      clearOAuthState();
      setBusy(false);
    },
    [clearOAuthState],
  );

  // Diagnostics only. The OAuth round trip leaves no server-side trace when it
  // fails part-way — the browser goes to the bank and simply never comes back —
  // so these breadcrumbs are the only record of how far the handoff got.
  const onEvent = useCallback<PlaidLinkOnEvent>((eventName, metadata) => {
    console.debug(`[plaid] ${eventName}`, metadata);
  }, []);

  const { open, ready } = usePlaidLink({
    token: linkToken,
    onSuccess,
    onExit,
    onEvent,
    receivedRedirectUri,
  });

  // Resume an OAuth handoff. The bank returns the browser here with an
  // oauth_state_id, and Link only picks the flow back up if it is handed both
  // the original token and the full return URL.
  useEffect(() => {
    if (!new URLSearchParams(window.location.search).has("oauth_state_id")) return;
    const stored = window.sessionStorage.getItem(OAUTH_TOKEN_KEY);
    if (!stored) {
      setLinkError(
        "Could not finish connecting your bank — this browser has no record of the request. Please click Link account and try again.",
      );
      window.history.replaceState({}, "", window.location.pathname);
      return;
    }
    setReceivedRedirectUri(window.location.href);
    setLinkToken(stored);
  }, []);

  // Plaid Link can only open once the SDK has consumed the token, so the click
  // handler fetches the token and this effect opens as soon as it is usable.
  useEffect(() => {
    if (linkToken && ready) open();
  }, [linkToken, ready, open]);

  async function handleLink() {
    setBusy(true);
    setLinkError(null);
    try {
      const { link_token } = await createLinkToken();
      // Stored before opening: if the user picks an OAuth bank, the redirect
      // away happens inside Plaid's UI with no further callback to us.
      window.sessionStorage.setItem(OAUTH_TOKEN_KEY, link_token);
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
    // Column so the message sits under the buttons instead of inline beside
    // them, and width-capped: this renders inside the header's button row, and
    // an unbounded message squeezes the sibling "Re-run rules" button.
    <div className="flex flex-col items-end gap-2">
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
      {linkError && (
        <p
          role="alert"
          className="max-w-xs rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-right text-sm text-amber-900"
        >
          {linkError}
        </p>
      )}
    </div>
  );
}
