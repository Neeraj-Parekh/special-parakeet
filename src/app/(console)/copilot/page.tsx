"use client";

// AI Copilot — the full-page policy-bounded chat.
//
// The boundedness guarantee lives server-side in /api/copilot/route.ts: a
// deterministic intent classifier runs FIRST and decides the verdict
// (refused | read | simulated | unknown) before any LLM call — so a
// "block order" prompt can never come back as anything but REFUSED. This
// page just renders the conversation, the verdict pill, the source chips,
// and the policy citation the server attached.
//
// Conversation state is component-local (no storage). No auth header is
// needed for POST /api/copilot.

import * as React from "react";
import { Bot, SendHorizonal } from "lucide-react";

import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MockModeBadge } from "@/components/app-header";
import { cn } from "@/lib/utils";

// ----------------------------------------------------------------------------
// Types — mirrors CopilotResponse from src/app/api/copilot/route.ts.
// ----------------------------------------------------------------------------

type Verdict = "refused" | "read" | "simulated" | "unknown";

interface CopilotResponse {
  answer: string;
  verdict: Verdict;
  policyCite?: string;
  sources: string[];
  mock: boolean;
}

interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  verdict?: Verdict;
  policyCite?: string;
  sources?: string[];
  mock?: boolean;
  error?: boolean;
}

const SUGGESTED_PROMPTS: string[] = [
  "Block order ORD-123",
  "What's the drift state?",
  "List active rules",
  "Explain why ORD-123 was REJECTED",
  "What's the current model health?",
  "Show me the audit chain status",
];

const VERDICT_STYLES: Record<Verdict, string> = {
  refused: "border-signal-red/30 bg-signal-red/10 text-danger",
  read: "border-mint-500/30 bg-mint-500/10 text-mint-700",
  simulated: "border-brand-500/30 bg-brand-500/10 text-brand-600",
  unknown: "border-border bg-muted/40 text-muted-foreground",
};

const VERDICT_LABELS: Record<Verdict, string> = {
  refused: "REFUSED",
  read: "READ",
  simulated: "SIMULATED",
  unknown: "UNKNOWN",
};

const PILL_BASE =
  "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wide";

const UNREACHABLE = "Copilot unreachable — try again.";

// ----------------------------------------------------------------------------
// Page
// ----------------------------------------------------------------------------

