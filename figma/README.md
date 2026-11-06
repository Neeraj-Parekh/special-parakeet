# RTO Trust Layer — Figma Board Kit

The full competition board — **14 code-drawn figures + the locked 5:00 pitch script** — built from one source of truth: the console design tokens. No AI images, no screenshots. Everything imports into Figma as **fully editable vector + text layers**.

Built with the reference-image grammar decoded from your 11 mood boards: containment = meaning, dashed = trust boundary, color = category, orthogonal routing, direction = narrative, pastel 4% zone tints, white components, radius 8/12.

---

## What's in here

```
figma/
├── SPEC.md                     ← the visual grammar + per-figure briefs
├── svgs/                       ← 14 figures (the canonical source)
│   ├── cover.svg               board cover + token swatches
│   ├── kit-legend.svg          the grammar, documented
│   ├── fig-01-system-context.svg
│   ├── fig-02-scoring-pipeline.svg     ← the hero (draw order 1–8 printed on it)
│   ├── fig-03-trust-chain.svg          ← Merkle / dual-control / bounded copilot
│   ├── fig-04-deployment-perf.svg      ← multi-AZ + honest perf + demo seam
│   └── beat-01…beat-08         ← storyboard frames for every script beat
├── svg-kit/
│   ├── lib.mjs                 ← the drawing kit (tokens, zones, nodes, arrows, chips)
│   ├── hero.mjs                ← builds the 9 hero figures
│   └── beats.mjs               ← builds the 5 storyboard frames
└── plugin/
    ├── manifest.json           ← Figma plugin manifest
    ├── plugin.template.js      ← board builder logic
    ├── build-plugin.mjs        ← assembles code.js (embeds all SVGs + script)
    └── code.js                 ← READY TO RUN — import this folder into Figma
```

Rebuild any figure: `bun figma/svg-kit/hero.mjs && bun figma/svg-kit/beats.mjs && bun figma/plugin/build-plugin.mjs`

---

## Path A — the one-run plugin (builds the ENTIRE board)

1. Open **Figma desktop** (dev plugins need the desktop app).
2. Create a new design file (or open any file you want the board added to).
3. Menu → **Plugins → Development → Import plugin from manifest…**
4. Select **`figma/plugin/manifest.json`** from this repo.
5. Menu → Plugins → Development → **RTO Trust Layer — Board Builder** → Run.

You get, in one run:

| Page | Contents |
|---|---|
| `RTO · 00 Cover` | cover figure |
| `RTO · 01 Tokens & kit` | grammar legend + **13 bound paint styles** + 4 text styles |
| `RTO · 02 Figures` | FIG.01–04 |
| `RTO · 03 Storyboards` | beats 1–8 |
| `RTO · 04 Script` | the **entire 5:00 pitch script**, beat by beat — time chip, tone, VISUAL note, full VO — plus the timing-budget table |
| `RTO · 05 How to import` | this README, inside the file |

Idempotent: running it twice just notifies. Deleting the `RTO · …` pages lets you rebuild clean.

## Path B — no desktop app (browser Figma)

Open any SVG in `figma/svgs/`, **copy the raw source, paste onto the Figma canvas** (or drag-drop the file). Each figure imports as editable layers. Create the 6 pages manually if you want the full structure.

## Path C — optional

The `html.to.design` community plugin imports hosted HTML pages; for this kit paths A/B are higher fidelity (SVG → vector layers, exact tokens).

---

## The Figma API token — what it can and cannot do

Verified live with the provided PAT (`GET /v1/me` → authenticated; write probes `POST /v1/files`, `POST /v1/projects/:id/files`, `PATCH /v1/files/:key` → **all 404**):

- The **REST API is read-only** for file content, no matter the token's scopes. It cannot create files, pages, frames or nodes. (It *can* read files/images, and write comments + webhooks.)
- **Writing requires the Plugin API**, which runs *inside* Figma — that's why Path A is a one-run plugin rather than a pure-API push. Nothing was written to Figma's servers by any generator script here.
- If you paste a **file key** (from a Figma URL) into a future session, I can pin the build checklist onto that file as REST comments.
- **Rotate/revoke the PAT** at figma.com → Settings → Security → Personal access tokens when you're done with it — it was shared in chat.

---

## Notes for the video

- **FIG.02 carries the live-draw order** — numbered chips 1–8 match Beat 3's narration exactly; draw one box per sentence.
- **Beat frames = the .excalidraw frames in the script, redesigned** — same names (`beat2_problem`, `vision_vs_shipped`, …), so the script's `[VISUAL]` notes map 1:1.
- Beat 4 includes a **4-keyframe MANIM storyboard** for `MerkleProofScene` (t=0/4/8/12s).
- Every number on every figure is code-verified against `docs/deepseek-script.md`.
- The same 14 figures are viewable in the browser on the deployed site at `/diagrams/<name>.svg`.
