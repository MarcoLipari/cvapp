"""Markdown and PDF export styled after the user's established CV."""
from __future__ import annotations

import html
import re
from pathlib import Path

from database import CV, DEFAULT_PROFILE


# Link destinations present in the reference CV. Explicit Markdown links still
# take precedence and are the supported way to add links to future entries.
REFERENCE_LINKS = {
    "OpenLineage": "https://github.com/OpenLineage/OpenLineage/tree/main",
    "Marquez": "https://github.com/MarquezProject/marquez",
    "Remote Desktop Control via AI agent - Example CodeJam 2025 Winner": "https://github.com/example-user/remoto",
    "Fine-tuned Transformers Model for Text Classification": "https://github.com/example-user/MAIS202-project",
}


def render_markdown(cv: CV) -> str:
    """Render a CV snapshot as editable Markdown in the personal CV format."""
    profile = DEFAULT_PROFILE | cv.profile
    contact = " | ".join(value for value in (profile["phone"], profile["email"], profile["github"], profile["website"]) if value)
    parts = [f"# {profile['name'].upper()}", contact, ""]
    for section in cv.sections:
        parts.extend([f"## {section['title']}", section["content"].strip(), ""])
    return "\n".join(parts).strip() + "\n"


def _inline(text: str) -> str:
    links: list[tuple[str, str]] = []

    def stash_link(match: re.Match[str]) -> str:
        links.append((match.group(1), match.group(2)))
        return f"@@CVLINK{len(links) - 1}@@"

    escaped = html.escape(
        re.sub(r"\[([^\]]+)]\((https?://[^)\s]+)\)", stash_link, text)
    )
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*", r"<i>\1</i>", escaped)

    for label, url in REFERENCE_LINKS.items():
        escaped_label = html.escape(label)
        escaped = escaped.replace(
            escaped_label,
            f'<a href="{html.escape(url, quote=True)}">{escaped_label}</a>',
        )
    for index, (label, url) in enumerate(links):
        escaped = escaped.replace(
            f"@@CVLINK{index}@@",
            f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>',
        )
    return escaped


def _link(url: str) -> str:
    href = url if url.startswith(("http://", "https://")) else f"https://{url}"
    return f'<a href="{html.escape(href, quote=True)}">{html.escape(url)}</a>'


def export_cv(cv: CV, output_dir: str | Path) -> tuple[Path, Path]:
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QPainter

    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", cv.name).strip("-") or "cv"
    stem = f"{safe_name}-{cv.id}"
    markdown_path, pdf_path = output / f"{stem}.md", output / f"{stem}.pdf"
    profile = DEFAULT_PROFILE | cv.profile
    markdown = render_markdown(cv); markdown_path.write_text(markdown, encoding="utf-8")
    writer = QPdfWriter(str(pdf_path))
    writer.setResolution(72)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
    painter = QPainter(writer)
    _draw_reference_layout(painter, markdown, profile)
    painter.end()
    if not pdf_path.exists():
        raise RuntimeError("Qt did not create the PDF")
    return markdown_path, pdf_path


