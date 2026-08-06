/**
 * The headline figure block shared by the budgets, subscriptions and forecast
 * views. Extracted rather than copied so the three pages cannot drift into
 * three slightly different card styles.
 */
export default function StatCard({
  label,
  value,
  sub,
  tone = "default",
}: {
  label: string;
  value: string;
  sub?: string;
  /** `alert` is for a number the user should act on, not merely a large one. */
  tone?: "default" | "alert" | "positive";
}) {
  const valueTone =
    tone === "alert"
      ? "text-rose-600"
      : tone === "positive"
        ? "text-emerald-600"
        : "text-slate-900";

  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-xl font-semibold tabular-nums ${valueTone}`}>
        {value}
      </p>
      {sub && <p className="text-xs text-slate-400">{sub}</p>}
    </div>
  );
}