export default function CopilotPage() {
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [input, setInput] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const idRef = React.useRef(0);
  const listRef = React.useRef<HTMLDivElement | null>(null);

  // Auto-scroll to the newest message.
  React.useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  async function send(question: string) {
    const q = question.trim();
    if (!q || loading) return;
    setInput("");
    const userId = idRef.current + 1;
    idRef.current = userId;
    setMessages((prev) => [...prev, { id: userId, role: "user", text: q }]);
    setLoading(true);
    try {
      const r = await fetch("/api/copilot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, scope: "scorer" }),
      });
      const data = (await r.json().catch(() => null)) as CopilotResponse | null;
      const replyId = idRef.current + 1;
      idRef.current = replyId;
      if (!r.ok || !data) {
        setMessages((prev) => [
          ...prev,
          { id: replyId, role: "assistant", text: UNREACHABLE, error: true },
        ]);
        return;
      }
      setMessages((prev) => [
        ...prev,
        {
          id: replyId,
          role: "assistant",
          text: data.answer,
          verdict: data.verdict,
          policyCite: data.policyCite,
          sources: Array.isArray(data.sources) ? data.sources : [],
          mock: data.mock,
        },
      ]);
    } catch {
      const errorId = idRef.current + 1;
      idRef.current = errorId;
      setMessages((prev) => [
        ...prev,
        { id: errorId, role: "assistant", text: UNREACHABLE, error: true },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">AI Copilot</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          Policy-bounded operator assistant — refusals are code-enforced before
          the LLM runs. Try asking it to block an order.
        </p>
      </div>

      {/* Chat card — fills most of the page */}
      <Card className="flex h-[calc(100vh-15rem)] min-h-[32rem] max-h-[60rem] flex-col gap-0 py-0 shadow-card">
        <div className="border-b border-border/60">
          <CardHeader className="py-4">
            <CardTitle className="flex items-center gap-2 text-base">
              <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-brand-500/10 text-brand-500">
                <Bot className="size-4" aria-hidden />
              </span>
              Bounded operator console
            </CardTitle>
            <CardDescription>
              Every question is classified server-side first — READ, SIMULATE,
              or REFUSED. The verdict is enforced in code before any LLM call.
            </CardDescription>
          </CardHeader>
        </div>

        {/* Message list */}
        <div ref={listRef} className="flex-1 space-y-4 overflow-y-auto p-4">
          {messages.length === 0 && !loading && <EmptyChat />}
          {messages.map((m) => (
            <ChatBubble key={m.id} message={m} />
          ))}
          {loading && <ThinkingBubble />}
        </div>

        {/* Composer */}
        <div className="space-y-3 border-t border-border/60 p-4">
          <div className="flex flex-wrap gap-2">
            {SUGGESTED_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => send(prompt)}
                disabled={loading}
                className="rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground shadow-xs transition-colors duration-200 ease-brand hover:border-brand-500/50 hover:bg-brand-500/5 hover:text-brand-600 disabled:pointer-events-none disabled:opacity-50"
              >
                {prompt}
              </button>
            ))}
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask a read-only question — or try to make it block an order"
              className="h-11 flex-1"
              disabled={loading}
              aria-label="Question for the AI Copilot"
              autoComplete="off"
            />
            <Button
              type="submit"
              disabled={loading || input.trim().length === 0}
              className="h-11 px-5"
            >
              {loading ? "Thinking…" : "Send"}
              {!loading && <SendHorizonal className="size-4" aria-hidden />}
            </Button>
          </form>
        </div>
      </Card>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Subcomponents
// ----------------------------------------------------------------------------

function ChatBubble({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand-500 px-4 py-2.5 text-sm text-white">
          {message.text}
        </div>
      </div>
    );
  }

  if (message.error) {
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] rounded-2xl rounded-bl-md bg-signal-red/10 px-4 py-2.5 text-sm text-danger">
          {message.text}
        </div>
      </div>
    );
  }

  const sources = message.sources ?? [];
  return (
    <div className="flex flex-col items-start gap-1.5">
      <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-bl-md border border-border bg-card px-4 py-2.5 text-sm text-foreground">
        {message.text}
      </div>
      {message.policyCite && (
        <p className="max-w-[80%] border-l-2 border-warning pl-2 text-xs text-muted-foreground">
          {message.policyCite}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        {message.verdict && (
          <span className={cn(PILL_BASE, VERDICT_STYLES[message.verdict])}>
            {VERDICT_LABELS[message.verdict]}
          </span>
        )}
        {sources.map((s) => (
          <span
            key={s}
            className="rounded-md border border-border/70 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
          >
            {s}
          </span>
        ))}
        <MockModeBadge mock={!!message.mock} />
      </div>
    </div>
  );
}

function ThinkingBubble() {
  return (
    <div className="flex justify-start">
      <div
        className="flex items-center gap-1.5 rounded-2xl rounded-bl-md border border-border bg-card px-4 py-3"
        role="status"
        aria-label="Copilot is thinking"
      >
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="size-1.5 rounded-full bg-muted-foreground/60 motion-safe:animate-pulse"
            style={{ animationDelay: `${i * 150}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

function EmptyChat() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 py-12 text-center">
      <Bot className="size-8 text-muted-foreground/40" aria-hidden />
      <p className="text-sm text-muted-foreground">
        No messages yet — try a suggested prompt below.
      </p>
      <p className="max-w-sm text-xs text-muted-foreground/70">
        The copilot can READ live state and SIMULATE what-ifs. It cannot block,
        override, or delete — those paths are refused in code before the LLM
        ever runs.
      </p>
    </div>
  );
}
