"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { Moon, Sun, ShieldCheck, KeyRound, Lock } from "lucide-react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  useApiKeys,
} from "@/components/api-key-context";

const NAV_ITEMS = [
  { href: "/", label: "Risk Console" },
  { href: "/audit", label: "Audit Explorer" },
  { href: "/rules", label: "Rules Manager" },
  { href: "/model-health", label: "Model Health" },
];

export function AppHeader() {
  const pathname = usePathname();
  const { theme, setTheme } = useTheme();
  const { scorerKey, adminKey, setScorerKey, setAdminKey } = useApiKeys();
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => setMounted(true), []);

  return (
    <header
      className={cn(
        "sticky top-0 z-40 w-full border-b border-border/80 bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/70",
      )}
    >
      <div className="flex h-14 items-center gap-4 px-4 md:px-6">
        <Link href="/" className="flex shrink-0 items-center gap-2">
          <ShieldCheck className="size-5 text-success" aria-hidden />
          <span className="text-sm font-semibold tracking-tight">
            RTO Trust Layer
          </span>
          <span className="hidden text-[10px] font-medium uppercase tracking-widest text-muted-foreground sm:inline">
            Console v2
          </span>
        </Link>
        <nav className="hidden flex-1 items-center gap-1 md:flex" aria-label="Primary">
          {NAV_ITEMS.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname?.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  active
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                )}
                aria-current={active ? "page" : undefined}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <div className="hidden items-center gap-2 lg:flex">
            <div className="relative">
              <KeyRound className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="password"
                value={scorerKey}
                onChange={(e) => setScorerKey(e.target.value)}
                placeholder="Enter scorer key"
                aria-label="Scorer API key"
                autoComplete="off"
                className="h-8 w-44 pl-7 text-xs"
              />
            </div>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="password"
                value={adminKey}
                onChange={(e) => setAdminKey(e.target.value)}
                placeholder="Enter admin key"
                aria-label="Admin API key"
                autoComplete="off"
                className="h-8 w-44 pl-7 text-xs"
              />
            </div>
          </div>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Toggle dark mode"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="size-8"
          >
            {mounted ? (
              theme === "dark" ? (
                <Sun className="size-4" aria-hidden />
              ) : (
                <Moon className="size-4" aria-hidden />
              )
            ) : (
              <Moon className="size-4" aria-hidden />
            )}
          </Button>
        </div>
      </div>
      {/* Mobile nav row */}
      <nav
        className="flex items-center gap-1 overflow-x-auto border-t border-border/60 px-2 py-1 md:hidden"
        aria-label="Primary mobile"
      >
        {NAV_ITEMS.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname?.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                active
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50",
              )}
              aria-current={active ? "page" : undefined}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      {/* Mobile API-key row */}
      <div className="grid grid-cols-2 gap-2 border-t border-border/60 px-3 py-2 lg:hidden">
        <Input
          type="password"
          value={scorerKey}
          onChange={(e) => setScorerKey(e.target.value)}
          placeholder="Scorer key"
          aria-label="Scorer API key"
          autoComplete="off"
          className="h-8 text-xs"
        />
        <Input
          type="password"
          value={adminKey}
          onChange={(e) => setAdminKey(e.target.value)}
          placeholder="Admin key"
          aria-label="Admin API key"
          autoComplete="off"
          className="h-8 text-xs"
        />
      </div>
    </header>
  );
}

export function MockModeBadge({ mock }: { mock: boolean }) {
  if (!mock) return null;
  return (
    <Badge
      variant="outline"
      className="border-warning/50 bg-warning/15 text-warning"
      title="Python backend unreachable — showing preview data"
    >
      <span className="mr-1 inline-block size-1.5 rounded-full bg-warning" aria-hidden />
      Mock mode
    </Badge>
  );
}
