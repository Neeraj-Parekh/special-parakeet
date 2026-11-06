// figma/svg-kit/hero.mjs
// RTO Trust Layer — hero figure set for the Figma board.
// Builds: cover, kit-legend, fig-01..04, beat-01, beat-03, beat-04 into figma/svgs/.
// Run: cd /home/z/my-project && bun figma/svg-kit/hero.mjs

import {
  T, esc, txt, para, rect, zone, node, chip, chipW, arrow, badge, icons,
  numchip, figTitle, beatHead, voCaption, wrap, svgDoc, save,
} from './lib.mjs';

const W = (s) => s; // identity for readability

/* ================================================================ COVER */
{
  const body = [];
  // logo mark
  body.push(rect({ x: 48, y: 48, w: 48, h: 48, rx: 12, fill: T.navy }));
  body.push(txt({ x: 72, y: 76, s: 'RTO', size: 12, fill: '#FFFFFF', mono: true, anchor: 'middle', weight: 600, spacing: 1 }));
  body.push(txt({ x: 116, y: 84, s: 'RTO Trust Layer', size: 40, weight: 700, fill: T.text }));
  body.push(txt({ x: 116, y: 110, s: 'COD return-risk platform · architecture & pitch kit', size: 15, fill: T.text2 }));
  // identity chips
  let cx = 48;
  const chips = [
    ['FIG.01–04 · ARCHITECTURE', T.blue], ['BEAT 1–8 · STORYBOARD', T.amber],
    ['5:00 PITCH · LOCKED', T.mint], ['INTER · 8PT GRID · FLAT', T.text2],
  ];
  for (const [label, tint] of chips) { body.push(chip({ x: cx, y: 140, label, tint, size: 9, pad: 9, h: 20 })); cx += chipW(label, { size: 9, pad: 9 }) + 10; }
  // token swatches
  body.push(txt({ x: 48, y: 192, s: 'TOKENS — ONE SOURCE OF TRUTH (matches the console · globals.css)', size: 9, fill: T.text2, mono: true }));
  const tokens = [
    ['navy-950', T.navy], ['blue-500', T.blue], ['blue-400', T.blue4], ['mint-500', T.mint],
    ['amber-500', T.amber], ['red-500', T.red], ['bg', T.bg], ['card', T.card],
    ['border', T.border], ['text', T.text], ['text-2', T.text2], ['dashed', T.dashed], ['async', T.async],
  ];
  tokens.forEach(([name, hex], i) => {
    const x = 48 + i * 36;
    body.push(rect({ x, y: 204, w: 24, h: 24, rx: 6, fill: hex, stroke: T.border, sw: 1 }));
    body.push(txt({ x: x + 12, y: 242, s: name, size: 8, fill: T.text2, mono: true, anchor: 'middle' }));
  });
  // board map
  body.push(txt({ x: 48, y: 288, s: 'THE BOARD', size: 12, weight: 600, fill: T.text }));
  const pages = ['00 COVER', '01 TOKENS & KIT', '02 FIGURES · FIG.01–04', '03 STORYBOARDS · BEATS 1–8', '04 SCRIPT · 5:00 LOCKED', '05 HOW TO IMPORT'];
  pages.forEach((p, i) => {
    const y = 314 + i * 24;
    body.push(rect({ x: 48, y: y - 8, w: 8, h: 8, rx: 2, fill: T.navy }));
    body.push(txt({ x: 68, y, s: p, size: 10, fill: T.text2, mono: true }));
  });
  // signature verdict trio
  body.push(txt({ x: 860, y: 268, s: 'THE 75-MS DECISION', size: 9, fill: T.text2, mono: true }));
  body.push(node({ x: 860, y: 280, w: 120, h: 64, title: 'ACCEPT', subs: ['expected ₹248'], tint: T.mint, icon: 'check', border: T.mint }));
  body.push(node({ x: 990, y: 280, w: 120, h: 64, title: 'REVIEW', subs: ['₹99 · OTP gate'], tint: T.amber, icon: 'msg', border: T.amber }));
  body.push(node({ x: 1120, y: 280, w: 120, h: 64, title: 'REJECT', subs: ['expected ₹980'], tint: T.red, icon: 'x', border: T.red }));
  body.push(chip({ x: 860, y: 364, label: 'P50 45–75 MS · PYTHON', tint: T.amber, size: 9, pad: 9 }));
  body.push(chip({ x: 860, y: 392, label: 'TARGET <10 MS · COMPILED', tint: T.navy, size: 9, pad: 9 }));
  // bottom bar
  body.push(arrow({ pts: [[48, 566], [1232, 566]], color: T.border, lw: 1, head: false }));
  body.push(txt({ x: 48, y: 600, s: 'github.com/Neeraj-Parekh/special-parakeet', size: 11, fill: T.text2, mono: true }));
  body.push(txt({ x: 1232, y: 600, s: 'rto-trust-layer.vercel.app', size: 11, fill: T.text2, mono: true, anchor: 'end' }));
  save('cover.svg', svgDoc({ w: 1280, h: 640, body: body.join('\n'), title: 'RTO Trust Layer — board cover' }));
}

