import { CircleHelp, TriangleAlert, XCircle, CheckCircle2 } from "lucide-react";
import type { CheckStatus } from "@/lib/types";

/**
 * The single source of truth for what a verdict looks like.
 *
 * `UNKNOWN` is deliberately slate, not amber. The engine's whole premise is
 * that a missing input is not a soft fail — Apple tags no interest expense, and
 * colouring that the same as "coverage is thin" would invent a risk signal out
 * of a gap in someone's XBRL. Grey reads as "not measured", which is what it is.
 */
export const STATUS_STYLES: Record<
  CheckStatus,
  { label: string; badge: string; dot: string; icon: typeof CheckCircle2 }
> = {
  PASS: {
    label: "Green flag",
    badge: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    dot: "bg-emerald-500",
    icon: CheckCircle2,
  },
  WARN: {
    label: "Watch",
    badge: "bg-amber-50 text-amber-700 ring-amber-200",
    dot: "bg-amber-500",
    icon: TriangleAlert,
  },
  FAIL: {
    label: "Red flag",
    badge: "bg-rose-50 text-rose-700 ring-rose-200",
    dot: "bg-rose-500",
    icon: XCircle,
  },
  UNKNOWN: {
    label: "Not available",
    badge: "bg-slate-100 text-slate-600 ring-slate-200",
    dot: "bg-slate-400",
    icon: CircleHelp,
  },
};

export default function FlagBadge({
  status,
  label,
}: {
  status: CheckStatus;
  label?: string;
}) {
  const style = STATUS_STYLES[status];
  const Icon = style.icon;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${style.badge}`}
    >
      <Icon className="h-3.5 w-3.5" />
      {label ?? style.label}
    </span>
  );
}
