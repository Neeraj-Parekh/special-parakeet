// figma/svg-kit/lib.mjs
// RTO Trust Layer — Diagram Kit · shared drawing library (single source of truth).
// Generates flat, Figma-import-safe SVG: no defs/markers/gradients/filters/use.
// Grammar (from the 11 reference mood boards): containment = meaning,
// dashed = logical/trust boundary, color = category, orthogonal routing,
// direction = narrative. Icons are Lucide-style stroke geometry (ISC).

import { writeFileSync, mkdirSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(__dirname, '..', 'svgs');

export const T = {
  navy: '#02042B', blue: '#2B84EB', blue4: '#528FF0',
  mint: '#00B878', amber: '#F5A623', red: '#E5484D',
  bg: '#F6F8FC', card: '#FFFFFF', border: '#E3E8F0',
  text: '#1A2233', text2: '#5B6478',
  dashed: '#B9C2D0', async: '#94A3B8',
};

export const FONT = 'Inter, Roboto, sans-serif';
export const MONO = 'JetBrains Mono, Roboto Mono, monospace';

export const esc = (s) => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&apos;');

export function txt({ x, y, s, size = 12, weight = 400, fill = T.text, mono = false, anchor = 'start', spacing = 0 }) {
  const fam = mono ? MONO : FONT;
  return `<text x="${x}" y="${y}" font-family="${fam}" font-size="${size}" font-weight="${weight}" fill="${fill}"${anchor !== 'start' ? ` text-anchor="${anchor}"` : ''}${spacing ? ` letter-spacing="${spacing}"` : ''}>${esc(s)}</text>`;
}

export function para({ x, y, lines, size = 11, lh = 15, weight = 400, fill = T.text2, mono = false, anchor = 'start' }) {
  return lines.map((s, i) => txt({ x, y: y + i * lh, s, size, weight, fill, mono, anchor })).join('\n  ');
}

export function rect({ x, y, w, h, rx = 8, fill = T.card, stroke = null, sw = 1, dash = null, fillOpacity = 1, strokeOpacity = 1 }) {
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}"${fillOpacity !== 1 ? ` fill-opacity="${fillOpacity}"` : ''}${stroke ? ` stroke="${stroke}" stroke-width="${sw}"` : ''}${dash ? ` stroke-dasharray="${dash}"` : ''}${strokeOpacity !== 1 ? ` stroke-opacity="${strokeOpacity}"` : ''}/>`;
}

export function wrap(s, n = 100) {
  const words = String(s).split(' ');
  const lines = [];
  let cur = '';
  for (const wd of words) {
    if ((cur + ' ' + wd).trim().length > n) { lines.push(cur.trim()); cur = wd; }
    else cur += ' ' + wd;
  }
  if (cur.trim()) lines.push(cur.trim());
  return lines;
}

export function chipW(label, { size = 10, pad = 10 } = {}) {
  const per = size * 0.62;
  return Math.round(String(label).length * per + pad * 2);
}

export function chip({ x, y, label, tint = T.navy, size = 10, pad = 10, h = 20, anchorEnd = false, weight = 500, solid = false }) {
  const w = chipW(label, { size, pad });
  const xx = anchorEnd ? x - w : x;
  const bg = solid
    ? rect({ x: xx, y, w, h, rx: h / 2, fill: tint })
    : rect({ x: xx, y, w, h, rx: h / 2, fill: tint, fillOpacity: 0.08 });
  const fill = solid ? '#FFFFFF' : tint;
  return bg + txt({ x: xx + w / 2, y: y + h / 2 + size * 0.36, s: label, size, fill, mono: true, anchor: 'middle', weight });
}

export function zone({ x, y, w, h, label, sub = '', tint = T.blue, dashed = false }) {
  const stroke = dashed ? T.dashed : T.border;
  const dash = dashed ? '5 4' : null;
  const out = [rect({ x, y, w, h, rx: 12, fill: tint, fillOpacity: 0.04, stroke, sw: 1, dash })];
  if (label) out.push(txt({ x: x + 16, y: y + 24, s: label.toUpperCase(), size: 10, fill: tint, mono: true, weight: 500, spacing: 1 }));
  if (sub) out.push(txt({ x: x + 16, y: y + 40, s: sub, size: 10, fill: T.text2 }));
  return out.join('\n  ');
}

