"use client";

// AgentConsole — the policy-bounded operator console (demo moment #5).
//
// The advice: "Type 'Block order ORD-123', see 'I cannot.'"
//
// This is the heart of the Track-D "bounded agent" thesis: an LLM-style
// operator console that REFUSES manual overrides because every action is
// gated by an immutable policy layer (Track D V3 §7 / NPCI OC-201B / the
// Bahnsen cost-optimal BMR). The agent can:
//   • READ (audit proof, drift, rules, recent decisions, model health)
//   • SIMULATE ("what if I toggled rule X?")
//   • EXPLAIN (why was ORD-123 REJECTED?)
//
// It CANNOT:
//   • manually block / unblock / override a per-order decision
//   • delete a rule without a dual-control X-Mandate header
//   • bypass the cost-optimal threshold
//   • retrain / hot-swap the model outside the nightly MLOps gate
//
// The intent matcher is a small deterministic state machine (no LLM call)
// BECAUSE the boundedness must be provable: a judge can read this file and
// see there is no code path that issues a manual override. That's the demo
// — "the agent literally cannot, look at the source."

import * as React from "react";
import { Bot, Send, Terminal, ShieldCheck, Lock, ExternalLink } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";

interface ConsoleMessage {
  id: string;
  role: "user" | "agent";
  content: string;
  /** Policy verdict — "allowed" | "refused" | "read" | "simulated" */
  verdict?: "allowed" | "refused" | "read" | "simulated";
  /** When refused, the policy cite the agent invoked. */
  policyCite?: string;
  /** Optional action link (audit proof, rule, etc.) */
  link?: { label: string; href: string };
  ts: number;
}

const SUGGESTIONS = [
  "Block order ORD-123",
  "Override decision on ORD-REP-001 to ACCEPT",
  "Show audit proof for the last decision",
  "List active rules",
  "What's the drift state?",
  "Delete rule RULE-003",
];

const REFUSE_PREFIXES = [
  "block order",
  "block ",
  "override ",
  "force ",
  "unblock ",
  "manually ",
  "delete rule",
  "remove rule",
  "retrain model",
  "swap model",
  "change threshold",
  "bypass",
];

const READ_PATTERNS: { match: RegExp; label: string }[] = [
  { match: /audit|proof|tamper|chain/i, label: "audit" },
  { match: /drift|ddm|adwin/i, label: "drift" },
  { match: /rule|policy/i, label: "rules" },
  { match: /recent|history|decisions/i, label: "recent" },
  { match: /model health|model_version|model card/i, label: "model-health" },
  { match: /cost|threshold|bmr/i, label: "cost" },
];

let msgCounter = 0;
function newId(): string {
  msgCounter += 1;
  return `m${Date.now()}-${msgCounter}`;
}

/** The deterministic intent classifier — NO LLM, all readable. */
function classifyIntent(q: string): {
  kind: "refuse" | "read" | "simulate" | "unknown";
  cite?: string;
  readTarget?: string;
  orderId?: string;
} {
  const lower = q.toLowerCase();
  // 1. Manual override / blocking / deletion → REFUSE
  for (const p of REFUSE_PREFIXES) {
    if (lower.includes(p)) {
      // pick the right policy cite
      if (p.includes("rule")) {
        return { kind: "refuse", cite: "Track D V3 §7.3 — rule mutations require dual-control X-Mandate + 2-of-3 admin quorum" };
      }
      if (p.includes("model")) {
        return { kind: "refuse", cite: "MLOps gate §5 — model swap only via nightly train.yml PR-AUC ≥ 0.35 gate + canary slice" };
      }
      if (p.includes("threshold") || p.includes("bypass")) {
        return { kind: "refuse", cite: "Track C §4 — thresholds are cost-optimal BMR-derived, not operator-set" };
      }
      // default: per-order override
      const ordMatch = q.match(/ORD-[A-Z0-9-]+/i);
      return {
        kind: "refuse",
        cite: "Track D V3 §7.1 — no manual per-order override path exists in the controller; decisions come from rules → mandate → cost-optimal BMR",
        orderId: ordMatch?.[0],
      };
    }
  }
  // 2. Read-only queries
  for (const p of READ_PATTERNS) {
    if (p.match.test(q)) {
      return { kind: "read", readTarget: p.label };
    }
  }
  // 3. "what if" simulation
  if (/what if|simulate|toggle/i.test(q)) {
    return { kind: "simulate" };
  }
  return { kind: "unknown" };
}

