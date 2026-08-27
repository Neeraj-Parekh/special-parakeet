"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import {
  FileClock,
  ShieldCheck,
  ShieldAlert,
  Download,
  TreePine,
  Hash,
} from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";
import { MockModeBadge } from "@/components/app-header";
import {
  DecisionBadge,
  DecisionSourcePill,
} from "@/components/decision-badge";
import type { AuditRecord, MerkleProof } from "@/lib/mock-data";

interface AuditListResponse {
  records: AuditRecord[];
  source?: string;
}

interface VerifyResult {
  intact: boolean;
  records_checked: number;
  first_bad_audit_id: string | null;
}

export default function AuditExplorerPage() {
  const params = useSearchParams();
  const keys = useApiKeys();
  const [records, setRecords] = React.useState<AuditRecord[]>([]);
  const [loadingList, setLoadingList] = React.useState(true);
  const [mockList, setMockList] = React.useState(false);

  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [detail, setDetail] = React.useState<AuditRecord | null>(null);
  const [loadingDetail, setLoadingDetail] = React.useState(false);
  const [detailError, setDetailError] = React.useState<string | null>(null);
  const [detailMock, setDetailMock] = React.useState(false);

  const [proof, setProof] = React.useState<MerkleProof | null>(null);
  const [proofOpen, setProofOpen] = React.useState(false);

  const [verify, setVerify] = React.useState<VerifyResult | null>(null);
  const [verifying, setVerifying] = React.useState(false);

  // ---- list (always mock — Python has no JSON list endpoint) ----
  const refreshList = React.useCallback(async () => {
    setLoadingList(true);
    try {
      const r = await fetch("/api/audit", { headers: buildAuthHeader(keys, "admin") });
      const data = (await r.json().catch(() => ({}))) as AuditListResponse;
      setRecords(data.records || []);
      setMockList(r.headers.get("X-Mock-Mode") === "true");
    } finally {
      setLoadingList(false);
    }
  }, [keys]);

  React.useEffect(() => {
    refreshList();
  }, [refreshList]);

  // ---- pick up ?id=<audit_id> from the URL (the Risk Console links here) ----
  React.useEffect(() => {
    const id = params.get("id");
    if (id) setSelectedId(id);
  }, [params]);

  // ---- fetch a single audit record when one is selected ----
  const fetchDetail = React.useCallback(
    async (id: string) => {
      setLoadingDetail(true);
      setDetailError(null);
      setDetail(null);
      setProof(null);
      try {
        const r = await fetch(`/api/audit/${encodeURIComponent(id)}`, {
          headers: buildAuthHeader(keys, "admin"),
        });
        if (!r.ok) {
          const data = await r.json().catch(() => null);
          throw new Error(data?.detail || `HTTP ${r.status}`);
        }
        const data = (await r.json()) as AuditRecord;
        setDetail(data);
        setDetailMock(r.headers.get("X-Mock-Mode") === "true");
      } catch (e) {
        setDetailError(String(e));
      } finally {
        setLoadingDetail(false);
      }
    },
    [keys],
  );

  React.useEffect(() => {
    if (selectedId) fetchDetail(selectedId);
  }, [selectedId, fetchDetail]);

  // ---- verify chain ----
  const verifyChain = React.useCallback(async () => {
    setVerifying(true);
    setVerify(null);
    try {
      const r = await fetch("/api/v1/audit/verify-chain", {
        headers: buildAuthHeader(keys, "admin"),
      });
      const data = (await r.json().catch(() => null)) as VerifyResult | null;
      if (!r.ok || !data) throw new Error(`HTTP ${r.status}`);
      setVerify(data);
    } catch (e) {
      setVerify({
        intact: false,
        records_checked: 0,
        first_bad_audit_id: String(e),
      });
    } finally {
      setVerifying(false);
    }
  }, [keys]);

  // ---- merkle proof (mock lookup uses an int record_id; for demo we
  // just use the index in the list + 1) ----
  const showProof = React.useCallback(async () => {
    if (!detail) return;
    const idx = records.findIndex((r) => r.audit_id === detail.audit_id);
    const recordId = idx >= 0 ? idx + 1 : 1;
    try {
      const r = await fetch(`/api/v1/audit/${recordId}/proof`, {
        headers: buildAuthHeader(keys, "admin"),
      });
      if (r.ok) {
        setProof((await r.json()) as MerkleProof);
        setProofOpen(true);
      }
    } catch {
      /* ignore — proof is best-effort */
    }
  }, [detail, records, keys]);

  // ---- download CSV ----
  const downloadCsv = React.useCallback(async () => {
    try {
      const r = await fetch("/api/v1/compliance/audit-export", {
        headers: buildAuthHeader(keys, "admin"),
      });
      if (!r.ok) return;
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const cd = r.headers.get("content-disposition") || "";
      const m = cd.match(/filename="([^"]+)"/);
      a.href = url;
      a.download = m?.[1] || `audit-export-${Date.now()}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      /* ignore */
    }
  }, [keys]);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <FileClock className="size-5 text-success" aria-hidden />
          <h1 className="text-2xl font-semibold tracking-tight">Audit Explorer</h1>
        </div>
        <p className="text-sm text-muted-foreground">
          Every <code className="text-xs">POST /risk/score</code> decision lands in a
          tamper-evident audit log. Records chain via{" "}
          <code className="text-xs">sha256(canonical(body) + prev_hash)</code>; intervals
          seal to a Merkle root (Track H V3 §10.3).
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={verifyChain} disabled={verifying}>
          <ShieldCheck className="size-3.5" aria-hidden />
          {verifying ? "Verifying…" : "Verify chain"}
        </Button>
        <Button size="sm" variant="secondary" onClick={downloadCsv}>
          <Download className="size-3.5" aria-hidden />
          Download CSV
        </Button>
        {verify && (
          <VerifyPill result={verify} />
        )}
        {mockList && <MockModeBadge mock={mockList} />}
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        <RecordListCard
          records={records}
          loading={loadingList}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
        <RecordDetailCard
          record={detail}
          loading={loadingDetail}
          error={detailError}
          mock={detailMock}
          onShowProof={showProof}
        />
      </div>

      <Dialog open={proofOpen} onOpenChange={setProofOpen}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <TreePine className="size-4 text-success" aria-hidden />
              Merkle inclusion proof
            </DialogTitle>
            <DialogDescription>
              Path from leaf to interval root (RFC 6962-style padding to next power of two;
              last leaf repeated for the padding sibling).
            </DialogDescription>
          </DialogHeader>
          {proof ? <ProofView proof={proof} /> : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function VerifyPill({ result }: { result: VerifyResult }) {
  const ok = result.intact;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium ${
        ok
          ? "border-success/50 bg-success/15 text-success"
          : "border-danger/50 bg-danger/15 text-danger"
      }`}
    >
      {ok ? <ShieldCheck className="size-3.5" aria-hidden /> : <ShieldAlert className="size-3.5" aria-hidden />}
      {ok
        ? `Chain intact ✓ (${result.records_checked} records)`
        : `Chain BROKEN at ${result.first_bad_audit_id || "?"} ✗`}
    </span>
  );
}

