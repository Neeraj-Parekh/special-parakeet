"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "@/components/theme-provider";
import { ApiKeyProvider } from "@/components/api-key-context";
import { AppHeader } from "@/components/app-header";
import { AppFooter } from "@/components/app-footer";
import { CopilotFab } from "@/components/copilot-fab";

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
    <ThemeProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem={false}
      disableTransitionOnChange
    >
      <QueryClientProvider client={client}>
        <ApiKeyProvider>
          <div className="flex min-h-screen flex-col bg-background text-foreground">
            <AppHeader />
            <main className="flex-1 px-4 py-6 md:px-6 md:py-8">
              <div className="mx-auto w-full max-w-7xl">{children}</div>
            </main>
            <AppFooter />
          </div>
          <CopilotFab />
        </ApiKeyProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
