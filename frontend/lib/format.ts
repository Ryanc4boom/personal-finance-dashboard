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

/**
 * Axis-sized money: "$1.2k", "-$840", "$4.3T". Never for a figure read as an
 * exact total.
 *
 * The scale runs all the way to trillions because the same helper labels both a
 * grocery budget and NVIDIA's revenue. Stopping at thousands renders the latter
 * as "$130488660k", which is a longer string than the number it abbreviates.
 */
const COMPACT_UNITS = [
  { at: 1e12, suffix: "T" },
  { at: 1e9, suffix: "B" },
  { at: 1e6, suffix: "M" },
  { at: 1e3, suffix: "k" },
] as const;

export function formatCentsCompact(cents: number): string {
  const dollars = cents / 100;
  const sign = dollars < 0 ? "-" : "";
  const abs = Math.abs(dollars);
  for (const { at, suffix } of COMPACT_UNITS) {
    if (abs >= at) {
      const scaled = abs / at;
      // One decimal below 10, none above: "$4.3T" carries the same information
      // as "$4.31T" at a tick label's size, but "$130B" and "$131B" do not.
      return `${sign}$${scaled.toFixed(scaled >= 10 ? 0 : 1)}${suffix}`;
    }
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

/** Basis points as a percentage: 3765 -> "37.65%". */
export function formatBps(
  bps: number | null | undefined,
  digits = 1,
): string {
  if (bps === null || bps === undefined) return "—";
  return `${(bps / 100).toFixed(digits)}%`;
}

/** Signed form for a return figure: "+37.7%" / "-4.2%". */
export function formatSignedBps(
  bps: number | null | undefined,
  digits = 1,
): string {
  if (bps === null || bps === undefined) return "—";
  const sign = bps > 0 ? "+" : bps < 0 ? "-" : "";
  return `${sign}${(Math.abs(bps) / 100).toFixed(digits)}%`;
}
