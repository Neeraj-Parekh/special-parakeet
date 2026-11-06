"use client";

export const dynamic = "force-dynamic";

import * as React from "react";
import {
  Activity,
  Gauge,
  Boxes,
  TrendingDown,
  ShieldCheck,
  Waves,
  ExternalLink,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";
import { MockModeBadge } from "@/components/app-header";
import {
  SAMPLE_COST_CURVES,
  SAMPLE_DRIFT,
  SAMPLE_MODEL_CURRENT,
  SAMPLE_OPTIMAL_THRESHOLD,
} from "@/lib/mock-data";

interface MetricsState {
  ddmState: "STABLE" | "WARNING" | "DRIFT" | "UNKNOWN";
  adwinState: "STABLE" | "WARNING" | "DRIFT" | "UNKNOWN";
  circuitState: "CLOSED" | "HALF_OPEN" | "OPEN" | "UNKNOWN";
  ddmErrorRate: number | null;
  adwinWindowLen: number | null;
  mock: boolean;
}

function parsePrometheus(text: string): MetricsState {
  const gauges: Record<string, number> = {};
  for (const line of text.split("\n")) {
    if (!line || line.startsWith("#")) continue;
    const m = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)\s+([0-9eE.+-]+)/);
    if (!m) continue;
    gauges[m[1]] = Number(m[2]);
  }
  const map = ["STABLE", "WARNING", "DRIFT", "UNKNOWN"];
  const pick = (v: number | undefined) =>
    v === undefined ? "UNKNOWN" : (map[Math.min(Math.max(v, 0), 2)] ?? "UNKNOWN");
  return {
    ddmState: pick(gauges["rto_drift_ddm_state"]),
    adwinState: pick(gauges["rto_drift_adwin_state"]),
    circuitState:
      gauges["rto_circuit_state"] === undefined
        ? "UNKNOWN"
        : (["CLOSED", "HALF_OPEN", "OPEN", "UNKNOWN"][Math.min(gauges["rto_circuit_state"], 2)] as never),
    ddmErrorRate: gauges["rto_drift_ddm_error_rate"] ?? null,
    adwinWindowLen: gauges["rto_drift_adwin_window_len"] ?? null,
    mock: false,
  };
}

export default function ModelHealthPage() {
  const keys = useApiKeys();
  const [model, setModel] = React.useState(SAMPLE_MODEL_CURRENT);
  const [modelMock, setModelMock] = React.useState(true);
  const [drift, setDrift] = React.useState(SAMPLE_DRIFT);
  const [driftMock, setDriftMock] = React.useState(true);
  const [metrics, setMetrics] = React.useState<MetricsState>({
    ddmState: "STABLE",
    adwinState: "STABLE",
    circuitState: "CLOSED",
    ddmErrorRate: 0.183,
    adwinWindowLen: 412,
    mock: true,
  });

  // Fetch model + drift once.
  React.useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/api/v1/models/current", {
          headers: buildAuthHeader(keys, "scorer"),
        });
        if (r.ok) {
          const data = await r.json();
          if (data && data.champion) {
            setModel(data);
            setModelMock(r.headers.get("X-Mock-Mode") === "true");
          }
        }
      } catch {
        /* keep mock defaults */
      }
    })();
    (async () => {
      try {
        const r = await fetch("/api/v1/models/drift", {
          headers: buildAuthHeader(keys, "admin"),
        });
        if (r.ok) {
          const data = await r.json();
          if (data) {
            setDrift(data);
            setDriftMock(r.headers.get("X-Mock-Mode") === "true");
          }
        }
      } catch {
        /* keep mock defaults */
      }
    })();
  }, [keys]);

  // Poll /metrics every 5 seconds for the live drift state.
  React.useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const r = await fetch("/api/metrics");
        if (!r.ok) return;
        const text = await r.text();
        const parsed = parsePrometheus(text);
        parsed.mock = r.headers.get("X-Mock-Mode") === "true";
        if (!cancelled) setMetrics(parsed);
      } catch {
        /* keep last state */
      }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <Activity className="size-5 text-success" aria-hidden />
          <h1 className="text-2xl font-semibold tracking-tight">Model Health</h1>
        </div>
        <p className="text-sm text-muted-foreground">
          Champion model registry + DDM/ADWIN drift detection (Track G — Gama 2014 ACM CSUR
          46(4) §3.2/§3.3) + Drummond-Holte cost curves (Track C — Machine Learning 65:95-130, 2006).
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {modelMock && <MockModeBadge mock={modelMock} />}
        {driftMock && <span className="text-xs text-muted-foreground">
          drift: {drift.status}
        </span>}
        {metrics.mock && <span className="text-xs text-muted-foreground">
          · metrics: polling every 5s
        </span>}
      </div>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="PR-AUC"
          value={model.champion?.metrics?.pr_auc?.toFixed(3) ?? "—"}
          sublabel="primary metric (class imbalance ~23%)"
        />
        <MetricCard
          label="ROC-AUC"
          value={model.champion?.metrics?.roc_auc?.toFixed(3) ?? "—"}
          sublabel="secondary"
        />
        <MetricCard
          label="Precision"
          value={model.champion?.metrics?.precision?.toFixed(3) ?? "—"}
          sublabel="at registration threshold"
        />
        <MetricCard
          label="Recall"
          value={model.champion?.metrics?.recall?.toFixed(3) ?? "—"}
          sublabel="RTO catch rate"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <ChampionCard model={model} />
        <DriftCard drift={drift} metrics={metrics} />
      </div>

      <CostCurvesCard />
    </div>
  );
}

