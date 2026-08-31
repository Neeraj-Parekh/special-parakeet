"use client";

// Console topbar — page title, API keys, mobile nav Sheet.
// NOTE: no dark-mode toggle. The product is light-only by design
// (dark surfaces are reserved for code blocks — Stripe docs pattern).

import * as React from "react";
import { usePathname } from "next/navigation";
import { KeyRound, Lock, Menu } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { useApiKeys } from "@/components/api-key-context";
import { ConsoleSidebar } from "@/components/console-sidebar";
import { pageMetaFor } from "@/lib/nav";

export function AppHeader() {
  const { scorerKey, adminKey, setScorerKey, setAdminKey } = useApiKeys();
  const [navOpen, setNavOpen] = React.useState(false);
  const meta = pageMetaFor(usePathname());

  return (
    <header className="sticky top-0 z-40 w-full border-b border-border bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/70">
      <div className="flex h-14 items-center gap-3 px-4 md:px-6 lg:px-8">
        {/* Mobile nav */}
        <Sheet open={navOpen} onOpenChange={setNavOpen}>
          <SheetTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-9 shrink-0 lg:hidden"
              aria-label="Open navigation"
            >
              <Menu className="size-5" aria-hidden />
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-64 p-0">
            <SheetTitle className="sr-only">Console navigation</SheetTitle>
            <ConsoleSidebar onNavigate={() => setNavOpen(false)} />
          </SheetContent>
        </Sheet>

        {/* Page title */}
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">
            {meta.title}
          </div>
          <div className="hidden truncate text-xs text-muted-foreground sm:block">
            {meta.description}
          </div>
        </div>

        {/* Right cluster */}
        <div className="ml-auto flex items-center gap-2">
          <Badge
            variant="outline"
            className="hidden font-mono text-[10px] font-medium text-muted-foreground xl:inline-flex"
          >
            scorer v2.1
          </Badge>
          <div className="hidden items-center gap-2 lg:flex">
            <div className="relative">
              <KeyRound className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="password"
                value={scorerKey}
                onChange={(e) => setScorerKey(e.target.value)}
                placeholder="Scorer key"
                aria-label="Scorer API key"
                autoComplete="off"
                className="h-9 w-40 pl-7 text-xs"
              />
            </div>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="password"
                value={adminKey}
                onChange={(e) => setAdminKey(e.target.value)}
                placeholder="Admin key"
                aria-label="Admin API key"
                autoComplete="off"
                className="h-9 w-40 pl-7 text-xs"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Mobile API-key row */}
      <div className="grid grid-cols-2 gap-2 border-t border-border/60 px-3 py-2 lg:hidden">
        <Input
          type="password"
          value={scorerKey}
          onChange={(e) => setScorerKey(e.target.value)}
          placeholder="Scorer key"
          aria-label="Scorer API key"
          autoComplete="off"
          className="h-9 text-xs"
        />
        <Input
          type="password"
          value={adminKey}
          onChange={(e) => setAdminKey(e.target.value)}
          placeholder="Admin key"
          aria-label="Admin API key"
          autoComplete="off"
          className="h-9 text-xs"
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
      className="border-warning/50 bg-warning/10 text-warning"
      title="Python backend unreachable — showing preview data"
    >
      <span className="mr-1 inline-block size-1.5 rounded-full bg-gold-500" aria-hidden />
      Mock mode
    </Badge>
  );
}
