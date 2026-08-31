// Console navigation — the single list behind the sidebar AND the mobile
// sheet nav. HARD RULE: zero dead links — every href below resolves to a
// real page under src/app/(console)/.

import {
  LayoutDashboard,
  Shield,
  CreditCard,
  Briefcase,
  ScrollText,
  Bot,
  BookOpen,
  Blocks,
  SlidersHorizontal,
  Activity,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  description: string;
}

/** Primary merchant journey — 8 items, in this exact order. */
export const CONSOLE_NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, description: "Volume, blocked value, audit chain health" },
  { href: "/score", label: "Risk Scoring", icon: Shield, description: "Score an order, see the BMR verdict + costs" },
  { href: "/checkout", label: "Checkout Demo", icon: CreditCard, description: "The consumer face: 3-step gate at checkout" },
  { href: "/cases", label: "Cases", icon: Briefcase, description: "REVIEW queue with live SLA clocks" },
  { href: "/audit", label: "Audit Trail", icon: ScrollText, description: "Merkle-sealed decision ledger + proof" },
  { href: "/copilot", label: "AI Copilot", icon: Bot, description: "Policy-bounded operator assistant" },
  { href: "/api-docs", label: "API Docs", icon: BookOpen, description: "REST reference with copyable cURL" },
  { href: "/integrations", label: "Integrations", icon: Blocks, description: "NPCI, Shiprocket, Delhivery, Razorpay" },
];

/** Internal platform tools (kept one click deep, below the primary nav). */
export const PLATFORM_NAV: NavItem[] = [
  { href: "/rules", label: "Rules Engine", icon: SlidersHorizontal, description: "Rule registry + live what-if re-scoring" },
  { href: "/model-health", label: "Model Health", icon: Activity, description: "Champion metrics, DDM/ADWIN drift state" },
];

export function pageMetaFor(pathname: string | null | undefined): { title: string; description: string } {
  const all = [...CONSOLE_NAV, ...PLATFORM_NAV];
  const hit =
    all.find((n) => n.href === pathname) ??
    all.find((n) => n.href !== "/" && pathname?.startsWith(n.href));
  if (hit) return { title: hit.label, description: hit.description };
  return { title: "Console", description: "RTO Trust Layer merchant console" };
}