function MetricCard({
  label,
  value,
  sublabel,
}: {
  label: string;
  value: string;
  sublabel: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardDescription className="text-xs">{label}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="font-mono text-3xl font-bold tabular-nums">{value}</div>
        <p className="mt-1 text-[11px] text-muted-foreground">{sublabel}</p>
      </CardContent>
    </Card>
  );
}

function ChampionCard({
  model,
}: {
  model: { champion?: { version: string; deployed_at: string; metrics: Record<string, number>; training_data: string; notes?: string } };
}) {
  const c = model.champion;
  if (!c) {
    return (
      <Card>
        <CardContent className="p-6">
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    );
  }
  const deployedDate = new Date(c.deployed_at);
  const sinceDays = Math.max(
    0,
    Math.floor((Date.now() - deployedDate.getTime()) / 86_400_000),
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Boxes className="size-4 text-success" aria-hidden />
          Champion model
        </CardTitle>
        <CardDescription>
          ML registry (Track E) · registered via <code className="text-xs">POST /v1/models/register</code>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-2xl font-bold">{c.version}</span>
          <Badge variant="outline" className="border-success/50 bg-success/15 text-success">
            active
          </Badge>
        </div>
        <dl className="grid grid-cols-1 gap-1.5 text-xs">
          <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2">
            <dt className="text-muted-foreground">deployed_at</dt>
            <dd className="font-mono">{c.deployed_at}</dd>
          </div>
          <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2">
            <dt className="text-muted-foreground">active since</dt>
            <dd className="font-mono">{sinceDays}d ago ({deployedDate.toLocaleDateString()})</dd>
          </div>
          <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2">
            <dt className="text-muted-foreground">training_data</dt>
            <dd className="text-foreground/80">{c.training_data}</dd>
          </div>
          {c.notes && (
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2">
              <dt className="text-muted-foreground">notes</dt>
              <dd className="text-foreground/80">{c.notes}</dd>
            </div>
          )}
        </dl>
        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          {Object.entries(c.metrics || {}).slice(0, 6).map(([k, v]) => (
            <div key={k} className="rounded-md border border-border/60 bg-card/40 p-2">
              <div className="font-mono text-[10px] text-muted-foreground">{k}</div>
              <div className="font-mono text-sm font-semibold">{typeof v === "number" ? v.toFixed(3) : String(v)}</div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function DriftCard({
  drift,
  metrics,
}: {
  drift: { status: string; n_observed?: number; psi?: Record<string, number>; worst_psi?: number };
  metrics: MetricsState;
}) {
  const psiEntries = Object.entries(drift.psi || {});
  const driftColor =
    drift.status === "OK"
      ? "border-success/50 bg-success/15 text-success"
      : drift.status === "WARNING"
        ? "border-warning/50 bg-warning/15 text-warning"
        : drift.status === "CRITICAL"
          ? "border-danger/50 bg-danger/15 text-danger"
          : "border-border/60 bg-muted/40 text-muted-foreground";
  const chartData = psiEntries.map(([k, v]) => ({ feature: k, psi: v }));
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Waves className="size-4 text-success" aria-hidden />
            Drift status
          </CardTitle>
          <Badge variant="outline" className={`font-mono text-[10px] ${driftColor}`}>
            {drift.status}
          </Badge>
        </div>
        <CardDescription>
          DDM (Bernoulli control chart) + ADWIN (variable-length sliding window)
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <DetectorPill label="DDM" state={metrics.ddmState} extra={metrics.ddmErrorRate !== null ? `p=${metrics.ddmErrorRate.toFixed(3)}` : ""} />
          <DetectorPill label="ADWIN" state={metrics.adwinState} extra={metrics.adwinWindowLen !== null ? `W=${metrics.adwinWindowLen}` : ""} />
        </div>
        <div className="rounded-md border border-border/60 bg-card/40 p-3 text-xs">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-muted-foreground">PSI per feature (n={drift.n_observed ?? "—"})</span>
            <span className="font-mono text-muted-foreground">worst: {drift.worst_psi?.toFixed(3) ?? "—"}</span>
          </div>
          {chartData.length === 0 ? (
            <p className="text-muted-foreground">No PSI data.</p>
          ) : (
            <div className="h-32 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: -20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="feature" tick={{ fontSize: 9, fill: "var(--muted-foreground)" }} interval={0} angle={-25} textAnchor="end" height={36} stroke="var(--border)" />
                  <YAxis tick={{ fontSize: 9, fill: "var(--muted-foreground)" }} stroke="var(--border)" />
                  <Tooltip
                    cursor={{ fill: "var(--muted)" }}
                    contentStyle={{
                      background: "var(--card)",
                      border: "1px solid var(--border)",
                      borderRadius: 6,
                      fontSize: 11,
                      color: "var(--foreground)",
                    }}
                  />
                  <Bar dataKey="psi" radius={[3, 3, 0, 0]}>
                    {chartData.map((d, i) => (
                      <Cell
                        key={d.feature}
                        fill={
                          d.psi > 0.25
                            ? "var(--danger)"
                            : d.psi > 0.1
                              ? "var(--warning)"
                              : "var(--success)"
                        }
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
          <div className="mt-1 flex items-center gap-3 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1"><span className="inline-block size-1.5 rounded-full bg-success" /> OK (&lt;0.1)</span>
            <span className="flex items-center gap-1"><span className="inline-block size-1.5 rounded-full bg-warning" /> WARNING (0.1–0.25)</span>
            <span className="flex items-center gap-1"><span className="inline-block size-1.5 rounded-full bg-danger" /> CRITICAL (&gt;0.25)</span>
          </div>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <Gauge className="size-3.5" aria-hidden />
          Circuit breaker: <span className="font-mono">{metrics.circuitState}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function DetectorPill({
  label,
  state,
  extra,
}: {
  label: string;
  state: "STABLE" | "WARNING" | "DRIFT" | "UNKNOWN";
  extra: string;
}) {
  let cls = "border-border/60 bg-muted/40 text-muted-foreground";
  if (state === "STABLE") cls = "border-success/50 bg-success/15 text-success";
  else if (state === "WARNING") cls = "border-warning/50 bg-warning/15 text-warning";
  else if (state === "DRIFT") cls = "border-danger/50 bg-danger/15 text-danger";
  return (
    <div className={`rounded-md border p-2 ${cls}`}>
      <div className="flex items-center justify-between text-[11px]">
        <span className="font-mono uppercase tracking-wider">{label}</span>
        <span className="font-semibold">{state}</span>
      </div>
      {extra && <div className="mt-0.5 font-mono text-[10px] opacity-80">{extra}</div>}
    </div>
  );
}

function CostCurvesCard() {
  const [data, setData] = React.useState(SAMPLE_COST_CURVES);
  const [optimal, setOptimal] = React.useState(SAMPLE_OPTIMAL_THRESHOLD);
  const [meta, setMeta] = React.useState<{ n_samples: number; n_pos: number; n_neg: number; data_source: string; mock: boolean }>({
    n_samples: 7235,
    n_pos: 1664,
    n_neg: 5571,
    data_source: "mock — docs/cost_table.md",
    mock: true,
  });
  const [loading, setLoading] = React.useState(false);
  const keys = useApiKeys();

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/v1/policy/cost-curves?n_resamples=100", {
        headers: buildAuthHeader(keys, "scorer"),
      });
      if (!r.ok) return;
      const data = await r.json();
      if (data && Array.isArray(data.curves) && data.curves.length) {
        setData(data.curves);
        setOptimal(data.optimal_threshold);
        setMeta({
          n_samples: data.n_samples,
          n_pos: data.n_pos,
          n_neg: data.n_neg,
          data_source: data.data_source,
          mock: r.headers.get("X-Mock-Mode") === "true",
        });
      }
    } catch {
      /* keep defaults */
    } finally {
      setLoading(false);
    }
  }, [keys]);

  React.useEffect(() => {
    load();
  }, [load]);

  const max = Math.max(...data.map((d) => d.cost));
  const chartData = data.map((d) => ({
    threshold: d.threshold,
    cost: d.cost,
    optimal: Math.abs(d.threshold - optimal) < 1e-6,
    ci_lower: d.ci_lower,
    ci_upper: d.ci_upper,
  }));

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <TrendingDown className="size-4 text-success" aria-hidden />
              Cost curves
            </CardTitle>
            <CardDescription>
              Drummond-Holte (Machine Learning 65:95-130, 2006). Threshold on X, total expected cost on Y.
              Highlighted bar = cost-optimal threshold (Bahnsen Eq.1 with c_fn=12×c_fp).
            </CardDescription>
          </div>
          {meta.mock && <MockModeBadge mock={meta.mock} />}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis
                dataKey="threshold"
                tickFormatter={(v) => String(v)}
                tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
                stroke="var(--border)"
                label={{ value: "threshold", position: "insideBottom", offset: -2, fontSize: 10, fill: "var(--muted-foreground)" }}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
                stroke="var(--border)"
                domain={[0, Math.ceil(max * 1.1)]}
              />
              <Tooltip
                cursor={{ fill: "var(--muted)" }}
                contentStyle={{
                  background: "var(--card)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  fontSize: 11,
                  color: "var(--foreground)",
                }}
                formatter={(value: number) => [`₹${value}`, "total cost"]}
                labelFormatter={(label: number) => `threshold ${label}`}
              />
              <Bar dataKey="cost" radius={[3, 3, 0, 0]} maxBarSize={48}>
                {chartData.map((d, i) => (
                  <Cell
                    key={i}
                    fill={d.optimal ? "var(--success)" : "var(--chart-4)"}
                  />
                ))}
              </Bar>
              <ReferenceLine
                y={Math.min(...data.map((d) => d.cost))}
                stroke="var(--success)"
                strokeDasharray="3 3"
                label={{
                  value: `optimal: t=${optimal}`,
                  position: "right",
                  fill: "var(--success)",
                  fontSize: 10,
                }}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
          <MetaItem label="n_samples" value={meta.n_samples.toLocaleString("en-IN")} />
          <MetaItem label="n_pos (RTO)" value={meta.n_pos.toLocaleString("en-IN")} />
          <MetaItem label="n_neg (delivered)" value={meta.n_neg.toLocaleString("en-IN")} />
          <MetaItem label="data_source" value={meta.data_source} />
        </div>
      </CardContent>
    </Card>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border/60 bg-card/40 p-2">
      <div className="font-mono text-[10px] text-muted-foreground">{label}</div>
      <div className="font-mono text-xs text-foreground/80">{value}</div>
    </div>
  );
}
