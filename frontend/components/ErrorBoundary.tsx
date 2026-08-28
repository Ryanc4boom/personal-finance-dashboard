"use client";

import { Component, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

interface Props {
  children: ReactNode;
  /** Named in the fallback so the user can tell *what* broke, not just that something did. */
  label: string;
  /**
   * Run when the user retries, for state the boundary cannot reach — refetching
   * a list, clearing a stale token. Remounting alone re-runs effects but does
   * not undo whatever the parent is holding.
   */
  onRetry?: () => void;
}

interface State {
  error: Error | null;
}

/**
 * Catches render errors in one part of the page instead of the whole page.
 *
 * Next.js `error.tsx` already covers a route segment, but it replaces the
 * *entire* route: one broken widget and the user loses the whole dashboard. This
 * is for wrapping the pieces where that trade is wrong — the Plaid Link button,
 * the transaction table — so a failure there leaves the rest of the page usable.
 *
 * Still a class component: as of React 19 there is no hook equivalent, because
 * `componentDidCatch`/`getDerivedStateFromError` have no function-component API.
 *
 * Note what this does *not* catch, since assuming otherwise leaves gaps that
 * look like the boundary failing: errors in event handlers and in async code
 * (a rejected fetch in an onClick) never reach a boundary. Those still need
 * try/catch at the call site, which is why the API helpers throw typed errors
 * the callers surface as banners.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    // Kept to the console rather than sent anywhere: a component tree in this
    // app can hold balances and merchant names, and this payload is not
    // scrubbed. Telemetry goes through lib/telemetry.ts, which is.
    console.error(`[error-boundary] ${this.props.label}`, error, info);
  }

  handleRetry = () => {
    this.setState({ error: null });
    this.props.onRetry?.();
  };

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div
        role="alert"
        className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900"
      >
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div className="flex-1">
            <p className="font-medium">{this.props.label} could not be displayed.</p>
            <p className="mt-1 text-amber-800">
              The rest of the page is still usable. This is a display problem — your
              accounts and transactions have not been changed.
            </p>
            <button
              type="button"
              onClick={this.handleRetry}
              className="mt-3 inline-flex items-center gap-2 rounded-lg border border-amber-400 bg-white px-3 py-1.5 font-medium text-amber-900 transition hover:border-amber-500"
            >
              <RefreshCw className="h-4 w-4" />
              Try again
            </button>
          </div>
        </div>
      </div>
    );
  }
}
