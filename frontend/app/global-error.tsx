"use client";

import { useEffect } from "react";
import { captureError } from "@/lib/telemetry";

/**
 * Fallback for errors thrown by the root layout itself.
 *
 * `error.tsx` renders *inside* the layout, so it cannot catch a layout that
 * fails to render. This one replaces the document, which is why it ships its
 * own `<html>` and `<body>` and why the styling is inline: `globals.css` is
 * pulled in by the layout that just failed, so Tailwind classes would resolve
 * to nothing here.
 *
 * In development Next.js shows its own overlay instead of this, so the only way
 * to see it is a production build.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    captureError(error, "root-layout");
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          fontFamily: "system-ui, -apple-system, sans-serif",
          margin: 0,
          padding: "4rem 1.5rem",
          color: "#1e293b",
        }}
      >
        <div style={{ margin: "0 auto", maxWidth: "36rem" }}>
          <h1 style={{ fontSize: "1.125rem", fontWeight: 600 }}>
            The application could not start.
          </h1>
          <p style={{ marginTop: "0.75rem", fontSize: "0.875rem", color: "#475569" }}>
            Your accounts and transactions have not been changed. Reloading is safe.
          </p>
          {error.digest && (
            <p
              style={{
                marginTop: "0.75rem",
                fontFamily: "ui-monospace, monospace",
                fontSize: "0.75rem",
                color: "#64748b",
              }}
            >
              Reference: {error.digest}
            </p>
          )}
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: "1.25rem",
              borderRadius: "0.5rem",
              border: "1px solid #cbd5e1",
              background: "#fff",
              padding: "0.5rem 0.75rem",
              fontSize: "0.875rem",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
