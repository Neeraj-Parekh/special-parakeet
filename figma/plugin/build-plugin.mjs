// figma/plugin/build-plugin.mjs
// Assembles the final Figma plugin (code.js) by embedding:
//   · all 14 SVG figures from figma/svgs/ (parsed dims)
//   · the locked 5:00 pitch script (docs/deepseek-script.md), beat by beat
//   · token map, page layout, README page content
// Run: cd /home/z/my-project && bun figma/plugin/build-plugin.mjs

import { readFileSync, writeFileSync, readdirSync, copyFileSync } from 'fs';
import { join, dirname, basename } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const SVGS = join(root, 'figma', 'svgs');
const PLUGIN = join(root, 'figma', 'plugin');
const PUBLIC = join(root, 'public', 'diagrams');

/* ---------- tokens ---------- */
const TOKENS = {
  'navy-950': '#02042B',
  'blue-500': '#2B84EB',
  'blue-400': '#528FF0',
  'mint-500': '#00B878',
  'amber-500': '#F5A623',
  'red-500': '#E5484D',
  'bg': '#F6F8FC',
  'card': '#FFFFFF',
  'border': '#E3E8F0',
  'text': '#1A2233',
  'text-2': '#5B6478',
  'dashed': '#B9C2D0',
  'async': '#94A3B8',
};

/* ---------- load + parse svgs ---------- */
function loadSvg(file) {
  const svg = readFileSync(join(SVGS, file), 'utf8');
  const head = svg.slice(0, 400);
  const w = Number((head.match(/width="(\d+)"/) || [])[1]);
  const h = Number((head.match(/height="(\d+)"/) || [])[1]);
  if (!w || !h) throw new Error('missing dims in ' + file);
  return { name: file.replace(/\.svg$/, ''), svg, w, h, x: 0, y: 0 };
}

const files = readdirSync(SVGS).filter((f) => f.endsWith('.svg')).sort();
const figs = {};
for (const f of files) { const d = loadSvg(f); figs[d.name] = d; }

