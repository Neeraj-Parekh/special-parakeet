// figma/svg-kit/beats.mjs
// RTO Trust Layer — Figma board · STORYBOARD SET (beats 2, 5, 6, 7, 8).
//
// VO caption text is VERBATIM from docs/deepseek-script.md (spoken words only;
// [VISUAL] stage directions and the (left column)/(right column) cues trimmed).
// Layout follows figma/SPEC.md §5 briefs; tokens/grammar from SPEC §1–§4.
// Figma-import-safe: no defs/markers/gradients/filters/use/CSS — presentation
// attributes only, orthogonal arrows, containment = meaning.

import {
  T, txt, para, rect, zone, node, chip, chipW, arrow, badge,
  beatHead, voCaption, wrap, svgDoc, save,
} from './lib.mjs';

/* ------------------------------------------------------------------ */
/* locked voiceover (docs/deepseek-script.md)                          */

const VO = {
  beat02: `Let me frame the problem the way we'd frame it in a design review. The goal is not "reduce returns"—nobody can. The goal: at checkout time, in under seventy-five milliseconds, decide whether this order is worth shipping—and attach three things: a cost-optimal decision, an explanation the merchant can act on, and a receipt a regulator can verify.`,
  beat05: `Around that core: JWT auth with replay detection, case management with SLA clocks, a fraud-ring graph detector, and courier and payment integrations—each with a documented mock-to-production swap. A circuit breaker that degrades to rules-only, never fails open. And what holds it together: nearly four hundred tests across five dimensions—security, validation, money paths, ML, and platform. All green.`,
  beat06: `So that's the target architecture. Here's what we actually built—and every piece of it is clickable. The trust core is real: the Merkle chain, the bounded agent, dual-control override, the mandate caps—all live, all tested. The streaming has one deliberate seam: production runs Kafka and Flink; the demo runs the same detection logic on an in-process engine, because Vercel can't host Kafka. Same alert, different transport. Everything else on screen right now is running, not mocked.`,
  beat07: `Results. On around 1.3 lakh real orders—Amazon India and public datasets—with a strict time split: PR-AUC 0.11, which is 5.7× base rate, ROC 0.89. The stronger number is the cross-check: on Olist, real data with per-customer history, the same pipeline scores 0.40—32× base rate. Against the fifty-thousand-crore number we opened with, even a fraction of that signal is real money back. The gaps, before you ask. The deployed console runs a deterministic mock scorer today—the Python scorer wires with one environment variable. Ensemble probabilities run hot; calibration is planned and ranking-neutral. The Merkle chain in file-mode breaks across processes—Postgres mode holds; that's a thirty-minute migration. Multi-AZ and the Go hot path are documented plans, not deployed—we won't claim them. And we will not claim sub-ten-millisecond latency; the honest Python ceiling is 45–75 ms p50.`,
  beat08: `We didn't just design the production system. We built the part that matters most—the trust core, the working detector, the audit trail—and we know exactly how to take it to production. Repo's public, console is live, tests are green. Try it, break it, check the audit trail.`,
};

/* ------------------------------------------------------------------ */
/* local helpers                                                       */

// lib.node() renders a single-line title; the storyboard briefs need wrapped
// 2-line titles inside narrow nodes, so this is a thin multi-line variant
// built from the same lib primitives (rect + badge + txt).
function linesNode({ x, y, w, h, lines, subs = [], tint = T.blue, icon = null, border = null, titleSize = 11 }) {
  const p = [rect({ x, y, w, h, rx: 8, fill: T.card, stroke: border || T.border, sw: border ? 1.5 : 1 })];
  if (icon) p.push(badge({ x: x + 8, y: y + (h - 36) / 2, tint, icon }));
  const tx = x + (icon ? 52 : 14);
  const tlh = titleSize + 3;
  const blockH = lines.length * tlh + subs.length * 12;
  let base = y + (h - blockH) / 2 + titleSize * 0.78;
  for (const s of lines) { p.push(txt({ x: tx, y: base, s, size: titleSize, weight: 600, fill: T.text })); base += tlh; }
  for (const s of subs) { p.push(txt({ x: tx, y: base, s, size: 10, fill: T.text2 })); base += 12; }
  return p.join('\n  ');
}