export const icons = {
  shield: '<path d="M12 22s8-4 8-10V6l-8-4-8 4v6c0 6 8 10 8 10z"/>',
  zap: '<path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/>',
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  db: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a13.5 13.5 0 0 1 4 9 13.5 13.5 0 0 1-4 9 13.5 13.5 0 0 1-4-9 13.5 13.5 0 0 1 4-9z"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17.5" cy="9.5" r="2.5"/><path d="M16 15.5a6 6 0 0 1 5.5 4.5"/>',
  layers: '<path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="M2 12l10 5 10-5"/><path d="M2 17l10 5 10-5"/>',
  branch: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M6 8.5v7"/><path d="M18 8.5a9 9 0 0 1-9 9"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h8"/>',
  truck: '<path d="M10 17h4V5H2v12h3"/><path d="M20 17h2v-3.3a4 4 0 0 0-1.2-2.8L19 9h-5v8h2"/><circle cx="7.5" cy="17.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
  banknote: '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/>',
  bank: '<path d="M12 2 3 7h18L12 2z"/><path d="M5 7v11M9.5 7v11M14.5 7v11M19 7v11"/><path d="M3 21h18"/>',
  timer: '<circle cx="12" cy="13" r="8"/><path d="M12 13V9"/><path d="M10 2h4"/>',
  box: '<path d="M21 8 12 3 3 8v8l9 5 9-5V8z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v8"/>',
  key: '<circle cx="7.5" cy="15.5" r="4"/><path d="M11 12 21 2"/><path d="M17 6l3 3"/>',
  network: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 10.5 15.4 6.5"/><path d="M8.6 13.5l6.8 4"/>',
  alert: '<path d="M12 3 2 21h20L12 3z"/><path d="M12 10v4"/><path d="M12 17.5h.01"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="M6 6l12 12"/>',
  msg: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  scale: '<path d="M12 3v18"/><path d="M7 21h10"/><path d="M5 7h14"/><path d="m5 7-3 7h6l-3-7z"/><path d="m19 7-3 7h6l-3-7z"/>',
  flask: '<path d="M9 3h6"/><path d="M10 3v6l-6 9a2 2 0 0 0 1.7 3h12.6a2 2 0 0 0 1.7-3l-6-9V3"/><path d="M7 15h10"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  fingerprint: '<path d="M12 6a6 6 0 0 0-6 6c0 2 .5 3.5 1 5"/><path d="M12 6a6 6 0 0 1 6 6c0 2-.3 4-1 6"/><path d="M12 9a3 3 0 0 0-3 3c0 2.5.5 4 1 6"/><path d="M12 9a3 3 0 0 1 3 3c0 2.5-.4 4.5-1 6.5"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
};

export function badge({ x, y, tint = T.blue, icon = 'box' }) {
  const inner = icons[icon] || icons.box;
  return `<g transform="translate(${x},${y})"><rect x="0" y="0" width="36" height="36" rx="8" fill="${tint}" fill-opacity="0.12"/><g transform="translate(6,6) scale(0.75)" fill="none" stroke="${tint}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">${inner}</g></g>`;
}

export function node({ x, y, w = 150, h = 84, title, subs = [], tint = T.blue, icon = null, num = null, border = null, chipLabel = null, titleSize = 12, fill = T.card, chipTint = null, chipInside = false }) {
  const out = [rect({ x, y, w, h, rx: 8, fill, stroke: border || T.border, sw: border ? 1.5 : 1 })];
  if (num != null) out.push(numchip({ x: x + 12, y: y - 10, n: num }));
  const hasIcon = !!icon;
  const tx = x + 14 + (hasIcon ? 46 : 0);
  const n = subs.length;
  const titleY = y + h / 2 + (n === 0 ? 4 : n === 1 ? -3 : -11);
  out.push(txt({ x: tx, y: titleY, s: title, size: titleSize, weight: 600, fill: T.text }));
  subs.forEach((s, i) => out.push(txt({ x: tx, y: titleY + 15 + i * 12, s, size: 10, fill: T.text2 })));
  if (hasIcon) out.push(badge({ x: x + 12, y: y + (h - 36) / 2, tint, icon }));
  if (chipLabel) {
    if (chipInside) out.push(chip({ x: tx, y: y + h - 24, label: chipLabel, tint: chipTint || tint, size: 9, pad: 8, h: 18 }));
    else out.push(chip({ x: tx, y: y + h + 6, label: chipLabel, tint: chipTint || tint, size: 9, pad: 8, h: 18 }));
  }
  return out.join('\n  ');
}

export function numchip({ x, y, n }) {
  return `<circle cx="${x}" cy="${y}" r="10" fill="${T.navy}"/>` +
    txt({ x, y: y + 4, s: String(n), size: 11, fill: '#FFFFFF', mono: true, anchor: 'middle', weight: 500 });
}