/* ---------- script beats (verbatim from docs/deepseek-script.md) ---------- */
const BEATS = [
  { n: 1, time: '0:00–0:15', tone: 'Fireship', title: 'Hook — the number',
    visual: 'Black frame · white text stamps, one per 1.2 s · snap-zoom: RTO TRUST LAYER · cut',
    vo: 'Indian e-commerce loses fifty thousand crore a year to cash-on-delivery returns. Three in ten COD parcels never find a home. This is how you stop them—before the courier leaves.' },
  { n: 2, time: '0:15–0:45', tone: 'Gaurav Sen', title: 'Problem, framed like an SDE interview',
    visual: 'beat2_problem.excalidraw — two columns',
    vo: 'Let me frame the problem the way we\u2019d frame it in a design review. The goal is not \u201creduce returns\u201d—nobody can. The goal: at checkout time, in under seventy-five milliseconds, decide whether this order is worth shipping—and attach three things: a cost-optimal decision, an explanation the merchant can act on, and a receipt a regulator can verify. Today the merchant ships everything and discovers the bounce two weeks later. Double shipping, lost sale, refund fight—no proof of why any decision was made. The target: every order scored at placement. Accept, review-with-OTP, or reject—with the cost math shown, and every decision tamper-evident.' },
  { n: 3, time: '0:45–2:25', tone: 'ByteByteGo + Arpit', title: 'Act 1 — the production architecture',
    visual: 'vision_production_stack.excalidraw — LIVE-DRAW left to right, one box per sentence (order printed on FIG.02)',
    vo: 'A naive version of this is one model behind one API—and at real volume it breaks three specific ways: attackers probe the threshold until they find it, one write race corrupts a mandate counter, and a regulator asks for proof of a decision that only ever existed as a log line. So here\u2019s what actually holds up. Here\u2019s the production system, left to right. This is the target architecture—the one a Razorpay-scale deploy would run. A scoring request hits the gateway. First: per-IP sliding-window rate limiting, then the deterministic rule engine—four pre-screens with every monetary threshold randomized plus-or-minus five hundred rupees. That\u2019s model-extraction defense; attackers probing the boundary get noise. The mandate check: NPCI\u2019s OC-201B UPI Circle caps—five thousand per transaction, fifteen thousand per month, twenty-four-hour cooling, six-month auto-revoke. Money moves only inside regulator-shaped fences. Then inference. Seventy-nine leak-safe features—expanding rates with shift-one, m-estimate smoothing over sparse cities. ONNX Runtime: one-point-six microseconds per row. The champion is an Optuna-weighted ensemble, XGBoost at ninety-four percent. The decision is Bahnsen cost-optimal—expected cost of accept, review, and reject; cheapest wins. Response carries the actual breakdown: accept two-forty-eight, review ninety-nine, reject nine-eighty. Now the parts that make it a platform. The hot path is Go or Rust—the Python ceiling is forty-five to seventy-five milliseconds; a compiled hot path with kernel-bypass takes us under the ten-millisecond target. Multi-AZ: pods spread across availability zones, writes pinned to a leader because mandate counters must not race, reads round-robined to replicas. Decisions stream into Kafka—twenty-four partitions, Avro schemas—a Flink CEP job watches for patterns like three rejects in five minutes from one customer, sinking into ClickHouse with materialized views for the fraud dashboard. And every decision, everywhere, is a Merkle leaf in a tamper-evident chain.' },
  { n: 4, time: '2:25–3:05', tone: 'Nana', title: 'The trust core',
    visual: 'beat4_trust_layer.excalidraw — three panels, one at a time · then MANIM MerkleProofScene 12 s',
    vo: 'Zoom into the trust core—the three things that make this a trust layer, not a model with an API in front of it. Merkle audit chain. Every decision is a leaf; the root seals it. Tamper one record—the chain breaks, provably. RBI\u2019s model-risk-management guidance wants tamper-evident decisions; we ship the cryptographic version. Inclusion proof: leaf, sibling, up to the root—any auditor verifies in log-n. Dual-control override. No single admin can reverse a decision—two signatures, RFC-5869-derived keys, one-shot replay nonces. The AI copilot is bounded. Seven actions, enforced as a server-side dependency—not a prompt. The agent is unable to do anything else.' },
  { n: 5, time: '3:05–3:30', tone: 'ByteByteGo', title: 'Operational backbone',
    visual: 'final_endpoints.excalidraw — endpoint groups appear one by one',
    vo: 'Around that core: JWT auth with replay detection, case management with SLA clocks, a fraud-ring graph detector, and courier and payment integrations—each with a documented mock-to-production swap. A circuit breaker that degrades to rules-only, never fails open. And what holds it together: nearly four hundred tests across five dimensions—security, validation, money paths, ML, and platform. All green.' },
  { n: 6, time: '3:30–4:02', tone: 'the key reveal', title: 'The pivot — and here\u2019s what we actually built',
    visual: 'vision_vs_shipped.excalidraw — TARGET vs SHIPPED · or OBS recording of the real dashboard',
    vo: 'So that\u2019s the target architecture. Here\u2019s what we actually built—and every piece of it is clickable. The trust core is real: the Merkle chain, the bounded agent, dual-control override, the mandate caps—all live, all tested. The streaming has one deliberate seam: production runs Kafka and Flink; the demo runs the same detection logic on an in-process engine, because Vercel can\u2019t host Kafka. Same alert, different transport. Everything else on screen right now is running, not mocked.' },
  { n: 7, time: '4:02–4:48', tone: 'CodeOpinion', title: 'Results & honest gaps',
    visual: 'beat7_results.excalidraw — metrics table → gaps',
    vo: 'Results. On around 1.3 lakh real orders—Amazon India and public datasets—with a strict time split: PR-AUC 0.11, which is 5.7× base rate, ROC 0.89. The stronger number is the cross-check: on Olist, real data with per-customer history, the same pipeline scores 0.40—32× base rate. Against the fifty-thousand-crore number we opened with, even a fraction of that signal is real money back. The gaps, before you ask. The deployed console runs a deterministic mock scorer today—the Python scorer wires with one environment variable. Ensemble probabilities run hot; calibration is planned and ranking-neutral. The Merkle chain in file-mode breaks across processes—Postgres mode holds; that\u2019s a thirty-minute migration. Multi-AZ and the Go hot path are documented plans, not deployed—we won\u2019t claim them. And we will not claim sub-ten-millisecond latency; the honest Python ceiling is 45–75 ms p50.' },
  { n: 8, time: '4:48–5:00', tone: 'Fireship', title: 'Close',
    visual: 'Snap zoom to logo · text card: github.com/Neeraj-Parekh/special-parakeet + rto-trust-layer.vercel.app · no fade',
    vo: 'We didn\u2019t just design the production system. We built the part that matters most—the trust core, the working detector, the audit trail—and we know exactly how to take it to production. Repo\u2019s public, console is live, tests are green. Try it, break it, check the audit trail.' },
];

const SCRIPT = {
  header: [
    'Act 1 (0:00–3:30) — the architect draws the production system. Act 2 (3:30–4:45) — the builder proves it ships. The pivot at 3:30 is the key.',
    'Tone: Fireship energy only in the first and last 15 seconds. Everything between: calm, measured, senior-engineer-to-senior-engineer.',
    'Total 5:00 · ~720 words · every number code-verified · source: docs/deepseek-script.md (repo)',
  ],
  table: [
    'BEAT · WORDS · TARGET · PACE',
    '1 · 40 · 15s · 2.7 w/s',
    '2 · 95 · 30s · 3.2 w/s',
    '3 · 250 · 100s · 2.5 w/s (slow draw + why-preamble)',
    '4 · 95 · 40s · 2.4 w/s',
    '5 · 70 · 25s · 2.8 w/s',
    '6 · 85 · 32s · 2.7 w/s (reveal, not rush)',
    '7 · 130 · 46s · 2.8 w/s',
    '8 · 40 · 12s · 3.3 w/s',
    'NET: ~5:00',
  ],
  beats: BEATS,
};

