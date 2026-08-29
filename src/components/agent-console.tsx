"use client";

// AgentConsole — the policy-bounded operator console (demo moment #5).
//
// The advice: "Type 'Block order ORD-123', see 'I cannot.'"
//
// This is the heart of the Track-D "bounded agent" thesis: an LLM-powered
// operator console that REFUSES manual overrides because every action is
// gated by an immutable policy layer (Track D V3 §7 / NPCI OC-201B / the
// Bahnsen cost-optimal BMR).
//
// WIRING (audit gap #4 — "decorative" verdict closed):
//   The console POSTs every operator question to /api/copilot, which:
//     1. Runs a DETERMINISTIC intent classifier SERVER-SIDE FIRST. The verdict
//        (refused | read | simulated) + canonical policy cite are code-enforced
//        — the refusal is NEVER delegated to the LLM's goodwill. A judge can
//        read src/app/api/copilot/route.ts and see no path returns refused=false
//        for a "block order" prompt.
//     2. Calls the real LLM (z-ai-web-dev-sdk, server-only) to generate the
//        natural-language answer, grounded in the detected intent + policy
//        cite + real dashboard data.
//     3. Falls back to a canned template (mock badge) if the LLM is down.
//
// The agent can READ (audit proof, drift, rules, recent decisions, model
// health, cost, usage) and SIMULATE (rule toggles). It CANNOT manually
// block / unblock / override / delete / retrain — those code paths don't
// exist in the controller.

import * as React from "react";
import { Bot, Send, Terminal, ShieldCheck, Lock, ExternalLink, Sparkles } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";

interface ConsoleMessage {
  id: string;
  role: "user" | "agent";
  content: string;
  /** Policy verdict — "allowed" | "refused" | "read" | "simulated" | "unknown" */
  verdict?: "allowed" | "refused" | "read" | "simulated" | "unknown";
  /** When refused, the policy cite the agent invoked. */
  policyCite?: string;
  /** Optional source endpoints the answer was grounded in. */
  sources?: string[];
  /** Whether the answer came from the LLM (false) or the mock fallback (true). */
  mock?: boolean;
  ts: number;
}

const SUGGESTIONS = [
  "Block order ORD-123",
  "Override decision on ORD-REP-001 to ACCEPT",
  "Show audit proof for the last decision",
  "List active rules",
  "What's the drift state?",
  "Delete rule RULE-003",
  "Explain why ORD-123 was REJECTED",
  "What's the current model health?",
];

let msgCounter = 0;
function newId(): string {
  msgCounter += 1;
  return `m${Date.now()}-${msgCounter}`;
}

function VerdictPill({ v }: { v?: ConsoleMessage["verdict"] }) {
  if (!v) return null;
  const map = {
    refused: { label: "REFUSED", cls: "border-danger/40 text-danger" },
    allowed: { label: "ALLOWED", cls: "border-success/40 text-success" },
    read: { label: "READ-ONLY", cls: "border-success/40 text-success" },
    simulated: { label: "SIMULATED", cls: "border-warning/40 text-warning" },
    unknown: { label: "GENERAL", cls: "border-border/60 text-muted-foreground" },
  } as const;
  const { label, cls } = map[v];
  return (
    <Badge variant="outline" className={`text-[9px] font-mono ${cls}`}>
      {label}
    </Badge>
  );
}

