"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { captureError } from "@/lib/telemetry";

/**
 * Route-segment fallback: catches anything a page throws that the in-page
 * `ErrorBoundary` components did not already contain.
 *
 * This replaces the whole route, which is why the granular boundaries exist —
 * by the time a user sees *this*, the page is gone. Treat it as the last line
 * rather than the intended one.
 *
 * `NavBar` still renders: this sits inside the root layout, so the user is not
 * stranded on a page with no way out.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    captureError(error, "route");
  }, [error]);

  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <div className="rounded-xl border border-amber-300 bg-amber-50 p-6 text-amber-900">
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-6 w-6 shrink-0" />
          <div className="flex-1">
            <h1 className="text-lg font-semibold">This page could not be displayed.</h1>
            <p className="mt-2 text-sm text-amber-800">
              Something failed while rendering. Your accounts and transactions have not
              been changed — this is a display problem, and retrying is safe.
            </p>
            {error.digest && (
              // The digest is the only handle on the server-side stack, which
              // Next.js deliberately withholds from the client in production.
              <p className="mt-2 font-mono text-xs text-amber-700">
                Reference: {error.digest}
              </p>
            )}
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={reset}
                className="inline-flex items-center gap-2 rounded-lg border border-amber-400 bg-white px-3 py-2 text-sm font-medium text-amber-900 transition hover:border-amber-500"
              >
                <RefreshCw className="h-4 w-4" />
                Try again
              </button>
              <Link
                href="/"
                className="rounded-lg px-3 py-2 text-sm font-medium text-amber-800 underline underline-offset-4 transition hover:text-amber-900"
              >
                Back to the ledger
              </Link>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