/* ============================================================ KIT LEGEND */
{
  const body = [];
  body.push(figTitle({ fig: 'KIT', title: 'Diagram kit legend', sub: 'containment = meaning · dashed = trust boundary · color = category · orthogonal routing · direction = narrative' }));
  // row 1: zones + node + chips
  body.push(zone({ x: 48, y: 120, w: 250, h: 130, label: 'solid zone', sub: 'physical / owned boundary', tint: T.blue }));
  body.push(zone({ x: 330, y: 120, w: 250, h: 130, label: 'dashed zone', sub: 'logical / trust boundary', tint: T.navy, dashed: true }));
  body.push(node({ x: 612, y: 136, w: 190, h: 90, title: 'Component', subs: ['white fill · rx 8', 'badge + 1px border'], tint: T.blue, icon: 'zap' }));
  const r1 = [['CATEGORY', T.blue], ['LATENCY / PLAN', T.amber], ['RISK / TAMPER', T.red], ['VERIFIED / SHIPPED', T.mint]];
  r1.forEach(([label, tint], i) => body.push(chip({ x: 830, y: 136 + i * 28, label, tint, size: 9, pad: 9 })));
  // row 2: arrows + badges + numchips + type
  body.push(arrow({ pts: [[48, 310], [210, 310]], color: T.text2, lw: 1.5, label: 'PRIMARY FLOW', labelDx: 10, labelDy: -8 }));
  body.push(arrow({ pts: [[48, 350], [210, 350]], color: T.async, lw: 1.25, dash: '4 4', label: 'ASYNC', labelDx: 10, labelDy: -8 }));
  body.push(arrow({ pts: [[48, 390], [120, 390]], color: T.mint, lw: 2, label: 'ACCEPT', labelDx: 10, labelDy: -6 }));
  body.push(arrow({ pts: [[48, 426], [120, 426]], color: T.amber, lw: 2, label: 'REVIEW', labelDx: 10, labelDy: -6 }));
  body.push(arrow({ pts: [[48, 462], [120, 462]], color: T.red, lw: 2, label: 'REJECT', labelDx: 10, labelDy: -6 }));
  const rowA = [['shield', 'SHIELD'], ['cpu', 'CPU'], ['db', 'STORE'], ['lock', 'LOCK'], ['activity', 'MONITOR'], ['fingerprint', 'IDENT']];
  const rowB = [['key', 'KEYS'], ['network', 'GRAPH'], ['timer', 'CLOCKS'], ['bank', 'BANKS'], ['truck', 'COURIER'], ['layers', 'STACK']];
  rowA.forEach(([icon, label], i) => {
    body.push(badge({ x: 280 + i * 52, y: 300, tint: T.navy, icon }));
    body.push(txt({ x: 298 + i * 52, y: 352, s: label, size: 8, fill: T.text2, mono: true, anchor: 'middle' }));
  });
  rowB.forEach(([icon, label], i) => {
    body.push(badge({ x: 280 + i * 52, y: 380, tint: T.navy, icon }));
    body.push(txt({ x: 298 + i * 52, y: 432, s: label, size: 8, fill: T.text2, mono: true, anchor: 'middle' }));
  });
  [1, 2, 3].forEach((n) => body.push(numchip({ x: 640 + n * 40, y: 316, n })));
  body.push(txt({ x: 640, y: 348, s: 'NARRATION / DRAW ORDER', size: 9, fill: T.text2, mono: true }));
  body.push(zone({ x: 900, y: 290, w: 232, h: 190, label: 'type', tint: T.navy }));
  body.push(txt({ x: 916, y: 336, s: 'Inter 18 — figure title', size: 18, weight: 600, fill: T.text }));
  body.push(txt({ x: 916, y: 364, s: 'Inter 12/600 — node title', size: 12, weight: 600, fill: T.text }));
  body.push(txt({ x: 916, y: 388, s: 'Inter 10 — node sub', size: 10, fill: T.text2 }));
  body.push(txt({ x: 916, y: 412, s: 'JetBrains 10 — chips, data', size: 10, fill: T.text, mono: true }));
  body.push(txt({ x: 916, y: 436, s: 'JetBrains 9 — micro labels', size: 9, fill: T.text2, mono: true }));
  body.push(chip({ x: 916, y: 450, label: 'P50 45–75 MS', tint: T.amber, size: 9, pad: 8, h: 18 }));
  body.push(txt({ x: 48, y: 532, s: 'same tokens as the console (src/app/globals.css) · icons lucide-style (ISC) · no gradients, no filters — everything imports flat & editable into Figma', size: 9, fill: T.text2, mono: true }));
  save('kit-legend.svg', svgDoc({ w: 1180, h: 560, body: body.join('\n'), title: 'Diagram kit legend' }));
}

/* ================================================ FIG.01 — SYSTEM CONTEXT */
{
  const body = [];
  body.push(figTitle({ fig: 'FIG.01', title: 'System context — who talks to the trust layer', sub: 'everything outside the dashed line is untrusted · every arrow crossing the boundary is a contract' }));
  // external actors
  body.push(node({ x: 48, y: 160, w: 170, h: 76, title: 'Customer', subs: ['checkout · order'], tint: T.blue, icon: 'users' }));
  body.push(node({ x: 48, y: 270, w: 170, h: 76, title: 'Merchant', subs: ['dashboard · cases'], tint: T.blue, icon: 'eye' }));
  body.push(node({ x: 48, y: 380, w: 170, h: 76, title: 'NPCI UPI Circle', subs: ['mandate caps'], tint: T.mint, icon: 'bank' }));
  body.push(node({ x: 1060, y: 160, w: 170, h: 76, title: 'Courier API', subs: ['delivery status'], tint: T.amber, icon: 'truck' }));
  body.push(node({ x: 1060, y: 270, w: 170, h: 76, title: 'Auditor / RBI', subs: ['proof, on demand'], tint: T.navy, icon: 'scale' }));
  // trust boundary
  body.push(zone({ x: 250, y: 120, w: 780, h: 650, label: 'RTO Trust Layer — trust boundary', tint: T.navy, dashed: true }));
  // scoring plane
  body.push(zone({ x: 278, y: 168, w: 724, h: 150, label: 'scoring plane', tint: T.blue }));
  body.push(node({ x: 302, y: 210, w: 170, h: 84, title: 'Scoring gateway', subs: ['rate limit · rules', 'mandate ±₹500'], tint: T.blue, icon: 'shield' }));
  body.push(node({ x: 496, y: 210, w: 170, h: 84, title: 'Inference', subs: ['79 features', 'ONNX 1.6 µs/row'], tint: T.blue, icon: 'cpu' }));
  body.push(node({ x: 690, y: 210, w: 170, h: 84, title: 'Cost decision', subs: ['Bahnsen · 3', 'verdicts + receipt'], tint: T.blue, icon: 'scale' }));
  body.push(arrow({ pts: [[472, 252], [496, 252]], color: T.text2, lw: 1.5 }));
  body.push(arrow({ pts: [[666, 252], [690, 252]], color: T.text2, lw: 1.5 }));
  // trust core
  body.push(zone({ x: 278, y: 360, w: 340, h: 230, label: 'trust core', tint: T.mint }));
  body.push(node({ x: 294, y: 396, w: 290, h: 52, title: 'Merkle audit chain', subs: ['tamper-evident decision leaves'], tint: T.mint, icon: 'link' }));
  body.push(node({ x: 294, y: 462, w: 290, h: 52, title: 'Dual-control override', subs: ['two keys · one-shot nonces'], tint: T.mint, icon: 'key' }));
  body.push(node({ x: 294, y: 528, w: 290, h: 52, title: 'Bounded copilot', subs: ['7 actions, enforced as code'], tint: T.mint, icon: 'lock' }));
  // ops plane
  body.push(zone({ x: 646, y: 360, w: 356, h: 230, label: 'ops plane', tint: T.amber }));
  body.push(node({ x: 662, y: 396, w: 160, h: 52, title: 'Case mgmt + SLA', subs: ['clocks, deadlines'], tint: T.amber, icon: 'timer' }));
  body.push(node({ x: 838, y: 396, w: 160, h: 52, title: 'Fraud-ring graph', subs: ['linked customers'], tint: T.amber, icon: 'network' }));
  body.push(node({ x: 662, y: 462, w: 160, h: 52, title: 'Integrations', subs: ['courier · payment'], tint: T.amber, icon: 'globe' }));
  body.push(node({ x: 838, y: 462, w: 160, h: 52, title: 'Circuit breaker', subs: ['rules-only, never open'], tint: T.amber, icon: 'activity' }));
  // admins (privileged externals)
  body.push(node({ x: 430, y: 800, w: 180, h: 64, title: 'Admin A', subs: ['signature 1'], tint: T.navy, icon: 'key' }));
  body.push(node({ x: 660, y: 800, w: 180, h: 64, title: 'Admin B', subs: ['signature 2'], tint: T.navy, icon: 'key' }));
  // arrows — left actors
  body.push(arrow({ pts: [[218, 198], [240, 198], [240, 222], [302, 222]], color: T.blue, lw: 1.5, label: 'SCORE REQUEST · ≤75 MS', labelDy: -8 }));
  body.push(arrow({ pts: [[218, 308], [240, 308], [240, 290], [302, 290]], color: T.blue, lw: 1.25, dash: '4 4', label: 'CONSOLE · EXPLANATIONS', labelDy: -8 }));
  body.push(arrow({ pts: [[218, 418], [240, 418], [240, 330], [400, 330], [400, 318]], color: T.mint, lw: 1.25, dash: '4 4', label: 'OC-201B CAPS', labelDy: -8 }));
  // scoring → core / ops
  body.push(arrow({ pts: [[775, 294], [775, 340], [448, 340], [448, 396]], color: T.text2, lw: 1.5, label: 'EVERY DECISION = A MERKLE LEAF', labelDy: -8 }));
  body.push(arrow({ pts: [[860, 252], [940, 252], [940, 360]], color: T.async, lw: 1.25, dash: '4 4', label: 'EVENTS', labelDy: -10 }));
  // core → auditor (routes below ops zone, up the outside lane)
  body.push(arrow({ pts: [[448, 580], [448, 620], [1038, 620], [1038, 308], [1060, 308]], color: T.navy, lw: 1.25, dash: '4 4', label: 'INCLUSION PROOF · LOG N', labelAt: 0.3, labelDy: -8 }));
  // courier → integrations
  body.push(arrow({ pts: [[1060, 198], [1020, 198], [1020, 422], [1002, 422]], color: T.amber, lw: 1.25, dash: '4 4', label: 'DELIVERY STATUS', labelAt: 0.55, labelDx: 10 }));
  // admins → dual-control gate → core
  body.push(`<circle cx="560" cy="680" r="12" fill="${T.navy}"/>`);
  body.push(txt({ x: 560, y: 684, s: '2×', size: 10, fill: '#FFFFFF', mono: true, anchor: 'middle', weight: 600 }));
  body.push(arrow({ pts: [[520, 800], [520, 680], [546, 680]], color: T.navy, lw: 1.25, dash: '4 4' }));
  body.push(arrow({ pts: [[750, 800], [750, 680], [574, 680]], color: T.navy, lw: 1.25, dash: '4 4' }));
  body.push(arrow({ pts: [[560, 668], [560, 594]], color: T.navy, lw: 1.25, dash: '4 4', label: 'DUAL-CONTROL — 2 SIGNATURES', labelAt: 0.25, labelDx: 12, labelDy: -6 }));
  // footer
  body.push(txt({ x: 48, y: 906, s: 'external actors sit outside the trust boundary · nothing inside is reachable without crossing a stated contract', size: 10, fill: T.text2, mono: true }));
  save('fig-01-system-context.svg', svgDoc({ w: 1280, h: 940, body: body.join('\n'), title: 'FIG.01 system context' }));
}

