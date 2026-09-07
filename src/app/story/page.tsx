"use client";

// /story — "One order, three views" product demo.
//
// Left  : the shopper's checkout (the trust layer is invisible)
// Middle: the merchant's seller dashboard (money language)
// Right : the payment network's control room (aggregates + consent gate)
//
// One shared order lifecycle drives all three panes; every verdict is a
// REAL call to /api/risk/score on this deployment. Split view for the
// one-shot recording; Focus (tabbed) mode when three panes feel dense.

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  ShieldCheck,
  Columns3,
  SquareStack,
  Info,
  X,
  ArrowLeft,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ShopperPane } from "@/components/story/shopper-pane";
import { MerchantPane } from "@/components/story/merchant-pane";
import { PlatformPane } from "@/components/story/platform-pane";
import type {
  PaneId,
  Phase,
  StoryOrder,
  ViewMode,
} from "@/components/story/story-state";
import {
  RING_ORDERS,
  SCENES,
  type SceneId,
} from "@/lib/story/scenes";
import type { OrderInput, ScoreResponse } from "@/lib/mock-data";
import { cn } from "@/lib/utils";
const DISCLAIMER_KEY = "story-disclaimer-collapsed";

export default function StoryPage() {
  const [queryClient] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 },
        },
      }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      <StoryPageInner />
    </QueryClientProvider>
  );
}