export function arrow({ pts, color = T.text2, lw = 1.5, dash = null, head = true, label = null, labelAt = 0.5, labelDx = 8, labelDy = -6, labelAnchor = 'start' }) {
  const d = 'M' + pts.map((p) => `${p[0]} ${p[1]}`).join(' L');
  const out = [`<path d="${d}" fill="none" stroke="${color}" stroke-width="${lw}"${dash ? ` stroke-dasharray="${dash}"` : ''} stroke-linecap="round" stroke-linejoin="round"/>`];
  if (head) {
    const [px, py] = pts[pts.length - 2];
    const [cx, cy] = pts[pts.length - 1];
    const dx = cx - px, dy = cy - py;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len, uy = dy / len;
    const s = 5 + lw;
    const bx = cx - ux * s, by = cy - uy * s;
    const nx = -uy, ny = ux;
    out.push(`<path d="M${bx + nx * s * 0.6} ${by + ny * s * 0.6} L${cx} ${cy} L${bx - nx * s * 0.6} ${by - ny * s * 0.6} Z" fill="${color}"/>`);
  }
  if (label) {
    let total = 0;
    const segs = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const l = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
      segs.push(l); total += l;
    }
    let target = total * labelAt, acc = 0, lx = pts[0][0], ly = pts[0][1];
    for (let i = 0; i < segs.length; i++) {
      if (acc + segs[i] >= target) {
        const f = (target - acc) / segs[i];
        lx = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f;
        ly = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f;
        break;
      }
      acc += segs[i];
    }
    out.push(txt({ x: lx + labelDx, y: ly + labelDy, s: label, size: 9, fill: color, mono: true, anchor: labelAnchor }));
  }
  return out.join('\n  ');
}

export function figTitle({ fig, title, sub, x = 48, y = 40, accent = T.navy }) {
  const w = chipW(fig, { size: 11, pad: 12 });
  const out = [
    rect({ x, y, w, h: 26, rx: 13, fill: accent }),
    txt({ x: x + w / 2, y: y + 17.5, s: fig, size: 11, fill: '#FFFFFF', mono: true, anchor: 'middle', weight: 600, spacing: 1 }),
    txt({ x: x + w + 14, y: y + 18.5, s: title, size: 18, weight: 600, fill: T.text }),
  ];
  if (sub) out.push(txt({ x, y: y + 48, s: sub, size: 12, fill: T.text2 }));
  return out.join('\n  ');
}

export function beatHead({ x = 48, y = 40, beat, title, time, tone, canvasW = 1180, right = 48 }) {
  const out = [];
  const bw = chipW(beat, { size: 11, pad: 12 });
  out.push(rect({ x, y, w: bw, h: 26, rx: 13, fill: T.navy }));
  out.push(txt({ x: x + bw / 2, y: y + 17.5, s: beat, size: 11, fill: '#FFFFFF', mono: true, anchor: 'middle', weight: 600, spacing: 1 }));
  out.push(txt({ x: x + bw + 14, y: y + 18.5, s: title, size: 18, weight: 600, fill: T.text }));
  let rx = canvasW - right;
  if (tone) {
    out.push(chip({ x: rx, y: y + 3, label: tone, tint: T.text2, size: 9, pad: 8, h: 20, anchorEnd: true }));
    rx -= chipW(tone, { size: 9, pad: 8 }) + 8;
  }
  if (time) {
    out.push(chip({ x: rx, y: y + 3, label: time, tint: T.amber, size: 9, pad: 8, h: 20, anchorEnd: true }));
    rx -= chipW(time, { size: 9, pad: 8 }) + 8;
  }
  return out.join('\n  ');
}

export function voCaption({ x = 48, y, w = 1084, text, lines = null, label = 'VO', size = 10, lh = 14 }) {
  const ls = lines || wrap(text, Math.floor(w / (size * 0.52)));
  const out = [chip({ x, y, label, tint: T.navy, size: 9, pad: 8, h: 18 })];
  out.push(para({ x: x + 46, y: y + 13, lines: ls, size, lh, fill: T.text2 }));
  return out.join('\n  ');
}

export function svgDoc({ w, h, bg = T.bg, body, title }) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"${title ? ` role="img" aria-label="${esc(title)}"` : ''}>
  <rect x="0" y="0" width="${w}" height="${h}" fill="${bg}"/>
  ${body}
</svg>`;
}

export function save(name, svg) {
  mkdirSync(OUT_DIR, { recursive: true });
  const p = join(OUT_DIR, name);
  writeFileSync(p, svg);
  console.log('wrote', p, svg.length, 'bytes');
}
