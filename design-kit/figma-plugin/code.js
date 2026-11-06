/**
 * RTO Diagram Kit — Figma dev plugin
 * ------------------------------------------------------------------
 * Run once inside Figma (desktop or web):
 *   1. Figma → menu Plugins → Development → New Plugin…
 *   2. Choose "Figma design" → "Run once" → Figma creates a folder
 *      with its own manifest.json + code.js
 *   3. Replace BOTH files with the ones in this folder, but keep the
 *      "id" line from the manifest Figma generated for you.
 *   4. Plugins → Development → RTO Diagram Kit  →  Run
 *
 * What it builds (no network, no UI — everything is local):
 *   · Page "00 · Tokens & Kit"  → paint styles (tokens + tints),
 *     text styles, and component masters (Zone solid/dashed, Card,
 *     Chip, Actor, Arrows, Figure frame)
 *   · Pages "01 … 04"           → title blocks for the four figures
 *     + a note telling you to drag the matching .svg in
 *
 * The figures themselves come from the generated SVGs
 * (public/figures/fig0X-*.svg) — drag them onto the pages, then
 * select-all → apply the Kit styles. SVG import gives you real
 * vectors + editable text; this plugin gives you the live token
 * system. Together = the full kit.
 */

/* eslint-disable */

// ---------------------------------------------------------------- tokens

const TOKENS = {
  "navy-950": "#02042B",
  "blue-500": "#2B84EB",
  "blue-400": "#528FF0",
  "mint-500": "#00B878",
  "amber-500": "#F5A623",
  "red-500": "#E5484D",
  "bg": "#F6F8FC",
  "card": "#FFFFFF",
  "border": "#E3E8F0",
  "text": "#1A2233",
  "text-2": "#5B6478",
};

function hexToFigma(hex) {
  return {
    r: parseInt(hex.slice(1, 3), 16) / 255,
    g: parseInt(hex.slice(3, 5), 16) / 255,
    b: parseInt(hex.slice(5, 7), 16) / 255,
  };
}
function solid(hex, opacity) {
  const p = { type: "SOLID", color: hexToFigma(hex) };
  if (opacity != null && opacity < 1) p.opacity = opacity;
  return p;
}
function shadow() {
  return [
    {
      type: "DROP_SHADOW",
      color: { r: hexToFigma(TOKENS["navy-950"]).r, g: hexToFigma(TOKENS["navy-950"]).g, b: hexToFigma(TOKENS["navy-950"]).b, a: 0.08 },
      offset: { x: 0, y: 2 },
      spread: 0,
      blur: 6,
    },
  ];
}
function setDashes(node, dashes) {
  // property name differs across API versions — try the known aliases
  const names = ["strokeDashes", "dashPattern", "strokeDashPattern"];
  for (const n of names) {
    try { node[n] = dashes; } catch (e) { /* ignore */ }
  }
}

// ---------------------------------------------------------------- fonts

const SANS = { family: "Inter", style: "Regular" };      // Figma ships Inter natively
const SANS_SEMI = { family: "Inter", style: "Semi Bold" };
const SANS_BOLD = { family: "Inter", style: "Bold" };
let MONO = null; // resolved below (JetBrains Mono if installed, else Inter)

async function loadFonts() {
  const wanted = [SANS, SANS_SEMI, SANS_BOLD];
  for (const f of wanted) await figma.loadFontAsync(f);
  try {
    await figma.loadFontAsync({ family: "JetBrains Mono", style: "Regular" });
    await figma.loadFontAsync({ family: "JetBrains Mono", style: "SemiBold" });
    MONO = { family: "JetBrains Mono", style: "SemiBold" };
  } catch (e) {
    try {
      await figma.loadFontAsync({ family: "Roboto Mono", style: "Medium" });
      MONO = { family: "Roboto Mono", style: "Medium" };
    } catch (e2) {
      MONO = SANS_SEMI; // graceful fallback
    }
  }
}

// ---------------------------------------------------------------- helpers

