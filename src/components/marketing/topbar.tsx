"use client";

// Marketing topbar — the navy #02042B chrome that frames the vendor site.

import Link from "next/link";
import { ShieldCheck, ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/button";

const ANCHORS = [
  { href: "/#product", label: "Product" },
  { href: "/#trust", label: "Trust" },
  { href: "/#developers", label: "Developers" },
];

export function MarketingTopbar() {
  return (
    <header className="sticky top-0 z-40 w-full bg-navy-950/95 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-6 px-4 md:px-6">
        <Link href="/" className="flex shrink-0 items-center gap-2.5" aria-label="RTO Trust Layer home">
          <div className="flex size-8 items-center justify-center rounded-lg bg-white/10">
            <ShieldCheck className="size-5 text-brand-400" aria-hidden />
          </div>
          <span className="text-sm font-semibold tracking-tight text-white">
            RTO Trust Layer
          </span>
        </Link>

        <nav className="hidden items-center gap-1 md:flex" aria-label="Marketing">
          {ANCHORS.map((a) => (
            <Link
              key={a.href}
              href={a.href}
              className="rounded-md px-3 py-2 text-sm font-medium text-white/70 transition-colors duration-200 ease-brand hover:bg-white/10 hover:text-white"
            >
              {a.label}
            </Link>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <Button
            asChild
            className="h-9 rounded-lg bg-brand-500 px-4 text-sm font-semibold text-white shadow-none transition-colors duration-200 ease-brand hover:bg-brand-600"
          >
            <Link href="/dashboard">
              Open Console
              <ArrowRight className="ml-1.5 size-3.5" aria-hidden />
            </Link>
          </Button>
        </div>
      </div>
    </header>
  );
}
