"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LineChart,
  PiggyBank,
  Receipt,
  RefreshCw,
  Scale,
  Search,
  Target,
  TrendingUp,
} from "lucide-react";

const LINKS = [
  { href: "/", label: "Ledger", icon: Receipt },
  { href: "/budgets", label: "Budgets", icon: PiggyBank },
  { href: "/subscriptions", label: "Subscriptions", icon: RefreshCw },
  { href: "/forecast", label: "Forecast", icon: LineChart },
  { href: "/investments", label: "Investments", icon: TrendingUp },
  { href: "/net-worth", label: "Net Worth", icon: Scale },
  { href: "/goals", label: "Goals", icon: Target },
  { href: "/research", label: "Research", icon: Search },
];

export default function NavBar() {
  const pathname = usePathname();

  return (
    <nav className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-7xl items-center gap-1 px-6">
        {LINKS.map(({ href, label, icon: Icon }) => {
          // Prefix match so /research/NVDA keeps the Research tab lit.
          const active =
            href === "/" ? pathname === href : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`-mb-px flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-3 text-sm font-medium transition ${
                active
                  ? "border-slate-900 text-slate-900"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
