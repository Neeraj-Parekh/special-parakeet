// figma/plugin/plugin.template.js
// RTO Trust Layer — Board Builder (Figma plugin template).
// build-plugin.mjs injects the scene graph (SVGs + script beats) as __SCENE__
// and emits code.js. Run once in Figma: builds 6 pages, paint + text styles,
// 14 figures as editable vector layers, and the full 5:00 pitch script.
// Idempotent: re-running after a build just notifies.

const SCENE = __SCENE__;

/* ---------------- utils ---------------- */

function hexToRgb(hex) {
  const h = hex.replace('#', '');
  return {
    r: parseInt(h.slice(0, 2), 16) / 255,
    g: parseInt(h.slice(2, 4), 16) / 255,
    b: parseInt(h.slice(4, 6), 16) / 255,
  };
}

function rgbToHex(c) {
  const f = (v) => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, '0');
  return ('#' + f(c.r) + f(c.g) + f(c.b)).toUpperCase();
}

async function loadFontSafe(pairs) {
  for (const p of pairs) {
    try { await figma.loadFontAsync(p); return p; } catch (e) { /* try next */ }
  }
  return null;
}

function solid(hexOrRgb) {
  return [{ type: 'SOLID', color: typeof hexOrRgb === 'string' ? hexToRgb(hexOrRgb) : hexOrRgb }];
}

/* ---------------- style binding ---------------- */

function bindPaints(node, styleByHex) {
  try {
    if ('fills' in node && Array.isArray(node.fills) && node.fills.length === 1 && node.fills[0] && node.fills[0].type === 'SOLID') {
      const st = styleByHex[rgbToHex(node.fills[0].color)];
      if (st) node.paintStyleId = st.id;
    }
  } catch (e) { /* some nodes reject style binding — ignore */ }
  if (node.children) for (const c of node.children) bindPaints(c, styleByHex);
}

/* ---------------- text helper ---------------- */

function addText(parent, opts) {
  const t = figma.createText();
  t.fontName = opts.font;
  t.fontSize = opts.size;
  t.characters = opts.chars;
  if (opts.fill) t.fills = solid(opts.fill);
  if (opts.lineHeight) t.lineHeight = { unit: 'PIXELS', value: opts.lineHeight };
  if (opts.letterSpacing) t.letterSpacing = { unit: 'PIXELS', value: opts.letterSpacing };
  if (opts.textAlign) t.textAlignHorizontal = opts.textAlign;
  t.textAutoResize = 'HEIGHT';
  t.x = opts.x; t.y = opts.y;
  t.resizeWithoutConstraints(opts.w, t.height);
  t.name = opts.name || ('text: ' + opts.chars.slice(0, 28));
  parent.appendChild(t);
  return t;
}

/* ---------------- page builders ---------------- */

function drawSwatches(page, styleByHex, fonts) {
  const entries = Object.entries(SCENE.tokens);
  const x0 = 0, y0 = 760, cw = 230, ch = 64;
  const title = addText(page, { x: x0, y: y0 - 40, w: 900, chars: 'PAINT STYLES — created and bound (edit once, restyle everywhere)', size: 14, font: fonts.semi, fill: '#1A2233' });
  title.name = 'swatches: title';
  entries.forEach(([name, hex], i) => {
    const x = x0 + (i % 5) * cw, y = y0 + Math.floor(i / 5) * ch;
    const r = figma.createRectangle();
    r.name = 'swatch: ' + name;
    r.x = x; r.y = y; r.cornerRadius = 8;
    r.resizeWithoutConstraints(40, 40);
    r.fills = solid(hex);
    const st = styleByHex[hex.toUpperCase()];
    if (st) r.paintStyleId = st.id;
    page.appendChild(r);
    addText(page, { x: x + 52, y: y + 6, w: 170, chars: name + '  ' + hex, size: 9, font: fonts.mono, fill: '#5B6478', name: 'swatch label: ' + name });
  });
}

function drawScript(page, script, fonts) {
  let y = 0;
  addText(page, { x: 0, y, w: 1000, chars: 'RTO TRUST LAYER — 5:00 PITCH (LOCKED)', size: 24, font: fonts.semi, fill: '#02042B', name: 'script: title' });
  y += 40;
  for (const line of script.header) {
    addText(page, { x: 0, y, w: 1000, chars: line, size: 12, font: fonts.body, fill: '#5B6478' });
    y += 18;
  }
  y += 16;
  addText(page, { x: 0, y, w: 640, chars: 'TIMING BUDGET', size: 12, font: fonts.semi, fill: '#1A2233', name: 'timing: title' });
  y += 20;
  for (const row of script.table) {
    addText(page, { x: 0, y, w: 640, chars: row, size: 10, font: fonts.mono, fill: row.startsWith('BEAT') ? '#1A2233' : '#5B6478' });
    y += 15;
  }
  y += 32;
  for (const b of script.beats) {
    const estLines = Math.ceil(b.vo.length / 92);
    const h = 118 + estLines * 17 + 24;
    const fr = figma.createFrame();
    fr.name = 'BEAT ' + b.n + ' · ' + b.time;
    fr.x = 0; fr.y = y;
    fr.fills = solid('#FFFFFF');
    fr.strokes = solid('#E3E8F0');
    fr.strokeWeight = 1;
    fr.cornerRadius = 12;
    page.appendChild(fr);
    addText(fr, { x: 24, y: 26, w: 620, chars: 'BEAT ' + b.n + ' · ' + b.time + ' · ' + b.tone.toUpperCase(), size: 11, font: fonts.monoSemi, fill: '#02042B', name: 'beat chip' });
    addText(fr, { x: 24, y: 46, w: 620, chars: b.title, size: 16, font: fonts.semi, fill: '#1A2233', name: 'beat title' });
    addText(fr, { x: 24, y: 72, w: 620, chars: 'VISUAL: ' + b.visual, size: 9, font: fonts.mono, fill: '#5B6478', name: 'beat visual' });
    addText(fr, { x: 24, y: 94, w: 620, chars: b.vo, size: 12, font: fonts.body, fill: '#1A2233', lineHeight: 17, name: 'beat vo' });
    fr.resizeWithoutConstraints(680, h);
    y += h + 48;
  }
}