// width self-lint (Inter ≈ 0.53em avg regular / 0.575em 600-weight; mono 0.62em)
const estW = (s, size, { mono = false, w600 = false } = {}) =>
  s.length * size * (mono ? 0.62 : w600 ? 0.575 : 0.53);

const LINT = [];
function check(id, s, size, max, opts = {}) {
  const w = estW(s, size, opts);
  LINT.push(`${w <= max ? 'ok  ' : 'FAIL'}  ${id.padEnd(26)} ${String(Math.round(w)).padStart(4)}px / ${max}px  (${s.length}ch)`);
}

/* ================================================================== */
/* BEAT 02 — problem, framed like a design review · 1180×660           */

function beat02() {
  const W = 1180, H = 660;
  const p = [];
  p.push(beatHead({ beat: 'BEAT 2', title: 'Problem — framed like a design review', time: '0:15–0:45', tone: 'GAURAV SEN', canvasW: W }));

  // banner card — the reframed goal
  p.push(rect({ x: 48, y: 120, w: 1084, h: 72, rx: 8, fill: T.card, stroke: T.navy, sw: 1.5 }));
  p.push(txt({ x: 64, y: 146, s: 'The goal is not "reduce returns" — nobody can.', size: 12, weight: 600, fill: T.text }));
  p.push(para({
    x: 64, y: 167, size: 11, lh: 17, fill: T.text2,
    lines: [
      'The goal: at checkout, in ≤75 ms, decide whether this order is worth shipping —',
      'with a cost-optimal decision, an explanation the merchant can act on, and a receipt a regulator can verify.',
    ],
  }));

  // two columns: today (red) vs target (mint)
  p.push(zone({ x: 48, y: 232, w: 520, h: 280, label: 'TODAY — SHIP FIRST, DISCOVER LATER', tint: T.red }));
  p.push(zone({ x: 612, y: 232, w: 520, h: 280, label: 'TARGET — SCORE AT PLACEMENT', tint: T.mint }));

  // LEFT — ship first, discover later (3-step chain, red)
  p.push(linesNode({ x: 64, y: 296, w: 148, h: 64, tint: T.red, icon: 'truck', lines: ['Merchant ships', 'everything'] }));
  p.push(linesNode({ x: 234, y: 296, w: 148, h: 64, tint: T.red, icon: 'box', lines: ['Courier', 'attempts COD'] }));
  p.push(linesNode({ x: 404, y: 296, w: 148, h: 64, tint: T.red, icon: 'alert', border: T.red, lines: ['Bounce found', 't+14 days'] }));
  p.push(arrow({ pts: [[212, 328], [234, 328]], color: T.red, lw: 1.5 }));
  p.push(arrow({ pts: [[382, 328], [404, 328]], color: T.red, lw: 1.5 }));

  // LEFT — cost chips 2×2 (the consequences)
  p.push(chip({ x: 208, y: 388, label: 'double shipping', tint: T.red }));
  p.push(chip({ x: 333, y: 388, label: 'lost sale', tint: T.red }));
  p.push(chip({ x: 198, y: 416, label: 'refund fight', tint: T.red }));
  p.push(chip({ x: 304, y: 416, label: 'no proof of why', tint: T.red }));

  // LEFT — dashed bracket: the blind window between ship and bounce
  for (const bx of [138, 478]) {
    p.push(arrow({ pts: [[bx, 360], [bx, 484]], color: T.red, lw: 1.25, dash: '4 4', head: false }));
  }
  p.push(arrow({ pts: [[138, 484], [478, 484]], color: T.red, lw: 1.25, dash: '4 4', head: false }));
  p.push(txt({ x: 308, y: 478, s: '14 blind days', size: 9, fill: T.red, mono: true, anchor: 'middle' }));

  // RIGHT — score at placement → verdict fan
  p.push(linesNode({ x: 628, y: 296, w: 210, h: 64, tint: T.blue, icon: 'zap', lines: ['Checkout scores', 'every order'], subs: ['≤75 ms at placement'] }));
  p.push(arrow({ pts: [[838, 328], [920, 328]], color: T.blue, lw: 2, head: false }));
  const verdicts = [
    { label: 'ACCEPT', tint: T.mint, y: 276 },
    { label: 'REVIEW + OTP', tint: T.amber, y: 330 },
    { label: 'REJECT', tint: T.red, y: 384 },
  ];
  for (const v of verdicts) {
    p.push(rect({ x: 1000, y: v.y, w: 96, h: 44, rx: 8, fill: T.card, stroke: v.tint, sw: 1.5 }));
    p.push(txt({ x: 1048, y: v.y + 27, s: v.label, size: 11, weight: 600, fill: v.tint, anchor: 'middle' }));
    p.push(arrow({ pts: [[920, 328], [920, v.y + 22], [1000, v.y + 22]], color: v.tint, lw: 2 }));
  }

  // RIGHT — what every verdict carries
  p.push(chip({ x: 628, y: 460, label: 'cost math shown', tint: T.mint }));
  p.push(chip({ x: 747, y: 460, label: 'explanation to act on', tint: T.mint }));
  p.push(chip({ x: 909, y: 460, label: 'regulator-verifiable receipt', tint: T.mint }));

  const lines = wrap(VO.beat02, 160);
  p.push(voCaption({ y: 556, text: VO.beat02, lines }));
  p.push(txt({ x: 48, y: 644, s: 'was: beat2_problem.excalidraw · two columns', size: 9, fill: T.text2, mono: true }));

  // self-lint the tight strings
  for (const s of ['Merchant ships', 'attempts COD', 'Bounce found', 't+14 days']) check(`b02 node title "${s}"`, s, 11, 92, { w600: true });
  check('b02 banner line 2b', 'with a cost-optimal decision, an explanation the merchant can act on, and a receipt a regulator can verify.', 11, 1052);
  check('b02 zap sub', '≤75 ms at placement', 10, 149);
  check('b02 verdict label', 'REVIEW + OTP', 11, 84, { w600: true });

  return { name: 'beat-02-problem.svg', w: W, h: H, body: p.join('\n  '), title: 'Beat 2 — Problem, framed like a design review', capY: 556, capLines: lines };
}