/* ============================================ FIG.02 — SCORING PIPELINE */
{
  const body = [];
  body.push(figTitle({ fig: 'FIG.02', title: 'Scoring pipeline — the 75-millisecond decision', sub: 'left to right: the hot path · below: what happens after the verdict · numbers = live-draw order (Beat 3)' }));
  body.push(chip({ x: 1392, y: 40, label: 'P50 45–75 MS · PYTHON', tint: T.amber, size: 9, pad: 9, anchorEnd: true }));
  body.push(chip({ x: 1392, y: 66, label: 'TARGET <10 MS · COMPILED', tint: T.navy, size: 9, pad: 9, anchorEnd: true }));
  // request actor
  body.push(node({ x: 48, y: 158, w: 130, h: 88, title: 'Scoring request', subs: ['checkout', 'order · ₹ value'], tint: T.blue, icon: 'zap' }));
  // gateway zone + 6 stages
  body.push(zone({ x: 210, y: 120, w: 1070, h: 200, label: 'gateway · hot path', tint: T.blue }));
  body.push(numchip({ x: 210, y: 120, n: 1 }));
  const stages = [
    { x: 240, title: 'Rate limit', subs: ['per-IP sliding', 'window'], icon: 'shield', n: 2 },
    { x: 410, title: 'Rule engine', subs: ['4 pre-screens', '±₹500 jitter'], icon: 'layers', n: 3 },
    { x: 580, title: 'Mandate check', subs: ['OC-201B caps', '₹5k · ₹15k/mo'], icon: 'bank', n: 4 },
    { x: 750, title: 'Feature builder', subs: ['79 leak-safe', 'shift-1 · m-est'], icon: 'flask', n: 5 },
    { x: 920, title: 'ONNX runtime', subs: ['1.6 µs / row', 'Optuna-weighted'], icon: 'cpu', n: 6 },
    { x: 1090, title: 'Ensemble', subs: ['XGBoost 94%', 'weighted blend'], icon: 'branch' },
  ];
  for (const st of stages) body.push(node({ x: st.x, y: 160, w: 158, h: 84, title: st.title, subs: st.subs, tint: T.blue, icon: st.icon, num: st.n }));
  for (let i = 0; i < stages.length - 1; i++) {
    body.push(arrow({ pts: [[stages[i].x + 158, 202], [stages[i + 1].x, 202]], color: T.text2, lw: 1.5 }));
  }
  body.push(arrow({ pts: [[178, 202], [240, 202]], color: T.blue, lw: 1.5 }));
  // anti-extraction note
  body.push(chip({ x: 240, y: 344, label: '±₹500 JITTER → PROBES GET NOISE (MODEL-EXTRACTION DEFENSE)', tint: T.red, size: 9, pad: 9 }));
  // decision
  body.push(node({ x: 560, y: 390, w: 320, h: 76, title: 'Bahnsen cost decision', subs: ['expected cost: accept · review · reject', 'cheapest wins — breakdown in response'], tint: T.navy, icon: 'scale', border: T.navy, num: 7, titleSize: 13 }));
  body.push(arrow({ pts: [[1169, 244], [1169, 340], [720, 340], [720, 390]], color: T.text2, lw: 1.5 }));
  // response
  body.push(node({ x: 1120, y: 390, w: 260, h: 76, title: 'Response + explanation', subs: ['cost breakdown', 'receipt for the auditor'], tint: T.blue, icon: 'file' }));
  body.push(arrow({ pts: [[880, 428], [1120, 428]], color: T.text2, lw: 1.5 }));
  // return loop (dashed)
  body.push(arrow({ pts: [[1250, 390], [1250, 96], [113, 96], [113, 158]], color: T.blue, lw: 1.25, dash: '4 4', label: 'VERDICT + RECEIPT · ≤75 MS', labelAt: 0.35, labelDy: -8 }));
  // verdict branches
  body.push(arrow({ pts: [[720, 466], [720, 486]], color: T.text2, lw: 1.5, head: false }));
  body.push(arrow({ pts: [[720, 486], [400, 486], [400, 510]], color: T.mint, lw: 2 }));
  body.push(arrow({ pts: [[720, 486], [720, 510]], color: T.amber, lw: 2 }));
  body.push(arrow({ pts: [[720, 486], [1040, 486], [1040, 510]], color: T.red, lw: 2 }));
  body.push(node({ x: 300, y: 510, w: 200, h: 64, title: 'ACCEPT', subs: ['expected cost ₹248'], tint: T.mint, icon: 'check', border: T.mint }));
  body.push(node({ x: 620, y: 510, w: 200, h: 64, title: 'REVIEW + OTP', subs: ['expected cost ₹99'], tint: T.amber, icon: 'msg', border: T.amber }));
  body.push(node({ x: 940, y: 510, w: 200, h: 64, title: 'REJECT', subs: ['expected cost ₹980'], tint: T.red, icon: 'x', border: T.red }));
  // streaming zone
  body.push(zone({ x: 210, y: 620, w: 1070, h: 150, label: 'streaming · after the verdict', tint: T.async, dashed: true }));
  body.push(numchip({ x: 210, y: 620, n: 8 }));
  const stream = [
    { x: 240, title: 'Kafka', subs: ['24 partitions · Avro'], icon: 'db' },
    { x: 470, title: 'Flink CEP', subs: ['3 rejects / 5 min', 'per customer'], icon: 'activity' },
    { x: 700, title: 'ClickHouse', subs: ['materialized views'], icon: 'server' },
    { x: 930, title: 'Fraud dashboard', subs: ['rings · patterns'], icon: 'eye' },
  ];
  for (const st of stream) body.push(node({ x: st.x, y: 660, w: 200, h: 76, title: st.title, subs: st.subs, tint: T.async, icon: st.icon }));
  for (let i = 0; i < stream.length - 1; i++) {
    body.push(arrow({ pts: [[stream[i].x + 200, 698], [stream[i + 1].x, 698]], color: T.async, lw: 1.25, dash: '4 4' }));
  }
  body.push(arrow({ pts: [[720, 574], [720, 600], [340, 600], [340, 656]], color: T.async, lw: 1.25, dash: '4 4', label: 'DECISION EVENTS', labelAt: 0.75, labelDy: -8 }));
  // bottom pointers
  body.push(chip({ x: 48, y: 806, label: 'GO/RUST HOT PATH + MULTI-AZ → FIG.04', tint: T.navy, size: 9, pad: 9 }));
  body.push(chip({ x: 500, y: 806, label: 'EVERY DECISION = A MERKLE LEAF → FIG.03', tint: T.mint, size: 9, pad: 9 }));
  save('fig-02-scoring-pipeline.svg', svgDoc({ w: 1440, h: 860, body: body.join('\n'), title: 'FIG.02 scoring pipeline' }));
}