def _draw_reference_layout(painter, markdown: str, profile: dict[str, str]) -> None:
    """Draw a CV with the measured typography and spacing of the reference PDF."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QFont, QFontMetricsF, QTextDocument

    page_width, page_height = 612.0, 792.0  # US Letter at the writer's 72dpi resolution.
    # Measured from example-userCV.pdf (all values are PDF points).
    left, right, top, bottom = 35.76, 37.92, 10.0, 17.0
    content_width = page_width - left - right

    def document(fragment: str, size: float, width: float, body_leading: bool = True) -> QTextDocument:
        doc = QTextDocument()
        # QTextDocument otherwise lays itself out against the application's
        # screen DPI. That makes rich text smaller in the native macOS app
        # than in headless previews, even though both target the same PDF.
        doc.documentLayout().setPaintDevice(painter.device())
        doc.setDocumentMargin(0)
        # ``size`` includes the exporter's 0.75 PDF density calibration. HTML
        # point sizes do not need that 96-to-72 DPI correction once the text
        # document is attached to the 72-DPI PDF device, so restore it here.
        html_size = size / 0.75
        font = QFont("Times New Roman")
        font.setPointSizeF(html_size)
        doc.setDefaultFont(font)
        doc.setDefaultStyleSheet("a { color: #0000ee; text-decoration: underline; }")
        leading = " line-height: 1.09;" if body_leading else ""
        doc.setHtml(f'<html><body style="color: #000000; font-family: Times New Roman; font-size: {html_size:.2f}pt; margin: 0; padding: 0;{leading}">{fragment}</body></html>')
        doc.setTextWidth(width)
        return doc

    def draw_document(doc: QTextDocument, x: float, y: float, draw: bool) -> float:
        height = doc.size().height()
        if draw:
            painter.save(); painter.translate(x, y)
            doc.drawContents(painter, QRectF(0, 0, doc.textWidth(), height))
            painter.restore()
        return height

    def plain_font(size: float, bold: bool = False, italic: bool = False) -> QFont:
        font = QFont("Times New Roman")
        font.setPointSizeF(size)
        font.setBold(bold); font.setItalic(italic)
        return font

    def layout(density: float, draw: bool = False) -> float:
        body = 11.33 * density
        y = top

        # Measured reference sizes: 24pt name, 10pt contact, 12pt section,
        # and 11.33pt body/metadata.
        # QPdfWriter emits these direct bold glyphs at 75% of the requested
        # size, so use the calibrated values that yield the reference PDF's
        # 24pt title and 12pt section labels.
        painter.setFont(plain_font(32.0 * density, bold=True))
        title_metrics = QFontMetricsF(painter.font())
        title = profile["name"].upper()
        if draw:
            title_x = left + (content_width - title_metrics.horizontalAdvance(title)) / 2
            painter.drawText(QPointF(title_x, y + title_metrics.ascent()), title)
        y += title_metrics.height() + 1.0 * density

        contact_items = []
        for key in ("phone", "email", "github", "website"):
            value = profile[key]
            if value:
                contact_items.append(_link(value) if key in {"github", "website"} else html.escape(value))
        contact_doc = document(f'<div align="center">{" | ".join(contact_items)}</div>', 10.0 * density, content_width, body_leading=False)
        y += draw_document(contact_doc, left, y, draw) + 5.0 * density

        lines = markdown.splitlines()[3:]
        has_entry_in_section = False
        section_count = 0
        for index, line in enumerate(lines):
            previous_line = lines[index - 1] if index else ""
            next_line = lines[index + 1] if index + 1 < len(lines) else ""
            if line.startswith("## "):
                if section_count:
                    y += 4.0 * density
                section_count += 1
                has_entry_in_section = False
                y += 2.7 * density
                painter.setFont(plain_font(15.43 * density, bold=True))
                metrics = QFontMetricsF(painter.font())
                if draw:
                    painter.drawText(QPointF(left, y + metrics.ascent()), line[3:].upper())
                    painter.drawLine(QPointF(left, y + metrics.height() + 1.5 * density), QPointF(left + content_width, y + metrics.height() + 1.5 * density))
                y += metrics.height() + (9.0 if next_line and " :: " not in next_line else 12.5) * density
            elif not line:
                y += 1.5 * density
            elif " :: " in line:
                left_text, right_text = line.split(" :: ", 1)
                is_entry_title = left_text.lstrip().startswith("**")
                if is_entry_title and has_entry_in_section:
                    # Separate consecutive roles/projects without adding a
                    # gap between an entry's title and its organisation line.
                    y += (8.0 if previous_line.startswith(("- ", "* ")) else 5.12) * density
                has_entry_in_section = has_entry_in_section or is_entry_title
                left_fragment = _inline(left_text)
                left_doc = document(left_fragment, body, content_width * 0.72)
                right_doc = document(
                    f'<div align="right">{_inline(right_text)}</div>',
                    body,
                    content_width * 0.28,
                )
                height = max(left_doc.size().height(), right_doc.size().height())
                if draw:
                    draw_document(left_doc, left, y, True)
                    painter.save(); painter.translate(left + content_width * 0.72, y)
                    right_doc.drawContents(painter, QRectF(0, 0, right_doc.textWidth(), height))
                    painter.restore()
                y += height - 0.52 * density
                if next_line.startswith(("- ", "* ")):
                    y += 6.0 * density
            elif line.startswith("- ") or line.startswith("* "):
                bullet_doc = document(_inline(line[2:]), body, content_width - 9.0 * density)
                painter.setFont(plain_font(body))
                metrics = QFontMetricsF(painter.font())
                if draw:
                    painter.drawText(QPointF(left, y + metrics.ascent()), "•")
                y += draw_document(bullet_doc, left + 8.5 * density, y, draw)
            else:
                body_doc = document(_inline(line), body, content_width)
                y += draw_document(body_doc, left, y, draw)
        return y

    # Qt lays out HTML point sizes at 96 dpi while the PDF uses 72 dpi. A
    # 0.75 scale therefore reproduces the source document's physical type
    # sizes (24 pt name, 10 pt contact, 12 pt headings, 11.33 pt body). Only
    # shrink denser tailored variants when the reference size would overflow.
    low, high, best = 0.58, 0.75, 0.58
    if layout(low) > page_height - bottom:
        raise ValueError("This CV cannot fit legibly on one page. Remove or shorten a section, then export again.")
    for _ in range(10):
        candidate = (low + high) / 2
        if layout(candidate) <= page_height - bottom:
            best, low = candidate, candidate
        else:
            high = candidate
    painter.fillRect(QRectF(0, 0, page_width, page_height), Qt.GlobalColor.white)
    painter.setPen(Qt.GlobalColor.black)
    layout(best, draw=True)