function StoryPageInner() {
  const [sceneId, setSceneId] = React.useState<SceneId>("safe");
  const scene = SCENES.find((s) => s.id === sceneId) ?? SCENES[0];

  // ---- shared story state -----------------------------------------------
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [orders, setOrders] = React.useState<StoryOrder[]>([]);
  /** True once the shopper chose "pay online instead" (drives copy + resolution). */
  const [prepaidFlow, setPrepaidFlow] = React.useState(false);
  const [consent, setConsent] = React.useState(false);
  const [ringFired, setRingFired] = React.useState(false);
  const [drill, setDrill] = React.useState<string | null>(null);
  const [drillVerified, setDrillVerified] = React.useState(false);

  // ---- view state --------------------------------------------------------
  const [mode, setMode] = React.useState<ViewMode>("split");
  const [activeTab, setActiveTab] = React.useState<PaneId>("shopper");
  const [disclaimerOpen, setDisclaimerOpen] = React.useState(true);

  // Mobile → default to tabs; remember dismissal across reloads.
  React.useEffect(() => {
    const isWide = window.matchMedia("(min-width: 1024px)").matches;
    setMode(isWide ? "split" : "tabs");
    try {
      if (window.sessionStorage.getItem(DISCLAIMER_KEY) === "1") {
        setDisclaimerOpen(false);
      }
    } catch {
      /* ignore */
    }
  }, []);

  function dismissDisclaimer() {
    setDisclaimerOpen(false);
    try {
      window.sessionStorage.setItem(DISCLAIMER_KEY, "1");
    } catch {
      /* ignore */
    }
  }

  function resetStory(nextScene?: SceneId) {
    setPhase("idle");
    setOrders([]);
    setPrepaidFlow(false);
    setRingFired(false);
    setDrill(null);
    setDrillVerified(false);
    if (nextScene && nextScene !== sceneId) setSceneId(nextScene);
  }

  // ---- scoring (the real API) --------------------------------------------
  async function scoreOrder(order: OrderInput): Promise<ScoreResponse> {
    const r = await fetch("/api/risk/score", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": `${order.order_id}:story:${Date.now()}`,
      },
      body: JSON.stringify(order),
    });
    const data = (await r.json().catch(() => null)) as ScoreResponse | null;
    if (!r.ok || !data) throw new Error(`score ${r.status}`);
    return data;
  }

  function pushOrder(order: OrderInput, res: ScoreResponse) {
    setOrders((prev) => [
      {
        key: `${order.order_id}:${res.prediction_id}`,
        orderId: order.order_id,
        amount: order.amount_inr,
        result: res,
        resolution: "pending",
        ts: Date.now(),
      },
      ...prev,
    ]);
  }

  async function placeOrder() {
    if (phase !== "idle") return;
    setPhase("checking");
    setPrepaidFlow(false);

    if (scene.id === "ring") {
      // Fire all three concurrently; each lands as it arrives.
      try {
        const ringInputs = RING_ORDERS;
        const results = await Promise.all(ringInputs.map((o) => scoreOrder(o)));
        results.forEach((res, i) => pushOrder(ringInputs[i], res));
        setRingFired(true);
        setPhase("declined"); // shopper sees the first order's soft decline
      } catch {
        setPhase("failed");
      }
      return;
    }

    try {
      const res = await scoreOrder(scene.order);
      pushOrder(scene.order, res);
      if (res.decision === "ACCEPT") {
        markResolution(scene.order.order_id, "auto");
        setPhase("confirmed");
      } else if (res.decision === "REVIEW") {
        setPhase("otp");
      } else {
        setPhase("declined");
      }
    } catch {
      setPhase("failed");
    }
  }

  async function verifyOtp(_code: string) {
    if (phase !== "otp") return;
    setPhase("otp_checking");
    await new Promise((r) => setTimeout(r, 800)); // realistic verify beat
    markResolution(
      scene.id === "ring" ? RING_ORDERS[0].order_id : scene.order.order_id,
      prepaidFlow ? "prepaid_otp" : "otp",
    );
    setPhase("confirmed");
  }

  async function convertToPrepaid() {
    if (phase !== "declined") return;
    setPhase("prepaid_checking");
    const base = scene.id === "ring" ? RING_ORDERS[0] : scene.order;
    const prepaid: OrderInput = { ...base, payment_method: "Prepaid" };
    setPrepaidFlow(true);
    try {
      const res = await scoreOrder(prepaid);
      if (res.decision === "ACCEPT") {
        markResolution(base.order_id, "prepaid");
        setPhase("confirmed");
      } else if (res.decision === "REVIEW") {
        // Big prepaid orders get the verification step too — honest and
        // exactly how real checkouts behave.
        pushPrepaidPending(base.order_id, res);
        setPhase("otp");
      } else {
        setPhase("failed");
      }
    } catch {
      setPhase("failed");
    }
  }

  function markResolution(orderId: string, resolution: StoryOrder["resolution"]) {
    setOrders((prev) =>
      prev.map((o) => (o.orderId === orderId ? { ...o, resolution } : o)),
    );
  }

  function pushPrepaidPending(orderId: string, res: ScoreResponse) {
    // The conversion result becomes the "live" result of the same order
    // (REVIEW) so the merchant pane follows along; resolution stays pending
    // until the OTP completes.
    setOrders((prev) =>
      prev.map((o) => (o.orderId === orderId ? { ...o, result: res } : o)),
    );
  }

  function toggleConsent() {
    setConsent((c) => {
      if (c) setDrillVerified(false); // revoking also un-verifies drill state
      return !c;
    });
  }

  function verifyProof() {
    // The chain itself is fetched live by the platform pane; this beat
    // mirrors an auditor re-running the verification.
    setDrillVerified(true);
  }

  // ---- panes (shared props) ----------------------------------------------
  const currentOrder =
    orders.find((o) =>
      o.orderId === (scene.id === "ring" ? RING_ORDERS[0].order_id : scene.order.order_id),
    ) ?? null;

  const shopper = (
    <ShopperPane
      scene={scene}
      phase={phase}
      order={currentOrder}
      onPlace={placeOrder}
      onVerify={verifyOtp}
      onConvert={convertToPrepaid}
      onReset={() => resetStory()}
    />
  );
  const merchant = (
    <MerchantPane
      phase={phase}
      orders={orders}
      consent={consent}
      onToggleConsent={toggleConsent}
    />
  );
  const platform = (
    <PlatformPane
      orders={orders}
      ringFired={ringFired}
      consent={consent}
      drill={drill}
      drillVerified={drillVerified}
      onDrill={(k) => {
        setDrill(k);
        setDrillVerified(false);
      }}
      onVerifyProof={verifyProof}
    />
  );

  // ---- shell ---------------------------------------------------------------
  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex min-h-screen flex-col bg-background">
        {/* Topbar */}
        <header className="sticky top-0 z-40 border-b border-border bg-background/90 backdrop-blur">
          <div className="mx-auto flex h-12 max-w-[1600px] items-center gap-3 px-3 md:px-4">
            <Button
              variant="ghost"
              size="sm"
              asChild
              className="h-8 gap-1 px-2 text-xs text-muted-foreground"
            >
              <a href="/dashboard">
                <ArrowLeft className="size-3.5" aria-hidden /> Console
              </a>
            </Button>
            <div className="flex items-center gap-2">
              <ShieldCheck className="size-4 text-success" aria-hidden />
              <span className="text-sm font-semibold tracking-tight">
                RTO Trust Layer
              </span>
              <StoryBadge>Product story</StoryBadge>
            </div>

            <div className="ml-auto flex items-center gap-2">
              {/* Discreet scene picker */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <div>
                    <Select
                      value={sceneId}
                      onValueChange={(v) => {
                        resetStory(v as SceneId);
                        setPhase("idle");
                      }}
                    >
                      <SelectTrigger className="h-8 w-[170px] text-xs">
                        <SelectValue placeholder="Scene" />
                      </SelectTrigger>
                      <SelectContent>
                        {SCENES.map((s) => (
                          <SelectItem key={s.id} value={s.id} className="text-xs">
                            {s.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="max-w-[260px] text-xs">
                  {scene.note}
                </TooltipContent>
              </Tooltip>

              {/* View mode toggle */}
              <div className="flex items-center rounded-md border border-border p-0.5">
                <ModeButton
                  active={mode === "split"}
                  onClick={() => setMode("split")}
                  label="Split"
                  icon={<Columns3 className="size-3.5" aria-hidden />}
                />
                <ModeButton
                  active={mode === "tabs"}
                  onClick={() => setMode("tabs")}
                  label="Focus"
                  icon={<SquareStack className="size-3.5" aria-hidden />}
                />
              </div>

              {/* Reopen disclaimer */}
              {!disclaimerOpen && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  aria-label="Show demo disclaimer"
                  onClick={() => setDisclaimerOpen(true)}
                >
                  <Info className="size-4" aria-hidden />
                </Button>
              )}
            </div>
          </div>

          {/* Collapsible disclaimer */}
          <AnimatePresence initial={false}>
            {disclaimerOpen && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden border-t border-border/60 bg-muted/50"
              >
                <div className="mx-auto flex max-w-[1600px] items-center gap-2 px-3 py-1.5 md:px-4">
                  <p className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
                    Fictional demo — UrbanKart &amp; PayGrid are illustrative
                    brands, ambient stats are simulated. Order verdicts run
                    live on this deployment&apos;s risk API. Demo tip: any
                    6-digit code verifies.
                  </p>
                  <button
                    type="button"
                    onClick={dismissDisclaimer}
                    aria-label="Hide disclaimer"
                    className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  >
                    <X className="size-3.5" aria-hidden />
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </header>

        {/* Panes */}
        <main className="mx-auto w-full max-w-[1600px] flex-1 p-3 md:p-4">
          {mode === "split" ? (
            <div className="grid grid-cols-1 gap-3 lg:h-[calc(100dvh-108px)] lg:grid-cols-3 lg:gap-4">
              <PaneFrame pane={shopper} label="Shopper" />
              <PaneFrame pane={merchant} label="Merchant" />
              <PaneFrame pane={platform} label="Platform" />
            </div>
          ) : (
            <Tabs
              value={activeTab}
              onValueChange={(v) => setActiveTab(v as PaneId)}
              className="lg:h-[calc(100dvh-108px)]"
            >
              <TabsList className="grid h-9 w-full max-w-md grid-cols-3">
                <TabsTrigger value="shopper" className="text-xs">
                  Shopper
                </TabsTrigger>
                <TabsTrigger value="merchant" className="text-xs">
                  Merchant
                </TabsTrigger>
                <TabsTrigger value="platform" className="text-xs">
                  Platform
                </TabsTrigger>
              </TabsList>
              <TabsContent value="shopper" className="mt-3 lg:mt-4 lg:h-[calc(100%-48px)]">
                <div className="mx-auto h-full max-w-md">{shopper}</div>
              </TabsContent>
              <TabsContent value="merchant" className="mt-3 lg:mt-4 lg:h-[calc(100%-48px)]">
                <div className="mx-auto h-full max-w-md">{merchant}</div>
              </TabsContent>
              <TabsContent value="platform" className="mt-3 lg:mt-4 lg:h-[calc(100%-48px)]">
                <div className="mx-auto h-full max-w-md">{platform}</div>
              </TabsContent>
            </Tabs>
          )}
        </main>
      </div>
    </TooltipProvider>
  );
}

function StoryBadge({ children }: { children: React.ReactNode }) {
  return (
    <span className="hidden rounded-full border border-border bg-muted px-2 py-0.5 text-[9px] font-semibold uppercase tracking-widest text-muted-foreground sm:inline">
      {children}
    </span>
  );
}

function ModeButton({
  active,
  onClick,
  label,
  icon,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  icon: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex items-center gap-1 rounded-[4px] px-2 py-1 text-[11px] font-medium transition-colors",
        active
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

/** Frame with an sr-only caption (split mode). */
function PaneFrame({ pane, label }: { pane: React.ReactNode; label: string }) {
  return (
    <section className="flex min-h-0 flex-col lg:h-full" aria-label={label}>
      <div className="min-h-0 flex-1">{pane}</div>
    </section>
  );
}
