"use client";

// Console sidebar — the merchant app navigation. 8 primary items (exact
// order from the design brief) + a Platform group for internal tools.
// HARD RULES: next/link only (no <a> — no full reloads), zero dead links,
// 44px touch targets, active state = blue tint + brand icon.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ShieldCheck } from "lucide-react";

import { cn } from "@/lib/utils";
import { CONSOLE_NAV, PLATFORM_NAV, type NavItem } from "@/lib/nav";

function SidebarLink({
  item,
  pathname,
  onNavigate,
}: {
  item: NavItem;
  pathname: string | null | undefined;
  onNavigate?: () => void;
}) {
  const active =
    item.href === "/dashboard"
      ? pathname === "/dashboard"
      : pathname?.startsWith(item.href);
  const Icon = item.icon;
  return (
    <li>
      <Link
        href={item.href}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex items-center gap-3 rounded-lg px-3 py-3 text-sm transition-colors duration-200 ease-brand",
          active
            ? "bg-sidebar-accent font-semibold text-sidebar-accent-foreground"
            : "font-medium text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        <Icon
          className={cn(
            "size-4 shrink-0",
            active ? "text-brand-500" : "text-muted-foreground",
          )}
          aria-hidden
        />
        {item.label}
      </Link>
    </li>
  );
}

export function ConsoleSidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <div className="flex h-full flex-col bg-sidebar">
      {/* Brand */}
      <div className="flex items-center gap-2.5 border-b border-sidebar-border px-5 py-4">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-navy-950">
          <ShieldCheck className="size-5 text-brand-500" aria-hidden />
        </div>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-sidebar-foreground">
            RTO Trust Layer
          </div>
          <div className="text-[11px] text-muted-foreground">Merchant Console</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-3 py-4" aria-label="Console">
        <ul className="space-y-0.5">
          {CONSOLE_NAV.map((item) => (
            <SidebarLink
              key={item.href}
              item={item}
              pathname={pathname}
              onNavigate={onNavigate}
            />
          ))}
        </ul>
        <div className="mb-2 mt-6 px-3 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          Platform
        </div>
        <ul className="space-y-0.5">
          {PLATFORM_NAV.map((item) => (
            <SidebarLink
              key={item.href}
              item={item}
              pathname={pathname}
              onNavigate={onNavigate}
            />
          ))}
        </ul>
      </nav>

      {/* Status */}
      <div className="border-t border-sidebar-border px-5 py-3.5">
        <div className="flex items-center gap-2">
          <span className="relative flex size-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-mint-500 opacity-60 motion-safe:animate-ping" />
            <span className="relative inline-flex size-2 rounded-full bg-mint-500" />
          </span>
          <span className="text-xs text-muted-foreground">
            System online · mock fallback ready
          </span>
        </div>
      </div>
    </div>
  );
}