function drawReadme(page, readme, fonts) {
  let y = 0;
  for (const l of readme) {
    addText(page, { x: 0, y, w: 900, chars: l.text, size: l.size, font: l.mono ? fonts.mono : (l.h ? fonts.semi : fonts.body), fill: l.color || (l.mono ? '#5B6478' : '#1A2233'), lineHeight: l.size * 1.45 });
    y += (l.size * 1.45) + (l.gap || 6);
  }
}

/* ---------------- main ---------------- */

async function main() {
  const firstPageName = SCENE.pages[0].name;
  if (figma.root.children.some((p) => p.name === firstPageName)) {
    figma.notify('RTO board already exists in this file — delete the “RTO · …” pages to rebuild.');
    figma.closePlugin();
    return;
  }

  const fonts = {
    body: await loadFontSafe([{ family: 'Inter', style: 'Regular' }, { family: 'Roboto', style: 'Regular' }]),
    semi: await loadFontSafe([{ family: 'Inter', style: 'SemiBold' }, { family: 'Roboto', style: 'Bold' }, { family: 'Roboto', style: 'Regular' }]),
    mono: await loadFontSafe([{ family: 'JetBrains Mono', style: 'Regular' }, { family: 'Roboto Mono', style: 'Regular' }, { family: 'Roboto', style: 'Regular' }]),
    monoSemi: await loadFontSafe([{ family: 'JetBrains Mono', style: 'SemiBold' }, { family: 'JetBrains Mono', style: 'Regular' }, { family: 'Roboto Mono', style: 'Medium' }, { family: 'Roboto', style: 'Regular' }]),
  };
  const fallback = fonts.body || fonts.semi || fonts.mono;
  if (!fonts.body) fonts.body = fallback;
  if (!fonts.semi) fonts.semi = fallback;
  if (!fonts.mono) fonts.mono = fallback;
  if (!fonts.monoSemi) fonts.monoSemi = fonts.mono;

  /* paint styles (skip existing names) */
  const existingPaint = new Set(figma.getPaintStyles().map((s) => s.name));
  const styleByHex = {};
  for (const [name, hex] of Object.entries(SCENE.tokens)) {
    const styleName = 'RTO/tokens/' + name;
    let ps = null;
    if (existingPaint.has(styleName)) {
      ps = figma.getPaintStyles().find((s) => s.name === styleName);
    } else {
      ps = figma.createPaintStyle();
      ps.name = styleName;
      ps.description = 'RTO Trust Layer diagram kit — matches the console (src/app/globals.css)';
      ps.paints = solid(hex);
    }
    styleByHex[hex.toUpperCase()] = ps;
  }

  /* text styles */
  const existingTS = new Set(figma.getTextStyles().map((s) => s.name));
  const mkTS = (name, font, size, lh) => {
    if (existingTS.has(name) || !font) return;
    try {
      const ts = figma.createTextStyle();
      ts.name = name; ts.fontName = font; ts.fontSize = size;
      ts.lineHeight = { unit: 'PIXELS', value: lh };
      ts.description = 'RTO Trust Layer diagram kit';
    } catch (e) { /* ignore */ }
  };
  mkTS('RTO/type/title', fonts.semi, 18, 24);
  mkTS('RTO/type/body', fonts.body, 12, 17);
  mkTS('RTO/type/sub', fonts.body, 10, 14);
  mkTS('RTO/type/chip', fonts.mono, 10, 14);

  /* pages */
  const createdPages = [];
  let figCount = 0;
  for (const page of SCENE.pages) {
    const p = figma.createPage();
    p.name = page.name;
    createdPages.push(p);
    for (const f of page.frames || []) {
      const fr = figma.createFrame();
      fr.name = f.name;
      fr.x = f.x; fr.y = f.y;
      fr.fills = solid('#FFFFFF');
      fr.cornerRadius = 0;
      p.appendChild(fr);
      fr.resizeWithoutConstraints(f.w, f.h);
      const svgNode = figma.createNodeFromSvg(f.svg);
      svgNode.name = f.name + ' · vector';
      svgNode.x = 0; svgNode.y = 0;
      fr.appendChild(svgNode);
      bindPaints(svgNode, styleByHex);
      figCount++;
    }
    if (page.swatches) drawSwatches(p, styleByHex, fonts);
    if (page.script) drawScript(p, page.script, fonts);
    if (page.readme) drawReadme(p, page.readme, fonts);
  }

  figma.currentPage = createdPages[0];
  figma.notify('RTO Trust Layer board built — ' + createdPages.length + ' pages · ' + figCount + ' figures · ' + Object.keys(SCENE.tokens).length + ' paint styles · full 5:00 script.');
  figma.closePlugin();
}

main().catch((e) => {
  figma.notify('Board build failed: ' + (e && e.message ? e.message : e));
  figma.closePlugin();
});