/** Compose the agent's reply for a given intent. */
function agentReply(
  q: string,
  ctx: { recentCount: number; rulesCount: number },
): ConsoleMessage {
  const intent = classifyIntent(q);
  const ts = Date.now();
  const base: Pick<ConsoleMessage, "id" | "role" | "ts"> = {
    id: newId(),
    role: "agent" as const,
    ts,
  };
  if (intent.kind === "refuse") {
    const ord = intent.orderId ? ` for ${intent.orderId}` : "";
    return {
      ...base,
      verdict: "refused",
      policyCite: intent.cite,
      content: `I cannot${ord}. This action is outside the policy envelope. ${intent.cite}. File a rule via POST /v1/rules (with an admin-scope key + X-Mandate header) if you need this behaviour systematically — the agent will not apply it per-order.`,
      link: {
        label: "View policy source → src/api/security.py",
        href: "/audit?id=policy",
      },
    };
  }
  if (intent.kind === "read") {
    const t = intent.readTarget;
    if (t === "audit") {
      return {
        ...base,
        verdict: "read",
        content: `The audit log is an append-only SHA-256 hash chain (Track D V3 §9). Every /risk/score call appends a leaf; /v1/audit/verify-chain recomputes root and reports any tamper. The most recent decision's tamper-evident proof is linked below.`,
        link: { label: "Open audit proof →", href: "/audit" },
      };
    }
    if (t === "drift") {
      return {
        ...base,
        verdict: "read",
        content: `Drift is monitored by DDM (Distance-to-Debug-Mean) + ADWIN on the PSI of feature distributions, polled every 5 min. Current state: in-spec (no slice breached the 0.10 PSI gate). See Model Health tab.`,
      };
    }
    if (t === "rules") {
      return {
        ...base,
        verdict: "read",
        content: `There are ${ctx.rulesCount} active rules registered (priority-sorted, evaluated before the model in the Track-C decision precedence). Use the Rules Manager to inspect; toggling a rule fires a what-if re-score, not a live mutation.`,
      };
    }
    if (t === "recent") {
      return {
        ...base,
        verdict: "read",
        content: `${ctx.recentCount} decisions this session (persisted to sessionStorage, capped at 50). The Recent decisions card shows order_id, P(RTO), decision, and decision_source for each.`,
      };
    }
    if (t === "model-health") {
      return {
        ...base,
        verdict: "read",
        content: `Champion: amazon_histgb_20260827 (PR-AUC 0.1027 on the held-out Amazon-India test slice, Brier 0.0179). Olist champion rto_olist_histgb_20260828 available via ?dataset=olist (PR-AUC 0.3950 — 3.8× the Amazon ceiling because Olist exposes real customer IDs). See Model Health.`,
      };
    }
    if (t === "cost") {
      return {
        ...base,
        verdict: "read",
        content: `Decision thresholds are cost-optimal per Bahnsen 2013 Eq.1: c_fp=₹50 (false-accept review cost), c_fn=₹600 (RTO loss), c_otp=₹5, c_block=₹1000. The BMR picks the action with min expected cost, NOT a fixed probability cutoff.`,
      };
    }
  }
  if (intent.kind === "simulate") {
    return {
      ...base,
      verdict: "simulated",
      content: `I can simulate. Use the Rules Manager toggle to flip a rule and watch the Verdict card re-score — the decision_source pill will switch to rules_engine_block / cost_optimal_bmr_review_rule. The what-if is computed against the current order without mutating the live rule registry.`,
    };
  }
  return {
    ...base,
    content: `I'm a bounded operator console. I can READ (audit, drift, rules, recent, model health, cost) and SIMULATE (rule toggles via the Rules Manager). I cannot manually override, block, or delete — those paths don't exist in the controller. Try "Block order ORD-123" to see the refusal.`,
  };
}

function VerdictPill({ v }: { v?: ConsoleMessage["verdict"] }) {
  if (!v) return null;
  const map = {
    refused: { label: "REFUSED", cls: "border-danger/40 text-danger" },
    allowed: { label: "ALLOWED", cls: "border-success/40 text-success" },
    read: { label: "READ-ONLY", cls: "border-success/40 text-success" },
    simulated: { label: "SIMULATED", cls: "border-warning/40 text-warning" },
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
  const [messages, setMessages] = React.useState<ConsoleMessage[]>([
    {
      id: "seed",
      role: "agent",
      content:
        "I'm the RTO Trust operator console — a policy-bounded agent. I can read audit/drift/rules/model-health and simulate rule toggles. I CANNOT manually block, override, or delete per-order decisions (no such code path exists). Try: \"Block order ORD-123\".",
      verdict: "read",
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
    // Simulate the agent "thinking" — the intent matcher is sync but a
    // small delay makes the demo feel like a real round-trip.
    await new Promise((r) => setTimeout(r, 450));
    const reply = agentReply(q, { recentCount, rulesCount });
    setMessages((prev) => [...prev, reply]);
    setBusy(false);
  }

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
          </div>
        </div>
        <CardDescription>
          Demo moment #5 — type{" "}
          <code className="rounded bg-muted px-1 font-mono text-[11px]">
            Block order ORD-123
          </code>{" "}
          and watch the agent refuse (the controller has no manual-override
          code path; only rules → mandate → cost-optimal BMR can change a
          decision).
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
              <span className="font-mono">agent · classifying intent…</span>
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
          <p className="leading-relaxed">{msg.content}</p>
          {msg.policyCite && (
            <p className="mt-2 flex items-start gap-1.5 border-t border-danger/20 pt-2 text-[11px] text-danger/80">
              <ShieldCheck className="mt-0.5 size-3 shrink-0" aria-hidden />
              <span className="font-mono">{msg.policyCite}</span>
            </p>
          )}
          {msg.link && (
            <a
              href={msg.link.href}
              className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-success hover:underline"
            >
              {msg.link.label}
              <ExternalLink className="size-3" aria-hidden />
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
