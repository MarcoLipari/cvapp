"""Markdown and PDF export styled after the user's established CV."""
from __future__ import annotations

import html
import re
from collections import Counter
from pathlib import Path

from database import CV, DEFAULT_PROFILE


class CVOverflowError(ValueError):
    """Raised when a CV needs more than one page at the reference sizes."""


def export_stem(cv: CV) -> str:
    """Return the unique name used to keep one CV's exports together."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", cv.name).strip("-") or "cv"
    return f"{safe_name}-{cv.id}"


def pdf_filename(cv: CV) -> str:
    """Return the professional, user-facing filename for a CV PDF."""
    profile = DEFAULT_PROFILE | cv.profile
    person_name = re.sub(r"[^A-Za-z0-9]+", "", profile["name"])
    return f"{person_name}CV.pdf" if person_name else "CV.pdf"


def render_markdown(cv: CV) -> str:
    """Render a CV snapshot as editable Markdown in the personal CV format."""
    profile = DEFAULT_PROFILE | cv.profile
    contact = " | ".join(
        value
        for value in (
            profile["phone"],
            profile["email"],
            profile["github"],
            profile["linkedin"],
            profile["website"],
        )
        if value
    )
    parts = [f"# {profile['name'].upper()}", contact, ""]
    previous_title = None
    for section in cv.sections:
        title = section["title"]
        if title != previous_title:
            parts.append(f"## {title}")
        parts.extend([section["content"].strip(), ""])
        previous_title = title
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

    for index, (label, url) in enumerate(links):
        escaped = escaped.replace(
            f"@@CVLINK{index}@@",
            f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>',
        )
    return escaped


def _link(url: str) -> str:
    href = url if url.startswith(("http://", "https://")) else f"https://{url}"
    return f'<a href="{html.escape(href, quote=True)}">{html.escape(url)}</a>'


def export_cv(
    cv: CV,
    output_dir: str | Path,
    *,
    allow_multipage: bool = False,
    shrink_to_fit: bool = False,
) -> tuple[Path, Path]:
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QPainter

    if allow_multipage and shrink_to_fit:
        raise ValueError("Choose either multipage output or shrink-to-fit, not both")

    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    profile = DEFAULT_PROFILE | cv.profile
    file_stem = export_stem(cv)
    markdown_path = output / f"{file_stem}.md"
    pdf_dir = output / file_stem; pdf_dir.mkdir(exist_ok=True)
    pdf_path = pdf_dir / pdf_filename(cv)
    pending_pdf_path = pdf_dir / f".{pdf_path.stem}.pending.pdf"
    markdown = render_markdown(cv); markdown_path.write_text(markdown, encoding="utf-8")
    writer = QPdfWriter(str(pending_pdf_path))
    # Keep the file broadly compatible and make its purpose unambiguous to
    # document-management systems before they inspect the page contents.
    writer.setPdfVersion(QPdfWriter.PdfVersion.PdfVersion_1_4)
    writer.setTitle(pdf_path.stem)
    writer.setResolution(72)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
    painter = QPainter(writer)
    try:
        _draw_reference_layout(
            painter,
            markdown,
            profile,
            allow_multipage=allow_multipage,
            shrink_to_fit=shrink_to_fit,
        )
        painter.end()
        if not pending_pdf_path.exists():
            raise RuntimeError("Qt did not create the PDF")
        _validate_ats_text_layer(pending_pdf_path, cv, profile)
        pending_pdf_path.replace(pdf_path)
    except Exception:
        if painter.isActive():
            painter.end()
        pending_pdf_path.unlink(missing_ok=True)
        raise
    return markdown_path, pdf_path


def _plain_markdown(text: str) -> str:
    """Return the words an ATS should see after the supported Markdown renders."""
    text = re.sub(r"\[([^\]]+)]\(https?://[^)\s]+\)", r"\1", text)
    return text.replace("**", "").replace("*", "").replace(" :: ", " ")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)?", text.casefold(), flags=re.UNICODE)


def _validate_ats_text_layer(pdf_path: Path, cv: CV, profile: dict[str, str]) -> None:
    """Refuse an export whose searchable text no longer represents the CV."""
    from PySide6.QtPdf import QPdfDocument

    document = QPdfDocument()
    error = document.load(str(pdf_path))
    if error != QPdfDocument.Error.None_ or document.pageCount() < 1:
        raise RuntimeError("The exported PDF is not a readable document")

    extracted = "\n".join(
        document.getAllText(page).text()
        for page in range(document.pageCount())
    )
    normalized_extracted = " ".join(_tokens(extracted))
    required_fields = [profile["name"]]
    required_fields.extend(value for value in profile.values() if value)
    required_fields.extend(section["title"] for section in cv.sections if section.get("title"))
    missing = [
        value for value in required_fields
        if " ".join(_tokens(value)) not in normalized_extracted
    ]
    if missing:
        raise RuntimeError(f"The exported PDF text layer is missing: {', '.join(missing)}")

    expected_section_text = []
    previous_title = None
    for section in cv.sections:
        title = section.get("title", "")
        if title != previous_title:
            expected_section_text.append(title)
        expected_section_text.append(_plain_markdown(section.get("content", "")))
        previous_title = title
    expected_text = " ".join(list(profile.values()) + expected_section_text)
    expected_counts = Counter(_tokens(expected_text))
    extracted_counts = Counter(_tokens(extracted))
    matched_words = sum(min(count, extracted_counts[word]) for word, count in expected_counts.items())
    expected_words = sum(expected_counts.values())
    if expected_words and matched_words / expected_words < 0.95:
        raise RuntimeError("The exported PDF text layer does not contain enough of the CV content")


def _draw_reference_layout(
    painter,
    markdown: str,
    profile: dict[str, str],
    *,
    allow_multipage: bool = False,
    shrink_to_fit: bool = False,
) -> None:
    """Draw a CV with the measured typography and spacing of the reference PDF."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QFont, QFontMetricsF, QTextDocument

    page_width, page_height = 612.0, 792.0  # US Letter at the writer's 72dpi resolution.
    # Measured from the reference CV (all values are PDF points).
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

    def layout(density: float, draw: bool = False, paginate: bool = False) -> float:
        body = 11.00 * density
        y = top

        def ensure_space(height: float) -> None:
            nonlocal y
            if not paginate or y + height <= page_height - bottom:
                return
            if draw:
                if not painter.device().newPage():
                    raise RuntimeError("Qt could not add another PDF page")
                painter.fillRect(
                    QRectF(0, 0, page_width, page_height),
                    Qt.GlobalColor.white,
                )
                painter.setPen(Qt.GlobalColor.black)
            y = top

        # Reference sizes: 24pt name, 10pt contact, 12pt section,
        # and 11pt body/metadata.
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
        for key in ("phone", "email", "github", "linkedin", "website"):
            value = profile[key]
            if value:
                contact_items.append(
                    _link(value)
                    if key in {"github", "linkedin", "website"}
                    else html.escape(value)
                )
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
                trailing_space = (9.0 if next_line and " :: " not in next_line else 12.5) * density
                ensure_space(metrics.height() + trailing_space)
                if draw:
                    painter.drawText(QPointF(left, y + metrics.ascent()), line[3:].upper())
                    painter.drawLine(QPointF(left, y + metrics.height() + 1.5 * density), QPointF(left + content_width, y + metrics.height() + 1.5 * density))
                y += metrics.height() + trailing_space
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
                ensure_space(height)
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
                ensure_space(bullet_doc.size().height())
                painter.setFont(plain_font(body))
                metrics = QFontMetricsF(painter.font())
                if draw:
                    painter.drawText(QPointF(left, y + metrics.ascent()), "•")
                y += draw_document(bullet_doc, left + 8.5 * density, y, draw)
            else:
                body_doc = document(_inline(line), body, content_width)
                ensure_space(body_doc.size().height())
                y += draw_document(body_doc, left, y, draw)
        return y

    # Qt lays out HTML point sizes at 96 dpi while the PDF uses 72 dpi. This
    # fixed density reproduces the source document's physical type sizes
    # (24 pt name, 10 pt contact, 12 pt headings, 11 pt body). Never shrink
    # an overfull CV, because that would make formatting vary between exports.
    reference_density = 0.75
    density = reference_density
    overflows = layout(reference_density) > page_height - bottom
    if overflows and shrink_to_fit:
        low, high = 0.1, reference_density
        if layout(low) > page_height - bottom:
            raise ValueError("This CV has too much content to shrink onto one page")
        for _ in range(12):
            candidate = (low + high) / 2
            if layout(candidate) <= page_height - bottom:
                density, low = candidate, candidate
            else:
                high = candidate
    elif overflows and not allow_multipage:
        raise CVOverflowError(
            "This CV cannot fit on one page at the standard reference sizes."
        )
    painter.fillRect(QRectF(0, 0, page_width, page_height), Qt.GlobalColor.white)
    painter.setPen(Qt.GlobalColor.black)
    layout(density, draw=True, paginate=allow_multipage)