/* ================================================ FIG.03 — TRUST CHAIN */
{
  const body = [];
  body.push(figTitle({ fig: 'FIG.03', title: 'Trust core — provable decisions', sub: 'near-monochrome · red only for tamper · mint only for verified — the three differentiators of the platform' }));
  /* panel 1 — merkle */
  body.push(zone({ x: 48, y: 120, w: 430, h: 640, label: '1 · merkle audit chain', sub: 'every decision is a leaf — the root seals it', tint: T.navy }));
  // root
  body.push(rect({ x: 193, y: 190, w: 140, h: 36, rx: 8, fill: T.navy }));
  body.push(txt({ x: 263, y: 213, s: 'ROOT SEAL', size: 11, fill: '#FFFFFF', mono: true, anchor: 'middle', weight: 600, spacing: 1 }));
  // L3
  body.push(rect({ x: 109, y: 280, w: 100, h: 32, rx: 6, fill: T.card, stroke: T.border }));
  body.push(txt({ x: 159, y: 301, s: 'h0123', size: 10, fill: T.text2, mono: true, anchor: 'middle' }));
  body.push(rect({ x: 317, y: 280, w: 100, h: 32, rx: 6, fill: T.card, stroke: T.border }));
  body.push(txt({ x: 367, y: 301, s: 'h4567', size: 10, fill: T.text2, mono: true, anchor: 'middle' }));
  // L2
  const l2 = [107, 211, 315, 419];
  const l2Labels = ['h01', 'h23', 'h45', 'h67'];
  l2.forEach((c, i) => {
    body.push(rect({ x: c - 32, y: 370, w: 64, h: 30, rx: 6, fill: T.card, stroke: T.border }));
    body.push(txt({ x: c, y: 390, s: l2Labels[i], size: 10, fill: T.text2, mono: true, anchor: 'middle' }));
  });
  // leaves
  const leafX = (i) => 60 + 52 * i;
  const leafC = (i) => leafX(i) + 21;
  for (let i = 0; i < 8; i++) {
    const tampered = i === 4;
    body.push(rect({ x: leafX(i), y: 450, w: 42, h: 44, rx: 6, fill: tampered ? T.red : T.card, fillOpacity: tampered ? 0.08 : 1, stroke: tampered ? T.red : T.border, sw: tampered ? 1.5 : 1 }));
    body.push(txt({ x: leafC(i), y: 477, s: 'D' + (i + 1), size: 10, fill: tampered ? T.red : T.text, mono: true, anchor: 'middle', weight: 500 }));
  }
  // connectors leaf→L2
  for (let i = 0; i < 8; i++) {
    const p = l2[Math.floor(i / 2)];
    body.push(arrow({ pts: [[leafC(i), 450], [leafC(i), 425], [p, 425], [p, 400]], color: T.dashed, lw: 1, head: false }));
  }
  // connectors L2→L3, L3→root
  body.push(arrow({ pts: [[107, 370], [107, 341], [159, 341], [159, 312]], color: T.dashed, lw: 1, head: false }));
  body.push(arrow({ pts: [[211, 370], [211, 341], [159, 341]], color: T.dashed, lw: 1, head: false }));
  body.push(arrow({ pts: [[315, 370], [315, 341], [367, 341], [367, 312]], color: T.dashed, lw: 1, head: false }));
  body.push(arrow({ pts: [[419, 370], [419, 341], [367, 341]], color: T.dashed, lw: 1, head: false }));
  body.push(arrow({ pts: [[159, 280], [159, 253], [263, 253], [263, 226]], color: T.dashed, lw: 1, head: false }));
  body.push(arrow({ pts: [[367, 280], [367, 253], [263, 253]], color: T.dashed, lw: 1, head: false }));
  // tamper path (red) and inclusion proof (mint)
  body.push(arrow({ pts: [[289, 444], [289, 419], [315, 419], [315, 335], [367, 335], [367, 249], [263, 249], [263, 230]], color: T.red, lw: 1.5, dash: '4 4', head: false }));
  body.push(arrow({ pts: [[133, 444], [133, 431], [107, 431], [107, 347], [159, 347], [159, 259], [263, 259], [263, 232]], color: T.mint, lw: 2, head: false }));
  body.push(chip({ x: 60, y: 512, label: 'TAMPER D5 → CHAIN BREAKS, PROVABLY', tint: T.red, size: 9, pad: 9 }));
  body.push(chip({ x: 60, y: 540, label: 'INCLUSION PROOF — VERIFY IN LOG N = 3', tint: T.mint, size: 9, pad: 9 }));
  body.push(txt({ x: 60, y: 588, s: 'D = one scored decision · h = sha-256', size: 9, fill: T.text2, mono: true }));
  body.push(para({ x: 60, y: 608, lines: ['an auditor verifies any single decision', 'without trusting the decision store'], size: 10, lh: 13, fill: T.text2 }));
  body.push(chip({ x: 60, y: 668, label: 'RBI MRM — TAMPER-EVIDENT DECISIONS · WE SHIP THE CRYPTO', tint: T.navy, size: 9, pad: 9 }));
  /* panel 2 — dual control */
  body.push(zone({ x: 508, y: 120, w: 430, h: 640, label: '2 · dual-control override', sub: 'no single admin can reverse a decision', tint: T.navy }));
  body.push(node({ x: 528, y: 180, w: 180, h: 64, title: 'Admin A', subs: ['signature 1'], tint: T.navy, icon: 'key' }));
  body.push(node({ x: 736, y: 180, w: 180, h: 64, title: 'Admin B', subs: ['signature 2'], tint: T.navy, icon: 'key' }));
  body.push(node({ x: 528, y: 300, w: 180, h: 56, title: 'HKDF key A', subs: ['RFC-5869 derived'], tint: T.navy, icon: 'fingerprint' }));
  body.push(node({ x: 736, y: 300, w: 180, h: 56, title: 'HKDF key B', subs: ['RFC-5869 derived'], tint: T.navy, icon: 'fingerprint' }));
  body.push(arrow({ pts: [[618, 244], [618, 300]], color: T.navy, lw: 1.5 }));
  body.push(arrow({ pts: [[826, 244], [826, 300]], color: T.navy, lw: 1.5 }));
  body.push(node({ x: 632, y: 400, w: 180, h: 56, title: 'Replay nonces', subs: ['one-shot, single-use'], tint: T.navy, icon: 'timer' }));
  body.push(arrow({ pts: [[618, 356], [618, 380], [722, 380], [722, 400]], color: T.navy, lw: 1.5 }));
  body.push(arrow({ pts: [[826, 356], [826, 380], [722, 380]], color: T.navy, lw: 1.5, head: false }));
  body.push(rect({ x: 568, y: 500, w: 300, h: 72, rx: 8, fill: T.navy }));
  body.push(txt({ x: 718, y: 530, s: 'OVERRIDE GATE', size: 14, fill: '#FFFFFF', mono: true, anchor: 'middle', weight: 600, spacing: 1 }));
  body.push(txt({ x: 718, y: 552, s: 'requires two signatures — both keys, fresh nonce', size: 10, fill: '#B9C2D0', mono: true, anchor: 'middle' }));
  body.push(arrow({ pts: [[718, 456], [718, 500]], color: T.navy, lw: 1.5 }));
  body.push(arrow({ pts: [[718, 572], [718, 606]], color: T.red, lw: 1.5, dash: '4 4' }));
  body.push(rect({ x: 568, y: 612, w: 300, h: 56, rx: 8, fill: T.red, fillOpacity: 0.05, stroke: T.red, sw: 1, dash: '5 4' }));
  body.push(badge({ x: 584, y: 622, tint: T.red, icon: 'x' }));
  body.push(txt({ x: 634, y: 636, s: 'SINGLE-ADMIN REVERSAL', size: 11, fill: T.red, weight: 600 }));
  body.push(txt({ x: 634, y: 652, s: 'no such path exists', size: 10, fill: T.red }));
  body.push(txt({ x: 528, y: 706, s: 'reversals are rare, dual-signed, replay-proof, and audited', size: 9, fill: T.text2, mono: true }));
  /* panel 3 — bounded copilot */
  body.push(zone({ x: 968, y: 120, w: 424, h: 640, label: '3 · bounded ai copilot', sub: 'the agent cannot do anything else', tint: T.navy }));
  // prompt (logical card)
  body.push(rect({ x: 988, y: 180, w: 180, h: 72, rx: 8, fill: T.bg, stroke: T.dashed, sw: 1, dash: '5 4' }));
  body.push(badge({ x: 1000, y: 198, tint: T.navy, icon: 'msg' }));
  body.push(txt({ x: 1060, y: 212, s: 'Prompt', size: 12, weight: 600, fill: T.text }));
  body.push(txt({ x: 1060, y: 228, s: 'natural language', size: 10, fill: T.text2 }));
  body.push(arrow({ pts: [[1078, 252], [1078, 300]], color: T.text2, lw: 1.5 }));
  body.push(node({ x: 988, y: 300, w: 180, h: 64, title: 'LLM advisor', subs: ['read-only context', 'suggests only'], tint: T.navy, icon: 'eye' }));
  body.push(arrow({ pts: [[1078, 364], [1078, 420]], color: T.text2, lw: 1.5 }));
  body.push(rect({ x: 988, y: 420, w: 384, h: 88, rx: 8, fill: T.card, stroke: T.navy, sw: 1.5 }));
  body.push(txt({ x: 1002, y: 450, s: 'Server-side action graph', size: 13, weight: 600, fill: T.text }));
  body.push(txt({ x: 1002, y: 470, s: '7 whitelisted actions — enforced as a dependency,', size: 10, fill: T.text2 }));
  body.push(txt({ x: 1002, y: 484, s: 'not a prompt', size: 10, fill: T.text2 }));
  let ax = 988;
  const acts = ['open_case', 'summarize', 'tag_order', 'link_order', 'escalate', 'request_otp', 'add_note'];
  for (const a of acts) { body.push(chip({ x: ax, y: 522, label: a, tint: T.navy, size: 9, pad: 8, h: 18 })); ax += chipW(a, { size: 9, pad: 8 }) + 8; }
  body.push(chip({ x: 988, y: 548, label: 'ALLOW-LIST · 7 TOTAL', tint: T.navy, size: 9, pad: 8, h: 18, solid: true }));
  body.push(rect({ x: 988, y: 586, w: 384, h: 64, rx: 8, fill: T.red, fillOpacity: 0.04, stroke: T.red, sw: 1, dash: '5 4' }));
  body.push(badge({ x: 1002, y: 600, tint: T.red, icon: 'x' }));
  body.push(txt({ x: 1052, y: 616, s: 'EVERYTHING ELSE — HARD BOUNDARY', size: 11, fill: T.red, weight: 600 }));
  body.push(txt({ x: 1052, y: 634, s: 'enforced in code · the agent cannot reach it', size: 10, fill: T.red }));
  body.push(txt({ x: 988, y: 692, s: 'not a prompt guard — an allow-listed dependency', size: 9, fill: T.text2, mono: true }));
  // bottom
  body.push(chip({ x: 48, y: 792, label: 'TRUST CORE = THE DIFFERENTIATOR — LIVE IN THE SHIPPED CONSOLE', tint: T.mint, size: 9, pad: 9 }));
  save('fig-03-trust-chain.svg', svgDoc({ w: 1440, h: 820, body: body.join('\n'), title: 'FIG.03 trust chain' }));
}

