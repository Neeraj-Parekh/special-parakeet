# Charts for the RTO Trust Layer project report.
# Rules applied (typesetting/charts.md): no top/right spines, dashed grid at
# 20% opacity, palette colors only, horizontal bars for long labels,
# value labels placed outside bars, tight_layout with padding.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Cascade palette (seed 42) — chart subset
ACCENT    = "#92761f"   # series 1 (XS tier)
ACCENT_2  = "#3aa0c2"   # series 2 (XS tier, geometric harmony hue)
HEADER    = "#4e4732"   # M tier structural
MUTED     = "#7e7c74"
TEXT      = "#151513"

plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.size"] = 11

# ---------------------------------------------------------------- Chart 1
# RTO rate ranges by payment mode / source (horizontal RANGE bars)
fig, ax = plt.subplots(figsize=(7.6, 3.0), dpi=200)
rows = [
    # (label, min, max, color)
    ("Prepaid orders (industry)", 2, 8, ACCENT_2),
    ("COD — GoKwik network", 26, 27, ACCENT),
    ("COD — industry average", 28, 35, ACCENT),
    ("COD — last-mile (Shadowfax)", 35, 45, ACCENT),
]
labels = [r[0] for r in rows]
y = range(len(rows))
for i, (lab, lo, hi, c) in enumerate(rows):
    ax.barh(i, hi - lo, left=lo, height=0.52, color=c, alpha=0.85, zorder=3)
    ax.text(hi + 1.2, i, f"{lo}-{hi}%", va="center", ha="left",
            fontsize=10.5, color=TEXT, zorder=4)
ax.set_yticks(list(y))
ax.set_yticklabels(labels, fontsize=10.5, color=TEXT)
ax.set_xlim(0, 55)
ax.set_xlabel("Return-to-Origin (RTO) rate, % of orders", fontsize=10, color=MUTED)
ax.xaxis.grid(True, linestyle="--", alpha=0.20, color=HEADER, zorder=0)
ax.set_axisbelow(True)
for s in ("top", "right", "left"):
    ax.spines[s].set_visible(False)
ax.spines["bottom"].set_color(MUTED)
ax.tick_params(colors=MUTED, length=0)
fig.tight_layout(pad=2.0)
fig.savefig("/home/z/my-project/analysis/report-pdf/chart_rto_rates.png",
            bbox_inches="tight", facecolor="white")
plt.close(fig)

# ---------------------------------------------------------------- Chart 2
# Expected-cost-per-decision from the LIVE demo endpoint (BMR argmin logic)
fig, ax = plt.subplots(figsize=(7.6, 2.9), dpi=200)
decisions = ["ACCEPT\n(ship the order)", "REVIEW\n(manual check)", "REJECT\n(decline COD)"]
costs = [303, 84, 496]
colors_seq = [ACCENT_2, ACCENT, ACCENT_2]
bars = ax.bar(decisions, costs, width=0.5, color=colors_seq, alpha=0.9, zorder=3)
for rect, v in zip(bars, costs):
    ax.text(rect.get_x() + rect.get_width() / 2, v + 12, f"Rs {v}",
            ha="center", va="bottom", fontsize=11, color=TEXT, zorder=4)
# Highlight the chosen decision (argmin) with an annotation
ax.annotate("engine decision: REVIEW\n(lowest expected loss)",
            xy=(1.06, 95), xytext=(0.44, 545),
            fontsize=9.5, color=HEADER,
            arrowprops=dict(arrowstyle="->", color=HEADER, lw=1.0,
                            connectionstyle="arc3,rad=-0.18"))
ax.set_ylabel("Expected loss per order (INR)", fontsize=10, color=MUTED)
ax.set_ylim(0, 640)
ax.set_xlim(-0.55, 2.55)
ax.yaxis.grid(True, linestyle="--", alpha=0.20, color=HEADER, zorder=0)
ax.set_axisbelow(True)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
for s in ("left", "bottom"):
    ax.spines[s].set_color(MUTED)
ax.tick_params(colors=TEXT, length=0)
fig.tight_layout(pad=2.0)
fig.savefig("/home/z/my-project/analysis/report-pdf/chart_decision_cost.png",
            bbox_inches="tight", facecolor="white")
plt.close(fig)

print("charts written")
