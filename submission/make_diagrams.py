"""Diagrams for the K! Hacks submission deck.

Transparent PNGs so the template's own dark gradient shows through. The palette is the
product's: one lime accent, and the three verdict colours used only where they mean
something.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

LIME = "#B4FF39"
CHALK = "#F3F6F2"
MUTE = "#9AA69A"
FAINT = "#6E7A70"
PANEL = "#141A16"
EDGE = "#2C352E"
CONFORM = "#4ADE80"
VIOLATE = "#FF5F52"
AMBIG = "#FFC53D"

DPI = 200
FONT = "DejaVu Sans"


def canvas(w: float, h: float):
    fig = plt.figure(figsize=(w, h), dpi=DPI)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100 * h / w)
    ax.axis("off")
    fig.patch.set_alpha(0)
    return fig, ax


def card(ax, x, y, w, h, *, fill=PANEL, edge=EDGE, lw=1.4, alpha=0.96, r=1.8):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle=f"round,pad=0,rounding_size={r}",
            facecolor=fill, edgecolor=edge, linewidth=lw, alpha=alpha, zorder=2,
        )
    )


def text(ax, x, y, s, *, size=9, color=CHALK, weight="normal", ha="left", va="center",
         style="normal", zorder=4, wrap_w=None):
    ax.text(x, y, s, fontsize=size, color=color, fontweight=weight, ha=ha, va=va,
            fontfamily=FONT, fontstyle=style, zorder=zorder,
            wrap=bool(wrap_w), linespacing=1.45)


def arrow(ax, x1, y1, x2, y2, *, color=LIME, lw=2.0, style="-|>", ms=12, zorder=3):
    ax.add_patch(
        FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=ms,
                        color=color, linewidth=lw, zorder=zorder,
                        shrinkA=0, shrinkB=0)
    )


def save(fig, name):
    path = f"/Users/arya/Projects/anumati/submission/img/{name}.png"
    fig.savefig(path, transparent=True, dpi=DPI, bbox_inches=None)
    plt.close(fig)
    print("wrote", path)


# ---------------------------------------------------------------- 1. the gap
def diagram_problem():
    fig, ax = canvas(12.2, 3.70)
    H = 100 * 3.70 / 12.2

    # left: what is checked today
    card(ax, 0, 5.4, 46, H - 5.9, fill="#141A16")
    text(ax, 3, H - 3.4, "WHAT TODAY'S CHECKS VERIFY", size=10, color=MUTE, weight="bold")
    today = [
        "Signature on the mandate is valid",
        "The mandate has not expired",
        "Total is under the price cap",
        "Merchant is on the allowed list",
    ]
    for i, item in enumerate(today):
        y = H - 7.6 - i * 4.0
        ax.add_patch(mpatches.Circle((5.2, y), 1.25, color=CONFORM, zorder=3))
        text(ax, 5.2, y - 0.1, "✓", size=9, color="#0B0D0B", weight="bold", ha="center")
        text(ax, 8.5, y, item, size=10.5, color=CHALK)
    text(ax, 3, 7.3, "AP2 · Visa Intelligent Commerce · Mastercard Agent Pay",
         size=8.5, color=FAINT, style="italic")

    # right: what nobody checks
    card(ax, 54, 5.4, 46, H - 5.9, fill="#1C1416", edge="#43282A")
    text(ax, 57, H - 3.4, "WHAT NOBODY CHECKS", size=10, color=VIOLATE, weight="bold")
    gap = [
        "Is this the item that was asked for?",
        "Is a subscription the same as \"buy once\"?",
        "Does 4.9 kg satisfy \"5 kg\"?",
        "Did the agent stay inside its mandate?",
    ]
    for i, item in enumerate(gap):
        y = H - 7.6 - i * 4.0
        ax.add_patch(mpatches.Circle((59.2, y), 1.25, color=VIOLATE, zorder=3))
        text(ax, 59.2, y - 0.1, "✕", size=8.5, color="#0B0D0B", weight="bold", ha="center")
        text(ax, 62.5, y, item, size=10.5, color=CHALK)
    text(ax, 57, 7.3, "No agent-specific dispute code exists today",
         size=8.5, color=FAINT, style="italic")

    arrow(ax, 47.5, H / 2, 52.5, H / 2, color=LIME, lw=2.4, ms=16)

    # the worked example
    card(ax, 0, 0, 100, 4.0, fill="#0E1410", edge=EDGE)
    text(ax, 3, 2.0, "User says:", size=9.5, color=MUTE, weight="bold")
    text(ax, 11.5, 2.0, "\"white running shoes under ₹5,000\"", size=10, color=CHALK)
    text(ax, 46, 2.0, "→", size=12, color=LIME, weight="bold")
    text(ax, 50, 2.0, "Agent buys:", size=9.5, color=MUTE, weight="bold")
    text(ax, 60, 2.0, "a ₹4,999 treadmill", size=10, color=VIOLATE, weight="bold")
    text(ax, 74.5, 2.0, "Every numeric check passes.", size=10, color=AMBIG, style="italic")

    save(fig, "problem")


# ---------------------------------------------------------------- 2. verdicts
def diagram_solution():
    fig, ax = canvas(12.2, 3.70)
    H = 100 * 3.70 / 12.2

    specs = [
        (CONFORM, "CONFORMS", "Every hard constraint met\nabove threshold.",
         "The agent pays.", "white shoes · ₹4,499 · 2 days"),
        (VIOLATE, "VIOLATES", "At least one hard constraint\nclearly broken.",
         "Blocked. Reasons go back\nto the agent, which retries.",
         "a subscription plan, not one pair"),
        (AMBIG, "AMBIGUOUS", "No clear breach, but a shopper\nwould hesitate.",
         "One tap from the user.\nThe answer is logged.",
         "off-white · ₹4,799 + ₹99 delivery"),
    ]
    w, gap = 31.3, 3.05
    for i, (colour, name, meaning, action, example) in enumerate(specs):
        x = i * (w + gap)
        card(ax, x, 0, w, H, fill=PANEL)
        ax.add_patch(mpatches.Circle((x + 2.8, H - 3.6), 1.05, color=colour, zorder=4))
        text(ax, x + 5.2, H - 3.6, name, size=12.5, color=colour, weight="bold")
        text(ax, x + 2.4, H - 9.6, meaning, size=10, color=CHALK, va="top")
        text(ax, x + 2.4, H - 17.5, action, size=10, color=MUTE, va="top")
        ax.plot([x + 2.4, x + w - 2.4], [5.4, 5.4], color=EDGE, lw=1, zorder=3)
        text(ax, x + 2.4, 2.9, example, size=9, color=colour, style="italic")

    save(fig, "verdicts")


# ---------------------------------------------------------------- 3. pipeline
def diagram_architecture():
    fig, ax = canvas(12.2, 4.60)
    H = 100 * 4.60 / 12.2

    steps = [
        ("01", "Mandate\nBuilder", "instruction →\nsigned mandate"),
        ("02", "Shopping\nAgent", "searches, builds\na cart"),
        ("03", "Conformance\nEngine", "cart vs mandate,\nper constraint"),
        ("04", "Act", "pay · block ·\nask the user"),
        ("05", "Decision\nLedger", "hash-chained,\nsigned"),
        ("06", "Dispute\nPack", "evidence +\nreason codes"),
    ]
    w, gap = 14.35, 2.53
    top = H - 1
    box_h = 17
    for i, (num, name, sub) in enumerate(steps):
        x = i * (w + gap)
        accent = LIME if i in (2, 4) else EDGE
        card(ax, x, top - box_h, w, box_h, fill=PANEL, edge=accent,
             lw=2.0 if i in (2, 4) else 1.3)
        text(ax, x + 1.8, top - 3.2, num, size=9, color=LIME, weight="bold")
        text(ax, x + 1.8, top - 7.6, name, size=11, color=CHALK, weight="bold", va="top")
        text(ax, x + 1.8, top - 13.2, sub, size=8.5, color=MUTE, va="top")
        if i < len(steps) - 1:
            arrow(ax, x + w + 0.3, top - box_h / 2, x + w + gap - 0.3, top - box_h / 2,
                  color=LIME, lw=1.8, ms=11)

    # engine detail
    e_top = top - box_h - 4.5
    e_h = 12.4
    card(ax, 0, e_top - e_h, 100, e_h, fill="#0E1410", edge=EDGE)
    text(ax, 2.6, e_top - 3.2, "INSIDE THE CONFORMANCE ENGINE — each layer sees only what "
         "the one before left open", size=9.5, color=LIME, weight="bold")

    layers = [
        ("L1  Deterministic rules", "signature · expiry · budget incl. tax and shipping ·\n"
         "quantity · delivery date · recurrence · merchant", "fast, no ML"),
        ("L2  Attribute grounding", "category · colour · size · material · brand ·\n"
         "dietary flag · pack size, against catalogue fields", "near-match tables"),
        ("L3  LLM judge", "only what L1 and L2 could not settle.\n"
         "Fails safe: unreachable → ask the user, never approve", "9.6% of checks"),
    ]
    lw_ = 30.6
    for i, (name, body, tag) in enumerate(layers):
        x = 2.6 + i * (lw_ + 2.0)
        text(ax, x, e_top - 7.4, name, size=10, color=CHALK, weight="bold")
        text(ax, x, e_top - 9.9, body, size=8.3, color=MUTE, va="top")
        text(ax, x + lw_ - 0.5, e_top - 7.4, tag, size=8, color=LIME, ha="right",
             style="italic")
        if i < 2:
            arrow(ax, x + lw_ + 0.2, e_top - 9.0, x + lw_ + 1.6, e_top - 9.0,
                  color=FAINT, lw=1.4, ms=9)

    text(ax, 0, 0.9,
         "Python 3.11 · FastAPI · Pydantic v2 · SQLModel/SQLite · Ed25519 ledger + ES256 "
         "mandate signing · React + Vite + Tailwind console · Docker Compose",
         size=8.6, color=FAINT)

    save(fig, "architecture")


# ---------------------------------------------------------------- 4. results
def diagram_results():
    fig = plt.figure(figsize=(12.2, 4.30), dpi=DPI)
    fig.patch.set_alpha(0)

    systems = ["B0\nnumeric", "B1\nkeyword", "B2\nsimilarity", "ANUMATI"]
    macro = [36.5, 50.0, 42.5, 97.9]
    recall = [54.5, 91.6, 98.2, 99.4]
    block = [4.3, 28.7, 73.4, 4.3]

    panels = [
        (macro, "Macro-F1 across all three verdicts", "higher is better",
         ["#3F4A41", "#3F4A41", "#3F4A41", LIME]),
        (recall, "Violation recall", "higher is better",
         ["#3F4A41", "#3F4A41", "#3F4A41", LIME]),
        (block, "False-block rate on good carts", "lower is better",
         ["#3F4A41", "#3F4A41", VIOLATE, LIME]),
    ]

    for i, (values, title, hint, bar_colours) in enumerate(panels):
        ax = fig.add_axes((0.045 + i * 0.325, 0.17, 0.265, 0.56))
        ax.patch.set_alpha(0)
        bars = ax.bar(systems, values, color=bar_colours, width=0.62, zorder=3)
        ax.set_ylim(0, 112)
        # Title sits high; the hint goes in the gap below it, not on top of it.
        ax.text(0.5, 1.165, title, transform=ax.transAxes, ha="center", va="bottom",
                color=CHALK, fontsize=10.5, fontfamily=FONT, fontweight="bold")
        ax.text(0.5, 1.045, hint, transform=ax.transAxes, ha="center", va="bottom",
                color=FAINT, fontsize=8.5, fontfamily=FONT, fontstyle="italic")
        for b, v, c in zip(bars, values, bar_colours, strict=True):
            ax.text(b.get_x() + b.get_width() / 2, v + 2.5, f"{v:.1f}%",
                    ha="center", va="bottom", fontsize=9.5, fontfamily=FONT,
                    color=LIME if c == LIME else (VIOLATE if c == VIOLATE else MUTE),
                    fontweight="bold")
        ax.tick_params(axis="x", colors=MUTE, labelsize=8.6, length=0, pad=6)
        ax.tick_params(axis="y", colors=FAINT, labelsize=8, length=0)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_yticklabels(["0", "25", "50", "75", "100%"], fontfamily=FONT)
        for side in ("top", "right", "bottom", "left"):
            ax.spines[side].set_visible(False)
        ax.grid(axis="y", color="#232B25", linewidth=0.9, zorder=0)
        ax.set_axisbelow(True)
        for label in ax.get_xticklabels():
            label.set_fontfamily(FONT)
        ax.get_xticklabels()[3].set_color(LIME)
        ax.get_xticklabels()[3].set_fontweight("bold")

    fig.text(0.045, 0.045,
             "MCB-IN test split — 647 cases whose perturbation operators and product "
             "categories were held out of tuning entirely. 1,902 cases over 505 "
             "instructions, 32.5% Hinglish.",
             color=FAINT, fontsize=8.6, fontfamily=FONT)
    save(fig, "results")


# ---------------------------------------------------------------- 5. business
def diagram_business():
    fig, ax = canvas(12.2, 4.10)
    H = 100 * 4.10 / 12.2

    # who pays, and for what
    buyers = [
        ("Payment aggregators", "Razorpay, Cashfree, PayU", "Per-check API",
         "fewer wrong-purchase\ndisputes to absorb"),
        ("Agent platforms", "shopping assistants", "Per-check API",
         "a guardrail that stops\ntheir agent buying wrong"),
        ("Issuing banks", "incl. Temenos core", "Licensed module",
         "faster dispute handling,\nevidence already on file"),
    ]
    w, gap = 31.3, 3.05
    for i, (who, examples, model, value) in enumerate(buyers):
        x = i * (w + gap)
        card(ax, x, 11, w, H - 12.5, fill=PANEL)
        text(ax, x + 2.4, H - 5.7, who, size=11.5, color=CHALK, weight="bold")
        text(ax, x + 2.4, H - 9.5, examples, size=9, color=FAINT, style="italic")
        ax.add_patch(
            FancyBboxPatch((x + 2.4, H - 16.7), 14.5, 3.6,
                           boxstyle="round,pad=0,rounding_size=1.8",
                           facecolor="#243009", edgecolor=LIME, linewidth=1.2, zorder=3)
        )
        text(ax, x + 9.65, H - 14.9, model, size=8.8, color=LIME, weight="bold",
             ha="center")
        text(ax, x + 2.4, H - 21.0, value, size=9.5, color=MUTE, va="top")

    # the unit of value
    card(ax, 0, 0, 100, 9, fill="#0E1410", edge=EDGE)
    text(ax, 3, 6.0, "THE UNIT", size=8.5, color=LIME, weight="bold")
    text(ax, 3, 2.9,
         "One check, priced per call. Value is the dispute that never happens — "
         "and when one does, an evidence pack that is already written.",
         size=10, color=CHALK)
    text(ax, 3, 0.2, "", size=8)

    save(fig, "business")


if __name__ == "__main__":
    import pathlib

    pathlib.Path("/Users/arya/Projects/anumati/submission/img").mkdir(
        parents=True, exist_ok=True
    )
    diagram_problem()
    diagram_solution()
    diagram_architecture()
    diagram_results()
    diagram_business()
