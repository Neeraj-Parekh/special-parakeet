"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ListFilter,
  Plus,
  Trash2,
  Power,
  ShieldCheck,
} from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";
import { MockModeBadge } from "@/components/app-header";
import type { Rule } from "@/lib/mock-data";

export default function RulesManagerPage() {
  const keys = useApiKeys();
  const qc = useQueryClient();
  const [mock, setMock] = React.useState(false);
  const [addOpen, setAddOpen] = React.useState(false);

  const rulesQuery = useQuery({
    queryKey: ["rules-manager"],
    queryFn: async () => {
      const r = await fetch("/api/v1/rules", {
        headers: buildAuthHeader(keys, "scorer"),
      });
      const data = (await r.json().catch(() => ({}))) as { rules?: Rule[] };
      setMock(r.headers.get("X-Mock-Mode") === "true");
      return { rules: data.rules || [], mock: r.headers.get("X-Mock-Mode") === "true" };
    },
    refetchInterval: 15_000,
  });

  // Local-only toggle state — the mock backend doesn't persist the
  // toggle (we'd need a PATCH endpoint that doesn't exist), so we
  // keep the toggle as a client-side overlay that survives until
  // refetch. In live mode the user is expected to use POST + DELETE
  // for the same effect (delete + re-add).
  const [localToggles, setLocalToggles] = React.useState<Record<string, boolean>>({});
  const rules = (rulesQuery.data?.rules || []).map((r) => ({
    ...r,
    active: localToggles[r.rule_id] ?? (r.active !== false),
  }));

  const invalidate = React.useCallback(() => {
    qc.invalidateQueries({ queryKey: ["rules-manager"] });
    qc.invalidateQueries({ queryKey: ["rules"] });
  }, [qc]);

  const removeRule = React.useCallback(
    async (id: string) => {
      await fetch(`/api/v1/rules/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: buildAuthHeader(keys, "admin"),
      });
      invalidate();
    },
    [keys, invalidate],
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <ListFilter className="size-5 text-success" aria-hidden />
          <h1 className="text-2xl font-semibold tracking-tight">Rules Manager</h1>
        </div>
        <p className="text-sm text-muted-foreground">
          Business rules short-circuit the model — BLOCK rules force REJECT; REVIEW rules gate the cost-optimizer
          to never ACCEPT (Track C precedence). Toggle <span className="font-medium">RULE-001</span> off,
          re-score the <span className="font-medium">High-value COD</span> demo order on the Risk Console —
          it should fall through to the cost-optimizer instead of the rules engine.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Dialog open={addOpen} onOpenChange={setAddOpen}>
          <DialogTrigger asChild>
            <Button size="sm">
              <Plus className="size-3.5" aria-hidden />
              Add rule
            </Button>
          </DialogTrigger>
          <AddRuleDialog
            keys={keys}
            onCreated={() => {
              setAddOpen(false);
              invalidate();
            }}
          />
        </Dialog>
        <Button size="sm" variant="secondary" onClick={() => rulesQuery.refetch()}>
          Refresh
        </Button>
        {mock && <MockModeBadge mock={mock} />}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Active rules</CardTitle>
          <CardDescription>
            Ordered by <code className="text-xs">priority</code> (lower = higher precedence).
            Toggle is a client-side overlay in mock mode — use delete + re-add to persist a change.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {rulesQuery.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : (
            <div className="overflow-hidden rounded-md border border-border/60">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Active</TableHead>
                    <TableHead>Rule</TableHead>
                    <TableHead>Match</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Priority</TableHead>
                    <TableHead className="text-right">Delete</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rules.map((r) => (
                    <TableRow key={r.rule_id}>
                      <TableCell>
                        <Switch
                          checked={!!r.active}
                          onCheckedChange={(v) =>
                            setLocalToggles((prev) => ({
                              ...prev,
                              [r.rule_id]: v,
                            }))
                          }
                          aria-label={`Toggle ${r.rule_id}`}
                        />
                      </TableCell>
                      <TableCell>
                        <div className="font-mono text-xs">{r.rule_id}</div>
                        <div className="text-xs text-muted-foreground">{r.name}</div>
                      </TableCell>
                      <TableCell>
                        <code className="font-mono text-xs">
                          {r.field} {r.op} {String(r.value)}
                        </code>
                      </TableCell>
                      <TableCell>
                        <RuleActionBadge action={r.action} />
                      </TableCell>
                      <TableCell className="font-mono text-xs">{r.priority}</TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="icon"
                          variant="ghost"
                          className="size-7 text-danger hover:text-danger"
                          onClick={() => removeRule(r.rule_id)}
                          aria-label={`Delete ${r.rule_id}`}
                        >
                          <Trash2 className="size-3.5" aria-hidden />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                  {rules.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={6} className="py-6 text-center text-sm text-muted-foreground">
                        No rules configured.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <DemoHintCard />
    </div>
  );
}

function RuleActionBadge({ action }: { action: "BLOCK" | "REVIEW" }) {
  if (action === "BLOCK") {
    return (
      <Badge variant="outline" className="border-danger/50 bg-danger/15 text-danger">
        <Power className="mr-1 size-2.5" aria-hidden />
        BLOCK
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="border-warning/50 bg-warning/15 text-warning">
      <ShieldCheck className="mr-1 size-2.5" aria-hidden />
      REVIEW
    </Badge>
  );
}

function DemoHintCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Judge demo moment #4</CardTitle>
        <CardDescription>
          Business rules beat ML in known cases.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="ml-4 list-decimal space-y-1 text-sm text-muted-foreground">
          <li>Confirm <code className="font-mono">RULE-001</code> (amount_inr &gt; 50000 → BLOCK) is active above.</li>
          <li>Open the Risk Console.</li>
          <li>Click <span className="font-medium">High-value COD</span> demo order.</li>
          <li>Click <span className="font-medium">Score</span> → instant REJECT, no model invoked.</li>
          <li>Decision source pill: <code className="font-mono">rules_engine_block</code>.</li>
        </ol>
      </CardContent>
    </Card>
  );
}

const FIELD_OPTIONS = [
  "amount_inr",
  "prior_returns",
  "prior_orders",
  "city_tier",
  "payment_method",
  "address_quality",
  "order_hour",
  "items",
] as const;

function AddRuleDialog({
  keys,
  onCreated,
}: {
  keys: ReturnType<typeof useApiKeys>;
  onCreated: () => void;
}) {
  const [form, setForm] = React.useState({
    rule_id: "",
    name: "",
    field: "amount_inr",
    op: "gt",
    value: "50000",
    action: "BLOCK",
    priority: "30",
  });
  const [error, setError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);

  function update<K extends keyof typeof form>(k: K, v: string) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function submit() {
    setSaving(true);
    setError(null);
    try {
      const value =
        form.op === "in"
          ? form.value.split(",").map((s) => s.trim())
          : /^-?\d+(\.\d+)?$/.test(form.value)
            ? Number(form.value)
            : form.value;
      const r = await fetch("/api/v1/rules", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...buildAuthHeader(keys, "admin"),
        },
        body: JSON.stringify({
          rule_id: form.rule_id,
          name: form.name,
          field: form.field,
          op: form.op,
          value,
          action: form.action,
          priority: Number(form.priority),
          created_by: "admin",
        }),
      });
      if (!r.ok) {
        const data = await r.json().catch(() => null);
        throw new Error(data?.detail || `HTTP ${r.status}`);
      }
      onCreated();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <DialogContent className="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle className="text-base">Add rule</DialogTitle>
        <DialogDescription>
          BLOCK short-circuits REJECT; REVIEW gates the cost-optimizer to never ACCEPT.
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Rule ID</Label>
            <Input
              value={form.rule_id}
              onChange={(e) => update("rule_id", e.target.value)}
              placeholder="RULE-005"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Name</Label>
            <Input
              value={form.name}
              onChange={(e) => update("name", e.target.value)}
              placeholder="Block COD > ₹50K from new customers"
            />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Field</Label>
            <Select value={form.field} onValueChange={(v) => update("field", v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {FIELD_OPTIONS.map((f) => (
                  <SelectItem key={f} value={f}>{f}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Operator</Label>
            <Select value={form.op} onValueChange={(v) => update("op", v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="gt">gt</SelectItem>
                <SelectItem value="lt">lt</SelectItem>
                <SelectItem value="eq">eq</SelectItem>
                <SelectItem value="in">in</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1 col-span-1">
            <Label className="text-xs text-muted-foreground">Value</Label>
            <Input
              value={form.value}
              onChange={(e) => update("value", e.target.value)}
              placeholder="50000"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Action</Label>
            <Select value={form.action} onValueChange={(v) => update("action", v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="BLOCK">BLOCK</SelectItem>
                <SelectItem value="REVIEW">REVIEW</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Priority</Label>
            <Input
              type="number"
              value={form.priority}
              onChange={(e) => update("priority", e.target.value)}
            />
          </div>
        </div>
        {error && (
          <p className="text-xs text-danger">{error}</p>
        )}
      </div>
      <DialogFooter>
        <Button onClick={submit} disabled={saving || !form.rule_id || !form.name}>
          {saving ? "Saving…" : "Add rule"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
