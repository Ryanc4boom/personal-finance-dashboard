"use client";

import { useMemo, useState } from "react";
import {
  formatCents,
  formatCentsCompact,
  formatDateShort,
} from "@/lib/format";
import type { Forecast } from "@/lib/types";

const VIEW_W = 880;
const VIEW_H = 280;
const PAD = { top: 16, right: 16, bottom: 26, left: 58 };

const PLOT_W = VIEW_W - PAD.left - PAD.right;
const PLOT_H = VIEW_H - PAD.top - PAD.bottom;

/**
 * Projected daily balance, drawn by hand.
 *
 * No chart library is installed, and pulling one in for a single area chart
 * would cost more bundle than the whole forecast page. The shapes here are
 * simple: one filled area, one dashed threshold rule, and a shaded band for the
 * region that breaches it.
 *
 * The y-domain always includes the threshold and always includes zero when the
 * projection goes negative. Auto-scaling to the data alone would let a run that
 * dips $2 under the line look identical to one that dips $2,000 under it — the
 * chart would be technically accurate and practically a lie.
 */
export default function BalanceChart({ forecast }: { forecast: Forecast }) {
  const [hover, setHover] = useState<number | null>(null);
  const days = forecast.days;

  const geometry = useMemo(() => {
    const values = days.map((d) => d.closing_cents);
    const candidates = [...values, forecast.threshold_cents];
    let min = Math.min(...candidates);
    let max = Math.max(...candidates);
    if (min > 0) min = 0; // anchor the floor so dip depth is readable
    if (max === min) max = min + 1; // a flat projection still needs a height

    const headroom = (max - min) * 0.08;
    max += headroom;

    const x = (i: number) =>
      PAD.left + (days.length <= 1 ? PLOT_W / 2 : (i / (days.length - 1)) * PLOT_W);
    const y = (cents: number) =>
      PAD.top + (1 - (cents - min) / (max - min)) * PLOT_H;

    const line = values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
    const area = `M ${PAD.left},${PAD.top + PLOT_H} L ${line.split(" ").join(" L ")} L ${x(values.length - 1)},${PAD.top + PLOT_H} Z`;

    // Four gridlines, on round-ish numbers rather than exact data extremes.
    const ticks = Array.from({ length: 5 }, (_, i) => min + ((max - min) * i) / 4);

    return { x, y, area, line, ticks, min, max };
  }, [days, forecast.threshold_cents]);

  if (days.length === 0) return null;

  const thresholdY = geometry.y(forecast.threshold_cents);
  const hovered = hover === null ? null : days[hover];
  const breached = days.some((d) => d.below_threshold);

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        className="w-full"
        role="img"
        aria-label={`Projected balance over ${forecast.horizon_days} days, ending at ${formatCents(forecast.ending_cash_cents)}`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const ratio = (e.clientX - rect.left) / rect.width;
          const px = ratio * VIEW_W - PAD.left;
          const index = Math.round((px / PLOT_W) * (days.length - 1));
          setHover(Math.max(0, Math.min(days.length - 1, index)));
        }}
      >
        <defs>
          <linearGradient id="balanceFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#0f172a" stopOpacity="0.18" />
            <stop offset="100%" stopColor="#0f172a" stopOpacity="0.02" />
          </linearGradient>
          {/* Everything below the threshold line is clipped into the danger band. */}
          <clipPath id="belowThreshold">
            <rect
              x={PAD.left}
              y={thresholdY}
              width={PLOT_W}
              height={Math.max(0, PAD.top + PLOT_H - thresholdY)}
            />
          </clipPath>
        </defs>

        {geometry.ticks.map((value, i) => {
          const ty = geometry.y(value);
          return (
            <g key={i}>
              <line
                x1={PAD.left}
                y1={ty}
                x2={PAD.left + PLOT_W}
                y2={ty}
                stroke="#e2e8f0"
                strokeWidth="1"
              />
              <text
                x={PAD.left - 8}
                y={ty + 4}
                textAnchor="end"
                className="fill-slate-400"
                fontSize="11"
              >
                {formatCentsCompact(value)}
              </text>
            </g>
          );
        })}

        <path d={geometry.area} fill="url(#balanceFill)" />
        {breached && (
          <path
            d={geometry.area}
            fill="#e11d48"
            fillOpacity="0.16"
            clipPath="url(#belowThreshold)"
          />
        )}

        <line
          x1={PAD.left}
          y1={thresholdY}
          x2={PAD.left + PLOT_W}
          y2={thresholdY}
          stroke="#e11d48"
          strokeWidth="1.5"
          strokeDasharray="5 4"
        />
        <text
          x={PAD.left + PLOT_W}
          y={thresholdY - 6}
          textAnchor="end"
          className="fill-rose-500"
          fontSize="11"
        >
          safe floor {formatCentsCompact(forecast.threshold_cents)}
        </text>

        <polyline
          points={geometry.line}
          fill="none"
          stroke="#0f172a"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* Recurring events get a tick on the axis so the steps have a cause. */}
        {days.map((day, i) =>
          day.events.length > 0 ? (
            <line
              key={day.date}
              x1={geometry.x(i)}
              y1={PAD.top + PLOT_H}
              x2={geometry.x(i)}
              y2={PAD.top + PLOT_H - 4}
              stroke="#94a3b8"
              strokeWidth="1.5"
            />
          ) : null,
        )}

        {[0, Math.floor(days.length / 2), days.length - 1].map((i) => (
          <text
            key={i}
            x={geometry.x(i)}
            y={VIEW_H - 8}
            textAnchor={i === 0 ? "start" : i === days.length - 1 ? "end" : "middle"}
            className="fill-slate-400"
            fontSize="11"
          >
            {formatDateShort(days[i].date)}
          </text>
        ))}

        {hovered && hover !== null && (
          <g>
            <line
              x1={geometry.x(hover)}
              y1={PAD.top}
              x2={geometry.x(hover)}
              y2={PAD.top + PLOT_H}
              stroke="#94a3b8"
              strokeWidth="1"
            />
            <circle
              cx={geometry.x(hover)}
              cy={geometry.y(hovered.closing_cents)}
              r="4"
              fill="#0f172a"
              stroke="white"
              strokeWidth="2"
            />
          </g>
        )}
      </svg>

      {hovered && hover !== null && (
        <div
          className="pointer-events-none absolute top-2 z-10 w-56 -translate-x-1/2 rounded-lg border border-slate-200 bg-white p-2.5 shadow-lg"
          style={{
            // Clamped so the card never hangs off either edge of the plot.
            left: `${Math.min(88, Math.max(12, (geometry.x(hover) / VIEW_W) * 100))}%`,
          }}
        >
          <p className="text-xs font-medium text-slate-500">
            {formatDateShort(hovered.date)}
          </p>
          <p
            className={`text-base font-semibold tabular-nums ${
              hovered.below_threshold ? "text-rose-600" : "text-slate-900"
            }`}
          >
            {formatCents(hovered.closing_cents)}
          </p>
          {hovered.events.map((event) => (
            <p
              key={event.stream_id}
              className="mt-1 flex justify-between gap-2 text-xs"
            >
              <span className="truncate text-slate-600">
                {event.display_name}
              </span>
              <span
                className={`shrink-0 tabular-nums ${
                  event.amount_cents > 0 ? "text-emerald-600" : "text-slate-500"
                }`}
              >
                {event.amount_cents > 0 ? "+" : "−"}
                {formatCents(Math.abs(event.amount_cents))}
              </span>
            </p>
          ))}
          {hovered.variable_outflow_cents > 0 && (
            <p className="mt-1 flex justify-between gap-2 text-xs text-slate-400">
              <span>Everyday spending</span>
              <span className="tabular-nums">
                −{formatCents(hovered.variable_outflow_cents)}
              </span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
