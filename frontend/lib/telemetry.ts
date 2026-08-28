/**
 * Client-side error reporting, scrubbed before it goes anywhere.
 *
 * The browser is the worst place in this app to be careless with error text.
 * A React render error's message routinely quotes the value it choked on, and
 * on these pages that value is a merchant name or a balance. Anything a
 * reporter forwards is also anything a user might paste into a bug report, so
 * the scrubbing runs even when the destination is only the console.
 *
 * No reporting SDK is installed. `@sentry/nextjs` adds a build plugin, source
 * map upload and ~40kB to every page, which is a real cost for an app with one
 * user on localhost, and the security-relevant half of the integration is the
 * scrubber — not the transport. So the scrubber is here and tested, and the
 * transport is a slot:
 *
 *     // instrumentation-client.ts, after `npm i @sentry/nextjs`
 *     Sentry.init({ dsn, sendDefaultPii: false, beforeSend: (e) => e });
 *     setTelemetrySink((report) => Sentry.captureException(report.error, {
 *       tags: { scope: report.scope, route: report.route },
 *     }));
 *
 * Note what `report.error` still is: the original Error, unscrubbed. A sink
 * that forwards it must scrub on its own side too — `report.message` is the
 * safe rendering. Keeping the original is deliberate, because a sink that only
 * ever sees redacted text cannot group by stack.
 */

/** Anything that could be an amount, an account number or a Plaid token. */
const REDACTIONS: Array<[RegExp, string]> = [
  [/(?:access|link|public)-(?:sandbox|development|production)-[\w-]+/g, "[token]"],
  // Currency first, so "$1,234.56" is replaced whole rather than in pieces.
  [/-?\$\s?\d[\d,]*(?:\.\d+)?/g, "[amount]"],
  [/-?\b\d[\d,]*\.\d{2}\b/g, "[amount]"],
  // 4+ digit runs: account and routing numbers, card masks, ids.
  [/\b\d{4,}\b/g, "[num]"],
];

export function redact(text: string): string {
  return REDACTIONS.reduce((out, [pattern, replacement]) => out.replace(pattern, replacement), text);
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * `/transactions/2b1e…` -> `/transactions/{id}`.
 *
 * Query strings are dropped rather than templated: on this app they carry the
 * account id, the date range and whatever the user typed into search.
 */
export function templateRoute(href: string): string {
  const path = href.split("?")[0].split("#")[0];
  return path
    .split("/")
    .map((segment) => (UUID.test(segment) || /^\d+$/.test(segment) ? "{id}" : segment))
    .join("/");
}

export interface ErrorReport {
  /** Which part of the UI failed — "Account linking", "The transaction table". */
  scope: string;
  /** Templated path. Never the raw URL. */
  route: string;
  /** Redacted. Safe to display, log or forward. */
  message: string;
  /** The original, *unredacted*. For a sink that needs the real stack. */
  error: unknown;
}

type Sink = (report: ErrorReport) => void;

let sink: Sink | null = null;

/** Register a reporter. Called once at startup; see the note at the top. */
export function setTelemetrySink(next: Sink | null): void {
  sink = next;
}

/**
 * Report a caught error. Always safe to call — a throwing sink is swallowed,
 * because an error reporter that can itself break a render is a liability.
 */
export function captureError(error: unknown, scope: string): ErrorReport {
  const raw = error instanceof Error ? error.message : String(error);
  const report: ErrorReport = {
    scope,
    route: typeof window === "undefined" ? "" : templateRoute(window.location.pathname),
    message: redact(raw),
    error,
  };

  console.error(`[${scope}] ${report.message}`, error);

  try {
    sink?.(report);
  } catch {
    // Deliberately silent. Logging a failure to log invites a loop.
  }

  return report;
}
