import FlagBadge, { STATUS_STYLES } from "@/components/FlagBadge";
import type { ResearchStage } from "@/lib/types";

/**
 * One framework stage and its checks.
 *
 * Every check shows its value, its target and the sentence explaining the
 * verdict — all three come pre-rendered from the backend. A scorecard that
 * shows only a colour asks to be trusted; showing the number, the threshold it
 * was measured against and the reason lets the reader disagree with it, which
 * for a screener built on heuristics is the more useful property.
 */
export default function ScorecardStage({ stage }: { stage: ResearchStage }) {
  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-900 text-xs font-semibold text-white">
              {stage.number}
            </span>
            <h2 className="text-base font-semibold text-slate-900">
              {stage.title}
            </h2>
          </div>
          <p className="mt-1.5 max-w-2xl text-sm text-slate-500">
            {stage.description}
          </p>
        </div>
        <FlagBadge status={stage.status} />
      </header>

      <ul className="divide-y divide-slate-100">
        {stage.checks.map((check) => {
          const style = STATUS_STYLES[check.status];
          return (
            <li key={check.key} className="flex gap-3 px-5 py-4">
              <span
                className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${style.dot}`}
                aria-label={style.label}
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                  <p className="text-sm font-medium text-slate-900">
                    {check.label}
                  </p>
                  <p className="text-sm font-semibold tabular-nums text-slate-900">
                    {check.value_display}
                    <span className="ml-2 font-normal text-slate-400">
                      target {check.target_display}
                    </span>
                  </p>
                </div>
                <p className="mt-1 text-sm leading-relaxed text-slate-500">
                  {check.detail}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
