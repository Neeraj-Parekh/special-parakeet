"use client";

// ShopperPane — the consumer face of the story. A normal storefront
// checkout: the trust layer is INVISIBLE here. No risk words, no fraud
// words, no numbers except prices. REVIEW becomes "one quick step",
// REJECT becomes "COD isn't available — pay online instead".
//
// Visual identity: always light, even in dark mode — it's a different app
// (the store), not the console.

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ShoppingBag,
  MapPin,
  Truck,
  ShieldCheck,
  Loader2,
  Check,
  Smartphone,
  Lock,
  ArrowLeft,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  InputOTP,
  InputOTPGroup,
  InputOTPSlot,
} from "@/components/ui/input-otp";
import type { StoryScene } from "@/lib/story/scenes";
import type { Phase, StoryOrder } from "@/components/story/story-state";
import { cn } from "@/lib/utils";

const inr = (v: number) => `₹${v.toLocaleString("en-IN")}`;

export function ShopperPane({
  scene,
  phase,
  order,
  onPlace,
  onVerify,
  onConvert,
  onReset,
}: {
  scene: StoryScene;
  phase: Phase;
  order: StoryOrder | null;
  onPlace: () => void;
  onVerify: (code: string) => void;
  onConvert: () => void;
  onReset: () => void;
}) {
  const [otp, setOtp] = React.useState("");
  React.useEffect(() => setOtp(""), [scene.id]);

  const prepaid = scene.order.payment_method === "Prepaid";

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-neutral-200 bg-white text-neutral-900 shadow-sm">
      {/* Store header */}
      <div className="flex items-center justify-between border-b border-neutral-100 px-4 py-3">
        <div className="flex items-center gap-2">
          <ShoppingBag className="size-4 text-neutral-500" aria-hidden />
          <span className="text-sm font-bold tracking-tight">UrbanKart</span>
        </div>
        <span className="flex items-center gap-1 text-[10px] font-medium text-neutral-400">
          <Lock className="size-3" aria-hidden /> Secure checkout
        </span>
      </div>

      {/* Body */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <AnimatePresence mode="wait">
          {phase === "idle" && (
            <motion.div
              key="store"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.25 }}
              className="p-4"
            >
              <ProductCard scene={scene} />
              <AddressCard scene={scene} />
              <PaymentRow prepaid={prepaid} />
              <OrderSummary price={scene.product.price} prepaid={prepaid} />
              <Button
                onClick={onPlace}
                className="mt-4 h-11 w-full rounded-lg bg-neutral-900 text-sm font-semibold text-white hover:bg-neutral-800"
              >
                Place order · {inr(scene.product.price)}
              </Button>
              <p className="mt-2 flex items-center justify-center gap-1 text-[11px] text-neutral-400">
                <ShieldCheck className="size-3" aria-hidden /> Easy 7-day returns
              </p>
              <TrustRow />
            </motion.div>
          )}

          {(phase === "checking" || phase === "prepaid_checking") && (
            <Centered key="checking">
              <Loader2 className="size-8 animate-spin text-neutral-300" aria-hidden />
              <p className="text-sm font-medium text-neutral-600">
                {phase === "prepaid_checking" ? "Processing your payment…" : "Placing your order…"}
              </p>
              <p className="text-[11px] text-neutral-400">This takes a moment</p>
            </Centered>
          )}

          {phase === "otp" && (
            <motion.div
              key="otp"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="p-4"
            >
              <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4 text-center">
                <div className="mx-auto mb-3 flex size-11 items-center justify-center rounded-full bg-neutral-900 text-white">
                  <Smartphone className="size-5" aria-hidden />
                </div>
                <h3 className="text-sm font-semibold">One quick step</h3>
                <p className="mt-1 text-xs leading-relaxed text-neutral-500">
                  We sent a 6-digit code to {scene.address.phoneMasked}.
                  Enter it to confirm your order.
                </p>
                <div className="mt-4 flex justify-center">
                  <InputOTP
                    maxLength={6}
                    value={otp}
                    onChange={setOtp}
                    aria-label="6-digit verification code"
                  >
                    <InputOTPGroup>
                      <InputOTPSlot index={0} />
                      <InputOTPSlot index={1} />
                      <InputOTPSlot index={2} />
                      <InputOTPSlot index={3} />
                      <InputOTPSlot index={4} />
                      <InputOTPSlot index={5} />
                    </InputOTPGroup>
                  </InputOTP>
                </div>
                <Button
                  onClick={() => onVerify(otp)}
                  disabled={otp.length !== 6}
                  className="mt-4 h-10 w-full rounded-lg bg-neutral-900 text-sm font-semibold text-white hover:bg-neutral-800"
                >
                  Verify & confirm
                </Button>
                <button
                  type="button"
                  className="mt-2 text-[11px] text-neutral-400 underline underline-offset-2"
                >
                  Resend code
                </button>
              </div>
            </motion.div>
          )}

          {phase === "otp_checking" && (
            <Centered key="otp_checking">
              <Loader2 className="size-8 animate-spin text-neutral-300" aria-hidden />
              <p className="text-sm font-medium text-neutral-600">Verifying…</p>
            </Centered>
          )}

          {phase === "declined" && (
            <motion.div
              key="declined"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="p-4"
            >
              <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4 text-center">
                <div className="mx-auto mb-3 flex size-11 items-center justify-center rounded-full bg-neutral-200 text-neutral-600">
                  <Truck className="size-5" aria-hidden />
                </div>
                <h3 className="text-sm font-semibold">
                  COD isn&apos;t available for this address
                </h3>
                <p className="mt-1 text-xs leading-relaxed text-neutral-500">
                  You can still get it — pay securely online and we&apos;ll
                  ship it right away.
                </p>
                <Button
                  onClick={onConvert}
                  className="mt-4 h-10 w-full rounded-lg bg-neutral-900 text-sm font-semibold text-white hover:bg-neutral-800"
                >
                  Pay {inr(scene.product.price)} online instead
                </Button>
                <p className="mt-2 text-[11px] text-neutral-400">
                  Your payment details stay private
                </p>
              </div>
            </motion.div>
          )}

          {phase === "confirmed" && (
            <motion.div
              key="confirmed"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              className="p-4"
            >
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-5 text-center">
                <motion.div
                  initial={{ scale: 0.6, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ type: "spring", stiffness: 260, damping: 18, delay: 0.1 }}
                  className="mx-auto mb-3 flex size-12 items-center justify-center rounded-full bg-emerald-500 text-white"
                >
                  <Check className="size-6" aria-hidden />
                </motion.div>
                <h3 className="text-base font-bold text-emerald-900">
                  Order confirmed!
                </h3>
                <p className="mt-1 text-xs text-emerald-800/80">
                  {scene.order.payment_method === "Prepaid" || wasPrepaid(order)
                    ? "Paid online"
                    : "Pay on delivery"}{" "}
                  · Arriving {scene.address.eta}
                </p>
                {order && (
                  <p className="mt-2 font-mono text-[10px] text-emerald-700/60">
                    {order.orderId}
                  </p>
                )}
                <Button
                  variant="outline"
                  onClick={onReset}
                  className="mt-4 h-9 w-full rounded-lg border-neutral-300 bg-white text-xs font-medium text-neutral-700 hover:bg-neutral-50"
                >
                  <ArrowLeft className="mr-1 size-3.5" aria-hidden /> Continue shopping
                </Button>
              </div>
            </motion.div>
          )}

          {phase === "failed" && (
            <Centered key="failed">
              <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4 text-center">
                <p className="text-sm font-semibold">We couldn&apos;t process this order</p>
                <p className="mt-1 text-xs text-neutral-500">No money was taken.</p>
                <Button
                  variant="outline"
                  onClick={onReset}
                  className="mt-3 h-9 rounded-lg text-xs"
                >
                  Try again
                </Button>
              </div>
            </Centered>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function wasPrepaid(order: StoryOrder | null): boolean {
  return order?.resolution === "prepaid" || order?.resolution === "prepaid_otp";
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="flex h-full min-h-[300px] flex-col items-center justify-center gap-2 p-6"
    >
      {children}
    </motion.div>
  );
}

function ProductCard({ scene }: { scene: StoryScene }) {
  return (
    <div className="flex gap-3 rounded-lg border border-neutral-200 p-3">
      <div
        className={cn(
          "flex size-20 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br text-3xl",
          scene.product.tile,
        )}
        role="img"
        aria-label={scene.product.name}
      >
        {scene.product.emoji}
      </div>
      <div className="min-w-0 flex-1">
        <h3 className="truncate text-sm font-semibold">{scene.product.name}</h3>
        <p className="truncate text-[11px] text-neutral-400">{scene.product.blurb}</p>
        <p className="mt-1 text-base font-bold">{inr(scene.product.price)}</p>
        <p className="flex items-center gap-1 text-[11px] text-emerald-600">
          <Truck className="size-3" aria-hidden /> Free delivery by {scene.address.eta}
        </p>
      </div>
    </div>
  );
}

function AddressCard({ scene }: { scene: StoryScene }) {
  return (
    <div className="mt-3 rounded-lg border border-neutral-200 p-3">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-neutral-400">
          Deliver to
        </span>
        <span className="text-[10px] font-medium text-neutral-400">Change</span>
      </div>
      <p className="mt-1 text-xs font-semibold">{scene.address.name}</p>
      <p className="flex items-start gap-1 text-[11px] leading-relaxed text-neutral-500">
        <MapPin className="mt-0.5 size-3 shrink-0" aria-hidden />
        {scene.address.line1}, {scene.address.city}
      </p>
    </div>
  );
}

function PaymentRow({ prepaid }: { prepaid: boolean }) {
  return (
    <div className="mt-3 grid grid-cols-2 gap-2">
      <div
        className={cn(
          "rounded-lg border p-2.5",
          !prepaid ? "border-neutral-900 bg-neutral-50" : "border-neutral-200 opacity-50",
        )}
      >
        <p className="text-xs font-semibold">Cash on delivery</p>
        <p className="text-[10px] text-neutral-400">Pay when it arrives</p>
      </div>
      <div
        className={cn(
          "rounded-lg border p-2.5",
          prepaid ? "border-neutral-900 bg-neutral-50" : "border-neutral-200 opacity-50",
        )}
      >
        <p className="text-xs font-semibold">Pay online</p>
        <p className="text-[10px] text-neutral-400">UPI · cards · netbanking</p>
      </div>
    </div>
  );
}

function OrderSummary({ price, prepaid }: { price: number; prepaid: boolean }) {
  return (
    <div className="mt-3 rounded-lg border border-neutral-200 p-3">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-neutral-400">
        Order summary
      </p>
      <dl className="mt-1.5 space-y-1 text-xs">
        <div className="flex justify-between text-neutral-500">
          <dt>Item total</dt>
          <dd className="tabular-nums">{inr(price)}</dd>
        </div>
        <div className="flex justify-between text-neutral-500">
          <dt>Delivery</dt>
          <dd className="tabular-nums text-emerald-600">Free</dd>
        </div>
        {prepaid ? (
          <div className="flex justify-between text-neutral-500">
            <dt>Pay now</dt>
            <dd className="tabular-nums">{inr(price)}</dd>
          </div>
        ) : (
          <div className="flex justify-between text-neutral-500">
            <dt>Pay on delivery</dt>
            <dd className="tabular-nums">{inr(price)}</dd>
          </div>
        )}
        <div className="flex justify-between border-t border-neutral-100 pt-1.5 font-semibold">
          <dt>You pay</dt>
          <dd className="tabular-nums">{inr(price)}</dd>
        </div>
      </dl>
    </div>
  );
}

function TrustRow() {
  return (
    <div className="mt-4 grid grid-cols-3 gap-2 border-t border-neutral-100 pt-3 text-center">
      <div>
        <ShieldCheck className="mx-auto size-4 text-neutral-300" aria-hidden />
        <p className="mt-1 text-[10px] font-medium text-neutral-400">Secure payments</p>
      </div>
      <div>
        <Truck className="mx-auto size-4 text-neutral-300" aria-hidden />
        <p className="mt-1 text-[10px] font-medium text-neutral-400">On-time delivery</p>
      </div>
      <div>
        <Check className="mx-auto size-4 text-neutral-300" aria-hidden />
        <p className="mt-1 text-[10px] font-medium text-neutral-400">7-day returns</p>
      </div>
    </div>
  );
}
