"use client";

import * as React from "react";
import { MessageSquare, X, Send, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";
import { MockModeBadge } from "@/components/app-header";

interface Message {
  role: "user" | "assistant";
  content: string;
  intent?: string;
  mock?: boolean;
}

const SUGGESTIONS = [
  "Show me all high-risk orders from yesterday",
  "Is the audit chain intact?",
  "What's the current drift state?",
  "List all active rules",
  "What's the cost-optimal threshold?",
];

export function CopilotFab() {
  const keys = useApiKeys();
  const [open, setOpen] = React.useState(false);
  const [input, setInput] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [messages, setMessages] = React.useState<Message[]>([
    {
      role: "assistant",
      content:
        "Hi — I'm the RTO Copilot. I can answer questions about high-risk orders, the audit hash chain, drift status (DDM/ADWIN), active rules, cost curves, model health, and usage metering. Try one of the suggestions below.",
    },
  ]);

  async function ask(q: string) {
    if (!q.trim() || loading) return;
    const next: Message[] = [...messages, { role: "user", content: q }];
    setMessages(next);
    setInput("");
    setLoading(true);
    try {
      const r = await fetch("/api/copilot", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...buildAuthHeader(keys, "scorer"),
        },
        body: JSON.stringify({ question: q, scope: "scorer" }),
      });
      const data = await r.json().catch(() => null);
      if (!r.ok || !data) {
        setMessages([
          ...next,
          {
            role: "assistant",
            content:
              data?.detail || `Sorry — that failed with HTTP ${r.status}.`,
          },
        ]);
      } else {
        setMessages([
          ...next,
          {
            role: "assistant",
            content: data.answer,
            intent: data.intent,
            mock: data.mock,
          },
        ]);
      }
    } catch (e) {
      setMessages([
        ...next,
        { role: "assistant", content: `Error: ${e}` },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Ask Copilot"
        aria-expanded={open}
        className="fixed bottom-4 right-4 z-50 flex size-12 items-center justify-center rounded-full bg-success text-success-foreground shadow-lg transition-transform hover:scale-105 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
      >
        {open ? <X className="size-5" aria-hidden /> : <MessageSquare className="size-5" aria-hidden />}
      </button>
      {open && (
        <Card
          role="dialog"
          aria-label="RTO Copilot"
          className="fixed bottom-20 right-4 z-50 flex h-[28rem] w-[22rem] max-w-[calc(100vw-2rem)] flex-col overflow-hidden p-0 shadow-2xl"
        >
          <div className="flex items-center justify-between border-b border-border/60 bg-muted/40 px-3 py-2">
            <div className="flex items-center gap-2">
              <Sparkles className="size-3.5 text-success" aria-hidden />
              <span className="text-sm font-semibold">RTO Copilot</span>
              <Badge variant="outline" className="text-[10px]">NL Q&amp;A</Badge>
            </div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Close Copilot"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto space-y-2 p-3 text-xs">
            {messages.map((m, i) => (
              <div
                key={i}
                className={
                  m.role === "user"
                    ? "ml-6 rounded-md bg-success/15 px-2 py-1.5 text-foreground"
                    : "mr-2 rounded-md border border-border/60 bg-muted/30 px-2 py-1.5"
                }
              >
                <p className="whitespace-pre-wrap text-xs leading-relaxed">{m.content}</p>
                {(m.intent || m.mock) && (
                  <div className="mt-1 flex items-center gap-1">
                    {m.intent && (
                      <span className="text-[10px] text-muted-foreground">intent: {m.intent}</span>
                    )}
                    {m.mock && <MockModeBadge mock={!!m.mock} />}
                  </div>
                )}
              </div>
            ))}
            {loading && (
              <div className="mr-2 space-y-1 rounded-md border border-border/60 bg-muted/30 px-2 py-1.5">
                <Skeleton className="h-3 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            )}
          </div>
          <div className="border-t border-border/60 p-2">
            <div className="mb-1.5 flex flex-wrap gap-1">
              {SUGGESTIONS.slice(0, 3).map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => ask(s)}
                  className="rounded-md border border-border/60 bg-muted/30 px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                >
                  {s}
                </button>
              ))}
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                ask(input);
              }}
              className="flex items-center gap-2"
            >
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask about orders, audit, drift…"
                className="h-8 text-xs"
                aria-label="Copilot prompt"
              />
              <Button type="submit" size="icon" className="size-8" disabled={loading}>
                <Send className="size-3.5" aria-hidden />
              </Button>
            </form>
          </div>
        </Card>
      )}
    </>
  );
}
