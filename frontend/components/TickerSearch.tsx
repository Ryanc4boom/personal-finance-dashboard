"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Search } from "lucide-react";
import { searchTickers } from "@/lib/api";
import type { TickerMatch } from "@/lib/types";

/**
 * Ticker search backed by SEC's own ticker index.
 *
 * Suggestions come from the same cached `company_tickers.json` the report
 * resolves against, so the list can never offer something the deep dive then
 * rejects as unknown.
 *
 * Queries are debounced and every in-flight request is superseded by a sequence
 * check rather than an AbortController: the responses are cheap and cached, and
 * the only failure that matters is a slow early response landing after a fast
 * later one and repopulating the list with stale matches.
 */
export default function TickerSearch({
  autoFocus = false,
  placeholder = "Search a ticker or company — NVDA, MSFT, Apple",
}: {
  autoFocus?: boolean;
  placeholder?: string;
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<TickerMatch[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const sequence = useRef(0);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 1) {
      setMatches([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    const id = ++sequence.current;
    const timer = setTimeout(() => {
      searchTickers(trimmed)
        .then((result) => {
          if (id !== sequence.current) return;
          setMatches(result.results);
          setHighlight(0);
          setOpen(true);
        })
        .catch(() => {
          if (id === sequence.current) setMatches([]);
        })
        .finally(() => {
          if (id === sequence.current) setLoading(false);
        });
    }, 180);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    function onClickOutside(event: MouseEvent) {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  function go(ticker: string) {
    setOpen(false);
    router.push(`/research/${encodeURIComponent(ticker.toUpperCase())}`);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, matches.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      // Falling back to the raw text means a ticker SEC lists but the typeahead
      // has not returned yet still works — the report resolves it server-side.
      const chosen = open && matches[highlight] ? matches[highlight].ticker : query;
      if (chosen.trim()) go(chosen.trim());
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div ref={container} className="relative">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <input
          value={query}
          autoFocus={autoFocus}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => matches.length > 0 && setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          aria-label="Search ticker"
          className="w-full rounded-lg border border-slate-300 bg-white py-2.5 pl-10 pr-10 text-sm text-slate-900 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
        />
        {loading && (
          <Loader2 className="absolute right-3.5 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-slate-400" />
        )}
      </div>

      {open && matches.length > 0 && (
        <ul className="absolute z-20 mt-1.5 max-h-80 w-full overflow-auto rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
          {matches.map((match, index) => (
            <li key={`${match.ticker}-${match.cik}`}>
              <button
                type="button"
                onMouseEnter={() => setHighlight(index)}
                onClick={() => go(match.ticker)}
                className={`flex w-full items-baseline gap-3 px-4 py-2 text-left text-sm ${
                  index === highlight ? "bg-slate-100" : "hover:bg-slate-50"
                }`}
              >
                <span className="w-16 shrink-0 font-semibold text-slate-900">
                  {match.ticker}
                </span>
                <span className="truncate text-slate-500">{match.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
