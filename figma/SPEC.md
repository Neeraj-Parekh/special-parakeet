# RTO Trust Layer — Figma Diagram Kit · SPEC

One source of truth for every figure in the Figma board. All diagrams are **code-drawn SVG** (no AI image generation, per standing rule). They import into Figma as fully editable vector + text layers via drag-drop / raw-SVG paste / `figma.createNodeFromSvg()`.

The grammar is the one decoded from the user's 11 reference mood boards:
**containment = meaning** (Region ⊃ VPC ⊃ AZ ⊃ Subnet), **dashed = logical/trust boundary, solid = physical**, **color = category, never decoration**, **orthogonal routing only**, **direction = narrative** (left → right, top → bottom), pastel zone tints ≈ 4%, white component fills, radius 8–12.

---

## 1 · Tokens (match `src/app/globals.css` console tokens — exact hex)

| Token | Hex | Usage |
|---|---|---|
| navy-950 | `#02042B` | primary / FIG chips / solid emphasis / stamps |
| blue-500 | `#2B84EB` | scoring / gateway / request-path category |
| blue-400 | `#528FF0` | secondary blue accents (rare) |
| mint-500 | `#00B878` | accept / shipped / verified / success |
| amber-500 | `#F5A623` | review / latency / "documented plan" honesty chips |
| red-500 | `#E5484D` | reject / tamper / gaps / today-pain |
| bg | `#F6F8FC` | canvas |
| card | `#FFFFFF` | component fill |
| border | `#E3E8F0` | 1px component + zone strokes |
| text | `#1A2233` | primary text |
| text-2 | `#5B6478` | secondary text / primary arrows |
| dashed | `#B9C2D0` | dashed trust/zone boundary strokes |
| async | `#94A3B8` | dashed async arrows |

## 2 · Canvas + typography

