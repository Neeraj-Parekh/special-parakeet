// RTC-2 — Pre-build TreeSHAP explainer at startup.
//
// Cold-start p99 is dominated by lazy SHAP TreeExplainer construction
// (~800ms-1.2s on the 79-feature gradient-boosted model). The fix:
// build the explainer ONCE at module-load and reuse it across
// requests. This moves the cost from the first request's p99 to the
// process boot, where it overlaps with the rest of warmup.
//
// This module exports `prebuildExplainer(modelVersion)` which is
// called eagerly at import. Subsequent `explain(features)` calls
// reuse the cached explainer. For the hackathon, the "explainer" is
// a deterministic feature-attribution simulator (the real Python
// backend ships `shap.TreeExplainer(model)` over a LightGBM). The
// seam is identical; only the backing impl swaps.
//
// The async prebuild is awaited on first `explain()` call so the
// module-load doesn't block the process boot on a cold start — but
// the work starts immediately at import (fire-and-forget), so by the
// time the first request arrives it's usually done.

export interface ShapExplanation {
  base_value: number;
  feature_contributions: Array<{
    feature: string;
    value: number | string;
    shap: number; // signed contribution to the probability
  }>;
  model_version: string;
  prebuilt: boolean;
}

const EXPLAINER_BUILD_MS = 900; // simulated build cost

let explainerReady: Promise<void> | null = null;
let explainerBuilt = false;
let builtModelVersion: string | null = null;

/** Kick off the explainer build (idempotent). Returns the promise. */
export function prebuildExplainer(modelVersion: string): Promise<void> {
  if (!explainerReady || builtModelVersion !== modelVersion) {
    builtModelVersion = modelVersion;
    explainerBuilt = false;
    explainerReady = new Promise<void>((resolve) => {
      // Simulate the TreeExplainer tree traversal build. In
      // production this is `shap.TreeExplainer(lgbm_model)`.
      setTimeout(() => {
        explainerBuilt = true;
        resolve();
      }, EXPLAINER_BUILD_MS);
    });
  }
  return explainerReady;
}

/** Block until the explainer is ready (called on first request). */
export async function ensureExplainer(modelVersion: string): Promise<void> {
  await prebuildExplainer(modelVersion);
}

/** Is the explainer ready right now (non-blocking)? */
export function isExplainerReady(): boolean {
  return explainerBuilt;
}

/** Produce a SHAP-style explanation for a feature dict + probability. */
export async function explain(
  features: Record<string, number | string>,
  probability: number,
  featureNames: string[],
  modelVersion: string,
): Promise<ShapExplanation> {
  await ensureExplainer(modelVersion);
  // Deterministic attribution: rank features by their normalized
  // magnitude weighted by a per-feature direction. The real backend
  // uses shap values from the TreeExplainer; this is a faithful shape
  // mock (same fields, same sign convention).
  const base = 0.08; // population base rate
  const target = probability - base;
  const contributions: Array<{
    feature: string;
    value: number | string;
    shap: number;
  }> = [];
  // Pick the top features present in the input for attribution.
  const present = featureNames.filter((n) => n in features).slice(0, 8);
  const weights = present.map((_, i) => 1 / (i + 1));
  const wsum = weights.reduce((a, b) => a + b, 0);
  for (let i = 0; i < present.length; i++) {
    const name = present[i];
    const v = features[name];
    const sign = typeof v === "number" ? (v > 0.5 ? 1 : -1) : 1;
    contributions.push({
      feature: name,
      value: v,
      shap: Math.round(sign * target * (weights[i] / wsum) * 10000) / 10000,
    });
  }
  return {
    base_value: base,
    feature_contributions: contributions,
    model_version: modelVersion,
    prebuilt: explainerBuilt,
  };
}

// Eagerly start the prebuild at module load — RTC-2 fix. This runs
// during the first request that imports this module, overlapping
// with route compilation rather than paying the cost inline.
prebuildExplainer("v2025.08.29-track-c-v3");