/* ================================================================== */
/* BEAT 05 — operational backbone · 1180×620                            */

function beat05() {
  const W = 1180, H = 620;
  const p = [];
  p.push(beatHead({ beat: 'BEAT 5', title: 'Operational backbone', time: '3:05–3:30', tone: 'BYTEBYTEGO', canvasW: W }));

  const cards = [
    { x: 48, y: 120, icon: 'key', tint: T.blue, title: 'JWT auth + replay detection', subs: ['every token single-use'] },
    { x: 412, y: 120, icon: 'timer', tint: T.amber, title: 'Case management + SLA clocks', subs: ['verdicts become cases with deadlines'] },
    { x: 776, y: 120, icon: 'network', tint: T.blue, title: 'Fraud-ring graph detector', subs: ['linked customers, shared devices'] },
    { x: 48, y: 254, icon: 'link', tint: T.blue, title: 'Courier + payment integrations', subs: ['each with documented', 'mock → production swap'] },
    { x: 412, y: 254, icon: 'activity', tint: T.amber, title: 'Circuit breaker', subs: ['degrades to rules-only', 'never fails open'] },
    { x: 776, y: 254, icon: 'check', tint: T.mint, title: 'Test suite', subs: ['security · validation · money', 'ML · platform'], chipLabel: '~400 tests · 5 dimensions — all green', chipTint: T.mint },
  ];
  for (const c of cards) p.push(node({ ...c, w: 340, h: 110 }));

  const lines = wrap(VO.beat05, 160);
  p.push(voCaption({ y: 556, text: VO.beat05, lines }));

  check('b05 title longest', 'Courier + payment integrations', 12, 260, { w600: true });
  check('b05 sub longest', 'verdicts become cases with deadlines', 10, 262);
  check('b05 tests chip', '~400 tests · 5 dimensions — all green', 9, 266, { mono: true });

  return { name: 'beat-05-endpoints.svg', w: W, h: H, body: p.join('\n  '), title: 'Beat 5 — Operational backbone', capY: 556, capLines: lines };
}

