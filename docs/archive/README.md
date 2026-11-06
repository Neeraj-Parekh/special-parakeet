# Archived documentation

> Pre-TIER-1/2/3 planning artifacts, deep analyses, and audit reports. Kept
> for provenance — the findings in these docs were folded into the canonical
> `docs/*.md` files (the parent directory). Read these only for historical
> context on the Python project's state before the TIER 1/2/3 gap-closure
> wave.

## What's here

| Path | What it was | Superseded by |
|---|---|---|
| `command/` (12 files: `00-MASTER-PLAN.md` through `11-KAGGLE-TRAINING-PROMPT.md`) | The original planning docs from the Python project's wave-1/2/3 effort (master plan, execution sequence, current-state audit, work items, tech-stack decisions, paper-skills map, prompt-razor extraction, execution log, session snapshot, cross-verification matrix, re-scorecard, Kaggle training prompt). | `docs/ARCHITECTURE_OVERVIEW.md` (canonical 3-min read) + `docs/GAP_VERIFICATION.md` (the new verification matrix) |
| `analysis/` (3 files: `ADVERSARIAL_SECURITY_ANALYSIS.md`, `PRODUCTION_GAP_ANALYSIS.md`, `REAL_TIME_SYSTEMS_RESEARCH.md`) | Pre-TIER-1/2/3 deep analyses (101KB + 84KB + 64KB). The adversarial-security, production-gap, and real-time-systems research that informed the TIER 1/2/3 brief. | `docs/SECURITY_HARDENING.md` (STRIDE + citations) + `docs/LATENCY_ENGINEERING.md` (honest p50 ceiling + Phase 5) |
| `AUDIT_REPORT.md` (84KB) | The brutal 1-to-1 audit of all 37 Python features against 16 prompts, with `file:line` evidence. | `docs/GAP_VERIFICATION.md` (the new 18-item matrix — narrower scope but live-verified against the Next.js surface) |
| `UML_COMPREHENSIVE.md` (67KB) | 12 code-verified Mermaid diagrams, every box annotated with `%% evidence: file:line`. | `docs/ARCHITECTURE_OVERVIEW.md` §2 (ASCII system diagram, simpler + canonical) |
| `5-a-track-i-dashboard.md` | Pre-TIER-1/2/3 dashboard planning (Track I Day 3). | `docs/ARCHITECTURE_OVERVIEW.md` §3 + the live `src/app/page.tsx` (the actual dashboard) |

## How to navigate

1. Start at [`../README.md`](../README.md) — the canonical entry.
2. For "what's actually built + verified live", read [`../GAP_VERIFICATION.md`](../GAP_VERIFICATION.md).
3. For the 3-min architecture overview, read [`../ARCHITECTURE_OVERVIEW.md`](../ARCHITECTURE_OVERVIEW.md).
4. Read the archived docs here ONLY if you need the pre-TIER-1/2/3 historical context (e.g., "what did the Python project look like before the gap-closure wave?").

## Note on `agent-ctx/`

The `agent-ctx/tier2-B-multi-az-integrations.md` file is NOT archived — it's
the subagent worklog for the TIER 2 G6/G8 effort and remains in
`agent-ctx/` as live provenance for how those TIER 2 items were built.
