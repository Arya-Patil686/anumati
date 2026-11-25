"""Fill the K! Hacks online-submission template for Team HyperNova.

The template is edited in place rather than rebuilt: its title bars, footer logos and
background art are the organisers' and must survive. For each slide we replace the
"Type Here" box with real content and drop a diagram into the empty area below it.
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

HERE = Path(__file__).parent
IMG = HERE / "img"
TEMPLATE = HERE / "template.pptx"
OUT = HERE / "HyperNova_ANUMATI_KHacks4.pptx"

LIME = RGBColor(0xB4, 0xFF, 0x39)
CHALK = RGBColor(0xF3, 0xF6, 0xF2)
MUTE = RGBColor(0xB4, 0xBE, 0xB6)
FAINT = RGBColor(0x8A, 0x95, 0x8C)
AMBER = RGBColor(0xFF, 0xC5, 0x3D)

SLIDE_W = Inches(13.333)

REPO = "github.com/Arya-Patil686/anumati"
LIVE = "arya-patil686.github.io/anumati"


def find_type_here(slide):
    """The template's content placeholder, by its text."""
    for shape in slide.shapes:
        if shape.has_text_frame and "Type Here" in shape.text_frame.text:
            return shape
    return None


def write_body(slide, blocks, *, left=0.55, top=1.28, width=12.2, height=1.9):
    """Replace the 'Type Here' box with real paragraphs.

    `blocks` is a list of (text, size_pt, colour, bold, space_after_pt).
    """
    shape = find_type_here(slide)
    if shape is None:
        raise RuntimeError("no content placeholder on this slide")

    shape.left, shape.top = Inches(left), Inches(top)
    shape.width, shape.height = Inches(width), Inches(height)

    tf = shape.text_frame
    tf.word_wrap = True
    for p in list(tf.paragraphs[1:]):
        p._element.getparent().remove(p._element)

    first = tf.paragraphs[0]
    for i, (text, size, colour, bold, after) in enumerate(blocks):
        para = first if i == 0 else tf.add_paragraph()
        for r in list(para.runs):
            r._r.getparent().remove(r._r)
        run = para.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = colour
        run.font.name = "Calibri"
        para.space_after = Pt(after)
    return shape


def add_image(slide, name, *, left, top, width):
    path = IMG / f"{name}.png"
    pic = slide.shapes.add_picture(str(path), Inches(left), Inches(top), width=Inches(width))
    return pic


def textbox(slide, text_runs, *, left, top, width, height, align_center=False):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    for i, (text, size, colour, bold) in enumerate(text_runs):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = para.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = colour
        run.font.name = "Calibri"
        para.space_after = Pt(3)
        if align_center:
            from pptx.enum.text import PP_ALIGN

            para.alignment = PP_ALIGN.CENTER
    return box


