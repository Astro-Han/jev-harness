"""Render the figures in assets/ from the numbers in RESULTS.md.

Usage: uv run --with matplotlib scripts/plot_results.py
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

INK, ON, OFF, GRID = "#14161a", "#2f6df6", "#c2c7d0", "#e7e9ee"
ASSETS = Path(__file__).resolve().parent.parent / "assets"

# task: (f2p passed on, f2p passed off, f2p total)
DEEPSWE = [
    ("meriyah", 49, 0, 49),
    ("anko", 9, 5, 9),
    ("arktype", 24, 23, 25),
    ("katex", 94, 92, 94),
    ("python-statemachine", 72, 70, 72),
    ("httpx", 121, 120, 122),
    ("expr", 79, 79, 79),
    ("fastapi", 137, 137, 137),
    ("scc", 31, 31, 31),
]


# FrontierHarness Eval v1.0, Kimi K3, same 30 tasks: (harness, pass rate, median cost per success)
FRONTIER = [
    ("codex", 0.667, 0.1243), ("dsh-creator", 0.633, 0.1194), ("claude-code", 0.633, 0.2880),
    ("pi-responses", 0.600, 0.0709), ("dsh-ptc", 0.600, 0.1370), ("dsh-standard", 0.600, 0.1201),
    ("oh-my-pi", 0.567, 0.1354), ("kimi-code", 0.567, 0.1818), ("dsh-minimal", 0.567, 0.1214),
    ("exo", 0.533, 0.0748), ("opencode", 0.500, 0.0615), ("hermes", 0.500, 0.1746),
]
OURS = [("jev-harness, Jev on", 0.833, 0.0633, ON), ("jev-harness, Jev off", 0.733, 0.0258, "#8b93a1")]


def style(ax):
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK, length=0, labelsize=11)


def f2p_chart():
    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=200)
    names = [t[0] for t in DEEPSWE]
    y = range(len(names))
    on = [100 * t[1] / t[3] for t in DEEPSWE]
    off = [100 * t[2] / t[3] for t in DEEPSWE]
    h = 0.36
    ax.barh([i + h / 2 for i in y], on, h, color=ON, label="Jev on")
    ax.barh([i - h / 2 for i in y], off, h, color=OFF, label="Jev off")
    for i, t in enumerate(DEEPSWE):
        ax.text(101, i + h / 2, f"{t[1]}/{t[3]}", va="center", fontsize=9, color=INK)
        ax.text(101, i - h / 2, f"{t[2]}/{t[3]}", va="center", fontsize=9, color="#7c8391")
    ax.set_yticks(list(y), names)
    ax.invert_yaxis()
    ax.set_xlim(0, 118)
    ax.set_xticks([0, 25, 50, 75, 100], ["0", "25", "50", "75", "100%"])
    ax.xaxis.grid(True, color=GRID, lw=1)
    ax.set_xlabel("tests the task is meant to make pass", fontsize=11, color=INK)
    ax.set_title("DeepSWE: filtered context is never worse\n6 wins, 0 losses, 3 ties (sign test p≈0.031)",
                 fontsize=14, color=INK, loc="left", pad=14)
    ax.legend(frameon=False, fontsize=11, ncol=2, loc="upper center",
              bbox_to_anchor=(0.5, -0.12))
    style(ax)
    fig.tight_layout()
    fig.savefig(ASSETS / "deepswe-f2p.png", bbox_inches="tight", facecolor="white")


def summary_chart():
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6), dpi=200)
    for ax, (title, vals, fmt, ylim) in zip(axes, [
        ("pass@1, all 30 tasks", (25, 22), "{:.0f}/30", 30),
        ("cost per passing task", (0.113, 0.127), "${:.3f}", 0.16),
    ]):
        bars = ax.bar(["Jev on", "Jev off"], vals, 0.5, color=[ON, OFF])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, fmt.format(v), ha="center",
                    va="bottom", fontsize=13, color=INK)
        ax.set_ylim(0, ylim)
        ax.set_yticks([])
        ax.set_title(title, fontsize=12, color=INK, loc="left")
        style(ax)
    fig.tight_layout()
    fig.savefig(ASSETS / "summary.png", bbox_inches="tight", facecolor="white")


def frontier_chart():
    fig, ax = plt.subplots(figsize=(9, 5.4), dpi=200)
    # points collide at equal pass rates; nudge the labels apart by hand
    nudge = {"dsh-ptc": (7, 6), "oh-my-pi": (7, 6), "claude-code": (-8, 8), "dsh-standard": (7, -9)}
    for name, p, c in FRONTIER:
        ax.scatter(c, 100 * p, s=70, color=OFF, edgecolor="#a8aeb9", zorder=3)
        dx, dy = nudge.get(name, (7, -3))
        ax.annotate(name, (c, 100 * p), xytext=(dx, dy), textcoords="offset points",
                    fontsize=9, color="#7c8391",
                    ha="right" if name == "claude-code" else "left")
    for name, p, c, col in OURS:
        ax.scatter(c, 100 * p, s=150, color=col, zorder=4, marker="D")
        ax.annotate(name, (c, 100 * p), xytext=(10, -3), textcoords="offset points",
                    fontsize=10, color=INK, weight="bold")
    ax.set_xlim(0, 0.32)
    ax.set_ylim(45, 90)
    ax.grid(True, color=GRID, lw=1)
    ax.set_xlabel("median cost per solved task (USD)", fontsize=11, color=INK)
    ax.set_ylabel("pass rate", fontsize=11, color=INK)
    ax.set_yticks([50, 60, 70, 80, 90], ["50%", "60%", "70%", "80%", "90%"])
    ax.set_title("Same 30 tasks as FrontierHarness Eval\n"
                 "grey: 12 harness configurations on Kimi K3 (360 runs)",
                 fontsize=14, color=INK, loc="left", pad=14)
    ax.text(0.005, 46.2, "Not a ranking: our arms run deepseek-flash, one run per task, and grade "
            "themselves with the same verifiers.", fontsize=8.5, color="#7c8391")
    style(ax)
    fig.tight_layout()
    fig.savefig(ASSETS / "frontier-position.png", bbox_inches="tight", facecolor="white")


if __name__ == "__main__":
    ASSETS.mkdir(exist_ok=True)
    f2p_chart()
    summary_chart()
    frontier_chart()
    print("wrote", ASSETS)
