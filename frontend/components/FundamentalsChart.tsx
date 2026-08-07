"use client";

import { useMemo, useState } from "react";
import { formatBps, formatCents, formatCentsCompact } from "@/lib/format";
import type { AnnualRow } from "@/lib/types";

const VIEW_W = 880;
const VIEW_H = 260;
const PAD = { top: 16, right: 16, bottom: 28, left: 64 };
const PLOT_W = VIEW_W - PAD.left - PAD.right;
const PLOT_H = VIEW_H - PAD.top - PAD.bottom;

const BARS = [
  { key: "revenue_cents", label: "Revenue", fill: "#0f172a" },
  { key: "operating_income_cents", label: "Operating income", fill: "#64748b" },
  { key: "free_cash_flow_cents", label: "Free cash flow", fill: "#10b981" },
] as const;

const LINES = [
  { key: "gross_margin_bps", label: "Gross margin", stroke: "#f59e0b" },
  { key: "operating_margin_bps", label: "Operating margin", stroke: "#3b82f6" },
] as const;

/**
 * Five years of scale and five years of margin on one frame.
 *
 * Two independent y-domains: money on the left for the bars, percent on the
 * right for the margin lines. Sharing one axis would be honest only if margins
 * and dollars were commensurable, which they are not — the margin lines would
 * flatline against billions of revenue and the trend the framework actually
 * scores would be invisible.
 *
 * The money axis is anchored at zero so a loss-making year draws below the
 * baseline instead of rescaling the whole chart. Years where a filer never
 * tagged the concept are gaps, not zeros: plotting a missing capex figure as
 * $0 would draw free cash flow equal to operating cash flow and quietly
 * overstate it.
 */
export default function FundamentalsChart({ rows }: { rows: AnnualRow[] }) {
  const [hover, setHover] = useState<number | null>(null);

  const geometry = useMemo(() => {
    const money = rows.flatMap((row) =>
      BARS.map((bar) => row[bar.key]).filter((v): v is number => v !== null),
    );
    let min = Math.min(0, ...money);
    let max = Math.max(0, ...money, 1);
    max += (max - min) * 0.1;

    const marginValues = rows.flatMap((row) =>
      LINES.map((line) => row[line.key]).filter((v): v is number => v !== null),
    );
    const marginMin = Math.min(0, ...marginValues);
    const marginMax = Math.max(...marginValues, 100) * 1.1;

    const band = PLOT_W / Math.max(rows.length, 1);
    const barWidth = Math.min(26, (band * 0.62) / BARS.length);

    const groupX = (i: number) => PAD.left + band * i + band / 2;
    const y = (cents: number) =>
      PAD.top + (1 - (cents - min) / (max - min)) * PLOT_H;
    const marginY = (bps: number) =>
      PAD.top + (1 - (bps - marginMin) / (marginMax - marginMin)) * PLOT_H;

    return {
      groupX,
      y,
      marginY,
      barWidth,
      band,
      zero: y(0),
      ticks: Array.from({ length: 5 }, (_, i) => min + ((max - min) * i) / 4),
      marginTicks: Array.from(
        { length: 5 },
        (_, i) => marginMin + ((marginMax - marginMin) * i) / 4,
      ),
    };
  }, [rows]);

  if (rows.length === 0) return null;
  const active = hover === null ? null : rows[hover];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-slate-500">
        {BARS.map((bar) => (
          <span key={bar.key} className="flex items-center gap-1.5">
            <span
              className="h-2.5 w-2.5 rounded-sm"
              style={{ background: bar.fill }}
            />
            {bar.label}
          </span>
        ))}
        {LINES.map((line) => (
          <span key={line.key} className="flex items-center gap-1.5">
            <span
              className="h-0.5 w-4 rounded"
              style={{ background: line.stroke }}
            />
            {line.label}
          </span>
        ))}
      </div>

      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        className="w-full"
        role="img"
        aria-label="Revenue, operating income, free cash flow and margins by fiscal year"
      >
        {geometry.ticks.map((tick, i) => (
          <g key={`t${i}`}>
            <line
              x1={PAD.left}
              x2={VIEW_W - PAD.right}
              y1={geometry.y(tick)}
              y2={geometry.y(tick)}
              stroke="#e2e8f0"
              strokeWidth={1}
            />
            <text
              x={PAD.left - 8}
              y={geometry.y(tick) + 4}
              textAnchor="end"
              className="fill-slate-400 text-[10px]"
            >
              {formatCentsCompact(tick)}
            </text>
          </g>
        ))}
        {geometry.marginTicks.map((tick, i) => (
          <text
            key={`m${i}`}
            x={VIEW_W - PAD.right + 4}
            y={geometry.marginY(tick) + 4}
            textAnchor="start"
            className="fill-slate-300 text-[10px]"
          >
            {(tick / 100).toFixed(0)}%
          </text>
        ))}

        {rows.map((row, i) => (
          <g key={row.fiscal_label}>
            {BARS.map((bar, b) => {
              const value = row[bar.key];
              if (value === null) return null;
              const top = Math.min(geometry.y(value), geometry.zero);
              const height = Math.abs(geometry.y(value) - geometry.zero);
              const x =
                geometry.groupX(i) -
                (BARS.length * geometry.barWidth) / 2 +
                b * geometry.barWidth;
              return (
                <rect
                  key={bar.key}
                  x={x}
                  y={top}
                  width={geometry.barWidth - 2}
                  height={Math.max(height, 1)}
                  fill={bar.fill}
                  opacity={hover === null || hover === i ? 1 : 0.35}
                />
              );
            })}
            <text
              x={geometry.groupX(i)}
              y={VIEW_H - 8}
              textAnchor="middle"
              className="fill-slate-500 text-[11px]"
            >
              {row.fiscal_label}
            </text>
          </g>
        ))}

        {LINES.map((line) => {
          const points = rows
            .map((row, i) =>
              row[line.key] === null
                ? null
                : `${geometry.groupX(i)},${geometry.marginY(row[line.key] as number)}`,
            )
            .filter((p): p is string => p !== null);
          if (points.length < 2) return null;
          return (
            <polyline
              key={line.key}
              points={points.join(" ")}
              fill="none"
              stroke={line.stroke}
              strokeWidth={2}
              strokeLinejoin="round"
            />
          );
        })}

        {rows.map((row, i) => (
          <rect
            key={`hit-${row.fiscal_label}`}
            x={PAD.left + geometry.band * i}
            y={PAD.top}
            width={geometry.band}
            height={PLOT_H}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      </svg>

      <div className="mt-2 min-h-[22px] text-xs text-slate-500">
        {active && (
          <span className="tabular-nums">
            <strong className="text-slate-900">{active.fiscal_label}</strong>
            {" · revenue "}
            {formatCents(active.revenue_cents)}
            {" · operating income "}
            {formatCents(active.operating_income_cents)}
            {" · FCF "}
            {formatCents(active.free_cash_flow_cents)}
            {" · gross margin "}
            {formatBps(active.gross_margin_bps)}
          </span>
        )}
      </div>
    </div>
  );
}