/* ========================================== FIG.04 — DEPLOYMENT & PERF */
{
  const body = [];
  body.push(figTitle({ fig: 'FIG.04', title: 'Deployment shape & honest performance', sub: 'what runs where today, what stays a documented plan, and the one deliberate demo seam' }));
  // VPC
  body.push(zone({ x: 48, y: 120, w: 860, h: 510, label: 'region · vpc — multi-az (documented plan)', tint: T.blue }));
  body.push(zone({ x: 72, y: 176, w: 400, h: 190, label: 'az-a', tint: T.navy, dashed: true }));
  body.push(zone({ x: 476, y: 176, w: 400, h: 190, label: 'az-b', tint: T.navy, dashed: true }));
  const az = (x0) => {
    body.push(node({ x: x0 + 20, y: 216, w: 170, h: 64, title: 'Gateway pods ×2', subs: ['spread across AZs'], tint: T.blue, icon: 'server' }));
    body.push(node({ x: x0 + 210, y: 216, w: 170, h: 64, title: 'Scoring worker', subs: ['hot path replicas'], tint: T.blue, icon: 'cpu' }));
    body.push(node({ x: x0 + 20, y: 296, w: 170, h: 56, title: 'Replica reads', subs: ['round-robin'], tint: T.blue, icon: 'db' }));
    body.push(node({ x: x0 + 210, y: 296, w: 170, h: 56, title: 'Writes', subs: ['pinned to leader'], tint: T.navy, icon: 'lock' }));
  };
  az(72); az(476);
  // leader + postgres
  body.push(node({ x: 240, y: 410, w: 300, h: 84, title: 'Leader — writes pinned', subs: ['mandate counters', 'must not race'], tint: T.navy, icon: 'lock', border: T.navy }));
  body.push(node({ x: 580, y: 410, w: 300, h: 84, title: 'Postgres — Merkle + mandates', subs: ['cross-process safe', 'file-mode: 30-min migration'], tint: T.mint, icon: 'db' }));
  body.push(arrow({ pts: [[272, 366], [272, 410]], color: T.navy, lw: 1.5, label: 'WRITES', labelDx: 8, labelDy: -6 }));
  body.push(arrow({ pts: [[676, 366], [676, 390], [420, 390], [420, 410]], color: T.navy, lw: 1.5, head: false }));
  body.push(arrow({ pts: [[540, 452], [580, 452]], color: T.text2, lw: 1.5 }));
  body.push(chip({ x: 580, y: 510, label: 'FILE-MODE BREAKS ACROSS PROCESSES · POSTGRES HOLDS', tint: T.amber, size: 9, pad: 9 }));
  // streaming column
  body.push(node({ x: 940, y: 176, w: 200, h: 64, title: 'Kafka', subs: ['24 partitions · Avro'], tint: T.async, icon: 'db' }));
  body.push(node({ x: 940, y: 290, w: 200, h: 64, title: 'Flink CEP', subs: ['3 rejects / 5 min'], tint: T.async, icon: 'activity' }));
  body.push(node({ x: 940, y: 404, w: 200, h: 64, title: 'ClickHouse', subs: ['materialized views'], tint: T.async, icon: 'server' }));
  body.push(node({ x: 940, y: 518, w: 200, h: 64, title: 'Fraud dashboard', subs: ['rings · patterns'], tint: T.async, icon: 'eye' }));
  body.push(arrow({ pts: [[1040, 240], [1040, 290]], color: T.async, lw: 1.25, dash: '4 4' }));
  body.push(arrow({ pts: [[1040, 354], [1040, 404]], color: T.async, lw: 1.25, dash: '4 4' }));
  body.push(arrow({ pts: [[1040, 468], [1040, 518]], color: T.async, lw: 1.25, dash: '4 4' }));
  body.push(arrow({ pts: [[908, 208], [940, 208]], color: T.async, lw: 1.25, dash: '4 4', label: 'EVENTS', labelDy: -8 }));
  // perf zone
  body.push(zone({ x: 48, y: 660, w: 860, h: 170, label: 'performance — claimed vs honest', tint: T.amber, dashed: true }));
  body.push(chip({ x: 72, y: 700, label: 'P50 45–75 MS — PYTHON CEILING (HONEST)', tint: T.navy, size: 9, pad: 9 }));
  body.push(txt({ x: 400, y: 714, s: 'console runs deterministic mock scorer · Python scorer = 1 env var', size: 10, fill: T.text2 }));
  body.push(chip({ x: 72, y: 736, label: 'TARGET <10 MS — GO/RUST + KERNEL-BYPASS (PLAN, NOT CLAIMED)', tint: T.amber, size: 9, pad: 9 }));
  body.push(txt({ x: 474, y: 750, s: 'compiled hot path · multi-AZ documented', size: 10, fill: T.text2 }));
  body.push(chip({ x: 72, y: 772, label: 'ONNX 1.6 µS / ROW', tint: T.mint, size: 9, pad: 9 }));
  body.push(chip({ x: 213, y: 772, label: '~400 TESTS · 5 DIMENSIONS — GREEN', tint: T.navy, size: 9, pad: 9 }));
  body.push(chip({ x: 458, y: 772, label: 'CALIBRATION — PLANNED, RANKING-NEUTRAL', tint: T.amber, size: 9, pad: 9 }));
  // demo seam
  body.push(zone({ x: 940, y: 660, w: 452, h: 170, label: 'deployed demo — vercel', sub: 'the one deliberate seam', tint: T.amber, dashed: true }));
  body.push(node({ x: 960, y: 716, w: 190, h: 64, title: 'In-process CEP', subs: ['same detection', 'logic, in-process'], tint: T.amber, icon: 'activity' }));
  body.push(arrow({ pts: [[950, 748], [928, 748], [928, 322], [936, 322]], color: T.amber, lw: 1.25, dash: '4 4', label: 'SAME DETECTION LOGIC', labelAt: 0.5, labelDx: -8, labelDy: -6, labelAnchor: 'end' }));
  body.push(txt({ x: 1160, y: 720, s: 'production: Kafka + Flink', size: 10, fill: T.text2 }));
  body.push(txt({ x: 1160, y: 736, s: 'demo: in-process engine', size: 10, fill: T.text2 }));
  body.push(txt({ x: 1160, y: 758, s: 'same alert,', size: 10, fill: T.text, weight: 600 }));
  body.push(txt({ x: 1160, y: 774, s: 'different transport', size: 10, fill: T.text, weight: 600 }));
  body.push(txt({ x: 960, y: 800, s: "Vercel can't host Kafka — the logic is identical, the transport differs", size: 9, fill: T.text2, mono: true }));
  // bottom honesty bar
  body.push(chip({ x: 48, y: 850, label: 'MULTI-AZ + GO HOT PATH — DOCUMENTED, NOT DEPLOYED. NOT CLAIMED.', tint: T.navy, size: 9, pad: 9 }));
  body.push(chip({ x: 470, y: 850, label: 'MERKLE · BOUNDED AGENT · DUAL-CONTROL — LIVE & TESTED', tint: T.mint, size: 9, pad: 9 }));
  save('fig-04-deployment-perf.svg', svgDoc({ w: 1440, h: 890, body: body.join('\n'), title: 'FIG.04 deployment & performance' }));
}