- Canvas: `#F6F8FC` (exception: `beat-01-hook.svg` uses navy-950 as the video's "black frame").
- Fonts: `Inter, Roboto, sans-serif` · mono chips `JetBrains Mono, Roboto Mono, monospace`.
- Sizes: figure title 18/600 · sub 12/400 · node title 12/600 · node sub 10/400 · chips 10 mono/500 · tiny labels 9 mono.
- 8pt grid. Component gap 24. Zone padding ≥ 16. Node default 150×84, rx 8. Zone rx 12.
- Every figure: FIG chip (solid navy pill, white mono 11) + title 18/600 + sub 12 at top-left (helper `figTitle`). Every storyboard frame: `beatHead` (BEAT chip + title + time chip amber + tone chip gray, right-aligned).

## 3 · SVG technical constraints (Figma-import safety — MANDATORY)

- **No** `<defs>`, `<marker>`, gradients, filters, `mask`, `clipPath`, `pattern`, `<use>`, CSS classes, `foreignObject`.
- Presentation **attributes only**. Arrowheads are explicit `<path>` triangles (the `arrow()` helper does this).
- Text: separate `<text>` elements; escape `& < > " '` via `esc()`. No `textLength`.
- Transforms: `translate`/`scale` only (used inside `badge()`); no rotate on text.
- Root: `<svg xmlns width height viewBox>` + `<?xml?>` decl + full-canvas bg rect.
- Opacity via `fill-opacity` / `stroke-opacity` only (imports as color alpha).

## 4 · `figma/svg-kit/lib.mjs` API

Import: `import { T, FONT, MONO, esc, txt, para, rect, zone, node, chip, chipW, arrow, badge, numchip, figTitle, beatHead, voCaption, wrap, svgDoc, save } from './lib.mjs'`

- `T` — token map above. `FONT`, `MONO` — font stacks.
- `esc(s)` — XML-escape.
- `txt({x,y,s,size=12,weight=400,fill=T.text,mono=false,anchor,spacing})` → one `<text>`.
- `para({x,y,lines,size=11,lh=15,weight,fill,mono,anchor})` → stacked lines.
- `rect({x,y,w,h,rx=8,fill,stroke,sw=1,dash,fillOpacity,strokeOpacity})`.
- `zone({x,y,w,h,label,sub,tint,dashed})` — tint fill @0.04, stroke `border` (solid) or `dashed` color + `5 4` dasharray when `dashed:true`; label = uppercase mono 10 in tint color at (x+16, y+24); optional sub 10px text-2.
- `node({x,y,w=150,h=84,title,subs=[],tint,icon,num,border,chipLabel,titleSize})` — white card rx 8, optional 36×36 `badge` (tint @0.12 + icon), title 12/600, subs 10px text-2; `num` = narration-order navy circle at top-left; `chipLabel` renders a chip **below** the node (y+h+6).
- `chip({x,y,label,tint,size=10,pad=10,h=20,anchorEnd})` + `chipW(label,{size,pad})` — pill, tint @0.08 bg, mono text in tint.
- `arrow({pts,color,lw=1.5,dash,head,label,labelAt,labelDx,labelDy})` — orthogonal polyline `[[x,y],…]`, explicit triangular head, optional 9px mono label positioned at fraction along path. Solid primary: `color=T.text2, lw=1.5`. Async: `color=T.async, lw=1.25, dash='4 4'`. Verdict branches: 2px colored, no dash.
- `badge({x,y,tint,icon})` + `icons` map — 24×24 stroke icon paths (Lucide-style, ISC), drawn at 18px inside tinted 36×36 square. Available: `shield zap cpu db globe lock users layers branch activity eye file truck banknote bank timer box key network alert check x msg server scale flask search fingerprint link`.
- `numchip({x,y,n})` — navy circle r10 + white mono number.
- `figTitle({fig,title,sub,x,y})`, `beatHead({beat,title,time,tone,canvasW})`, `voCaption({x,y,text,lines,label})` (VO excerpt, wrapped ~100 chars), `wrap(s,n)`.
- `svgDoc({w,h,bg,body,title})` → full SVG string; `save(name, svg)` writes to `figma/svgs/`.

Icons in badges use the node's **category tint**; color always = category, never decoration.

## 5 · File briefs (13 files → `figma/svgs/`)

**Hero set (built by `hero.mjs` — do not touch):** `cover.svg`, `kit-legend.svg`, `fig-01-system-context.svg`, `fig-02-scoring-pipeline.svg`, `fig-03-trust-chain.svg`, `fig-04-deployment-perf.svg`, `beat-01-hook.svg`, `beat-03-live-draw.svg`, `beat-04-trust-core.svg`.

**Storyboard set (built by `beats.mjs`):**

### beat-02-problem.svg — 1180×660
`beatHead`: BEAT 2 · "Problem — framed like a design review" · time `0:15–0:45` · tone `GAURAV SEN`.
- Banner card (48,120,1084×72, white, navy 1.5 border): line1 12/600 `The goal is not "reduce returns" — nobody can.` line2 11 text2 `The goal: at checkout, in ≤75 ms, decide whether this order is worth shipping — with a cost-optimal decision, an explanation the merchant can act on, and a receipt a regulator can verify.`
- LEFT zone (48,232,520×280, red tint, solid) label `TODAY — SHIP FIRST, DISCOVER LATER`: nodes 140×64 (icon, subs short): `Merchant ships everything` (truck) → `Courier attempts COD` (box) → `Bounce found t+14 days` (alert, red border). Arrows red. Below: 4 red chips 2×2: `double shipping`, `lost sale`, `refund fight`, `no proof of why`.
- RIGHT zone (612,232,520×280, mint) label `TARGET — SCORE AT PLACEMENT`: node (zap) `Checkout scores every order` sub `≤75 ms at placement` → 3 verdict mini-cards 96×44: ACCEPT (mint border), REVIEW+OTP (amber), REJECT (red) with small colored arrows; below chips: `cost math shown`, `explanation to act on`, `regulator-verifiable receipt`.
- `voCaption` (~y 556) with Beat 2 VO. Bottom mono 9: `was: beat2_problem.excalidraw · two columns`.

### beat-05-endpoints.svg — 1180×620
`beatHead`: BEAT 5 · "Operational backbone" · `3:05–3:30` · `BYTEBYTEGO`.
- Grid 3×2 (x=48, cols 340×110, gap 24, y=120/254):
  1. `JWT auth + replay detection` (key) sub `every token single-use`
  2. `Case management + SLA clocks` (timer) sub `verdicts become cases with deadlines`
  3. `Fraud-ring graph detector` (network) sub `linked customers, shared devices`
  4. `Courier + payment integrations` (link) subs `each with documented` / `mock → production swap`
  5. `Circuit breaker` (activity) subs `degrades to rules-only` / `never fails open`
  6. `Test suite` (check, mint tint) subs `security · validation · money` / `ML · platform` + chip `~400 tests · 5 dimensions — all green` (mint)
- `voCaption` bottom.

### beat-06-target-vs-shipped.svg — 1180×700
`beatHead`: BEAT 6 · "The pivot — what we actually built" · `3:30–4:02` (no tone chip).
- LEFT zone dashed navy (48,120,470×410) label `TARGET — RAZORPAY-SCALE DEPLOY`: five 420-wide rows (mini nodes h=52 + amber chip `documented plan`): `Kafka · 24 partitions, Avro`, `Flink CEP · pattern watch`, `Multi-AZ · leader writes, replica reads`, `Go/Rust hot path · compiled`, `kernel-bypass networking · <10 ms`.
- RIGHT zone mint (646,120,486×410) label `SHIPPED — EVERYTHING CLICKABLE`: five rows with mint check badges: `Merkle audit chain — live, tested`, `Bounded agent — 7 actions, enforced`, `Dual-control override — two keys`, `Mandatory caps — OC-201B fences` (read: `Mandate caps — OC-201B fences`), `In-process CEP — demo detection`.
- SEAM band: dashed amber zone (48,546,1084×72) label `ONE DELIBERATE SEAM` sub 11px: `Production runs Kafka + Flink. The demo runs the same detection logic on an in-process engine — Vercel can't host Kafka. Same alert, different transport.`
- `voCaption` y≈636.

### beat-07-results.svg — 1180×780
`beatHead`: BEAT 7 · "Results & honest gaps" · `4:02–4:48` · `CODEOPINION`.
- Metrics table (48,116, w=640): header band navy solid h=36 white mono 10: `DATASET · ORDERS · SPLIT · PR-AUC · ×BASE · ROC`. Two data rows h=44 white cards: row1 mono 11: `Amazon India + public · ~1.3 lakh · strict time split · 0.11 · 5.7× · 0.89`; row2 mint bg @0.06: `Olist · real, per-customer history · 0.40 · 32× · —` + mint chip `the cross-check`. Under table: navy chip row: `vs ₹50,000 crore lost to COD returns — a fraction of this signal is real money back`.
- GAPS zone dashed red (716,116,416×420) label `HONEST GAPS — BEFORE YOU ASK` sub `stated in the video, on purpose`: five rows (red alert icon + 2-line text 10px):
  1. `Console runs a deterministic mock scorer` / `Python scorer wires with one env var`
  2. `Ensemble probabilities run hot` / `calibration planned — ranking-neutral`
  3. `Merkle file-mode breaks across processes` / `Postgres mode holds · 30-min migration`
  4. `Multi-AZ + Go hot path` / `documented plans — not deployed, not claimed`
  5. `No sub-10 ms claim` / `honest Python ceiling: 45–75 ms p50`
- `voCaption` bottom (~y 700).

### beat-08-close.svg — 1180×560
`beatHead`: BEAT 8 · "Close" · `4:48–5:00` · `FIRESHIP`.
- Navy panel (140,132,900×300, rx 16, fill navy-950): centered `RTO TRUST LAYER` white 34/700 (letter-spacing 2); mono white 13 centered: `github.com/Neeraj-Parekh/special-parakeet` and `rto-trust-layer.vercel.app`; mint chip centered `repo public · console live · tests green`; white 12 centered `Try it, break it, check the audit trail.`
- Corner mono 9 chips text-2: `NO FADE`, `HARD CUT`.
- `voCaption` bottom.

## 6 · Build + verify

```bash
cd /home/z/my-project && bun figma/svg-kit/beats.mjs   # storyboard set
cd /home/z/my-project && bun figma/svg-kit/hero.mjs    # hero set
python3 -c "import xml.dom.minidom,glob;fs=glob.glob('figma/svgs/*.svg');[xml.dom.minidom.parse(f) for f in fs];print('valid xml',len(fs))"
```

All copy uses **only numbers code-verified in the locked script** (docs/deepseek-script.md). Icon paths are Lucide-style stroke geometry (ISC license).
