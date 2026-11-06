# RTO Diagram Kit

Architecture figures for RTO Trust Layer, **generated from code** — same token
set as the fintech-console-v2 re-theme, so the diagrams and the product speak
one language. No Excalidraw, no hand-drawn lines, no AI raster images: pure
deterministic vector output.

## Contents

```
design-kit/
├── generate.ts            # the single source of truth (tokens + 4 figures)
├── figma-plugin/          # dev plugin: builds token styles + component kit in Figma
│   ├── manifest.json
│   ├── code.js
│   └── README.md (this folder's usage is also in the root README below)
└── (output goes to ../public/figures/)

public/figures/
├── fig01-system-context.svg
├── fig02-scoring-pipeline.svg
├── fig03-trust-chain.svg
├── fig04-deployment.svg
├── preview.html           # open /figures/preview.html to eyeball everything
└── png/                   # 3200×2000 PNG @2x renders for the video timeline
```

## Regenerate

```bash
bun design-kit/generate.ts
```

Geometry is deterministic — same input, same output. Tweak any figure by
editing its builder (`fig01()` … `fig04()`) in `generate.ts`; tokens live at
the top of the file and **must match `src/app/globals.css`**.

## The figures

| # | Title | Family DNA borrowed |
|---|-------|---------------------|
| FIG.01 | System Context | B — gateway → services grouping, numbered flow (1·order → 2·score → 3·decision → 4·courier feedback) |
| FIG.02 | Scoring Pipeline (**hero**) | D — event → features → model → decision, with per-stage latency chips |
| FIG.03 | Trust Chain | A — Merkle tree rigor, provenance fields (`record_source`), verification paths |
| FIG.04 | Deployment & Performance | C — region ⊃ AZ ⊃ layer containment, primary/replica, latency budget rail |

Design rules baked into every figure:
- Containment = meaning: Zone (12px radius, tint fill) ⊃ Card (8px radius, white, shadow) ⊃ badge.
- Line style = semantics: solid = sync, dashed = async/logical, color = verdict (mint/amber/red).
- Color = category, never decoration. 8pt-ish grid, orthogonal arrows with 7px rounded corners.
- Typography: Inter (names 13/600, subs 11/400, zone labels 11/600 caps +8%) + JetBrains Mono (values, chips).

## Getting them into Figma — three paths

### Path A — SVG import (recommended, 2 minutes)
1. Open your Figma file → new page per figure.
2. **Drag** `fig0X-*.svg` from `public/figures/` onto the canvas
   (or File → Place image / `⌘⇧K`).
3. Figma imports every box as a rectangle/vector, every icon as vector
   paths, every label as an editable text layer. Move anything freely.
4. Optional: apply the Kit paint/text styles (Path B) via select-all →
   paint style / text style.

Notes: the SVG drop-shadow filter may not survive import — re-apply
`0 2 6 rgba(2,4,43,.08)` as a layer effect if you care. Fonts: if you don't
have Inter / JetBrains Mono installed locally, Figma falls back — install
both (free on Google Fonts) before importing for pixel-perfect text.

### Path B — dev plugin (token styles + component kit, native)
1. Figma (desktop) → **Plugins → Development → New Plugin…**
2. Pick "Figma design" → "Run once". Figma creates a folder + manifest.
3. Copy `design-kit/figma-plugin/manifest.json` and `code.js` into that
   folder — **but keep the `id` line from Figma's generated manifest**
   (replace the zero-placeholder id in ours with yours).
4. Plugins → Development → **RTO Diagram Kit** → Run.
5. You get: page `00 · Tokens & Kit` (paint styles `token/*`, `kit/tint/*`,
   text styles `text/*`, component masters: Zone solid/dashed, Card, Chip,
   Actor, 5 arrows, figure frame) + pages `01…04` with title blocks.

Edit a token hex once → every styled layer updates. That's the "single
source of truth" carried into Figma.

### Path C — HTML → Figma (html.to.design plugin)
Works (build each figure as HTML, import via the html.to.design plugin) but
diagram fidelity is worse than SVG: connectors detach, spacing shifts.
Not recommended — listed only for completeness.

## For the 10-minute video

- Use `public/figures/png/*@2x.png` (3200×2000) directly on the timeline —
  crisp at 1080p, zoomable to 200% without softening.
- Suggested beat: FIG.01 (10s context) → FIG.02 (25s hero, walk the latency
  chips) → cut to live console demo → FIG.03 (10s trust story) → FIG.04 (10s
  ops) → metrics card.
- Re-export any time: `bun design-kit/generate.ts` + the screenshot loop, or
  open the SVG in a browser at any zoom and screenshot.

## Preview

With the dev server running: open **`/figures/preview.html`** in the preview
panel (Open in New Tab → append `/figures/preview.html`). All four figures
render inline with the token strip.