/* ================================================= BEAT 01 — HOOK (dark) */
{
  const body = [];
  const bw = chipW('BEAT 1', { size: 11, pad: 12 });
  body.push(rect({ x: 48, y: 40, w: bw, h: 26, rx: 13, fill: '#FFFFFF' }));
  body.push(txt({ x: 48 + bw / 2, y: 57.5, s: 'BEAT 1', size: 11, fill: T.navy, mono: true, anchor: 'middle', weight: 600, spacing: 1 }));
  body.push(txt({ x: 48 + bw + 14, y: 58.5, s: 'Hook — the number', size: 18, weight: 600, fill: '#FFFFFF' }));
  body.push(chip({ x: 1132, y: 43, label: '0:00–0:15', tint: T.amber, size: 9, pad: 8, anchorEnd: true }));
  body.push(chip({ x: 1132 - chipW('0:00–0:15', { size: 9, pad: 8 }) - 8, y: 43, label: 'FIRESHIP', tint: '#B9C2D0', size: 9, pad: 8, anchorEnd: true }));
  // stamps
  const stamps = [['₹50,000 crore.', '0:00.0'], ['lost every year to COD returns.', '0:04.8'], ['3 in 10 parcels bounce.', '0:09.6']];
  stamps.forEach(([s, tc], i) => {
    const y = 170 + i * 55;
    body.push(txt({ x: 48, y, s, size: 30, weight: 700, fill: '#FFFFFF' }));
    body.push(txt({ x: 1132, y, s: tc, size: 9, fill: T.async, mono: true, anchor: 'end' }));
  });
  body.push(txt({ x: 48, y: 330, s: 'white text stamps in — one per 1.2 s · snap-zoom · hard cut', size: 9, fill: T.async, mono: true }));
  // snap card
  body.push(rect({ x: 48, y: 360, w: 480, h: 110, rx: 12, fill: '#FFFFFF' }));
  body.push(txt({ x: 76, y: 412, s: 'RTO TRUST LAYER', size: 26, weight: 700, fill: T.navy }));
  body.push(txt({ x: 76, y: 436, s: 'snap-zoom to title · then cut', size: 10, fill: T.text2, mono: true }));
  body.push(chip({ x: 76, y: 446, label: 'NO FADE', tint: T.amber, size: 9, pad: 8, h: 18 }));
  // VO caption (custom, dark)
  body.push(chip({ x: 48, y: 500, label: 'VO', tint: '#FFFFFF', size: 9, pad: 8, h: 18 }));
  body.push(para({ x: 96, y: 513, lines: wrap('Indian e-commerce loses fifty thousand crore a year to cash-on-delivery returns. Three in ten COD parcels never find a home. This is how you stop them—before the courier leaves.', 105), size: 10, lh: 14, fill: '#B9C2D0' }));
  save('beat-01-hook.svg', svgDoc({ w: 1180, h: 560, bg: T.navy, body: body.join('\n'), title: 'BEAT 1 hook storyboard' }));
}

