import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";

// Merchant console layout — sidebar shell wraps every (console)/* page.
// Owns the "Risk Console" title so the root (marketing-facing) title doesn't
// leak into console tabs.
export const metadata: Metadata = {
  title: "RTO Trust Layer — Risk Console",
};

export default function ConsoleLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return <AppShell>{children}</AppShell>;
}
