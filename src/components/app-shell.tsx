"use client";

// Console application shell — light merchant layout:
// fixed sidebar (lg+), sticky topbar, content column, slim footer that
// sticks to the bottom on short pages and is pushed down on long ones.

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiKeyProvider } from "@/components/api-key-context";
import { AppHeader } from "@/components/app-header";
import { AppFooter } from "@/components/app-footer";
import { CopilotFab } from "@/components/copilot-fab";
import { ConsoleSidebar } from "@/components/console-sidebar";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <ApiKeyProvider>
        <div className="flex min-h-screen bg-background text-foreground">
          {/* Sidebar (desktop) */}
          <aside className="sticky top-0 hidden h-screen w-60 shrink-0 border-r border-sidebar-border lg:flex">
            <ConsoleSidebar />
          </aside>

          {/* Content column */}
          <div className="flex min-w-0 flex-1 flex-col">
            <AppHeader />
            <main className="flex-1 px-4 py-6 md:px-6 lg:px-8">
              <div className="mx-auto w-full max-w-7xl">{children}</div>
            </main>
            <AppFooter />
          </div>
        </div>
        <CopilotFab />
      </ApiKeyProvider>
    </QueryClientProvider>
  );
}
