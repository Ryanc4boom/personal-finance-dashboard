"use client";

import { useMemo, useState } from "react";
import {
  formatCents,
  formatCentsCompact,
  formatDate,
  formatDateShort,
} from "@/lib/format";
import type { NetWorthPoint } from "@/lib/types";

const VIEW_W = 880;
const VIEW_H = 300;
const PAD = { top: 16, right: 16, bottom: 26, left: 64 };

const PLOT_W = VIEW_W - PAD.left - PAD.right;
const PLOT_H = VIEW_H - PAD.top - PAD.bottom;

/**
 * Net worth over time: filled area for net worth, lines for assets and
 * liabilities.
 *
 * **The y-domain always includes zero.** Assets and net worth usually sit far
 * apart, and letting the axis float to the data would draw a liability line
 * that hugs the floor no matter its size — a $2,000 card and a $400,000
 * mortgage would look the same. Anchoring at zero keeps the three series
 * comparable, which is the only reason to plot them together.
 *
 * The x-axis is index-based, not time-based. Points are one per day and
 * contiguous, so spacing them evenly is exact; a date scale would cost a
 * dependency to produce the same pixels.
 */
export default function NetWorthChart({ points }: { points: NetWorthPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);

  const geometry = useMemo(() => {
    const values = points.flatMap((p) => [
      p.net_worth_cents,
      p.total_assets_cents,
      p.total_liabilities_cents,
    ]);
    let min = Math.min(0, ...values);
    let max = Math.max(0, ...values);
    if (max === min) max = min + 1;
    max += (max - min) * 0.08;

    const x = (i: number) =>
      PAD.left + (points.length <= 1 ? PLOT_W / 2 : (i / (points.length - 1)) * PLOT_W);
    const y = (cents: number) => PAD.top + (1 - (cents - min) / (max - min)) * PLOT_H;

    const path = (pick: (p: NetWorthPoint) => number) =>
      points.map((p, i) => `${x(i)},${y(pick(p))}`).join(" ");

    const netLine = path((p) => p.net_worth_cents);
    const netArea = `M ${x(0)},${y(0)} L ${netLine.split(" ").join(" L ")} L ${x(points.length - 1)},${y(0)} Z`;

    const ticks = Array.from({ length: 5 }, (_, i) => min + ((max - min) * i) / 4);

    return {
      x,
      y,
      netArea,
      netLine,
      assetLine: path((p) => p.total_assets_cents),
      liabilityLine: path((p) => p.total_liabilities_cents),
      ticks,
    };
  }, [points]);

  if (points.length === 0) {
    return (
      <p className="py-16 text-center text-sm text-slate-400">
        No history yet. Run a backfill to reconstruct it from your ledger.
      </p>
    );
  }

  const hovered = hover === null ? null : points[hover];
  const last = points[points.length - 1];

  // Over a year or more the two endpoints can land on the same month and day
  // ("Aug 6" to "Aug 6"), which reads as a chart spanning no time at all. Add
  // the year only when it is what distinguishes them.
  const spansYears = points[0].date.slice(0, 4) !== last.date.slice(0, 4);
  const axisLabel = (iso: string) =>
    spansYears ? `${formatDateShort(iso)}, ${iso.slice(0, 4)}` : formatDateShort(iso);

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        className="w-full"
        role="img"
        aria-label={`Net worth history ending at ${formatCents(last.net_worth_cents)}`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const ratio = (e.clientX - rect.left) / rect.width;
          const px = ratio * VIEW_W - PAD.left;
          const index = Math.round((px / PLOT_W) * (points.length - 1));
          setHover(Math.max(0, Math.min(points.length - 1, index)));
        }}
      >
        <defs>
          <linearGradient id="netWorthFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#0f172a" stopOpacity="0.20" />
            <stop offset="100%" stopColor="#0f172a" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {geometry.ticks.map((value) => (
          <g key={value}>
            <line
              x1={PAD.left}
              x2={VIEW_W - PAD.right}
              y1={geometry.y(value)}
              y2={geometry.y(value)}
              stroke="#e2e8f0"
              strokeWidth={1}
            />
            <text
              x={PAD.left - 8}
              y={geometry.y(value) + 3}
              textAnchor="end"
              className="fill-slate-400 text-[10px] tabular-nums"
            >
              {formatCentsCompact(value)}
            </text>
          </g>
        ))}

        <path d={geometry.netArea} fill="url(#netWorthFill)" />
        <polyline
          points={geometry.assetLine}
          fill="none"
          stroke="#0d9488"
          strokeWidth={1.5}
          strokeDasharray="4 3"
        />
        <polyline
          points={geometry.liabilityLine}
          fill="none"
          stroke="#e11d48"
          strokeWidth={1.5}
          strokeDasharray="4 3"
        />
        <polyline
          points={geometry.netLine}
          fill="none"
          stroke="#0f172a"
          strokeWidth={2}
        />

        {points.length > 1 &&
          [0, points.length - 1].map((i) => (
            <text
              key={i}
              x={geometry.x(i)}
              y={VIEW_H - 8}
              textAnchor={i === 0 ? "start" : "end"}
              className="fill-slate-400 text-[10px]"
            >
              {axisLabel(points[i].date)}
            </text>
          ))}

        {hovered && (
          <>
            <line
              x1={geometry.x(hover!)}
              x2={geometry.x(hover!)}
              y1={PAD.top}
              y2={PAD.top + PLOT_H}
              stroke="#94a3b8"
              strokeWidth={1}
            />
            <circle
              cx={geometry.x(hover!)}
              cy={geometry.y(hovered.net_worth_cents)}
              r={3.5}
              fill="#0f172a"
            />
          </>
        )}
      </svg>

      {hovered && (
        <div
          className="pointer-events-none absolute top-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg"
          style={{
            // Flip to the left half once the cursor passes the midpoint, so the
            // card never runs off the right edge of the chart.
            left: hover! / points.length > 0.55 ? undefined : `${(geometry.x(hover!) / VIEW_W) * 100 + 2}%`,
            right:
              hover! / points.length > 0.55
                ? `${100 - (geometry.x(hover!) / VIEW_W) * 100 + 2}%`
                : undefined,
          }}
        >
          <p className="font-medium text-slate-900">{formatDate(hovered.date)}</p>
          <dl className="mt-1 space-y-0.5">
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500">Net worth</dt>
              <dd className="font-medium tabular-nums text-slate-900">
                {formatCents(hovered.net_worth_cents)}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-teal-600">Assets</dt>
              <dd className="tabular-nums text-slate-700">
                {formatCents(hovered.total_assets_cents)}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-rose-600">Liabilities</dt>
              <dd className="tabular-nums text-slate-700">
                {formatCents(hovered.total_liabilities_cents)}
              </dd>
            </div>
          </dl>
          {hovered.source === "RECONSTRUCTED" && (
            <p className="mt-1 border-t border-slate-100 pt-1 text-[10px] text-slate-400">
              Reconstructed from ledger history
            </p>
          )}
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-4 text-xs text-slate-500">
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 bg-slate-900" /> Net worth
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 border-t border-dashed border-teal-600" /> Assets
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 border-t border-dashed border-rose-600" /> Liabilities
        </span>
      </div>
    </div>
  );
}
