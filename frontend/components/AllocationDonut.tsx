"use client";

import { useMemo, useState } from "react";
import { formatBps, formatCents, titleCase } from "@/lib/format";
import type { AllocationSlice, AssetClass } from "@/lib/types";

const SIZE = 240;
const CENTER = SIZE / 2;
const OUTER = 104;
const INNER = 66;
/** How far a hovered slice pops out. Small enough to stay inside the viewBox. */
const LIFT = 6;

/**
 * Fixed colour per asset class, not per index.
 *
 * Assigning colours by position would repaint the whole chart whenever a slice
 * appeared or disappeared — sell every bond and "Crypto" inherits the blue that
 * meant "Fixed income" a second ago. Anchoring the mapping to the class means a
 * user can learn the legend once.
 */
export const ASSET_CLASS_COLORS: Record<AssetClass, string> = {
  US_EQUITY: "#0f172a",
  INTERNATIONAL_EQUITY: "#0284c7",
  FIXED_INCOME: "#0d9488",
  CRYPTO: "#d97706",
  REAL_ESTATE: "#7c3aed",
  CASH: "#94a3b8",
};

export const ASSET_CLASS_LABELS: Record<AssetClass, string> = {
  US_EQUITY: "US equities",
  INTERNATIONAL_EQUITY: "International equities",
  FIXED_INCOME: "Fixed income",
  CRYPTO: "Crypto",
  REAL_ESTATE: "Real estate",
  CASH: "Cash",
};

function polar(angle: number, radius: number) {
  // -90° so the first slice starts at twelve o'clock rather than three.
  const radians = ((angle - 90) * Math.PI) / 180;
  return [CENTER + radius * Math.cos(radians), CENTER + radius * Math.sin(radians)];
}

function arc(startAngle: number, endAngle: number, lift: number) {
  const mid = (startAngle + endAngle) / 2;
  const [dx, dy] = polar(mid, lift);
  const ox = dx - CENTER;
  const oy = dy - CENTER;

  const [x1, y1] = polar(startAngle, OUTER);
  const [x2, y2] = polar(endAngle, OUTER);
  const [x3, y3] = polar(endAngle, INNER);
  const [x4, y4] = polar(startAngle, INNER);
  const large = endAngle - startAngle > 180 ? 1 : 0;

  return [
    `M ${x1 + ox} ${y1 + oy}`,
    `A ${OUTER} ${OUTER} 0 ${large} 1 ${x2 + ox} ${y2 + oy}`,
    `L ${x3 + ox} ${y3 + oy}`,
    `A ${INNER} ${INNER} 0 ${large} 0 ${x4 + ox} ${y4 + oy}`,
    "Z",
  ].join(" ");
}

/**
 * Asset allocation, drawn by hand.
 *
 * Angles come from `weight_bps`, which the backend apportions by largest
 * remainder so the slices sum to exactly 10,000. Recomputing the percentages
 * here from `value_cents` would reintroduce the rounding the backend already
 * solved, and the legend would stop adding up to 100%.
 *
 * A single holding produces a full ring rather than a zero-length arc: an arc
 * of exactly 360° has identical start and end points, which SVG renders as
 * nothing at all.
 */
export default function AllocationDonut({
  slices,
  totalCents,
}: {
  slices: AllocationSlice[];
  totalCents: number;
}) {
  const [hover, setHover] = useState<number | null>(null);

  const wedges = useMemo(() => {
    let cursor = 0;
    return slices.map((slice) => {
      const sweep = (slice.weight_bps / 10_000) * 360;
      const wedge = { slice, start: cursor, end: cursor + sweep };
      cursor += sweep;
      return wedge;
    });
  }, [slices]);

  if (slices.length === 0) return null;

  const active = hover === null ? null : slices[hover];
  const full = wedges.length === 1;

  return (
    <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-center">
      <svg
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        className="h-56 w-56 shrink-0"
        role="img"
        aria-label={`Asset allocation across ${slices.length} classes, ${formatCents(totalCents)} total`}
        onMouseLeave={() => setHover(null)}
      >
        {full ? (
          <circle
            cx={CENTER}
            cy={CENTER}
            r={(OUTER + INNER) / 2}
            fill="none"
            stroke={ASSET_CLASS_COLORS[slices[0].asset_class] ?? "#0f172a"}
            strokeWidth={OUTER - INNER}
          />
        ) : (
          wedges.map(({ slice, start, end }, i) => (
            <path
              key={slice.asset_class}
              d={arc(start, end, hover === i ? LIFT : 0)}
              fill={ASSET_CLASS_COLORS[slice.asset_class] ?? "#0f172a"}
              opacity={hover === null || hover === i ? 1 : 0.35}
              className="cursor-pointer transition-opacity"
              onMouseEnter={() => setHover(i)}
            />
          ))
        )}

        <text
          x={CENTER}
          y={CENTER - 6}
          textAnchor="middle"
          className="fill-slate-500 text-[10px] uppercase tracking-wide"
        >
          {active ? ASSET_CLASS_LABELS[active.asset_class] ?? titleCase(active.asset_class) : "Total"}
        </text>
        <text
          x={CENTER}
          y={CENTER + 14}
          textAnchor="middle"
          className="fill-slate-900 text-base font-semibold tabular-nums"
        >
          {formatCents(active ? active.value_cents : totalCents)}
        </text>
      </svg>

      <ul className="w-full space-y-1.5">
        {slices.map((slice, i) => (
          <li
            key={slice.asset_class}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            className={`flex items-center gap-2.5 rounded px-2 py-1 text-sm transition ${
              hover === i ? "bg-slate-50" : ""
            }`}
          >
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ background: ASSET_CLASS_COLORS[slice.asset_class] ?? "#0f172a" }}
            />
            <span className="flex-1 truncate text-slate-700">
              {ASSET_CLASS_LABELS[slice.asset_class] ?? titleCase(slice.asset_class)}
            </span>
            <span className="tabular-nums text-slate-500">
              {formatBps(slice.weight_bps)}
            </span>
            <span className="w-24 text-right tabular-nums font-medium text-slate-900">
              {formatCents(slice.value_cents)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
