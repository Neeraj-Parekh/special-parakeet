import { AppShell } from "@/components/app-shell";

// Merchant console layout — sidebar shell wraps every (console)/* page.
export default function ConsoleLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return <AppShell>{children}</AppShell>;
}