/* ============================================== BEAT 03 — LIVE-DRAW ORDER */
{
  const body = [];
  body.push(beatHead({ beat: 'BEAT 3', title: 'Act 1 — the production architecture, live-drawn', time: '0:45–2:25', tone: 'BYTEBYTEGO + ARPIT', canvasW: 1180 }));
  // why-naive banner
  body.push(rect({ x: 48, y: 120, w: 1084, h: 56, rx: 8, fill: T.red, fillOpacity: 0.04, stroke: T.red, sw: 1, dash: '5 4' }));
  body.push(txt({ x: 64, y: 154, s: 'WHY THE NAIVE VERSION BREAKS', size: 11, weight: 600, fill: T.red }));
  body.push(chip({ x: 330, y: 138, label: 'THRESHOLD PROBING', tint: T.red, size: 9, pad: 8 }));
  body.push(chip({ x: 496, y: 138, label: 'WRITE RACES ON COUNTERS', tint: T.red, size: 9, pad: 8 }));
  body.push(chip({ x: 700, y: 138, label: 'NO PROOF FOR THE REGULATOR', tint: T.red, size: 9, pad: 8 }));
  body.push(txt({ x: 48, y: 212, s: 'Draw with the narration — one box per sentence:', size: 12, weight: 600, fill: T.text }));
  const rows = [
    ['Gateway', 'per-IP sliding-window rate limiting'],
    ['Rule engine', '4 pre-screens · thresholds ±₹500 — probes get noise'],
    ['Mandate check', 'OC-201B: ₹5k/txn · ₹15k/mo · 24h cooling · 6-mo revoke'],
    ['Inference', '79 leak-safe features · ONNX 1.6 µs/row · XGBoost 94%'],
    ['Decision', 'Bahnsen costs — accept ₹248 · review ₹99 · reject ₹980'],
    ['Platform', 'Go/Rust hot path · multi-AZ leader/replicas'],
    ['Streaming', 'Kafka 24p Avro · Flink CEP · ClickHouse MVs'],
    ['Trust', 'every decision is a Merkle leaf'],
  ];
  rows.forEach(([title, sub], i) => {
    const y = 240 + i * 52;
    body.push(rect({ x: 48, y, w: 1084, h: 44, rx: 8, fill: T.card, stroke: T.border }));
    body.push(numchip({ x: 76, y: y + 22, n: i + 1 }));
    body.push(txt({ x: 100, y: y + 27, s: title, size: 12, weight: 600, fill: T.text }));
    body.push(txt({ x: 380, y: y + 27, s: sub, size: 9, fill: T.text2, mono: true }));
  });
  body.push(chip({ x: 1132, y: 672, label: 'NUMBERS ARE PRINTED ON FIG.02 — DRAW IN THIS ORDER', tint: T.navy, size: 9, pad: 9, anchorEnd: true }));
  body.push(voCaption({ y: 700, text: 'A naive version of this is one model behind one API—and at real volume it breaks three specific ways: attackers probe the threshold until they find it, one write race corrupts a mandate counter, and a regulator asks for proof of a decision that only ever existed as a log line. So here\'s what actually holds up.' }));
  body.push(txt({ x: 48, y: 756, s: 'was: vision_production_stack.excalidraw · live-draw · full narration maps 1:1 onto FIG.02', size: 9, fill: T.text2, mono: true }));
  save('beat-03-live-draw.svg', svgDoc({ w: 1180, h: 780, body: body.join('\n'), title: 'BEAT 3 live-draw storyboard' }));
}