function makeText(chars, o) {
  const t = figma.createText();
  t.fontName = o.font || SANS;
  t.fontSize = o.size || 12;
  t.characters = chars;
  if (o.color) t.fills = [solid(o.color)];
  if (o.tracking) t.letterSpacing = { unit: "PERCENT", value: o.tracking };
  if (o.upper) t.textCase = "UPPER";
  if (o.align) t.textAlignHorizontal = o.align;
  return t;
}

function makeFrame(w, h, o) {
  o = o || {};
  const f = figma.createFrame();
  f.resize(w, h);
  f.fills = o.fills || [];
  if (o.stroke) {
    f.strokes = [solid(o.stroke, o.strokeOpacity != null ? o.strokeOpacity : 1)];
    f.strokeWeight = o.strokeWeight || 1;
    if (o.dashes) setDashes(f, [6, 4]);
  } else {
    f.strokes = [];
  }
  f.cornerRadius = o.radius || 0;
  if (o.shadow) f.effects = shadow();
  f.clipsContent = !!o.clips;
  return f;
}

function makeVector(vertices, segments, fillHex, o) {
  o = o || {};
  const v = figma.createVector();
  v.vectorPaths = [
    {
      windingRule: "NONZERO",
      network: {
        vertices: vertices.map((p) => ({ x: p[0], y: p[1] })),
        segments: segments.map((s) => ({ start: s[0], end: s[1] })),
        regions: o.region
          ? [
              {
                windingRule: "NONZERO",
                network: {
                  vertices: vertices.map((p) => ({ x: p[0], y: p[1] })),
                  segments: segments.map((s) => ({ start: s[0], end: s[1] })),
                },
              },
            ]
          : [],
      },
    },
  ];
  if (fillHex) {
    v.fills = [solid(fillHex)];
    v.strokes = [];
  } else {
    v.fills = [];
    v.strokes = [solid(o.color || TOKENS["text-2"])];
    v.strokeWeight = o.weight || 1.5;
    v.strokeCap = "ROUND";
  }
  return v;
}

// ---------------------------------------------------------------- main

