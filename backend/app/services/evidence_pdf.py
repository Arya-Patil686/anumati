"""Render an evidence pack as a PDF a dispute analyst can file.

Plain, dense and quotable. Every claim on the page carries the ledger sequence number and
entry hash that back it, so the bank can re-verify any line against the chain rather than
taking this document's word for it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import ENGINE_VERSION
from app.services.disputes import EvidencePack

INK = colors.HexColor("#101418")
MUTED = colors.HexColor("#5a6572")
ACCENT = colors.HexColor("#3f7d4f")
ALERT = colors.HexColor("#b3261e")
RULE = colors.HexColor("#d7dce2")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontSize=20, leading=24, textColor=INK,
            alignment=TA_LEFT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontSize=10, leading=14, textColor=MUTED,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontSize=12, leading=15, textColor=INK,
            spaceBefore=12, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontSize=9.5, leading=13.5, textColor=INK,
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontSize=8, leading=11, textColor=MUTED,
        ),
        "mono": ParagraphStyle(
            "mono", parent=base["Normal"], fontName="Courier", fontSize=7.5, leading=10,
            textColor=MUTED,
        ),
    }


def _escape(text: str) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _summary_table(pack: EvidencePack, styles: dict[str, ParagraphStyle]) -> Table:
    verdict = pack.decision_on_record.value if pack.decision_on_record else "no check on record"
    rows = [
        ["Claim category", pack.scenario.replace("_", " ")],
        ["Verdict on record", verdict],
        ["Proposed allocation", pack.allocation.value.replace("_", " ")],
        ["Visa reason code", f"{pack.visa_code} — {pack.visa_label}" if pack.visa_code else "—"],
        ["Mastercard code", f"{pack.mc_code} — {pack.mc_label}" if pack.mc_code else "—"],
        [
            "Decision chain",
            "verified" if pack.chain_ok else f"FAILS at entry {pack.chain_broken_at}",
        ],
    ]
    table = Table(
        [[Paragraph(f"<b>{k}</b>", styles["body"]), Paragraph(_escape(v), styles["body"])]
         for k, v in rows],
        colWidths=[45 * mm, 120 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TEXTCOLOR", (1, 5), (1, 5), ACCENT if pack.chain_ok else ALERT),
            ]
        )
    )
    return table


def _evidence_table(pack: EvidencePack, styles: dict[str, ParagraphStyle]) -> Table:
    header = [
        Paragraph("<b>Ledger</b>", styles["small"]),
        Paragraph("<b>What is on record</b>", styles["small"]),
    ]
    rows = [header]

    for item in pack.items:
        reference = f"#{item.ledger_seq}" if item.ledger_seq is not None else "—"
        if item.entry_hash:
            reference += f"<br/><font size=6>{item.entry_hash[:12]}…</font>"

        body = f"<b>{_escape(item.headline)}</b>"
        if item.detail:
            body += f"<br/>{_escape(item.detail)}"
        rows.append([Paragraph(reference, styles["mono"]), Paragraph(body, styles["body"])])

    table = Table(rows, colWidths=[22 * mm, 143 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
                ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def render(pack: EvidencePack, path: Path) -> Path:
    """Write the pack to `path` and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    generated = datetime.now(UTC).strftime("%d %b %Y %H:%M UTC")

    document = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm, bottomMargin=18 * mm,
        title=f"ANUMATI evidence pack — {pack.dispute.dispute_id}",
        author="ANUMATI",
    )

    flow = [
        Paragraph("Dispute evidence pack", styles["title"]),
        Paragraph(
            f"{pack.dispute.dispute_id} &nbsp;·&nbsp; cart {pack.dispute.cart_id} "
            f"&nbsp;·&nbsp; generated {generated} &nbsp;·&nbsp; engine {ENGINE_VERSION}",
            styles["subtitle"],
        ),
        Spacer(1, 10),
        Paragraph("The cardholder's claim", styles["h2"]),
        Paragraph(f"<i>“{_escape(pack.claim_text)}”</i>", styles["body"]),
        Spacer(1, 6),
        Paragraph(_escape(pack.scenario_explanation.capitalize()) + ".", styles["body"]),
        Paragraph("Summary", styles["h2"]),
        _summary_table(pack, styles),
        Paragraph("What the ledger holds", styles["h2"]),
        Paragraph(
            "Each line was written before this dispute existed, and is bound to the one "
            "before it by a SHA-256 chain signed with the service's Ed25519 key.",
            styles["small"],
        ),
        Spacer(1, 6),
        _evidence_table(pack, styles),
    ]

    if pack.mandate_read_back:
        flow += [
            Paragraph("What the user agreed to", styles["h2"]),
            Paragraph(
                "<br/>".join(_escape(line) for line in pack.mandate_read_back.splitlines()),
                styles["small"],
            ),
        ]

    flow += [
        Spacer(1, 10),
        KeepTogether(
            [
                Paragraph("Limitations", styles["h2"]),
                Paragraph(_escape(pack.caveat), styles["small"]),
            ]
        ),
    ]

    document.build(flow)
    return path