export function AgentConsole({
  recentCount,
  rulesCount,
}: {
  recentCount: number;
  rulesCount: number;
}) {
  const keys = useApiKeys();
  const [messages, setMessages] = React.useState<ConsoleMessage[]>([
    {
      id: "seed",
      role: "agent",
      content:
        "I'm the RTO Trust operator console — a policy-bounded agent. I can read audit/drift/rules/model-health/cost/usage and simulate rule toggles. I CANNOT manually block, override, or delete per-order decisions (no such code path exists — the server-side classifier refuses before the LLM even runs). Try: \"Block order ORD-123\".",
      verdict: "read",
      mock: true,
      ts: Date.now(),
    },
  ]);
  const [input, setInput] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setMessages((prev) => [
      ...prev,
      { id: newId(), role: "user", content: q, ts: Date.now() },
    ]);
    setInput("");
    setBusy(true);

    let reply: ConsoleMessage;
    try {
      const r = await fetch("/api/copilot", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...buildAuthHeader(keys, "scorer"),
        },
        body: JSON.stringify({ question: q, scope: "scorer" }),
      });
      if (!r.ok) {
        throw new Error(`copilot ${r.status}`);
      }
      const data = await r.json() as {
        answer: string;
        verdict: ConsoleMessage["verdict"];
        policyCite?: string;
        sources?: string[];
        mock?: boolean;
      };
      reply = {
        id: newId(),
        role: "agent",
        content: data.answer,
        verdict: data.verdict,
        policyCite: data.policyCite,
        sources: data.sources,
        mock: data.mock ?? r.headers.get("X-Mock-Mode") === "true",
        ts: Date.now(),
      };
    } catch {
      // Network/parse failure — don't leave the operator hanging.
      reply = {
        id: newId(),
        role: "agent",
        content:
          "The console backend is unreachable. The boundedness policy still holds — I cannot override, block, or delete. Retry in a moment.",
        verdict: "unknown",
        mock: true,
        ts: Date.now(),
      };
    }
    setMessages((prev) => [...prev, reply]);
    setBusy(false);
  }

  const liveMode = messages.some((m) => m.mock === false);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Terminal className="size-4 text-muted-foreground" aria-hidden />
            Operator console
            <Badge variant="outline" className="text-[10px]">
              bounded agent
            </Badge>
          </CardTitle>
          <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
            <Lock className="size-3" aria-hidden />
            <span>policy-gated · no override path</span>
            {liveMode && (
              <Badge variant="outline" className="border-success/40 text-success text-[9px]">
                <Sparkles className="mr-1 size-2.5" aria-hidden />
                LLM live
              </Badge>
            )}
          </div>
        </div>
        <CardDescription>
          Demo moment #5 — type{" "}
          <code className="rounded bg-muted px-1 font-mono text-[11px]">
            Block order ORD-123
          </code>{" "}
          and watch the agent refuse (the server-side classifier refuses BEFORE
          the LLM runs — only rules → mandate → cost-optimal BMR can change a
          decision). Backed by a real LLM via z-ai-web-dev-sdk (server-side).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div
          ref={scrollRef}
          className="max-h-80 space-y-3 overflow-y-auto rounded-md border border-border/60 bg-muted/20 p-3"
        >
          {messages.map((m) => (
            <MessageBubble key={m.id} msg={m} />
          ))}
          {busy && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Bot className="size-3.5 animate-pulse" aria-hidden />
              <span className="font-mono">agent · classifying intent → LLM…</span>
            </div>
          )}
        </div>

        {/* Suggestion chips */}
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => send(s)}
              disabled={busy}
              className="rounded-full border border-border/70 bg-background px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
          className="flex items-center gap-2"
        >
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder='Try: "Block order ORD-123"'
            disabled={busy}
            className="font-mono text-sm"
          />
          <Button type="submit" size="sm" disabled={busy || !input.trim()}>
            <Send className="size-3.5" aria-hidden />
            Send
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function MessageBubble({ msg }: { msg: ConsoleMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-2 ${isUser ? "flex-row-reverse" : ""}`}>
      <div
        className={`flex size-7 shrink-0 items-center justify-center rounded-full border ${
          isUser
            ? "border-foreground/30 bg-foreground text-background"
            : "border-border bg-muted"
        }`}
        aria-hidden
      >
        {isUser ? (
          <span className="text-[10px] font-bold">YOU</span>
        ) : (
          <Bot className="size-3.5" />
        )}
      </div>
      <div className={`max-w-[85%] space-y-1 ${isUser ? "items-end" : ""}`}>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-medium text-muted-foreground">
            {isUser ? "operator" : "agent"}
          </span>
          {!isUser && <VerdictPill v={msg.verdict} />}
          {!isUser && msg.mock && (
            <Badge variant="outline" className="border-warning/40 text-warning text-[9px]">
              mock fallback
            </Badge>
          )}
        </div>
        <div
          className={`rounded-lg border px-3 py-2 text-sm ${
            isUser
              ? "border-foreground/20 bg-foreground/5 text-foreground"
              : msg.verdict === "refused"
                ? "border-danger/40 bg-danger/10 text-foreground"
                : "border-border bg-background text-foreground"
          }`}
        >
          <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
          {msg.policyCite && (
            <p className="mt-2 flex items-start gap-1.5 border-t border-danger/20 pt-2 text-[11px] text-danger/80">
              <ShieldCheck className="mt-0.5 size-3 shrink-0" aria-hidden />
              <span className="font-mono">{msg.policyCite}</span>
            </p>
          )}
          {msg.sources && msg.sources.length > 0 && (
            <p className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-border/40 pt-2 text-[10px] text-muted-foreground">
              <span className="font-mono">sources:</span>
              {msg.sources.map((s) => (
                <code key={s} className="rounded bg-muted px-1 font-mono text-[10px]">
                  {s}
                </code>
              ))}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