/* ================================================================== */
/* BEAT 06 — the pivot: target vs shipped · 1180×700                    */

function beat06() {
  const W = 1180, H = 700;
  const p = [];
  p.push(beatHead({ beat: 'BEAT 6', title: 'The pivot — what we actually built', time: '3:30–4:02', canvasW: W }));

  p.push(zone({ x: 48, y: 120, w: 470, h: 410, label: 'TARGET — RAZORPAY-SCALE DEPLOY', tint: T.navy, dashed: true }));
  p.push(zone({ x: 646, y: 120, w: 486, h: 410, label: 'SHIPPED — EVERYTHING CLICKABLE', tint: T.mint }));

  const target = [
    'Kafka · 24 partitions, Avro',
    'Flink CEP · pattern watch',
    'Multi-AZ · leader writes, replica reads',
    'Go/Rust hot path · compiled',
    'kernel-bypass networking · <10 ms',
  ];
  const shipped = [
    'Merkle audit chain — live, tested',
    'Bounded agent — 7 actions, enforced',
    'Dual-control override — two keys',
    'Mandate caps — OC-201B fences',
    'In-process CEP — demo detection',
  ];
  const ys = [164, 238, 312, 386, 460];

  // LEFT — documented plan rows (each row carries the amber honesty chip)
  target.forEach((s, i) => {
    p.push(rect({ x: 64, y: ys[i], w: 420, h: 52, rx: 8, fill: T.card, stroke: T.border }));
    p.push(txt({ x: 78, y: ys[i] + 31, s, size: 12, weight: 600, fill: T.text }));
    p.push(chip({ x: 370, y: ys[i] + 17, label: 'documented plan', tint: T.amber, size: 9, pad: 8, h: 18 }));
  });

  // RIGHT — shipped rows, each sealed by a mint check badge
  shipped.forEach((s, i) => {
    p.push(rect({ x: 662, y: ys[i], w: 420, h: 52, rx: 8, fill: T.card, stroke: T.border }));
    p.push(badge({ x: 676, y: ys[i] + 8, tint: T.mint, icon: 'check' }));
    p.push(txt({ x: 724, y: ys[i] + 31, s, size: 12, weight: 600, fill: T.text }));
  });

  // the one deliberate seam — spans both columns
  p.push(zone({ x: 48, y: 546, w: 1084, h: 72, label: 'ONE DELIBERATE SEAM', tint: T.amber, dashed: true }));
  p.push(txt({
    x: 64, y: 588, size: 11, fill: T.text2,
    s: "Production runs Kafka + Flink. The demo runs the same detection logic on an in-process engine — Vercel can't host Kafka. Same alert, different transport.",
  }));

  const lines = wrap(VO.beat06, 170);
  const capY = Math.min(636, H - 16 - (lines.length - 1) * 14 - 13);
  p.push(voCaption({ y: capY, text: VO.beat06, lines }));

  check('b06 target row 3', 'Multi-AZ · leader writes, replica reads', 12, 285, { w600: true });
  check('b06 shipped row 2', 'Bounded agent — 7 actions, enforced', 12, 348, { w600: true });
  check('b06 seam sub', "Production runs Kafka + Flink. The demo runs the same detection logic on an in-process engine — Vercel can't host Kafka. Same alert, different transport.", 11, 1050);

  return { name: 'beat-06-target-vs-shipped.svg', w: W, h: H, body: p.join('\n  '), title: 'Beat 6 — The pivot: target vs shipped', capY, capLines: lines };
}

/* ================================================================== */
/* BEAT 07 — results & honest gaps · 1180×780                           */