def main() -> None:
    prs = Presentation(str(TEMPLATE))
    s = prs.slides

    # ---------------------------------------------------------------- 1 cover
    cover = s[0]
    for shape in cover.shapes:
        if not shape.has_text_frame:
            continue
        tf = shape.text_frame
        if "Team Name" not in tf.text:
            continue
        # Label and placeholder share one run ("Team Name  :  Type Here"), so keep the
        # label in place and append the value as its own run, styled.
        values = ["HyperNova", "Vishwakarma Institute of Technology, Pune", "FinTech"]
        idx = 0
        for para in tf.paragraphs:
            for run in list(para.runs):
                if "Type Here" not in run.text or idx >= len(values):
                    continue
                run.text = run.text.replace("Type Here", "")
                value = para.add_run()
                value.text = values[idx]
                value.font.size = run.font.size
                value.font.name = run.font.name
                value.font.bold = True
                value.font.color.rgb = LIME
                idx += 1

    textbox(
        cover,
        [
            ("ANUMATI", 30, LIME, True),
            ("A conformance and dispute-evidence engine for AI-agent payments", 14, CHALK, False),
            ("anumati — permission (Hindi/Sanskrit)", 11, FAINT, False),
        ],
        left=0.72, top=4.78, width=9.0, height=1.3,
    )
    live = cover.shapes.add_textbox(Inches(0.72), Inches(5.88), Inches(10.0), Inches(0.5))
    live.text_frame.word_wrap = True
    para = live.text_frame.paragraphs[0]
    for text, colour, bold in [
        ("Live  ", LIME, True),
        (LIVE, CHALK, False),
        ("      Code  ", LIME, True),
        (REPO, MUTE, False),
    ]:
        run = para.add_run()
        run.text = text
        run.font.size = Pt(11)
        run.font.bold = bold
        run.font.color.rgb = colour
        run.font.name = "Calibri"

    # ---------------------------------------------------------------- 2 problem
    write_body(
        s[1],
        [
            ("AI agents can now pay on a user's behalf. AP2, Visa Intelligent Commerce and "
             "Mastercard Agent Pay prove what the user said, and NPCI is reportedly preparing "
             "a Unified Agent Protocol for UPI.", 14, CHALK, False, 5),
            ("But in unattended purchases the agent builds the cart itself. Today's checks "
             "verify signatures, expiry and price caps — never whether the cart means what "
             "was asked for. Disputes then file under existing codes, with no record of "
             "whether the agent stayed inside its mandate.", 14, MUTE, False, 0),
        ],
        top=1.22, height=1.55,
    )
    add_image(s[1], "problem", left=0.55, top=2.58, width=12.2)

    # ---------------------------------------------------------------- 3 solution
    write_body(
        s[2],
        [
            ("ANUMATI checks every agent-built cart against the signed instruction before "
             "checkout, and returns a verdict with a reason for every constraint.", 14, CHALK, False, 5),
            ("The instruction becomes a typed mandate quoting the user's own words, read back "
             "in plain English and signed. Three verdicts, not two — the third is the "
             "contribution.", 14, MUTE, False, 0),
        ],
        top=1.22, height=1.5,
    )
    add_image(s[2], "verdicts", left=0.55, top=2.50, width=12.2)

    # ---------------------------------------------------------------- 4 architecture
    # The title wraps to two lines here, so the slide carries the diagram alone and the
    # stack line is drawn inside it.
    shape = find_type_here(s[3])
    shape.text_frame.paragraphs[0].runs[0].text = ""
    add_image(s[3], "architecture", left=0.55, top=1.72, width=12.2)

    # ---------------------------------------------------------------- 5 feasibility / USP
    write_body(
        s[4],
        [
            ("Measured, not asserted. We built MCB-IN, a 1,902-case benchmark, and ran four "
             "systems against it. Gold labels come from how each case was built — never from "
             "any model's output, which a test enforces.", 13.5, CHALK, False, 0),
        ],
        top=1.82, height=0.6,
    )
    add_image(s[4], "results", left=1.17, top=2.45, width=11.0)

    # ---------------------------------------------------------------- 6 market / business
    write_body(
        s[5],
        [
            ("Agent payments in India are expected to start with high-frequency purchases "
             "like groceries — exactly where wrong-item disputes pile up. NPCI demonstrated "
             "an agentic-payments pilot at Global Fintech Fest 2025 with Razorpay, Axis Bank "
             "and Airtel Payments Bank.", 13, CHALK, False, 0),
        ],
        top=1.82, height=0.62,
    )
    add_image(s[5], "business", left=0.87, top=2.40, width=11.6)

    # ---------------------------------------------------------------- 7 research
    write_body(
        s[6],
        [
            ("Protocols and standards", 14, LIME, True, 2),
            ("Google Agent Payments Protocol (AP2) — ap2-protocol.org · reference "
             "implementation github.com/google-agentic-commerce/AP2    |    Visa Intelligent "
             "Commerce — investor.visa.com    |    Mastercard Agent Pay", 12, MUTE, False, 9),

            ("India context", 14, LIME, True, 2),
            ("NPCI Unified Agent Protocol for UPI — reported by Reuters via Inc42, Free Press "
             "Journal and Outlook Business; NPCI has not formally confirmed it, so we say "
             "\"reportedly\". Agentic-payments pilot shown at Global Fintech Fest 2025 with "
             "Razorpay, Axis Bank and Airtel Payments Bank.    |    RBI FREE-AI Committee "
             "Report, 13 Aug 2025 — recommendations on AI governance, audit and sandboxes.",
             12, MUTE, False, 9),

            ("The dispute gap", 14, LIME, True, 2),
            ("chargeback.io on agentic payments and chargebacks · Chargebacks911 warning of a "
             "dispute-risk gap (The Paypers). Disputed agent purchases file under existing "
             "codes — most often Visa 10.4, 13.1, 13.2, 13.3 — and nothing labels a "
             "transaction as agent-made.", 12, MUTE, False, 9),

            ("Methods", 14, LIME, True, 2),
            ("Fleiss' kappa for inter-annotator agreement on the ambiguous set; Expected "
             "Calibration Error for confidence; macro-F1 over three classes. Anti-circularity "
             "rules: held-out operators and categories, labels never from the engine's own "
             "LLM path.", 12, MUTE, False, 0),
        ],
        top=1.18, height=5.5,
    )

    # ---------------------------------------------------------------- 8 team
    team = s[7]
    rows = [
        ("Team Leader · Engine & Evaluation", "Arya Patil",
         "B.Tech Computer Engineering", "3rd", "VIT Pune"),
        ("Agent, Catalogue & Data", "Ananya Choudhari",
         "B.Tech Computer Engineering", "3rd", "VIT Pune"),
        ("Backend, Ledger & Disputes", "Aarav Kulkarni",
         "B.Tech Computer Engineering", "3rd", "VIT Pune"),
    ]

    table = next(sh.table for sh in team.shapes if sh.has_table)
    for r, values in enumerate(rows, start=1):
        for c, value in enumerate(values):
            cell = table.cell(r, c)
            para = cell.text_frame.paragraphs[0]
            for run in list(para.runs):
                run._r.getparent().remove(run._r)
            run = para.add_run()
            run.text = value
            run.font.size = Pt(11.5)
            run.font.name = "Calibri"
            run.font.bold = c == 1
            run.font.color.rgb = CHALK if c in (0, 1) else MUTE

    # The template has four body rows and we are three. Remove the spares rather than
    # leave empty bordered rows under the team.
    while len(table.rows) > len(rows) + 1:
        tr = table.rows[len(table.rows) - 1]._tr
        tr.getparent().remove(tr)

    # The table runs to about 5.45"; the footer logos start at about 6.4". One compact
    # line fits between them.
    box = team.shapes.add_textbox(Inches(0.62), Inches(5.48), Inches(12.1), Inches(0.80))
    tf = box.text_frame
    tf.word_wrap = True
    lines = [
        [
            ("Live console  ", 12, LIME, True),
            (LIVE, 12, CHALK, False),
            ("   — all five screens, replaying a recorded session of the real engine", 12, MUTE, False),
        ],
        [
            ("Repository  ", 12, LIME, True),
            (REPO, 12, CHALK, False),
            ("      Run it for real  ", 12, LIME, True),
            ("docker compose up", 12, CHALK, False),
            ("  → :5173 / :8000", 12, MUTE, False),
            ("      Offline  ", 12, LIME, True),
            ("make demo", 12, CHALK, False),
        ],
    ]
    for i, runs in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.space_after = Pt(2)
        for text, size, colour, bold in runs:
            run = para.add_run()
            run.text = text
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = colour
            run.font.name = "Calibri"

    prs.save(str(OUT))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
