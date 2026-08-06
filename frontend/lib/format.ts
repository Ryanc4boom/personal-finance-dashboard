/**
 * Display helpers. Cents are the only representation that crosses the wire;
 * the division by 100 happens here and nowhere else.
 */

const CURRENCY = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

export function formatCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return CURRENCY.format(cents / 100);
}

/** Signed form for ledger rows: "+$1,500.00" / "-$89.40". */
export function formatSignedCents(cents: number): string {
  const formatted = CURRENCY.format(Math.abs(cents) / 100);
  if (cents === 0) return formatted;
  return `${cents > 0 ? "+" : "-"}${formatted}`;
}

/** Axis-sized money: "$1.2k", "-$840". Never for a figure read as an exact total. */
export function formatCentsCompact(cents: number): string {
  const dollars = cents / 100;
  const sign = dollars < 0 ? "-" : "";
  const abs = Math.abs(dollars);
  if (abs >= 1000) {
    return `${sign}$${(abs / 1000).toFixed(abs >= 10000 ? 0 : 1)}k`;
  }
  return `${sign}$${Math.round(abs)}`;
}

/**
 * Parse an ISO date as a plain calendar date.
 *
 * `new Date("2026-07-25")` is parsed as UTC midnight and renders as the previous
 * day in every negative-offset timezone — which is the whole of the US. Every
 * date in this app is a calendar date, never an instant, so it must be built
 * from parts in local time.
 */
export function parseISODate(iso: string): Date {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day);
}

export function formatDate(iso: string): string {
  return parseISODate(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/** "Aug 14" — for axis ticks and timeline headers, where the year is noise. */
export function formatDateShort(iso: string): string {
  return parseISODate(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

/** "in 6 days" / "tomorrow" / "today" / "12 days ago". */
export function formatRelativeDays(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  if (days > 0) return `in ${days} days`;
  return `${Math.abs(days)} days ago`;
}

export function titleCase(value: string | null): string {
  if (!value) return "";
  return value
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
