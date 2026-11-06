#!/usr/bin/env bun
/**
 * RTO Trust Layer — Diagram Kit generator
 * ------------------------------------------------------------------
 * Builds FIG.01–FIG.04 as standalone SVGs (Figma-importable: drag &
 * drop → editable vector + text layers) plus a preview.html.
 *
 * Design language: "engineer-drawn light fintech" — fused from the
 * reference library (Family A rigor / B grouping / C containment /
 * D pipeline narrative). Tokens are the SAME ones as the
 * fintech-console-v2 re-theme — diagram and product speak one language.
 *
 * Run:  bun design-kit/generate.ts
 * Out:  public/figures/fig0{1..4}-*.svg + preview.html
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/* ------------------------------------------------------------------ */
/* Tokens — single source of truth (must match globals.css)           */
/* ------------------------------------------------------------------ */

const T = {
  navy: "#02042B",
  blue: "#2B84EB",
  blueL: "#528FF0",
  mint: "#00B878",
  amber: "#F5A623",
  red: "#E5484D",
  bg: "#F6F8FC",
  card: "#FFFFFF",
  border: "#E3E8F0",
  text: "#1A2233",
  text2: "#5B6478",
  gray: "#98A2B3", // arrows: async / neutral
} as const;

const SANS = "Inter, 'Segoe UI', system-ui, -apple-system, sans-serif";
const MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace";