function beat07() {
  const W = 1180, H = 780;
  const p = [];
  p.push(beatHead({ beat: 'BEAT 7', title: 'Results & honest gaps', time: '4:02–4:48', tone: 'CODEOPINION', canvasW: W }));

  // ---- metrics table (48,116,640) ----------------------------------
  const tX = 48, tW = 640;
  p.push(rect({ x: tX, y: 116, w: tW, h: 36, rx: 0, fill: T.navy }));
  const hdr = [['DATASET', 60], ['ORDERS', 280], ['SPLIT', 380], ['PR-AUC', 520], ['×BASE', 588], ['ROC', 644]];
  for (const [s, x] of hdr) p.push(txt({ x, y: 138, s, size: 10, fill: '#FFFFFF', mono: true, weight: 500 }));

  p.push(rect({ x: tX, y: 152, w: tW, h: 44, rx: 0, fill: T.card, stroke: T.border }));
  const r1 = [['Amazon India + public', 60], ['~1.3 lakh', 280], ['strict time split', 380], ['0.11', 520], ['5.7×', 588], ['0.89', 644]];
  for (const [s, x] of r1) p.push(txt({ x, y: 179, s, size: 11, mono: true, fill: T.text }));

  p.push(rect({ x: tX, y: 196, w: tW, h: 44, rx: 0, fill: T.mint, fillOpacity: 0.06, stroke: T.border }));
  const r2 = [['Olist', 60], ['real, per-customer history', 280], ['0.40', 520], ['32×', 588], ['—', 644]];
  for (const [s, x] of r2) p.push(txt({ x, y: 223, s, size: 11, mono: true, fill: T.text }));

  p.push(chip({ x: 688, y: 248, label: 'the cross-check', tint: T.mint, size: 9, anchorEnd: true }));
  p.push(chip({
    x: tX, y: 280, size: 9, tint: T.navy, solid: true,
    label: 'vs ₹50,000 crore lost to COD returns — a fraction of this signal is real money back',
  }));

  // ---- headline stat cards (fills the left column under the table;
  //      every number is script-verified) -----------------------------
  const stats = [
    { x: 48, num: '5.7×', tint: T.navy, s1: 'vs base rate', s2: 'Amazon India + public' },
    { x: 269, num: '32×', tint: T.mint, s1: 'vs base rate · cross-check', s2: 'Olist · per-customer history', border: T.mint },
    { x: 490, num: '0.89', tint: T.navy, s1: 'ROC', s2: 'strict time split' },
  ];
  for (const st of stats) {
    p.push(rect({ x: st.x, y: 344, w: 197, h: 148, rx: 8, fill: T.card, stroke: st.border || T.border, sw: st.border ? 1.5 : 1 }));
    p.push(txt({ x: st.x + 98.5, y: 408, s: st.num, size: 28, weight: 700, fill: st.tint, anchor: 'middle' }));
    p.push(txt({ x: st.x + 98.5, y: 440, s: st.s1, size: 10, weight: 600, fill: T.text2, anchor: 'middle' }));
    p.push(txt({ x: st.x + 98.5, y: 455, s: st.s2, size: 10, fill: T.text2, anchor: 'middle' }));
  }

  // ---- honest gaps (dashed red zone, 716,116,416×420) ---------------
  p.push(zone({ x: 716, y: 116, w: 416, h: 420, label: 'HONEST GAPS — BEFORE YOU ASK', sub: 'stated in the video, on purpose', tint: T.red, dashed: true }));
  const gaps = [
    ['Console runs a deterministic mock scorer', 'Python scorer wires with one env var'],
    ['Ensemble probabilities run hot', 'calibration planned — ranking-neutral'],
    ['Merkle file-mode breaks across processes', 'Postgres mode holds · 30-min migration'],
    ['Multi-AZ + Go hot path', 'documented plans — not deployed, not claimed'],
    ['No sub-10 ms claim', 'honest Python ceiling: 45–75 ms p50'],
  ];
  gaps.forEach((g, i) => {
    const y = 172 + i * 72;
    p.push(rect({ x: 732, y, w: 384, h: 60, rx: 8, fill: T.card, stroke: T.border }));
    p.push(badge({ x: 744, y: y + 12, tint: T.red, icon: 'alert' }));
    p.push(txt({ x: 792, y: y + 26, s: g[0], size: 10, weight: 600, fill: T.text }));
    p.push(txt({ x: 792, y: y + 41, s: g[1], size: 10, fill: T.text2 }));
  });

  const lines = wrap(VO.beat07, 175);
  const capY = Math.min(700, H - 18 - (lines.length - 1) * 14 - 13);
  p.push(voCaption({ y: capY, text: VO.beat07, lines }));

  check('b07 table r2 span', 'real, per-customer history', 11, 234, { mono: true });
  check('b07 table r1 split', 'strict time split', 11, 134, { mono: true });
  check('b07 navy chip', 'vs ₹50,000 crore lost to COD returns — a fraction of this signal is real money back', 9, 620, { mono: true });
  check('b07 stat sub 2', 'Olist · per-customer history', 10, 181);
  check('b07 gap line 1 max', 'Merkle file-mode breaks across processes', 10, 316, { w600: true });
  check('b07 gap line 2 max', 'documented plans — not deployed, not claimed', 10, 316);

  return { name: 'beat-07-results.svg', w: W, h: H, body: p.join('\n  '), title: 'Beat 7 — Results and honest gaps', capY, capLines: lines };
}

