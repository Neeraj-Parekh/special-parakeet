"use client";

// Slim console footer — bottom-anchored via the shell's flex column +
// mt-auto (sticks on short pages, pushed down naturally on long ones).

import Link from "next/link";
import { ShieldCheck } from "lucide-react";

export function AppFooter() {
  return (
    <footer
      className="mt-auto border-t border-border bg-white/60 px-4 py-3 text-xs text-muted-foreground md:px-6 lg:px-8"
      aria-label="Console footer"
    >
      <div className="mx-auto flex max-w-7xl flex-col items-center gap-2 sm:flex-row sm:justify-between">
        <p className="flex items-center gap-2">
          <ShieldCheck className="size-3.5 shrink-0 text-brand-500" aria-hidden />
          <span>
            RTO Trust Layer · Bahnsen BMR cost-optimal decisions · Merkle-sealed
            audit · OC-201B UPI Circle
          </span>
        </p>
        <nav className="flex items-center gap-4" aria-label="Footer">
          <Link href="/dashboard" className="transition-colors hover:text-foreground">
            Console
          </Link>
          <Link href="/api-docs" className="transition-colors hover:text-foreground">
            API reference
          </Link>
          <a
            href="https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/fraud-detection"
            target="_blank"
            rel="noopener noreferrer"
            className="transition-colors hover:text-foreground"
          >
            Fabric reference
          </a>
        </nav>
      </div>
    </footer>
  );
}
