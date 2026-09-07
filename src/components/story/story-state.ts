"use client";

// Story state — the synchronized order lifecycle shared by all three panes.

import type { ScoreResponse } from "@/lib/mock-data";
import type { SceneId } from "@/lib/story/scenes";

/** The shopper-visible phase (drives all three panes). */
export type Phase =
  | "idle"
  | "checking" // placing the order (API in flight)
  | "otp" // review verdict: waiting for OTP entry
  | "otp_checking" // verifying the OTP
  | "confirmed" // order confirmed (any path)
  | "declined" // reject verdict: soft decline shown
  | "prepaid_checking" // conversion to prepaid (API in flight)
  | "failed"; // rare: conversion couldn't complete

export type ViewMode = "split" | "tabs";
export type PaneId = "shopper" | "merchant" | "platform";

/** How a story order finally resolved. */
export type Resolution = "pending" | "auto" | "otp" | "prepaid" | "prepaid_otp";

export interface StoryOrder {
  key: string;
  orderId: string;
  amount: number;
  result: ScoreResponse;
  resolution: Resolution;
  ts: number;
}

export interface StoryState {
  scene: SceneId;
  phase: Phase;
  /** Latest real API result for the active scene. */
  result: ScoreResponse | null;
  /** All story orders this scene (ring adds three). */
  orders: StoryOrder[];
  /** UrbanKart → PayGrid audit consent. */
  consent: boolean;
  /** Fraud-ring alert raised (scene 4, all three orders arrived). */
  ringFired: boolean;
  /** Platform drill-down target. */
  drill: string | null;
  drillVerified: boolean;
}

/** Status-pill mapping for the merchant feed. */
export function feedStatus(
  o: StoryOrder,
): "ok" | "check" | "blocked" {
  const d = o.result.decision;
  if (d === "ACCEPT") return "ok";
  if (d === "REVIEW") {
    return o.resolution === "otp" || o.resolution === "prepaid_otp" ? "ok" : "check";
  }
  // REJECT
  return o.resolution === "prepaid" || o.resolution === "prepaid_otp" ? "ok" : "blocked";
}

/** One-line merchant summary for an order (plain English, no numbers). */
export function orderOneLiner(o: StoryOrder): string {
  const rule = o.result.rule_fired;
  const rulePhrase: Record<string, string> = {
    "RULE-001": "COD above your safe limit",
    "RULE-002": "Address needs checking",
    "RULE-003": "Repeat returner",
    "RULE-004": "Late-night remote order",
  };
  if (rule && rulePhrase[rule]) return rulePhrase[rule];
  const d = o.result.decision;
  if (d === "ACCEPT") return "Trusted customer";
  if (d === "REVIEW") return "Needs one quick check";
  return "Too risky to ship COD";
}

/** Total ₹ value of blocked (REJECT) orders — the real session number. */
export function sessionBlockedValue(orders: StoryOrder[]): number {
  return orders
    .filter((o) => o.result.decision === "REJECT")
    .reduce((sum, o) => sum + o.amount, 0);
}