/* ================================================================== */
/* BEAT 08 — close · 1180×560                                           */

function beat08() {
  const W = 1180, H = 560;
  const p = [];
  p.push(beatHead({ beat: 'BEAT 8', title: 'Close', time: '4:48–5:00', tone: 'FIRESHIP', canvasW: W }));

  // navy end card
  p.push(rect({ x: 140, y: 132, w: 900, h: 300, rx: 16, fill: T.navy }));
  p.push(txt({ x: 590, y: 200, s: 'RTO TRUST LAYER', size: 34, weight: 700, fill: '#FFFFFF', anchor: 'middle', spacing: 2 }));
  p.push(txt({ x: 590, y: 250, s: 'github.com/Neeraj-Parekh/special-parakeet', size: 13, fill: '#FFFFFF', mono: true, anchor: 'middle' }));
  p.push(txt({ x: 590, y: 276, s: 'rto-trust-layer.vercel.app', size: 13, fill: '#FFFFFF', mono: true, anchor: 'middle' }));
  const cw = chipW('repo public · console live · tests green', { size: 10 });
  p.push(chip({ x: 590 - cw / 2, y: 304, label: 'repo public · console live · tests green', tint: T.mint, solid: true }));
  p.push(txt({ x: 590, y: 368, s: 'Try it, break it, check the audit trail.', size: 12, fill: '#FFFFFF', anchor: 'middle' }));

  // edit-direction corner chips
  p.push(chip({ x: 48, y: 520, label: 'NO FADE', tint: T.text2, size: 9 }));
  p.push(chip({ x: 1132, y: 520, label: 'HARD CUT', tint: T.text2, size: 9, anchorEnd: true }));

  const lines = wrap(VO.beat08, 150);
  const capY = Math.min(470, H - 44 - (lines.length - 1) * 14 - 13);
  p.push(voCaption({ y: capY, text: VO.beat08, lines }));

  check('b08 repo mono', 'github.com/Neeraj-Parekh/special-parakeet', 13, 860, { mono: true });
  check('b08 mint chip', 'repo public · console live · tests green', 10, 860, { mono: true });
  check('b08 title', 'RTO TRUST LAYER', 34, 860, { w600: true });

  return { name: 'beat-08-close.svg', w: W, h: H, body: p.join('\n  '), title: 'Beat 8 — Close', capY, capLines: lines };
}

/* ================================================================== */
/* build                                                               */

const frames = [beat02(), beat05(), beat06(), beat07(), beat08()];

for (const f of frames) {
  const lastBaseline = f.capY + 13 + (f.capLines.length - 1) * 14;
  if (lastBaseline > f.h - 8) {
    throw new Error(`${f.name}: VO caption overflows canvas (last baseline ${lastBaseline} > ${f.h - 8})`);
  }
  save(f.name, svgDoc({ w: f.w, h: f.h, body: f.body, title: f.title }));
  console.log(`   caption: ${f.capLines.length} lines, y=${f.capY}, last baseline ${lastBaseline} / ${f.h}`);
}

const fails = LINT.filter((l) => l.startsWith('FAIL'));
console.log('\ntext-fit lint:');
for (const l of LINT) console.log('  ' + l);
if (fails.length) throw new Error(`text-fit lint failures: ${fails.length}`);
console.log('\nall 5 storyboard frames generated; lint clean.');
