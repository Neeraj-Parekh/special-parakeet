// RTO Trust Layer — money/number formatting (India locale).
//
// HARD RULE (design tokens doc): ALL money renders through
// Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }) with
// tabular-nums so columns of rupees align. Never toLocaleString-by-hand.

const inrFmt = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const plainFmt = new Intl.NumberFormat("en-IN");

/** ₹12,499 (en-IN grouping, no paise). */
export function formatINR(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return inrFmt.format(v);
}

/** 1,24,783 (en-IN grouping, no currency symbol). */
export function formatNum(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return plainFmt.format(v);
}

/** Tailwind class pair to pair with the formatters above. */
export const MONEY_CLS = "tabular-nums font-mono";