async function main() {
  await loadFonts();

  // ============ page 00 · tokens & kit ============
  const kitPage = figma.createPage();
  kitPage.name = "00 · Tokens & Kit";

  // ---- paint styles
  const paints = {};
  for (const [name, hex] of Object.entries(TOKENS)) {
    const st = figma.createPaintStyle();
    st.name = "token/" + name;
    st.paints = [solid(hex)];
    paints[name] = st;
  }
  const tints = [
    ["tint/blue-500 @ 4%", "blue-500", 0.04],
    ["tint/blue-500 @ 10%", "blue-500", 0.1],
    ["tint/blue-500 @ 12%", "blue-500", 0.12],
    ["tint/mint-500 @ 5%", "mint-500", 0.05],
    ["tint/mint-500 @ 10%", "mint-500", 0.1],
    ["tint/amber-500 @ 12%", "amber-500", 0.12],
    ["tint/red-500 @ 12%", "red-500", 0.12],
  ];
  for (const [name, base, op] of tints) {
    const st = figma.createPaintStyle();
    st.name = "kit/" + name;
    st.paints = [solid(TOKENS[base], op)];
  }

  // ---- text styles
  const ts = (name, font, size, o) => {
    o = o || {};
    const t = figma.createTextStyle();
    t.name = "text/" + name;
    t.fontName = font;
    t.fontSize = size;
    t.lineHeight = { unit: "AUTO" };
    if (o.tracking) t.letterSpacing = { unit: "PERCENT", value: o.tracking };
    if (o.upper) t.textCase = "UPPER";
    return t;
  };
  ts("Title / 20", SANS_BOLD, 20);
  ts("Description / 12", SANS, 12);
  ts("Zone label / 11", SANS_SEMI, 11, { tracking: 8, upper: true });
  ts("Card name / 13", SANS_SEMI, 13);
  ts("Card sub / 11", SANS, 11);
  ts("Chip / 10", MONO, 10, { tracking: 3 });
  ts("Stat value / 19", SANS_SEMI, 19);
  ts("Legend / 10", SANS, 10);

  // ---- kit canvas label
  const kitTitle = makeText("RTO Diagram Kit — component masters", { font: SANS_BOLD, size: 18, color: TOKENS["navy-950"] });
  kitTitle.x = 0;
  kitTitle.y = -80;
  kitPage.appendChild(kitTitle);

  let cx = 0;
  const rowY = 0;
  const labelFor = (comp, label) => {
    const t = makeText(label, { size: 10, color: TOKENS["text-2"], upper: true, tracking: 8 });
    t.x = comp.x;
    t.y = comp.y + comp.height + 10;
    kitPage.appendChild(t);
  };

  // ---- Zone / solid
  const zoneSolid = figma.createComponent();
  zoneSolid.resize(340, 220);
  zoneSolid.fills = [solid(TOKENS.bg)];
  zoneSolid.strokes = [solid(TOKENS.border)];
  zoneSolid.strokeWeight = 1;
  zoneSolid.cornerRadius = 12;
  zoneSolid.clipsContent = false;
  const zl = makeText("ZONE LABEL", { font: SANS_SEMI, size: 11, color: TOKENS["text-2"], upper: true, tracking: 8 });
  zl.x = 16; zl.y = 16;
  zoneSolid.appendChild(zl);
  zoneSolid.name = "Zone / solid";
  zoneSolid.x = cx; zoneSolid.y = rowY;
  kitPage.appendChild(zoneSolid);
  labelFor(zoneSolid, "zone / solid");
  cx += 380;

  // ---- Zone / dashed (logical / external boundary)
  const zoneDash = figma.createComponent();
  zoneDash.resize(340, 220);
  zoneDash.fills = [solid(TOKENS["blue-500"], 0.04)];
  zoneDash.strokes = [solid(TOKENS["blue-500"], 0.5)];
  zoneDash.strokeWeight = 1.5;
  setDashes(zoneDash, [6, 4]);
  zoneDash.cornerRadius = 12;
  zoneDash.clipsContent = false;
  const zl2 = makeText("LOGICAL BOUNDARY", { font: SANS_SEMI, size: 11, color: TOKENS["blue-500"], upper: true, tracking: 8 });
  zl2.x = 16; zl2.y = 16;
  zoneDash.appendChild(zl2);
  zoneDash.name = "Zone / dashed";
  zoneDash.x = cx; zoneDash.y = rowY;
  kitPage.appendChild(zoneDash);
  labelFor(zoneDash, "zone / dashed — logical / external");
  cx += 380;

  // ---- Card / component (autolayout)
  const card = figma.createComponent();
  card.name = "Card / component";
  card.resize(320, 84);
  card.fills = [solid(TOKENS.card)];
  card.strokes = [solid(TOKENS.border)];
  card.strokeWeight = 1;
  card.cornerRadius = 8;
  card.effects = shadow();
  card.layoutMode = "HORIZONTAL";
  card.primaryAxisSizingMode = "AUTO";
  card.counterAxisSizingMode = "FIXED";
  card.itemSpacing = 12;
  card.paddingLeft = 14;
  card.paddingRight = 14;
  card.paddingTop = 0;
  card.paddingBottom = 0;
  const badge = makeFrame(36, 36, { fills: [solid(TOKENS["blue-500"], 0.12)], stroke: TOKENS["blue-500"], strokeOpacity: 0.3, radius: 8 });
  badge.name = "icon badge 36";
  badge.layoutAlign = "CENTER";
  try { badge.layoutSizingHorizontal = "FIXED"; badge.layoutSizingVertical = "FIXED"; } catch (e) {}
  card.appendChild(badge);
  const txtCol = figma.createFrame();
  txtCol.name = "label column";
  txtCol.layoutMode = "VERTICAL";
  txtCol.primaryAxisSizingMode = "AUTO";
  txtCol.counterAxisSizingMode = "AUTO";
  txtCol.itemSpacing = 2;
  txtCol.fills = [];
  txtCol.strokes = [];
  txtCol.layoutAlign = "CENTER";
  try { txtCol.layoutSizingHorizontal = "AUTO"; txtCol.layoutSizingVertical = "FIXED"; } catch (e) {}
  const cn = makeText("Component name", { font: SANS_SEMI, size: 13, color: TOKENS.text });
  const cs = makeText("one-line descriptor", { font: SANS, size: 11, color: TOKENS["text-2"] });
  txtCol.appendChild(cn);
  txtCol.appendChild(cs);
  card.appendChild(txtCol);
  card.x = cx; card.y = rowY;
  kitPage.appendChild(card);
  labelFor(card, "card / component — swap badge icon");
  cx += 380;

  // ---- Chip / latency
  const chip = figma.createComponent();
  chip.name = "Chip / latency";
  chip.layoutMode = "HORIZONTAL";
  chip.primaryAxisSizingMode = "AUTO";
  chip.counterAxisSizingMode = "FIXED";
  chip.itemSpacing = 0;
  chip.paddingLeft = 13; chip.paddingRight = 13;
  chip.paddingTop = 5; chip.paddingBottom = 5;
  chip.counterAxisAlignItems = "CENTER";
  chip.fills = [solid(TOKENS["mint-500"], 0.1)];
  chip.strokes = [solid(TOKENS["mint-500"], 0.32)];
  chip.strokeWeight = 1;
  chip.cornerRadius = 12;
  chip.resize(80, 24);
  const chT = makeText("1.6 µs/row", { font: MONO, size: 10, color: TOKENS["mint-500"], tracking: 3 });
  chip.appendChild(chT);
  chip.x = cx; chip.y = rowY;
  kitPage.appendChild(chip);
  labelFor(chip, "chip — latency / status");
  cx += 200;

  // ---- Actor / external
  const actor = figma.createComponent();
  actor.name = "Actor / external";
  actor.layoutMode = "VERTICAL";
  actor.primaryAxisSizingMode = "AUTO";
  actor.counterAxisSizingMode = "AUTO";
  actor.itemSpacing = 6;
  actor.paddingLeft = 0; actor.paddingRight = 0; actor.paddingTop = 0; actor.paddingBottom = 0;
  actor.counterAxisAlignItems = "CENTER";
  actor.fills = [];
  actor.strokes = [];
  const actorCircle = figma.createEllipse();
  actorCircle.resize(52, 52);
  actorCircle.fills = [solid(TOKENS.card)];
  actorCircle.strokes = [solid(TOKENS.border)];
  actorCircle.strokeWeight = 1;
  actorCircle.effects = shadow();
  actorCircle.name = "circle 52";
  try { actorCircle.layoutSizingHorizontal = "FIXED"; actorCircle.layoutSizingVertical = "FIXED"; } catch (e) {}
  actor.appendChild(actorCircle);
  const al = makeText("External actor", { font: SANS_SEMI, size: 12, color: TOKENS.text, align: "CENTER" });
  const as = makeText("outside trust boundary", { font: SANS, size: 10, color: TOKENS["text-2"], align: "CENTER" });
  actor.appendChild(al);
  actor.appendChild(as);
  actor.x = cx; actor.y = rowY;
  kitPage.appendChild(actor);
  labelFor(actor, "actor — always outside zones");
  cx += 240;

  // ---- arrows row
  const arrowY = 360;
  const mkArrow = (name, color, dashed) => {
    const c = figma.createComponent();
    c.name = name;
    c.resize(64, 14);
    c.fills = [];
    c.strokes = [];
    const shaft = makeVector([[0, 7], [52, 7]], [[0, 1]], null, { color: color, weight: 1.5 });
    shaft.x = 0; shaft.y = 0;
    if (dashed) setDashes(shaft, [6, 4]);
    c.appendChild(shaft);
    const head = makeVector([[0, 0], [9, 3.5], [0, 7]], [[0, 1], [1, 2], [2, 0]], color, { region: true });
    head.x = 52; head.y = 3.5;
    c.appendChild(head);
    return c;
  };
  const a1 = mkArrow("Arrow / primary", TOKENS["text-2"], false);
  a1.x = 0; a1.y = arrowY;
  kitPage.appendChild(a1);
  labelFor(a1, "arrow / primary — sync");
  const a2 = mkArrow("Arrow / async", TOKENS["text-2"], true);
  a2.x = 200; a2.y = arrowY;
  kitPage.appendChild(a2);
  labelFor(a2, "arrow / async — dashed");
  const a3 = mkArrow("Arrow / accept", TOKENS["mint-500"], false);
  a3.x = 400; a3.y = arrowY;
  kitPage.appendChild(a3);
  labelFor(a3, "arrow / accept");
  const a4 = mkArrow("Arrow / review", TOKENS["amber-500"], false);
  a4.x = 600; a4.y = arrowY;
  kitPage.appendChild(a4);
  labelFor(a4, "arrow / review");
  const a5 = mkArrow("Arrow / reject", TOKENS["red-500"], false);
  a5.x = 800; a5.y = arrowY;
  kitPage.appendChild(a5);
  labelFor(a5, "arrow / reject");

  // ---- Figure frame
  const figFrame = figma.createComponent();
  figFrame.name = "Frame / figure 1600";
  figFrame.resize(1600, 1000);
  figFrame.fills = [solid(TOKENS.card)];
  figFrame.strokes = [solid(TOKENS.border)];
  figFrame.strokeWeight = 1;
  figFrame.cornerRadius = 8;
  figFrame.x = 1000; figFrame.y = arrowY - 60;
  kitPage.appendChild(figFrame);
  labelFor(figFrame, "figure frame — 1600 × 1000");

  // ============ pages 01–04 ============
  const FIGS = [
    ["01", "System Context — a COD order under the microscope", "Where RTO Trust Layer sits: between the merchant storefront and the courier network. Verdicts return before the courier leaves.", "fig01-system-context.svg"],
    ["02", "Scoring Pipeline — event to verdict in 29 ms", "Left to right: every COD order is scored on the edge before it is accepted. Latency chips show per-stage budgets.", "fig02-scoring-pipeline.svg"],
    ["03", "Trust Chain — Merkle-sealed audit trail", "Every scored decision becomes a tamper-evident record. Anyone can re-verify the chain — no trust in us required.", "fig03-trust-chain.svg"],
    ["04", "Deployment — edge region, two AZs, one truth", "Scoring runs close to the merchant; every AZ reads a replica; writes seal once. Latency budget on the bottom rail.", "fig04-deployment.svg"],
  ];

  for (const [no, title, desc, file] of FIGS) {
    const page = figma.createPage();
    page.name = no + " · " + title.split(" — ")[0];

    const frame = figFrame.createInstance();
    frame.x = 0; frame.y = 0;
    page.appendChild(frame);

    const chipF = makeFrame(74, 26, { fills: [solid(TOKENS["blue-500"], 0.1)], stroke: TOKENS["blue-500"], strokeOpacity: 0.32, radius: 13 });
    chipF.x = 48; chipF.y = 40;
    const chipT = makeText("FIG " + no, { font: MONO, size: 10, color: TOKENS["blue-500"], tracking: 6, align: "CENTER" });
    chipT.x = 0; chipT.y = 7;
    chipT.resize(74, 12);
    chipF.appendChild(chipT);
    page.appendChild(chipF);

    const tt = makeText(title, { font: SANS_BOLD, size: 20, color: TOKENS["navy-950"] });
    tt.x = 48; tt.y = 88;
    page.appendChild(tt);

    const dd = makeText(desc, { font: SANS, size: 12, color: TOKENS["text-2"] });
    dd.resize(900, 18);
    dd.x = 48; dd.y = 114;
    page.appendChild(dd);

    const note = makeText(
      "→ drag " + file + " (from public/figures/) onto this frame, then select-all inside and apply Kit styles",
      { font: SANS_SEMI, size: 12, color: TOKENS["blue-500"] }
    );
    note.x = 48; note.y = 940;
    page.appendChild(note);
  }

  figma.currentPage = kitPage;
  figma.viewport.scrollAndZoomIntoView([kitTitle, zoneSolid]);
  figma.notify("RTO Diagram Kit ready — tokens, text styles, " + "components and 4 figure pages created.");
}

main().catch((e) => {
  figma.notify("RTO Diagram Kit failed: " + (e && e.message ? e.message : e), { error: true });
});
