"use client";

// Marketing footer — full vendor-site footer on navy.

import Link from "next/link";
import { ShieldCheck } from "lucide-react";

const COLUMNS: { title: string; links: { href: string; label: string; external?: boolean }[] }[] = [
  {
    title: "Product",
    links: [
      { href: "/dashboard", label: "Merchant console" },
      { href: "/score", label: "Risk scoring" },
      { href: "/checkout", label: "Checkout demo" },
      { href: "/api-docs", label: "API reference" },
    ],
  },
  {
    title: "Platform",
    links: [
      { href: "/integrations", label: "Integrations" },
      { href: "/rules", label: "Rules engine" },
      { href: "/model-health", label: "Model health" },
      { href: "/cases", label: "Case queue" },
    ],
  },
  {
    title: "Resources",
    links: [
      {
        href: "https://github.com/Neeraj-Parekh/special-parakeet",
        label: "GitHub repository",
        external: true,
      },
      {
        href: "https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/fraud-detection",
        label: "Reference architecture",
        external: true,
      },
      { href: "/audit", label: "Audit explorer" },
    ],
  },
];

export function MarketingFooter() {
  return (
    <footer className="mt-auto bg-navy-950 text-white/70" aria-label="Site footer">
      <div className="mx-auto max-w-6xl px-4 py-12 md:px-6">
        <div className="grid gap-10 md:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(0,1fr))]">
          {/* Brand */}
          <div>
            <div className="flex items-center gap-2.5">
              <div className="flex size-8 items-center justify-center rounded-lg bg-white/10">
                <ShieldCheck className="size-5 text-brand-400" aria-hidden />
              </div>
              <span className="text-sm font-semibold text-white">RTO Trust Layer</span>
            </div>
            <p className="mt-3 max-w-xs text-xs leading-relaxed">
              Pre-dispatch COD return-risk gating for Indian e-commerce. Cost-optimal
              decisions, a sealed audit trail, and mandate-fenced checkouts.
            </p>
            <p className="mt-4 text-[11px] text-white/40">
              Hackathon build — demo data is labeled; benchmarks cited in the repository docs.
            </p>
          </div>

          {/* Link columns */}
          {COLUMNS.map((col) => (
            <nav key={col.title} aria-label={col.title}>
              <h3 className="text-xs font-semibold uppercase tracking-widest text-white/50">
                {col.title}
              </h3>
              <ul className="mt-3 space-y-2.5">
                {col.links.map((l) => (
                  <li key={l.href}>
                    {l.external ? (
                      <a
                        href={l.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm transition-colors duration-200 ease-brand hover:text-white"
                      >
                        {l.label}
                      </a>
                    ) : (
                      <Link
                        href={l.href}
                        className="text-sm transition-colors duration-200 ease-brand hover:text-white"
                      >
                        {l.label}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>

        <div className="mt-10 flex flex-col items-start justify-between gap-2 border-t border-white/10 pt-6 text-xs text-white/40 sm:flex-row sm:items-center">
          <p>© 2025 RTO Trust Layer</p>
          <p className="font-mono">Bahnsen BMR · Merkle audit · OC-201B UPI Circle</p>
        </div>
      </div>
    </footer>
  );
}