function RecordListCard({
  records,
  loading,
  selectedId,
  onSelect,
}: {
  records: AuditRecord[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Records</CardTitle>
        <CardDescription>
          Recent audit records. Click one to inspect features, mandate metadata, and the hash chain.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : (
          <div className="max-h-[480px] overflow-y-auto rounded-md border border-border/60">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Order</TableHead>
                  <TableHead>Decision</TableHead>
                  <TableHead>Hash (12)</TableHead>
                  <TableHead>When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {records.map((r) => {
                  const sel = selectedId === r.audit_id;
                  return (
                    <TableRow
                      key={r.audit_id}
                      data-state={sel ? "selected" : undefined}
                      onClick={() => onSelect(r.audit_id)}
                      className="cursor-pointer"
                    >
                      <TableCell className="font-mono text-xs">
                        {r.body.request.order_id}
                      </TableCell>
                      <TableCell>
                        <DecisionBadge decision={r.body.decision as never} size="sm" />
                      </TableCell>
                      <TableCell className="font-mono text-[11px] text-muted-foreground">
                        {r.raw_hash.slice(0, 12)}…
                      </TableCell>
                      <TableCell className="text-[11px] text-muted-foreground">
                        {new Date(r.created_at).toLocaleTimeString()}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function RecordDetailCard({
  record,
  loading,
  error,
  mock,
  onShowProof,
}: {
  record: AuditRecord | null;
  loading: boolean;
  error: string | null;
  mock: boolean;
  onShowProof: () => void;
}) {
  if (loading) {
    return (
      <Card>
        <CardContent className="space-y-3 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <CardContent className="p-6">
          <div className="rounded-md border border-danger/50 bg-danger/15 p-4 text-sm text-danger">
            <p className="font-semibold">Audit lookup failed</p>
            <p className="mt-1 text-danger/80">{error}</p>
          </div>
        </CardContent>
      </Card>
    );
  }
  if (!record) {
    return (
      <Card>
        <CardContent className="p-6">
          <div className="flex flex-col items-center justify-center gap-2 py-16 text-center text-muted-foreground">
            <FileClock className="size-8 opacity-40" aria-hidden />
            <p className="text-sm">Select a record on the left.</p>
            <p className="text-xs opacity-70">Or paste an audit_id below.</p>
          </div>
        </CardContent>
      </Card>
    );
  }
  const req = record.body.request;
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="text-base">
              <code className="font-mono">{record.audit_id}</code>
            </CardTitle>
            <CardDescription>
              prediction <code className="font-mono">{record.prediction_id}</code> · model{" "}
              <code className="font-mono">{record.body.model_version}</code>
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            {mock && <MockModeBadge mock={mock} />}
            <Button size="sm" variant="secondary" onClick={onShowProof}>
              <TreePine className="size-3.5" aria-hidden />
              Merkle proof
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap items-center gap-2">
          <DecisionBadge decision={record.body.decision as never} />
          <DecisionSourcePill source={record.body.decision_source} />
          {record.body.rule_fired && (
            <Badge variant="secondary" className="font-mono text-[10px]">
              rule: {record.body.rule_fired}
            </Badge>
          )}
          {record.body.degraded && (
            <Badge variant="outline" className="text-warning">
              degraded
            </Badge>
          )}
          {record.body.case_id && (
            <Badge variant="outline" className="font-mono text-[10px]">
              case: {record.body.case_id}
            </Badge>
          )}
        </div>

        <HashChainCard record={record} />

        <div className="grid gap-4 md:grid-cols-2">
          <FeatureListCard record={record} />
          <MandateMetaCard record={record} />
        </div>

        <CostBreakdownInline record={record} />

        <RawBodyCard record={record} />
      </CardContent>
    </Card>
  );
}

function HashChainCard({ record }: { record: AuditRecord }) {
  const fields = [
    { label: "audit_id", value: record.audit_id },
    { label: "raw_hash", value: record.raw_hash, mono: true },
    { label: "prev_hash", value: record.prev_hash, mono: true },
    { label: "created_at", value: record.created_at },
  ];
  return (
    <div className="rounded-md border border-border/70 bg-muted/30 p-4">
      <div className="mb-2 flex items-center gap-2">
        <Hash className="size-3.5 text-success" aria-hidden />
        <h3 className="text-sm font-semibold">SHA-256 hash chain</h3>
      </div>
      <dl className="grid grid-cols-1 gap-1.5 text-xs">
        {fields.map((f) => (
          <div key={f.label} className="grid grid-cols-[120px_minmax(0,1fr)] gap-2">
            <dt className="text-muted-foreground">{f.label}</dt>
            <dd className={`break-all ${f.mono ? "font-mono" : ""}`}>
              {f.value}
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-[11px] text-muted-foreground">
        Track E dual-mode: per-record chain in file mode; Postgres + Merkle intervals in db mode.
      </p>
    </div>
  );
}

function FeatureListCard({ record }: { record: AuditRecord }) {
  const features = record.body.features_used || {};
  const fkeys = Object.keys(features);
  const req = record.body.request;
  return (
    <div className="rounded-md border border-border/70 bg-muted/30 p-4">
      <h3 className="mb-2 text-sm font-semibold">Features used</h3>
      {fkeys.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No features recorded (rules-only path). Order body below.
        </p>
      ) : (
        <ul className="space-y-1 text-xs">
          {fkeys.map((k) => (
            <li key={k} className="flex items-center justify-between gap-2">
              <span className="font-mono text-muted-foreground">{k}</span>
              <span className="font-mono">{features[k]}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 grid grid-cols-2 gap-1 text-[11px] text-muted-foreground">
        <span>amount: <span className="font-mono text-foreground/80">₹{req.amount_inr}</span></span>
        <span>method: <span className="font-mono text-foreground/80">{req.payment_method}</span></span>
        <span>addr: <span className="font-mono text-foreground/80">{req.address_quality}</span></span>
        <span>tier: <span className="font-mono text-foreground/80">{req.city_tier}</span></span>
      </div>
    </div>
  );
}

function MandateMetaCard({ record }: { record: AuditRecord }) {
  const m = record.body;
  const rows = [
    { label: "mandate_verdict", value: m.mandate_verdict },
    { label: "mandate_verdict_reason", value: m.mandate_verdict_reason ?? "—" },
    { label: "mandate_type", value: m.mandate_type ?? "—" },
    { label: "bh_purpose_code", value: m.bh_purpose_code ?? "—" },
    { label: "device_id", value: m.device_id ?? "—" },
    { label: "user_id", value: m.user_id ?? "—" },
    { label: "breach_note", value: m.breach_note ?? "—" },
  ];
  return (
    <div className="rounded-md border border-border/70 bg-muted/30 p-4">
      <h3 className="mb-2 text-sm font-semibold">Mandate metadata (Track D / H)</h3>
      <dl className="grid grid-cols-1 gap-1.5 text-xs">
        {rows.map((r) => (
          <div key={r.label} className="grid grid-cols-[160px_minmax(0,1fr)] gap-2">
            <dt className="text-muted-foreground">{r.label}</dt>
            <dd className="break-all font-mono">{r.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function CostBreakdownInline({ record }: { record: AuditRecord }) {
  const cb = record.body.cost_breakdown;
  if (!cb) return null;
  const rows = [
    { label: "ACCEPT", cost: cb.ACCEPT },
    { label: "REVIEW", cost: cb.REVIEW },
    { label: "REJECT", cost: cb.REJECT },
  ];
  const min = Math.min(...rows.map((r) => r.cost));
  return (
    <div className="rounded-md border border-border/70 bg-muted/30 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold">Cost breakdown</h3>
        <span className="text-[11px] text-muted-foreground">Bahnsen Eq.1</span>
      </div>
      <div className="grid grid-cols-3 gap-3 text-center">
        {rows.map((r) => (
          <div
            key={r.label}
            className={`rounded-md border p-2 text-xs ${
              r.cost === min
                ? "border-success/50 bg-success/15 text-success"
                : "border-border/60 bg-card/40"
            }`}
          >
            <div className="font-mono text-[10px] text-muted-foreground">{r.label}</div>
            <div className="font-mono text-sm font-semibold">
              ₹{r.cost.toLocaleString("en-IN")}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RawBodyCard({ record }: { record: AuditRecord }) {
  return (
    <details className="rounded-md border border-border/70 bg-muted/30 p-4 text-xs">
      <summary className="cursor-pointer text-sm font-semibold">
        Raw audit body (JSON)
      </summary>
      <pre className="mt-3 max-h-64 overflow-auto rounded-md border border-border/60 bg-background/60 p-3 text-[11px] leading-tight">
        {JSON.stringify(record, null, 2)}
      </pre>
    </details>
  );
}

function ProofView({ proof }: { proof: MerkleProof }) {
  return (
    <div className="space-y-3 text-xs">
      <dl className="grid grid-cols-1 gap-1.5">
        <div className="grid grid-cols-[160px_minmax(0,1fr)] gap-2">
          <dt className="text-muted-foreground">leaf_hash</dt>
          <dd className="break-all font-mono">{proof.leaf_hash}</dd>
        </div>
        <div className="grid grid-cols-[160px_minmax(0,1fr)] gap-2">
          <dt className="text-muted-foreground">interval_id</dt>
          <dd className="font-mono">{proof.interval_id} (pos {proof.position}, {proof.leaf_count} leaves)</dd>
        </div>
        <div className="grid grid-cols-[160px_minmax(0,1fr)] gap-2">
          <dt className="text-muted-foreground">merkle_root</dt>
          <dd className="break-all font-mono text-success">{proof.merkle_root}</dd>
        </div>
        <div className="grid grid-cols-[160px_minmax(0,1fr)] gap-2">
          <dt className="text-muted-foreground">prev_interval_root</dt>
          <dd className="break-all font-mono">{proof.prev_interval_root}</dd>
        </div>
        <div className="grid grid-cols-[160px_minmax(0,1fr)] gap-2">
          <dt className="text-muted-foreground">sealed_at</dt>
          <dd className="font-mono">{proof.sealed_at ?? "—"}</dd>
        </div>
      </dl>
      <div className="rounded-md border border-border/60 bg-background/60 p-3">
        <div className="mb-2 text-[11px] uppercase tracking-widest text-muted-foreground">
          Path (leaf → root)
        </div>
        <ol className="space-y-1.5">
          {proof.proof.map((step, i) => (
            <li key={i} className="flex items-center gap-2 text-[11px]">
              <span className="font-mono text-muted-foreground">{i + 1}.</span>
              <span className="font-mono text-foreground/70">{step.position}</span>
              <span className="break-all font-mono text-muted-foreground">
                {step.hash}
              </span>
            </li>
          ))}
        </ol>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Verify: hash(leaf + sibling) at each level, compare to <span className="font-mono text-success">merkle_root</span>.
      </p>
    </div>
  );
}