/* ================================================ BEAT 04 — TRUST CORE */
{
  const body = [];
  body.push(beatHead({ beat: 'BEAT 4', title: 'The trust core', time: '2:25–3:05', tone: 'NANA', canvasW: 1280 }));
  const cards = [
    { x: 48, title: 'Merkle audit chain', subs: ['every decision a leaf', 'the root seals it'], icon: 'link', tint: T.mint, chip: '→ FIG.03 · PANEL 1' },
    { x: 438, title: 'Dual-control override', subs: ['two signatures, always', 'one-shot replay nonces'], icon: 'key', tint: T.navy, chip: '→ FIG.03 · PANEL 2' },
    { x: 828, title: 'Bounded copilot', subs: ['7 actions, total', 'enforced as code'], icon: 'lock', tint: T.navy, chip: '→ FIG.03 · PANEL 3' },
  ];
  for (const c of cards) {
    body.push(node({ x: c.x, y: 120, w: 340, h: 120, title: c.title, subs: c.subs, icon: c.icon, tint: c.tint, border: c.tint === T.mint ? T.mint : null }));
    body.push(chip({ x: c.x, y: 252, label: c.chip, tint: T.navy, size: 9, pad: 9 }));
  }
  // MANIM strip
  body.push(txt({ x: 48, y: 308, s: 'MANIM — MerkleProofScene · 12 seconds', size: 12, weight: 600, fill: T.text }));
  body.push(zone({ x: 48, y: 322, w: 1184, h: 218, label: 'storyboard · 4 keyframes', tint: T.navy, dashed: true }));
  const kf = (x0) => {
    body.push(rect({ x: x0, y: 356, w: 256, h: 150, rx: 8, fill: T.card, stroke: T.border }));
  };
  // KF1 — t=0 leaf
  kf(76);
  body.push(chip({ x: 76 + 256 - 10 - chipW('T=0', { size: 9, pad: 8 }), y: 366, label: 'T=0', tint: T.amber, size: 9, pad: 8, h: 18 }));
  body.push(rect({ x: 76 + 98, y: 400, w: 60, h: 34, rx: 6, fill: T.bg, stroke: T.border }));
  body.push(txt({ x: 76 + 128, y: 422, s: 'h(D)', size: 10, fill: T.text, mono: true, anchor: 'middle' }));
  body.push(arrow({ pts: [[76 + 128, 400], [76 + 128, 384]], color: T.text2, lw: 1, head: false }));
  body.push(txt({ x: 76 + 16, y: 486, s: 'hash the decision leaf', size: 9, fill: T.text2, mono: true }));
  // KF2 — t≈4 pair
  kf(368);
  body.push(chip({ x: 368 + 256 - 10 - chipW('T≈4S', { size: 9, pad: 8 }), y: 366, label: 'T≈4S', tint: T.amber, size: 9, pad: 8, h: 18 }));
  body.push(rect({ x: 368 + 108, y: 382, w: 56, h: 26, rx: 6, fill: T.bg, stroke: T.border }));
  body.push(txt({ x: 368 + 136, y: 399, s: 'h01', size: 10, fill: T.text, mono: true, anchor: 'middle' }));
  body.push(rect({ x: 368 + 52, y: 430, w: 40, h: 28, rx: 6, fill: T.bg, stroke: T.border }));
  body.push(txt({ x: 368 + 72, y: 448, s: 'D0', size: 9, fill: T.text2, mono: true, anchor: 'middle' }));
  body.push(rect({ x: 368 + 180, y: 430, w: 40, h: 28, rx: 6, fill: T.bg, stroke: T.border }));
  body.push(txt({ x: 368 + 200, y: 448, s: 'D1', size: 9, fill: T.text2, mono: true, anchor: 'middle' }));
  body.push(arrow({ pts: [[368 + 72, 430], [368 + 72, 414], [368 + 118, 414]], color: T.text2, lw: 1, head: false }));
  body.push(arrow({ pts: [[368 + 200, 430], [368 + 200, 414], [368 + 154, 414]], color: T.text2, lw: 1, head: false }));
  body.push(txt({ x: 368 + 16, y: 486, s: 'pair them — hash the pair', size: 9, fill: T.text2, mono: true }));
  // KF3 — t≈8 path to root
  kf(660);
  body.push(chip({ x: 660 + 256 - 10 - chipW('T≈8S', { size: 9, pad: 8 }), y: 366, label: 'T≈8S', tint: T.amber, size: 9, pad: 8, h: 18 }));
  body.push(rect({ x: 660 + 96, y: 366, w: 64, h: 22, rx: 6, fill: T.navy }));
  body.push(txt({ x: 660 + 128, y: 381, s: 'root', size: 9, fill: '#FFFFFF', mono: true, anchor: 'middle' }));
  body.push(rect({ x: 660 + 58, y: 398, w: 48, h: 20, rx: 6, fill: T.bg, stroke: T.border }));
  body.push(txt({ x: 660 + 82, y: 412, s: 'h01', size: 8, fill: T.text2, mono: true, anchor: 'middle' }));
  body.push(rect({ x: 660 + 152, y: 398, w: 48, h: 20, rx: 6, fill: T.bg, stroke: T.border }));
  body.push(txt({ x: 660 + 176, y: 412, s: 'h23', size: 8, fill: T.text2, mono: true, anchor: 'middle' }));
  for (let i = 0; i < 4; i++) {
    body.push(rect({ x: 660 + 34 + i * 44, y: 432, w: 28, h: 18, rx: 4, fill: T.bg, stroke: T.border }));
    body.push(txt({ x: 660 + 48 + i * 44, y: 445, s: 'D' + i, size: 8, fill: T.text2, mono: true, anchor: 'middle' }));
  }
  body.push(arrow({ pts: [[660 + 48, 432], [660 + 48, 420], [660 + 82, 420], [660 + 82, 418]], color: T.mint, lw: 2, head: false }));
  body.push(arrow({ pts: [[660 + 82, 398], [660 + 82, 388], [660 + 118, 388]], color: T.mint, lw: 2, head: false }));
  body.push(txt({ x: 660 + 16, y: 486, s: 'walk the path up to the root', size: 9, fill: T.text2, mono: true }));
  // KF4 — t=12 verified
  kf(952);
  body.push(chip({ x: 952 + 256 - 10 - chipW('T=12S', { size: 9, pad: 8 }), y: 366, label: 'T=12S', tint: T.amber, size: 9, pad: 8, h: 18 }));
  body.push(`<g transform="translate(${952 + 70},392) scale(2.2)" fill="none" stroke="${T.mint}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${icons.check}</g>`);
  body.push(txt({ x: 952 + 150, y: 424, s: 'VERIFIED', size: 14, weight: 700, fill: T.mint }));
  body.push(chip({ x: 952 + 150, y: 436, label: 'LOG N', tint: T.mint, size: 9, pad: 8, h: 18 }));
  body.push(txt({ x: 952 + 16, y: 486, s: 'any auditor — 12-second scene', size: 9, fill: T.text2, mono: true }));
  // arrows between keyframes
  body.push(arrow({ pts: [[332, 431], [368, 431]], color: T.text2, lw: 1.5 }));
  body.push(arrow({ pts: [[624, 431], [660, 431]], color: T.text2, lw: 1.5 }));
  body.push(arrow({ pts: [[916, 431], [952, 431]], color: T.text2, lw: 1.5 }));
  // VO + footer
  body.push(voCaption({ y: 566, text: "Merkle audit chain. Every decision is a leaf; the root seals it. Tamper one record—the chain breaks, provably. RBI's model-risk-management guidance wants tamper-evident decisions; we ship the cryptographic version. Inclusion proof: leaf, sibling, up to the root—any auditor verifies in log-n." }));
  body.push(txt({ x: 48, y: 668, s: 'was: beat4_trust_layer.excalidraw · three panels + MANIM MerkleProofScene · dual-control + bounded copilot voiced over the mini cards', size: 9, fill: T.text2, mono: true }));
  save('beat-04-trust-core.svg', svgDoc({ w: 1280, h: 700, body: body.join('\n'), title: 'BEAT 4 trust core storyboard' }));
}
