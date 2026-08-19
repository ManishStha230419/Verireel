"""Create a compact, reproducible PDF summary for an analysis job."""

from __future__ import annotations

from html import escape
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
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


INK = colors.HexColor("#152034")
MUTED = colors.HexColor("#5C687A")
ACCENT = colors.HexColor("#5B55D6")
PALE = colors.HexColor("#F1F0FF")
LINE = colors.HexColor("#D9DEEA")


def build_pdf_report(result: dict[str, Any]) -> BytesIO:
    """Return a ready-to-send PDF report in memory."""
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=17 * mm,
        title="VeriReel video similarity report",
        author="VeriReel comparison tool",
    )
    styles = _styles()
    report = result["report"]
    similarity = result["similarity"]
    story = [
        Paragraph("VERIREEL VIDEO REVIEW", styles["eyebrow"]),
        Paragraph("Video Similarity Review Report", styles["title"]),
        Paragraph(
            "Transparent perceptual hashing and content fingerprinting",
            styles["subtitle"],
        ),
        Spacer(1, 6 * mm),
        _summary_table(similarity, report, styles),
        Spacer(1, 6 * mm),
        Paragraph("Evidence summary", styles["heading"]),
        Paragraph(report["analysis"].replace("\n\n", "<br/><br/>"), styles["body"]),
        Spacer(1, 5 * mm),
        Paragraph("Signal breakdown", styles["heading"]),
        _metrics_table(similarity, styles),
        Spacer(1, 5 * mm),
        Paragraph("Source metadata", styles["heading"]),
        _source_table(result["video1"], result["video2"], styles),
        Spacer(1, 5 * mm),
        Paragraph("Security and evidence handling", styles["heading"]),
        _security_table(result.get("security") or {}, styles),
        Spacer(1, 5 * mm),
    ]

    checklist = [Paragraph("Human review checklist", styles["heading"])]
    for action in report["action_steps"]:
        checklist.extend(
            [
                Paragraph(
                    f"{action['step']}. {action['action']}",
                    styles["step_title"],
                ),
                Paragraph(action["description"], styles["step_body"]),
                Spacer(1, 2.5 * mm),
            ]
        )
    story.append(KeepTogether(checklist))

    story.extend(
        [
            Spacer(1, 3 * mm),
            Paragraph("Limitations", styles["heading"]),
            *[
                Paragraph(f"- {limitation}", styles["bullet"])
                for limitation in report["limitations"]
            ],
            Spacer(1, 4 * mm),
            Paragraph(report["legal_notice"], styles["notice"]),
            Spacer(1, 3 * mm),
            Paragraph(
                f"Generated: {report['generated_at']} | Base weights: "
                "45% structure, 25% rhythm, 20% colour, 10% movement; supporting signals are structure-gated",
                styles["footer"],
            ),
        ]
    )

    document.build(story)
    buffer.seek(0)
    return buffer


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "eyebrow": ParagraphStyle(
            "Eyebrow",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=ACCENT,
            spaceAfter=3,
            tracking=1.1,
        ),
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=23,
            leading=27,
            textColor=INK,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
        "heading": ParagraphStyle(
            "Heading",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=INK,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=13.5,
            textColor=INK,
        ),
        "cell": ParagraphStyle(
            "Cell",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.4,
            leading=11,
            textColor=INK,
        ),
        "cell_bold": ParagraphStyle(
            "CellBold",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.4,
            leading=11,
            textColor=INK,
        ),
        "step_title": ParagraphStyle(
            "StepTitle",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9.2,
            leading=12,
            textColor=INK,
            spaceAfter=2,
        ),
        "step_body": ParagraphStyle(
            "StepBody",
            parent=base["BodyText"],
            fontSize=8.8,
            leading=12.5,
            leftIndent=12,
            textColor=MUTED,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            parent=base["BodyText"],
            fontSize=8.7,
            leading=12.5,
            leftIndent=10,
            firstLineIndent=-7,
            textColor=MUTED,
            spaceAfter=2,
        ),
        "notice": ParagraphStyle(
            "Notice",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.6,
            leading=12.5,
            borderColor=ACCENT,
            borderWidth=0.8,
            borderPadding=8,
            backColor=PALE,
            textColor=INK,
        ),
        "footer": ParagraphStyle(
            "Footer",
            parent=base["BodyText"],
            fontSize=7.5,
            leading=10,
            textColor=MUTED,
        ),
    }