function tint(hex: string, a: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${a})`;
}

const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/* ------------------------------------------------------------------ */
/* Icon set — Lucide-style stroke glyphs on a 24×24 grid              */
/* ------------------------------------------------------------------ */

function rrect(x: number, y: number, w: number, h: number, r: number): string {
  return (
    `M${x + r} ${y}h${w - 2 * r}a${r} ${r} 0 0 1 ${r} ${r}v${h - 2 * r}` +
    `a${r} ${r} 0 0 1 -${r} ${r}h-${w - 2 * r}a${r} ${r} 0 0 1 -${r} -${r}` +
    `v-${h - 2 * r}a${r} ${r} 0 0 1 ${r} -${r}z`
  );
}
function circ(cx: number, cy: number, r: number): string {
  return `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 -${2 * r} 0`;
}

const ICONS: Record<string, string[]> = {
  user: [circ(12, 7.5, 3.5), "M4 20.5c0-4.2 3.6-6.5 8-6.5s8 2.3 8 6.5"],
  truck: [rrect(2, 4.5, 12, 9.5, 1), "M14 7h4.2l3.3 3.3v3.7H14V7z", circ(6.5, 17.5, 1.8), circ(17.5, 17.5, 1.8)],
  store: ["M4 9 5.5 4h13L20 9", "M4.5 9.2V20h15V9.2", "M9.5 20v-5.5h5V20", "M4 9.2h16"],
  server: [rrect(3, 3.5, 18, 6.5, 1.5), rrect(3, 14, 18, 6.5, 1.5), "M6.2 6.75h.01", "M6.2 17.25h.01"],
  cpu: [rrect(5, 5, 14, 14, 1.5), rrect(9.5, 9.5, 5, 5, 1), "M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"],
  database: [
    "M5 5.5a7 2.8 0 1 0 14 0a7 2.8 0 1 0 -14 0",
    "M5 5.5v13a7 2.8 0 0 0 14 0v-13",
    "M5 12a7 2.8 0 0 0 14 0",
  ],
  shield: [
    "M12 2.5l7.5 3v5c0 4.8-3.2 8.9-7.5 10.7-4.3-1.8-7.5-5.9-7.5-10.7v-5l7.5-3z",
    "M9 11.5l2.2 2.2 4-4.4",
  ],
  zap: ["M13 2L5 13.2h5.4L10 22l8-11.2h-5.4L13 2z"],
  clock: [circ(12, 12, 8.5), "M12 7.5V12l3 2.5"],
  check: ["M4.5 12.5l5 5L19.5 6.5"],
  x: ["M6 6l12 12", "M18 6L6 18"],
  globe: [circ(12, 12, 8.5), "M3.5 12h17", "M12 3.5c-3.2 2.6-3.2 14.4 0 17", "M12 3.5c3.2 2.6 3.2 14.4 0 17"],
  network: [
    circ(6.5, 6.5, 2.6), circ(17.5, 6.5, 2.6), circ(6.5, 17.5, 2.6), circ(17.5, 17.5, 2.6),
    "M6.5 9.1v5.8", "M17.5 9.1v5.8", "M9.1 6.5h5.8", "M9.1 17.5h5.8",
  ],
  eye: ["M2.5 12S6 5.8 12 5.8 21.5 12 21.5 12 18 18.2 12 18.2 2.5 12 2.5 12z", circ(12, 12, 2.8)],
  box: ["M12 2.8 21 7.7v8.6l-9 4.9-9-4.9V7.7l9-4.9z", "M3 7.7l9 4.9 9-4.9", "M12 12.6v8.6"],
  link: [
    "M10.2 13.8a4.6 4.6 0 0 0 6.5 0l2.6-2.6a4.6 4.6 0 0 0-6.5-6.5l-1.5 1.5",
    "M13.8 10.2a4.6 4.6 0 0 0-6.5 0l-2.6 2.6a4.6 4.6 0 0 0 6.5 6.5l1.5-1.5",
  ],
  filecheck: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5z", "M14 3v5h5", "M9.2 14.6l1.9 1.9 3.7-3.9"],
  alert: ["M12 3.5 21.5 20H2.5L12 3.5z", "M12 10v4.2", "M12 17.3v.01"],
  activity: ["M2.5 12H6l3.5-8 4.5 16 3.5-8h4"],
  phone: [rrect(7, 2, 10, 20, 2.5), "M11 18.5h2"],
  layers: ["M12 2.8 3.5 7.6 12 12.4l8.5-4.8L12 2.8z", "M3.5 12.4 12 17.2l8.5-4.8", "M3.5 17 12 21.8 20.5 17"],
  gauge: ["M3.5 15a8.5 8.5 0 0 1 17 0", "M12 15l4.6-4.6", circ(12, 15, 1.2)],
};

/* ------------------------------------------------------------------ */
/* SVG builder primitives                                             */
/* ------------------------------------------------------------------ */

type P = [number, number];

interface TextOpts {
  size?: number;
  weight?: number;
  fill?: string;
  anchor?: "start" | "middle" | "end";
  font?: "sans" | "mono";
  tracking?: number; // px
  opacity?: number;
}

class Svg {
  els: string[] = [];
  constructor(public w = 1600, public h = 1000) {}

  add(s: string) { this.els.push(s); }

  text(x: number, y: number, str: string, o: TextOpts = {}) {
    const {
      size = 12, weight = 400, fill = T.text2, anchor = "start",
      font = "sans", tracking = 0, opacity = 1,
    } = o;
    this.add(
      `<text x="${x}" y="${y}" font-family="${font === "mono" ? MONO : SANS}" font-size="${size}" ` +
      `font-weight="${weight}" fill="${fill}" text-anchor="${anchor}"` +
      (tracking ? ` letter-spacing="${tracking}"` : "") +
      (opacity < 1 ? ` opacity="${opacity}"` : "") +
      `>${esc(str)}</text>`
    );
  }

  rect(x: number, y: number, w: number, h: number, o: {
    fill?: string; stroke?: string; sw?: number; rx?: number; dashed?: boolean; shadow?: boolean;
  } = {}) {
    const { fill = "none", stroke, sw = 1, rx = 0, dashed = false, shadow = false } = o;
    this.add(
      `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}"` +
      (stroke ? ` stroke="${stroke}" stroke-width="${sw}"` : "") +
      (dashed ? ` stroke-dasharray="6 5"` : "") +
      (shadow ? ` filter="url(#ds)"` : "") + `/>`
    );
  }

  icon(x: number, y: number, size: number, name: string, color: string) {
    const paths = ICONS[name] ?? ICONS.box;
    const s = size / 24;
    this.add(
      `<g transform="translate(${x} ${y}) scale(${s.toFixed(4)})" fill="none" stroke="${color}" ` +
      `stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
      paths.map((d) => `<path d="${d}"/>`).join("") + `</g>`
    );
  }

  zone(x: number, y: number, w: number, h: number, o: {
    label?: string; labelRight?: string; accent?: string; dashed?: boolean;
    fillAlpha?: number; strokeAlpha?: number; labelColor?: string;
  } = {}) {
    const {
      label, labelRight, accent = T.border, dashed = false,
      fillAlpha = 0.05, strokeAlpha = 1, labelColor,
    } = o;
    const isNeutral = accent === T.border;
    this.rect(x, y, w, h, {
      fill: isNeutral ? T.bg : tint(accent, fillAlpha),
      stroke: isNeutral ? T.border : tint(accent, strokeAlpha),
      sw: dashed ? 1.5 : 1, rx: 12, dashed,
    });
    if (label) {
      this.text(x + 16, y + 26, label, {
        size: 11, weight: 600, fill: labelColor ?? (isNeutral ? T.text2 : accent), tracking: 0.9,
      });
    }
    if (labelRight) {
      this.text(x + w - 16, y + 26, labelRight, {
        size: 10, weight: 500, fill: T.text2, anchor: "end", font: "mono", tracking: 0.4,
      });
    }
  }

  card(x: number, y: number, w: number, h: number, o: {
    name: string; sub?: string; sub2?: string; lines?: string[]; icon?: string;
    accent?: string; nameColor?: string; subMono?: boolean; badge?: number;
    nameSize?: number; subSize?: number;
  }) {
    const {
      name, sub, sub2, lines, icon = "box", accent = T.blue, nameColor,
      subMono = false, badge = 36, nameSize = 13, subSize = 11,
    } = o;
    this.rect(x, y, w, h, { fill: T.card, stroke: T.border, rx: 8, shadow: true });
    const by = lines ? y + 20 : y + (h - badge) / 2;
    this.rect(x + 14, by, badge, badge, {
      fill: tint(accent, 0.12), stroke: tint(accent, 0.3), rx: 8,
    });
    this.icon(x + 14 + (badge - badge * 0.5) / 2, by + (badge - badge * 0.5) / 2, badge * 0.5, icon, accent);
    const tx = x + 14 + badge + 12;
    if (lines) {
      this.text(tx, y + 34, name, { size: nameSize, weight: 600, fill: nameColor ?? T.text });
      lines.forEach((ln, i) => {
        this.text(tx, y + 60 + i * 21, ln, { size: subSize, fill: T.text2 });
      });
    } else if (sub2) {
      this.text(tx, y + 28, name, { size: nameSize, weight: 600, fill: nameColor ?? T.text });
      if (sub) this.text(tx, y + 47, sub, { size: subSize, fill: T.text2 });
      this.text(tx, y + 64, sub2, { size: 9.5, fill: T.text2, font: "mono", opacity: 0.85 });
    } else if (sub) {
      this.text(tx, y + h / 2 - 4, name, { size: nameSize, weight: 600, fill: nameColor ?? T.text });
      this.text(tx, y + h / 2 + 14, sub, { size: subSize, fill: T.text2, font: subMono ? "mono" : "sans" });
    } else {
      this.text(tx, y + h / 2 + 4.5, name, { size: nameSize, weight: 600, fill: nameColor ?? T.text });
    }
  }

  mini(x: number, y: number, w: number, h: number, o: {
    name: string; sub?: string; icon?: string; accent?: string; nameSize?: number; subSize?: number;
  }) {
    const { name, sub, icon = "box", accent = T.blue, nameSize = 12, subSize = 10 } = o;
    this.rect(x, y, w, h, { fill: T.card, stroke: T.border, rx: 8, shadow: true });
    const badge = 28;
    const by = y + (h - badge) / 2;
    this.rect(x + 10, by, badge, badge, { fill: tint(accent, 0.1), stroke: tint(accent, 0.28), rx: 6 });
    this.icon(x + 10 + (badge - 16) / 2, by + (badge - 16) / 2, 16, icon, accent);
    const tx = x + 10 + badge + 10;
    if (sub) {
      this.text(tx, y + h / 2 - 3, name, { size: nameSize, weight: 600, fill: T.text });
      this.text(tx, y + h / 2 + 13, sub, { size: subSize, fill: T.text2 });
    } else {
      this.text(tx, y + h / 2 + 4, name, { size: nameSize, weight: 600, fill: T.text });
    }
  }

  stat(x: number, y: number, w: number, h: number, label: string, value: string, accent?: string) {
    this.rect(x, y, w, h, { fill: accent ? tint(accent, 0.06) : T.card, stroke: accent ? tint(accent, 0.35) : T.border, rx: 8, shadow: !accent });
    this.text(x + w / 2, y + 36, label, { size: 10, weight: 600, fill: T.text2, anchor: "middle", tracking: 0.8 });
    this.text(x + w / 2, y + 68, value, { size: 19, weight: 600, fill: accent ?? T.navy, anchor: "middle", font: "mono" });
  }

  chip(x: number, y: number, label: string, accent: string, o: { h?: number; size?: number } = {}) {
    const { h = 24, size = 10 } = o;
    const w = label.length * size * 0.62 + 26;
    this.rect(x, y, w, h, { fill: tint(accent, 0.1), stroke: tint(accent, 0.32), rx: h / 2 });
    this.text(x + w / 2, y + h / 2 + 3.5, label, {
      size, weight: 600, fill: accent, anchor: "middle", font: "mono", tracking: 0.3,
    });
    return w;
  }

  actor(cx: number, cy: number, icon: string, label: string, sub?: string) {
    const r = 26;
    this.add(`<circle cx="${cx}" cy="${cy}" r="${r}" fill="${T.card}" stroke="${T.border}" filter="url(#ds)"/>`);
    this.icon(cx - 11, cy - 11, 22, icon, T.text2);
    this.text(cx, cy + r + 18, label, { size: 12, weight: 600, fill: T.text, anchor: "middle" });
    if (sub) this.text(cx, cy + r + 33, sub, { size: 10, fill: T.text2, anchor: "middle" });
  }

  /** Orthogonal arrow with rounded corners + solid triangular head. */
  arrow(pts: P[], o: {
    color?: string; dashed?: boolean; width?: number; head?: boolean; headSize?: number; radius?: number;
  } = {}) {
    const { color = T.text2, dashed = false, width = 1.5, head = true, headSize = 8, radius = 7 } = o;
    // pull the tip back so the shaft doesn't poke through the head
    const tip = pts[pts.length - 1];
    const prev = pts[pts.length - 2];
    const ang = Math.atan2(tip[1] - prev[1], tip[0] - prev[0]);
    const back = head ? headSize - 1.5 : 0;
    const tipAdj: P = [tip[0] - back * Math.cos(ang), tip[1] - back * Math.sin(ang)];
    const p = [...pts.slice(0, -1), tipAdj];
    // rounded-corner path
    let d = `M ${p[0][0]} ${p[0][1]}`;
    for (let i = 1; i < p.length - 1; i++) {
      const [px, py] = p[i - 1], [cx, cy] = p[i], [nx, ny] = p[i + 1];
      const v1 = [cx - px, cy - py], v2 = [nx - cx, ny - cy];
      const l1 = Math.hypot(v1[0], v1[1]), l2 = Math.hypot(v2[0], v2[1]);
      const rr = Math.min(radius, l1 / 2, l2 / 2);
      d += ` L ${cx - (v1[0] / l1) * rr} ${cy - (v1[1] / l1) * rr}`;
      d += ` Q ${cx} ${cy} ${cx + (v2[0] / l2) * rr} ${cy + (v2[1] / l2) * rr}`;
    }
    d += ` L ${p[p.length - 1][0]} ${p[p.length - 1][1]}`;
    this.add(
      `<path d="${d}" fill="none" stroke="${color}" stroke-width="${width}" stroke-linecap="round"` +
      (dashed ? ` stroke-dasharray="6 5"` : "") + `/>`
    );
    if (head) {
      const bx = tip[0] - headSize * Math.cos(ang), by = tip[1] - headSize * Math.sin(ang);
      const nx = -Math.sin(ang) * 3.4, ny = Math.cos(ang) * 3.4;
      this.add(
        `<polygon points="${tip[0]},${tip[1]} ${bx + nx},${by + ny} ${bx - nx},${by - ny}" fill="${color}"/>`
      );
    }
  }

  /** Headless thin connector (hash links, tree wires, bus rails). */
  wire(pts: P[], o: { color?: string; width?: number; dashed?: boolean } = {}) {
    const { color = "#B9C2D0", width = 1.2, dashed = false } = o;
    const d = pts.map((p, i) => `${i ? "L" : "M"} ${p[0]} ${p[1]}`).join(" ");
    this.add(
      `<path d="${d}" fill="none" stroke="${color}" stroke-width="${width}"` +
      (dashed ? ` stroke-dasharray="6 5"` : "") + `/>`
    );
  }

  dot(x: number, y: number, r: number, color: string) {
    this.add(`<circle cx="${x}" cy="${y}" r="${r}" fill="${color}"/>`);
  }

  titleBlock(figNo: string, title: string, desc: string, metric?: { label: string; accent?: string }) {
    this.rect(48, 40, 74, 26, { fill: tint(T.blue, 0.1), stroke: tint(T.blue, 0.32), rx: 13 });
    this.text(85, 57, `FIG ${figNo}`, { size: 10, weight: 600, fill: T.blue, anchor: "middle", font: "mono", tracking: 0.6 });
    this.text(48, 102, title, { size: 20, weight: 700, fill: T.navy });
    this.text(48, 126, desc, { size: 12, fill: T.text2 });
    if (metric) {
      const accent = metric.accent ?? T.blue;
      const w = metric.label.length * 6.6 + 30;
      this.rect(1552 - w, 40, w, 26, { fill: tint(accent, 0.1), stroke: tint(accent, 0.32), rx: 13 });
      this.text(1552 - w / 2, 57, metric.label, { size: 10.5, weight: 600, fill: accent, anchor: "middle", font: "mono", tracking: 0.3 });
    }
  }

  legend(x: number, y: number, items: { color: string; dashed?: boolean; label: string }[]) {
    let cx = x;
    for (const it of items) {
      this.wire([[cx, y], [cx + 26, y]], { color: it.color, width: 1.5, dashed: it.dashed });
      this.text(cx + 32, y + 3.5, it.label, { size: 10, fill: T.text2 });
      cx += 32 + it.label.length * 6.4 + 26;
    }
  }

  footer() {
    this.text(1552, 986, "RTO Trust Layer · generated by design-kit/generate.ts · tokens = fintech-console-v2 · Inter / JetBrains Mono", {
      size: 9, fill: T.text2, anchor: "end", opacity: 0.65,
    });
  }

  toString(): string {
    return (
      `<svg xmlns="http://www.w3.org/2000/svg" width="${this.w}" height="${this.h}" viewBox="0 0 ${this.w} ${this.h}" font-family="${SANS}">\n` +
      `<!-- RTO Trust Layer diagram kit · tokens: navy ${T.navy} blue ${T.blue} mint ${T.mint} amber ${T.amber} red ${T.red} bg ${T.bg} border ${T.border} -->\n` +
      `<defs><filter id="ds" x="-30%" y="-30%" width="160%" height="160%">` +
      `<feDropShadow dx="0" dy="2" stdDeviation="5" flood-color="${T.navy}" flood-opacity="0.08"/></filter></defs>\n` +
      `<rect width="${this.w}" height="${this.h}" fill="${T.card}"/>\n` +
      this.els.join("\n") + "\n</svg>"
    );
  }
}

/* ------------------------------------------------------------------ */
/* FIG.01 — System Context                                            */
/* ------------------------------------------------------------------ */

function fig01(): Svg {
  const s = new Svg();
  s.titleBlock(
    "01",
    "System Context — a COD order under the microscope",
    "Where RTO Trust Layer sits: between the merchant storefront and the courier network. Verdicts return before the courier leaves.",
    { label: "p50 29 ms", accent: T.mint }
  );

  // Actors
  s.actor(122, 338, "user", "Shopper", "COD checkout");
  s.actor(122, 770, "truck", "Courier", "delivery · RTO");

  // Merchant zone
  s.zone(230, 250, 330, 340, { label: "MERCHANT STACK" });
  s.card(250, 296, 290, 84, { name: "Storefront", sub: "Shopify · WooCommerce", icon: "store", accent: T.blue });
  s.card(250, 436, 290, 84, { name: "Order API", sub: "creates order · calls risk", icon: "server", accent: T.blue });
  s.text(250, 572, "integrations · Shopify · WooCommerce · custom REST", { size: 10, fill: T.text2, opacity: 0.8 });

  // RTO Trust Layer zone (hero)
  s.zone(680, 180, 540, 620, { label: "RTO TRUST LAYER", accent: T.blue, strokeAlpha: 0.45, labelRight: "SLA < 30 ms" });
  s.card(780, 250, 360, 84, { name: "API Gateway", sub: "scorer auth · 10 req/s cap", icon: "shield", accent: T.blue });
  s.card(780, 386, 360, 84, { name: "Feature Store", sub: "rings · history · device · geo", icon: "database", accent: T.blue });
  s.card(780, 522, 360, 84, { name: "ONNX Inference", sub: "GBDT · 60 features · 1.6 µs/row", icon: "cpu", accent: T.blue });
  s.card(780, 658, 360, 84, { name: "Decision Engine", sub: "thresholds · rules DSL", icon: "activity", accent: T.blue });
  // internal flow
  s.arrow([[960, 334], [960, 382]], { width: 1.25 });
  s.arrow([[960, 470], [960, 518]], { width: 1.25 });
  s.arrow([[960, 606], [960, 654]], { width: 1.25 });

  // Verdict zone
  s.zone(1290, 180, 262, 620, { label: "VERDICT" });
  s.card(1310, 290, 222, 84, { name: "ACCEPT", sub: "auto-approve COD", icon: "check", accent: T.mint, nameColor: T.mint });
  s.card(1310, 430, 222, 84, { name: "REVIEW · OTP", sub: "verify shopper", icon: "phone", accent: T.amber, nameColor: T.amber });
  s.card(1310, 570, 222, 84, { name: "REJECT", sub: "block COD · prepaid", icon: "x", accent: T.red, nameColor: T.red });

  // 1 · order
  s.arrow([[148, 338], [246, 338]]);
  s.text(198, 326, "1 · COD order", { size: 11, weight: 600, fill: T.text2, anchor: "middle" });
  // storefront -> order api
  s.arrow([[395, 380], [395, 432]], { width: 1.25 });

  // 2 · score request
  s.arrow([[540, 478], [625, 478], [625, 292], [776, 292]]);
  s.text(632, 282, "2 · POST /api/risk/score", { size: 10, weight: 600, fill: T.text2, font: "mono" });

  // 3 · decision back
  s.arrow([[780, 700], [660, 700], [660, 510], [544, 510]], { dashed: true, color: T.blueL, width: 1.25 });
  s.text(668, 692, "3 · decision JSON · verdict", { size: 11, weight: 600, fill: T.blueL });

  // verdict branches
  s.arrow([[1140, 672], [1180, 672], [1180, 332], [1306, 332]], { color: T.mint, width: 1.75 });
  s.arrow([[1140, 700], [1215, 700], [1215, 472], [1306, 472]], { color: T.amber, width: 1.75 });
  s.arrow([[1140, 728], [1250, 728], [1250, 612], [1306, 612]], { color: T.red, width: 1.75 });
  s.text(1188, 662, "accept", { size: 10, weight: 600, fill: T.mint });
  s.text(1223, 690, "review", { size: 10, weight: 600, fill: T.amber });
  s.text(1258, 718, "reject", { size: 10, weight: 600, fill: T.red });

  // 4 · courier feedback loop
  s.arrow([[148, 770], [700, 770], [700, 428], [776, 428]], { dashed: true, color: T.gray, width: 1.25 });
  s.text(260, 758, "4 · delivery outcome · RTO events (async)", { size: 11, weight: 600, fill: T.text2 });

  s.legend(48, 958, [
    { color: T.text2, label: "solid — sync request" },
    { color: T.gray, dashed: true, label: "dashed — async / feedback" },
    { color: T.mint, label: "verdict paths" },
  ]);
  s.footer();
  return s;
}

/* ------------------------------------------------------------------ */
/* FIG.02 — Scoring Pipeline (hero)                                   */
/* ------------------------------------------------------------------ */

function fig02(): Svg {
  const s = new Svg();
  s.titleBlock(
    "02",
    "Scoring Pipeline — event to verdict in 29 ms",
    "Left to right: every COD order is scored on the edge before it is accepted. Latency chips show per-stage budgets.",
    { label: "1.6 µs / row inference", accent: T.blue }
  );

  // column labels
  const colY = 196;
  s.text(48, colY, "EVENT", { size: 11, weight: 600, fill: T.text2, tracking: 0.9 });
  s.text(348, colY, "INGRESS", { size: 11, weight: 600, fill: T.text2, tracking: 0.9 });
  s.text(632, colY, "FEATURE ASSEMBLY", { size: 11, weight: 600, fill: T.text2, tracking: 0.9 });
  s.text(952, colY, "MODEL", { size: 11, weight: 600, fill: T.text2, tracking: 0.9 });
  s.text(1256, colY, "DECISION", { size: 11, weight: 600, fill: T.text2, tracking: 0.9 });

  // EVENT
  s.card(48, 220, 240, 96, { name: "order.created", sub: "COD ₹1,899 · pin 560103", icon: "zap", accent: T.amber, subMono: true });

  // INGRESS
  s.card(348, 220, 240, 96, { name: "Edge API", sub: "scorer auth · 10 r/s", icon: "shield", accent: T.blue });
  s.chip(348, 330, "p50 8 ms", T.blue);

  // FEATURES zone
  s.zone(632, 220, 280, 380, { label: "60 FEATURES", accent: T.blue, strokeAlpha: 0.4, fillAlpha: 0.04 });
  const feats: { name: string; sub: string; icon: string }[] = [
    { name: "Fraud-ring velocity", sub: "co-buyer graph · 30 d", icon: "network" },
    { name: "Shopper RTO history", sub: "orders · returns · RTO %", icon: "user" },
    { name: "Device fingerprint", sub: "hw + session signals", icon: "eye" },
    { name: "Geo velocity", sub: "addr ↔ pin distance", icon: "globe" },
    { name: "Basket profile", sub: "value · category mix", icon: "box" },
  ];
  feats.forEach((f, i) => {
    s.mini(652, 262 + i * 66, 240, 56, { ...f, accent: T.blue });
  });

  // MODEL
  s.card(952, 220, 240, 96, { name: "ONNX GBDT", sub: "60 features · quantised", icon: "cpu", accent: T.blue });
  s.chip(952, 330, "1.6 µs / row", T.mint);

  // DECISION
  s.card(1256, 220, 296, 96, { name: "Score 0–100", sub: "RTO risk probability", icon: "gauge", accent: T.blue });
  s.card(1256, 380, 296, 84, { name: "ACCEPT", sub: "score < 35 · auto-approve", icon: "check", accent: T.mint, nameColor: T.mint });
  s.card(1256, 524, 296, 84, { name: "REVIEW · OTP", sub: "35–70 · verify via SMS", icon: "phone", accent: T.amber, nameColor: T.amber });
  s.card(1256, 668, 296, 84, { name: "REJECT", sub: "score > 70 · offer prepaid", icon: "x", accent: T.red, nameColor: T.red });

  // stage arrows
  s.arrow([[290, 268], [344, 268]]);
  s.arrow([[590, 268], [628, 268]]);
  s.arrow([[914, 268], [948, 268]]);
  s.arrow([[1194, 268], [1252, 268]]);
  s.text(610, 258, "vector", { size: 10, weight: 600, fill: T.text2, anchor: "middle" });
  s.text(1223, 258, "score", { size: 10, weight: 600, fill: T.text2, anchor: "middle" });

  // branch bus: score card bottom -> left rail -> three verdict cards
  s.wire([[1404, 316], [1404, 344], [1218, 344], [1218, 700]], { color: T.text2, width: 1.5 });
  s.dot(1218, 344, 3.5, T.text2);
  s.arrow([[1218, 422], [1252, 422]], { color: T.mint, width: 1.75 });
  s.arrow([[1218, 566], [1252, 566]], { color: T.amber, width: 1.75 });
  s.arrow([[1218, 710], [1252, 710]], { color: T.red, width: 1.75 });
  s.dot(1218, 422, 3, T.mint);
  s.dot(1218, 566, 3, T.amber);
  s.dot(1218, 710, 3, T.red);

  // sealed rail
  s.zone(632, 848, 920, 104, { label: "SEALED — EVERY DECISION IS AN AUDIT RECORD", dashed: true });
  s.icon(664, 880, 30, "filecheck", T.blue);
  s.text(712, 894, "append-only audit log · Merkle root per batch · verify any time", { size: 11.5, fill: T.text2 });
  s.chip(1350, 890, "seal 4 ms", T.blue, { h: 22 });
  s.arrow([[1404, 752], [1404, 844]], { dashed: true, color: T.gray, width: 1.25 });
  s.text(1420, 800, "→ FIG. 03", { size: 10, weight: 600, fill: T.text2, font: "mono" });

  s.legend(48, 958, [
    { color: T.text2, label: "solid — sync" },
    { color: T.gray, dashed: true, label: "dashed — async / sealing" },
  ]);
  s.footer();
  return s;
}

/* ------------------------------------------------------------------ */
/* FIG.03 — Trust Chain (Merkle-sealed audit)                         */
/* ------------------------------------------------------------------ */

function fig03(): Svg {
  const s = new Svg();
  s.titleBlock(
    "03",
    "Trust Chain — Merkle-sealed audit trail",
    "Every scored decision becomes a tamper-evident record. Anyone can re-verify the chain — no trust in us required.",
    { label: "100% sealed", accent: T.mint }
  );

  // records
  s.text(48, 196, "DECISION RECORDS · BATCH #47", { size: 11, weight: 600, fill: T.text2, tracking: 0.9 });
  const records: { name: string; sub: string; accent: string }[] = [
    { name: "D-1001 · ACCEPT", sub: "COD ₹1,299 · 14:02:11 IST", accent: T.mint },
    { name: "D-1002 · REVIEW", sub: "COD ₹899 · 14:02:11 IST", accent: T.amber },
    { name: "D-1003 · REJECT", sub: "COD ₹2,499 · 14:02:12 IST", accent: T.red },
    { name: "D-1004 · ACCEPT", sub: "COD ₹649 · 14:02:12 IST", accent: T.mint },
  ];
  records.forEach((r, i) => {
    s.card(48, 214 + i * 92, 320, 76, {
      name: r.name, sub: r.sub, sub2: "record_source: order-api",
      icon: "filecheck", accent: r.accent, nameColor: T.text,
    });
  });

  // hash arrow
  s.arrow([[368, 390], [456, 390]], { width: 1.25 });
  s.text(412, 378, "SHA-256", { size: 10, weight: 600, fill: T.text2, anchor: "middle", font: "mono" });

  // merkle tree — leaves
  const leafX = 460, leafW = 200, leafH = 48;
  const leafY = [214, 290, 366, 442];
  leafY.forEach((y, i) => {
    s.rect(leafX, y, leafW, leafH, { fill: tint(T.blue, 0.06), stroke: tint(T.blue, 0.25), rx: 6 });
    s.text(leafX + leafW / 2, y + 29, `h(D-100${i + 1})`, { size: 11, weight: 600, fill: T.text, anchor: "middle", font: "mono" });
  });

  // internals
  const inX = 760, inW = 170, inH = 48;
  const inY = [252, 404];
  inY.forEach((y, i) => {
    s.rect(inX, y, inW, inH, { fill: tint(T.blue, 0.06), stroke: tint(T.blue, 0.25), rx: 6 });
    s.text(inX + inW / 2, y + 29, i === 0 ? "H(01·02)" : "H(03·04)", { size: 11, weight: 600, fill: T.text, anchor: "middle", font: "mono" });
  });

  // root
  s.rect(1030, 324, 180, 56, { fill: tint(T.mint, 0.1), stroke: tint(T.mint, 0.45), rx: 8, shadow: true });
  s.text(1120, 349, "Merkle root", { size: 13, weight: 700, fill: T.navy, anchor: "middle" });
  s.text(1120, 369, "9f3a…c7e1", { size: 10.5, weight: 600, fill: T.text2, anchor: "middle", font: "mono" });

  // tree wires
  s.wire([[660, 238], [710, 238], [710, 276], [756, 276]]);
  s.wire([[660, 314], [710, 314], [710, 276], [756, 276]]);
  s.wire([[660, 390], [710, 390], [710, 428], [756, 428]]);
  s.wire([[660, 466], [710, 466], [710, 428], [756, 428]]);
  s.wire([[930, 276], [980, 276], [980, 352], [1026, 352]]);
  s.wire([[930, 428], [980, 428], [980, 352], [1026, 352]]);

  // published anchor
  s.arrow([[1120, 380], [1120, 416]], { color: T.mint, width: 1.5 });
  s.card(1030, 420, 180, 72, { name: "Anchored daily", sub: "root is public", icon: "globe", accent: T.mint });

  // verification cards
  s.card(460, 640, 520, 190, {
    name: "Verification — INTACT",
    lines: [
      "1 · recompute leaf hashes from records",
      "2 · walk the path up to the root",
      "3 · compare with the published anchor",
    ],
    icon: "check", accent: T.mint, nameColor: T.mint,
  });
  s.chip(522, 772, "200 · INTACT", T.mint);

  s.card(1010, 640, 542, 190, {
    name: "Tamper test — MISMATCH",
    lines: [
      "flip 1 byte in D-1003 → leaf hash changes",
      "root ≠ published anchor → chain broken",
      "tamper is detected — not just claimed",
    ],
    icon: "alert", accent: T.red, nameColor: T.red,
  });
  s.chip(1072, 772, "409 · TAMPERED", T.red);

  // verify paths
  s.arrow([[1120, 492], [1120, 556]], { dashed: true, color: T.gray, width: 1.25 });
  s.dot(1120, 560, 3.5, T.gray);
  s.arrow([[1120, 560], [720, 560], [720, 636]], { dashed: true, color: T.gray, width: 1.25 });
  s.arrow([[1120, 560], [1280, 560], [1280, 636]], { dashed: true, color: T.gray, width: 1.25 });
  s.text(880, 548, "re-verify any time · GET /api/audit/verify", { size: 10, weight: 600, fill: T.text2, anchor: "middle", font: "mono" });

  s.legend(48, 958, [
    { color: T.text2, label: "solid — hash links" },
    { color: T.gray, dashed: true, label: "dashed — verification path" },
  ]);
  s.footer();
  return s;
}

/* ------------------------------------------------------------------ */
/* FIG.04 — Deployment & Performance                                  */
/* ------------------------------------------------------------------ */

function fig04(): Svg {
  const s = new Svg();
  s.titleBlock(
    "04",
    "Deployment — edge region, two AZs, one truth",
    "Scoring runs close to the merchant; every AZ reads a replica; writes seal once. Latency budget on the bottom rail.",
    { label: "SEC-5 · 10 req/s", accent: T.blue }
  );

  // actor
  s.actor(122, 300, "server", "merchant backend", "scorer key");

  // outer region zone
  s.zone(250, 180, 1030, 560, { label: "EDGE REGION · ap-south-1", labelRight: "Vercel", accent: T.blue, strokeAlpha: 0.3, fillAlpha: 0.03 });

  // WAF bar
  s.card(270, 228, 990, 60, { name: "Edge WAF + rate limit", sub: "scorer auth · 10 req/s · 429 + Retry-After", icon: "shield", accent: T.red, badge: 34 });

  // merchant -> WAF
  s.arrow([[148, 300], [200, 300], [200, 258], [266, 258]]);
  s.text(152, 320, "HTTPS + scorer key", { size: 10, weight: 600, fill: T.text2 });

  // WAF -> AZs
  s.arrow([[500, 288], [500, 368]]);
  s.arrow([[900, 288], [900, 368]]);
  s.text(700, 336, "load-balanced · any AZ", { size: 10, weight: 600, fill: T.text2, anchor: "middle" });

  // AZ zones
  s.zone(270, 320, 470, 390, { label: "AVAILABILITY ZONE 1" });
  s.zone(770, 320, 470, 390, { label: "AVAILABILITY ZONE 2" });

  // AZ1 cards
  s.card(300, 372, 400, 84, { name: "API route", sub: "/api/risk/score · stateless", icon: "server", accent: T.blue, subMono: true });
  s.mini(300, 472, 190, 100, { name: "ONNX runtime", sub: "1.6 µs / row", icon: "cpu", accent: T.blue, nameSize: 12.5 });
  s.mini(510, 472, 190, 100, { name: "Session cache", sub: "warm 6 ms", icon: "zap", accent: T.amber, nameSize: 12.5 });
  s.arrow([[395, 456], [395, 468]], { width: 1.25 });
  s.arrow([[605, 456], [605, 468]], { width: 1.25 });

  // AZ2 cards
  s.card(800, 372, 400, 84, { name: "API route", sub: "/api/risk/score · stateless", icon: "server", accent: T.blue, subMono: true });
  s.mini(800, 472, 190, 100, { name: "ONNX runtime", sub: "1.6 µs / row", icon: "cpu", accent: T.blue, nameSize: 12.5 });
  s.mini(1010, 472, 190, 100, { name: "SQLite replica", sub: "local reads · ms", icon: "database", accent: T.blueL, nameSize: 12.5 });
  s.arrow([[895, 456], [895, 468]], { width: 1.25 });
  s.arrow([[1105, 456], [1105, 468]], { width: 1.25 });

  // data tier zone
  s.zone(1310, 180, 242, 560, { label: "DATA TIER", accent: T.mint, strokeAlpha: 0.4, fillAlpha: 0.04 });
  s.card(1330, 260, 202, 84, { name: "Sealed log", sub: "append-only", icon: "filecheck", accent: T.mint });
  s.card(1330, 380, 202, 84, { name: "SQLite primary", sub: "single writer", icon: "database", accent: T.mint });
  s.card(1330, 500, 202, 84, { name: "Merkle roots", sub: "anchored daily", icon: "link", accent: T.mint });

  // replication dashed: primary -> replica (through the routing channel)
  s.arrow([[1330, 422], [1258, 422], [1258, 464], [1150, 464], [1150, 468]], { dashed: true, color: T.gray, width: 1.25 });
  s.text(1262, 446, "sync", { size: 10, weight: 600, fill: T.text2 });

  // latency budget rail
  s.zone(250, 770, 1030, 180, { label: "LATENCY BUDGET · PER ORDER" });
  const rail: { label: string; value: string; accent?: string }[] = [
    { label: "EDGE AUTH", value: "3 ms" },
    { label: "FEATURES", value: "6 ms" },
    { label: "INFERENCE", value: "1.6 µs/row" },
    { label: "SEAL", value: "4 ms" },
    { label: "E2E · P50", value: "29 ms", accent: T.mint },
  ];
  // 5 uniform cards + 4 gaps, centered in the 1030-wide zone
  const rw = 187, rg = 12;
  let rx = 250 + Math.round((1030 - (rw * 5 + rg * 4)) / 2);
  rail.forEach((r) => {
    s.stat(rx, 816, rw, 100, r.label, r.value, r.accent);
    rx += rw + rg;
  });

  s.legend(48, 958, [
    { color: T.text2, label: "solid — request path" },
    { color: T.gray, dashed: true, label: "dashed — replication" },
  ]);
  s.footer();
  return s;
}

/* ------------------------------------------------------------------ */
/* Emit                                                                */
/* ------------------------------------------------------------------ */

const FIGS = [
  { no: "01", slug: "fig01-system-context", title: "System Context", desc: "Where RTO Trust Layer sits — merchant ↔ risk engine ↔ courier loop.", build: fig01 },
  { no: "02", slug: "fig02-scoring-pipeline", title: "Scoring Pipeline", desc: "The hero: event → features → ONNX → verdict in 29 ms.", build: fig02 },
  { no: "03", slug: "fig03-trust-chain", title: "Trust Chain", desc: "Merkle-sealed audit trail — verify without trusting us.", build: fig03 },
  { no: "04", slug: "fig04-deployment", title: "Deployment & Performance", desc: "Edge region, two AZs, one writer, latency budget rail.", build: fig04 },
];

const OUT = join(process.cwd(), "public", "figures");
mkdirSync(OUT, { recursive: true });

let previewSections = "";
for (const f of FIGS) {
  const svg = f.build().toString();
  writeFileSync(join(OUT, `${f.slug}.svg`), svg);
  console.log(`OK ${f.slug}.svg (${(svg.length / 1024).toFixed(1)} KB)`);
  previewSections += `
  <section class="fig" id="${f.slug}">
    <div class="fig-head">
      <div>
        <h2>${esc(f.title)}</h2>
        <p>${esc(f.desc)}</p>
      </div>
      <a class="dl" href="${f.slug}.svg" download>Download .svg</a>
    </div>
    <div class="fig-canvas">${svg}</div>
  </section>`;
}

const tokenChips = [
  ["navy-950", T.navy], ["blue-500", T.blue], ["mint-500", T.mint], ["amber-500", T.amber],
  ["red-500", T.red], ["bg", T.bg], ["border", T.border], ["text", T.text], ["text-2", T.text2],
]
  .map(([n, c]) => `<span class="tok"><i style="background:${c}${c === "#FFFFFF" || c === "#F6F8FC" ? ";border:1px solid #E3E8F0" : ""}"></i>${n}<code>${c}</code></span>`)
  .join("");

const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>RTO Trust Layer — Architecture Figures</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #F6F8FC; color: #1A2233; font-family: Inter, 'Segoe UI', system-ui, sans-serif; }
  .wrap { max-width: 1680px; margin: 0 auto; padding: 40px 24px 80px; }
  header.top h1 { font-size: 26px; font-weight: 700; color: #02042B; margin: 0 0 6px; }
  header.top p { color: #5B6478; font-size: 14px; margin: 0; max-width: 760px; line-height: 1.55; }
  .toks { display: flex; flex-wrap: wrap; gap: 10px; margin: 22px 0 36px; }
  .tok { display: inline-flex; align-items: center; gap: 8px; background: #fff; border: 1px solid #E3E8F0; border-radius: 999px; padding: 6px 12px; font-size: 12px; color: #5B6478; }
  .tok i { width: 14px; height: 14px; border-radius: 4px; display: inline-block; }
  .tok code { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 11px; }
  .fig { background: #fff; border: 1px solid #E3E8F0; border-radius: 16px; padding: 24px; margin-bottom: 32px; box-shadow: 0 1px 2px rgba(2,4,43,.06), 0 8px 24px rgba(2,4,43,.08); }
  .fig-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
  .fig-head h2 { margin: 0; font-size: 18px; font-weight: 700; color: #02042B; }
  .fig-head p { margin: 4px 0 0; color: #5B6478; font-size: 13px; }
  .dl { font-size: 12px; font-weight: 600; color: #2B84EB; text-decoration: none; border: 1px solid rgba(43,132,235,.35); background: rgba(43,132,235,.08); padding: 8px 14px; border-radius: 8px; white-space: nowrap; }
  .dl:hover { background: rgba(43,132,235,.14); }
  .fig-canvas svg { width: 100%; height: auto; display: block; border: 1px solid #F0F3F8; border-radius: 10px; }
  footer { color: #5B6478; font-size: 12px; line-height: 1.6; }
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <h1>RTO Trust Layer — Architecture Figures</h1>
    <p>Generated from code (<code>design-kit/generate.ts</code>) — the same token set as the console. Drag any .svg into Figma and it imports as fully editable layers (vector paths + text). Re-run the generator any time; geometry is deterministic.</p>
    <div class="toks">${tokenChips}</div>
  </header>
  ${previewSections}
  <footer>
    Workflow — <b>1.</b> Drag the .svg files into a Figma page. <b>2.</b> Or run the dev plugin in <code>design-kit/figma-plugin/</code> to generate token styles + component kit natively. <b>3.</b> For the video, use the PNG @2x renders in <code>public/figures/png/</code>.
  </footer>
</div>
</body>
</html>`;

writeFileSync(join(OUT, "preview.html"), html);
console.log("OK preview.html");
console.log(`Done -> ${OUT}`);