const README_LINES = [
  { text: 'RTO TRUST LAYER — FIGMA BOARD', size: 24, h: true },
  { text: 'Everything on this board was generated by code (figma/svg-kit in the repo) — no AI images, no screenshots. One source of truth: the console design tokens.', size: 12 },
  { text: 'HOW THIS BOARD WAS BUILT', size: 14, h: true, gap: 10 },
  { text: '· 14 figures drawn as flat SVG (Inter + JetBrains Mono · 8pt grid · orthogonal arrows · zone containment = meaning)', size: 11, mono: true },
  { text: '· imported via figma.createNodeFromSvg() — fully editable vector + text layers', size: 11, mono: true },
  { text: '· 13 paint styles + 4 text styles created, swatches bound', size: 11, mono: true },
  { text: '· the 5:00 pitch script (docs/deepseek-script.md) rendered beat-by-beat on page RTO · 04', size: 11, mono: true },
  { text: 'IMPORT PATHS', size: 14, h: true, gap: 10 },
  { text: 'A · THIS PLUGIN (what you just ran) — desktop Figma → Plugins → Development → Import plugin from manifest… → select figma/plugin → Run.', size: 11, mono: true },
  { text: 'B · NO DESKTOP? — open any SVG in figma/svgs/, copy the raw code, paste directly onto the Figma canvas. Every figure imports as editable layers.', size: 11, mono: true },
  { text: 'C · OPTIONAL — the html.to.design community plugin can import hosted HTML; the SVG path (A/B) is higher fidelity for this kit.', size: 11, mono: true },
  { text: 'TOKENS', size: 14, h: true, gap: 10 },
  { text: 'navy-950 #02042B · blue-500 #2B84EB · mint-500 #00B878 · amber-500 #F5A623 · red-500 #E5484D · bg #F6F8FC · border #E3E8F0 · text #1A2233 · text-2 #5B6478', size: 11, mono: true },
  { text: 'Same values as src/app/globals.css — the board and the console are one system.', size: 11, mono: true },
  { text: 'ABOUT THE FIGMA API TOKEN', size: 14, h: true, gap: 10 },
  { text: 'The personal access token was used only to verify identity (GET /v1/me) and to probe capabilities — POST/PATCH returned 404: the REST API is read-only for file content. No content was ever written to Figma servers by the generator; this plugin builds the board locally inside Figma. Rotate the token when done.', size: 11 },
  { text: 'LINKS', size: 14, h: true, gap: 10 },
  { text: 'github.com/Neeraj-Parekh/special-parakeet · rto-trust-layer.vercel.app · figures also viewable at /diagrams/*.svg on the deployed site', size: 11, mono: true },
];

/* ---------- storyboard page layout (2 columns) ---------- */
const order = [
  'beat-01-hook', 'beat-02-problem', 'beat-03-live-draw', 'beat-04-trust-core',
  'beat-05-endpoints', 'beat-06-target-vs-shipped', 'beat-07-results', 'beat-08-close',
];
const col0 = [], col1 = [];
let y0 = 0, y1 = 0;
order.forEach((name, i) => {
  const f = figs[name];
  if (i % 2 === 0) { f.x = 0; f.y = y0; y0 += f.h + 120; col0.push(f); }
  else { f.x = 1400; f.y = y1; y1 += f.h + 120; col1.push(f); }
});

/* ---------- scene ---------- */
const scene = {
  tokens: TOKENS,
  fileCount: files.length,
  pages: [
    { name: 'RTO · 00 Cover', frames: [figs['cover']] },
    { name: 'RTO · 01 Tokens & kit', frames: [figs['kit-legend']], swatches: true },
    { name: 'RTO · 02 Figures · FIG.01–04', frames: [
      figs['fig-01-system-context'],
      figs['fig-02-scoring-pipeline'],
      figs['fig-03-trust-chain'],
      figs['fig-04-deployment-perf'],
    ] },
    { name: 'RTO · 03 Storyboards · beats 1–8', frames: [...col0, ...col1] },
    { name: 'RTO · 04 Script · 5:00 locked', script: SCRIPT },
    { name: 'RTO · 05 How to import', readme: README_LINES },
  ],
};

/* ---------- inject into template ---------- */
const template = readFileSync(join(PLUGIN, 'plugin.template.js'), 'utf8');
const marker = 'const SCENE = __SCENE__;';
if (!template.includes(marker)) throw new Error('template marker missing');
const code = template.replace(marker, 'const SCENE = ' + JSON.stringify(scene) + ';');
writeFileSync(join(PLUGIN, 'code.js'), code);

/* keep public/diagrams in sync */
for (const f of files) copyFileSync(join(SVGS, f), join(PUBLIC, f));

/* ---------- report ---------- */
console.log('plugin built: figma/plugin/code.js —', (code.length / 1024).toFixed(1) + ' KB');
console.log('figures embedded:', files.length);
for (const p of scene.pages) {
  console.log('  page:', p.name, p.frames ? '(' + p.frames.length + ' figures)' : p.script ? '(script)' : p.readme ? '(readme)' : '');
}
console.log('beats embedded:', BEATS.length, '· total VO chars:', BEATS.reduce((a, b) => a + b.vo.length, 0));
console.log('public/diagrams synced:', files.length, 'files');