def _summary_table(
    similarity: dict[str, Any],
    report: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Table:
    data = [
        [
            Paragraph("SIMILARITY SCORE", styles["cell_bold"]),
            Paragraph("REVIEW OUTCOME", styles["cell_bold"]),
            Paragraph("THRESHOLD", styles["cell_bold"]),
        ],
        [
            Paragraph(f"<b>{similarity['overall']:.1f}%</b>", styles["cell"]),
            Paragraph(report["verdict_text"], styles["cell"]),
            Paragraph(f"{report['decision_threshold']:.1f}%", styles["cell"]),
        ],
    ]
    table = Table(data, colWidths=[42 * mm, 89 * mm, 28 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), PALE),
                ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _metrics_table(
    similarity: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Table:
    rows = [
        ("Visual structure", similarity["perceptual"], "45%"),
        ("Editing rhythm", similarity["temporal"], "25%"),
        ("Colour profile", similarity["color"], "20%"),
        ("Movement pattern", similarity["motion"], "10%"),
        ("Supporting-evidence gate", similarity.get("support_gate", 0.0), "Control"),
        ("pHash / wHash / dHash / aHash (adjusted)", f"{similarity['phash']:.1f} / {similarity['whash']:.1f} / {similarity['dhash']:.1f} / {similarity['ahash']:.1f}%", "Detail"),
    ]
    data = [[Paragraph("Signal", styles["cell_bold"]), Paragraph("Score", styles["cell_bold"]), Paragraph("Fusion weight", styles["cell_bold"])]]
    for label, value, weight in rows:
        formatted = f"{value:.1f}%" if isinstance(value, (int, float)) else str(value)
        data.append([Paragraph(label, styles["cell"]), Paragraph(formatted, styles["cell"]), Paragraph(weight, styles["cell"])])
    table = Table(data, colWidths=[77 * mm, 50 * mm, 32 * mm], repeatRows=1)
    table.setStyle(_standard_table_style())
    return table


def _source_table(
    video1: dict[str, Any],
    video2: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Table:
    def details(meta: dict[str, Any]) -> str:
        resolution = meta.get("resolution") or {}
        dimensions = f"{resolution.get('width', 0)}x{resolution.get('height', 0)}"
        title = escape(str(meta.get("title") or "Video"))
        published = escape(str(meta.get("upload_date") or "Not provided"))
        platform = escape(str(meta.get("platform") or "Local upload"))
        author = escape(str(meta.get("author") or "Not provided"))
        codec = escape(str(meta.get("codec") or "unknown"))
        raw_digest = str(meta.get("sha256") or "Not recorded")
        digest = escape(" ".join(raw_digest[index:index + 16] for index in range(0, len(raw_digest), 16)))
        return (
            f"<b>{title}</b><br/>"
            f"Source: {platform} | Creator: {author}<br/>"
            f"Published: {published}<br/>"
            f"Duration: {float(meta.get('duration') or 0):.2f}s | Resolution: {dimensions}<br/>"
            f"Codec: {codec} | Sampled: {meta.get('sampled_frames', 0)} frames<br/>"
            f"SHA-256: {digest}"
        )

    data = [
        [Paragraph("Video 1", styles["cell_bold"]), Paragraph("Video 2", styles["cell_bold"])],
        [Paragraph(details(video1), styles["cell"]), Paragraph(details(video2), styles["cell"])],
    ]
    table = Table(data, colWidths=[79.5 * mm, 79.5 * mm])
    table.setStyle(_standard_table_style())
    return table


def _security_table(
    security: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Table:
    labels = (
        ("Job access", "job_access"),
        ("Input validation", "input_validation"),
        ("Integrity", "integrity"),
        ("Retention", "retention"),
        ("Deletion timestamp", "media_deleted_at"),
    )
    data = [[Paragraph("Control", styles["cell_bold"]), Paragraph("Recorded evidence", styles["cell_bold"])]]
    for label, key in labels:
        value = escape(str(security.get(key) or "Not recorded"))
        data.append([Paragraph(label, styles["cell"]), Paragraph(value, styles["cell"])])
    table = Table(data, colWidths=[43 * mm, 116 * mm], repeatRows=1)
    table.setStyle(_standard_table_style())
    return table


def _standard_table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), PALE),
            ("BOX", (0, 0), (-1, -1), 0.7, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]
    )
