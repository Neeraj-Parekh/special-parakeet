"use client";

import * as React from "react";
import { Github, ShieldCheck } from "lucide-react";

export function AppFooter() {
  return (
    <footer
      className="mt-auto border-t border-border/60 bg-background/60 px-4 py-3 text-xs text-muted-foreground md:px-6"
      aria-label="Site footer"
    >
      <div className="mx-auto flex max-w-7xl flex-col items-center gap-2 sm:flex-row sm:justify-between">
        <p className="flex items-center gap-2">
          <ShieldCheck className="size-3.5 text-success" aria-hidden />
          <span>
            RTO Trust Layer · HistGradientBoosting scorer v2.1 · Bahnsen BMR + Drummond-Holte cost curves
          </span>
        </p>
        <div className="flex items-center gap-4">
          <a
            href="https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/fraud-detection"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground"
          >
            Microsoft Fabric reference
          </a>
          <span aria-hidden>·</span>
          <span>
            Track I Day 3 — Stripe-like dashboard
          </span>
        </div>
      </div>
    </footer>
  );
}
