"""Page-like rich editing for a CV's supported Markdown content."""
from __future__ import annotations

import re
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QFont,
    QKeySequence,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextOption,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from database import CV, DEFAULT_PROFILE


BLOCK_KIND_PROPERTY = int(QTextFormat.Property.UserProperty) + 1
ENTRY_START_PROPERTY = BLOCK_KIND_PROPERTY + 1
ENTRY_TITLE_PROPERTY = BLOCK_KIND_PROPERTY + 2
ENTRY_HEADING_VISIBLE_PROPERTY = BLOCK_KIND_PROPERTY + 3
BLOCK_SECTION = "section"
BLOCK_BULLET = "bullet"
BLOCK_SPLIT = "split"
BLOCK_TEXT = "text"

_INLINE_MARKDOWN = re.compile(
    r"\*\*\*\[([^\]]+)]\((https?://[^)\s]+)\)\*\*\*"
    r"|\*\*\[([^\]]+)]\((https?://[^)\s]+)\)\*\*"
    r"|\*\*\*(.+?)\*\*\*"
    r"|\*\*(.+?)\*\*"
    r"|\*\[([^\]]+)]\((https?://[^)\s]+)\)\*"
    r"|\[([^\]]+)]\((https?://[^)\s]+)\)"
    r"|\*([^*\n]+?)\*"
)


def sections_markdown(sections: list[dict]) -> str:
    """Render editable Markdown with one heading per persisted entry.

    Repeated adjacent headings are intentional editor-only boundaries. The
    existing final Markdown/PDF renderer still groups them into one heading.
    """
    parts: list[str] = []
    for section in sections:
        title = section.get("title", "").strip()
        parts.append(f"## {title}")
        parts.extend([section.get("content", "").strip(), ""])
    return "\n".join(parts).strip() + "\n"


def _category_for_title(title: str, original_sections: list[dict]) -> str:
    for section in original_sections:
        if section.get("title", "").strip().casefold() == title.casefold():
            return section.get("category", "Other")
    normalized = title.casefold()
    if "skill" in normalized:
        return "Skills"
    if "experience" in normalized or "employment" in normalized or "work" in normalized:
        return "Experience"
    if "project" in normalized:
        return "Projects"
    if "education" in normalized:
        return "Education"
    if "profile" in normalized or "summary" in normalized:
        return "Profile"
    return "Other"


def parse_sections_markdown(source: str, original_sections: list[dict]) -> list[dict]:
    """Parse the editor's constrained Markdown back into CV section snapshots."""
    if not source.strip():
        raise ValueError("A CV needs at least one section.")

    parsed: list[dict] = []
    title: str | None = None
    content: list[str] = []

    def finish_section() -> None:
        nonlocal content
        if title is None:
            return
        section_content = "\n".join(content).strip()
        if not section_content:
            raise ValueError(f'“{title}” needs some content.')
        parsed.append({
            "title": title,
            "category": _category_for_title(title, original_sections),
            "content": section_content,
        })
        content = []

    for line in source.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            finish_section()
            title = heading.group(1).strip()
            if not title:
                raise ValueError("Section headings cannot be empty.")
            continue
        if re.match(r"^#(?:\s|$)", line):
            raise ValueError("Use Personal Details to edit the CV header. Sections use ## headings.")
        if title is None:
            if line.strip():
                raise ValueError("CV content must begin with a ## section heading.")
            continue
        content.append(line.rstrip())

    finish_section()
    if not parsed:
        raise ValueError("A CV needs at least one ## section heading.")

    if original_sections and len(parsed) != len(original_sections):
        raise ValueError(
            "Document editor changes cannot add or remove CV entries. "
            "Use Edit CV or Tree View for structural changes."
        )

    normalized_source = source.strip()
    if normalized_source == sections_markdown(original_sections).strip():
        return deepcopy(original_sections)

    # Document editing changes text, not entry identity. Carry all library-link
    # metadata positionally so formatting or wording edits never unlink an
    # existing section.
    for edited, original in zip(parsed, original_sections):
        edited.update({
            key: value for key, value in original.items()
            if key not in {"title", "category", "content"}
        })
        edited["category"] = original.get("category", edited["category"])
    return parsed


def _inline_parts(markdown: str):
    """Yield text and supported formatting from one Markdown line."""
    position = 0
    for match in _INLINE_MARKDOWN.finditer(markdown):
        if match.start() > position:
            yield markdown[position:match.start()], False, False, ""
        groups = match.groups()
        if groups[0] is not None:
            yield groups[0], True, True, groups[1]
        elif groups[2] is not None:
            yield groups[2], True, False, groups[3]
        elif groups[4] is not None:
            yield groups[4], True, True, ""
        elif groups[5] is not None:
            yield groups[5], True, False, ""
        elif groups[6] is not None:
            yield groups[6], False, True, groups[7]
        elif groups[8] is not None:
            yield groups[8], False, False, groups[9]
        else:
            yield groups[10], False, True, ""
        position = match.end()
    if position < len(markdown):
        yield markdown[position:], False, False, ""


class RichMarkdownEdit(QTextEdit):
    """Edit the supported CV Markdown as a page-like rich document."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        self.document().setDefaultFont(QFont("Times New Roman", 11))

    @staticmethod
    def _block_format(kind: str) -> QTextBlockFormat:
        block_format = QTextBlockFormat()
        block_format.setProperty(BLOCK_KIND_PROPERTY, kind)
        block_format.setLineHeight(
            109.0,
            QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
        )
        if kind == BLOCK_SECTION:
            block_format.setTopMargin(10)
            block_format.setBottomMargin(7)
        elif kind == BLOCK_BULLET:
            block_format.setLeftMargin(9)
        elif kind == BLOCK_SPLIT:
            tab = QTextOption.Tab()
            tab.position = 590
            tab.type = QTextOption.TabType.RightTab
            block_format.setTabPositions([tab])
        return block_format

    @staticmethod
    def _character_format(*, bold=False, italic=False, href="", section=False) -> QTextCharFormat:
        character_format = QTextCharFormat()
        character_format.setFontFamilies(["Times New Roman"])
        character_format.setFontPointSize(12 if section else 11)
        character_format.setFontWeight(QFont.Weight.Bold if bold or section else QFont.Weight.Normal)
        character_format.setFontItalic(italic)
        if section:
            character_format.setFontCapitalization(QFont.Capitalization.AllUppercase)
            character_format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
        if href:
            character_format.setAnchor(True)
            character_format.setAnchorHref(href)
            character_format.setForeground(Qt.GlobalColor.blue)
            character_format.setFontUnderline(True)
        return character_format

    def _insert_inline(self, cursor: QTextCursor, markdown: str, *, section=False) -> None:
        for text, bold, italic, href in _inline_parts(markdown):
            cursor.insertText(
                text,
                self._character_format(
                    bold=bold,
                    italic=italic,
                    href=href,
                    section=section,
                ),
            )

    def set_section_markdown(self, source: str) -> None:
        document = QTextDocument(self)
        document.setDocumentMargin(0)
        document.setDefaultFont(QFont("Times New Roman", 11))
        cursor = QTextCursor(document)
        first = True

        previous_title = None
        pending_entry_title = None
        pending_heading_visible = False
        for raw_line in source.strip().splitlines():
            if raw_line.startswith("## "):
                title = raw_line[3:].strip()
                pending_entry_title = title
                pending_heading_visible = title != previous_title
                previous_title = title
                if not pending_heading_visible:
                    continue
                kind, content = BLOCK_SECTION, title
            elif raw_line.startswith(("- ", "* ")):
                kind, content = BLOCK_BULLET, raw_line[2:]
            elif " :: " in raw_line:
                kind, content = BLOCK_SPLIT, raw_line.replace(" :: ", "\t", 1)
            else:
                kind, content = BLOCK_TEXT, raw_line
            if not first:
                cursor.insertBlock()
            first = False
            block_format = self._block_format(kind)
            if kind != BLOCK_SECTION and pending_entry_title is not None:
                block_format.setProperty(ENTRY_START_PROPERTY, True)
                block_format.setProperty(ENTRY_TITLE_PROPERTY, pending_entry_title)
                block_format.setProperty(
                    ENTRY_HEADING_VISIBLE_PROPERTY, pending_heading_visible
                )
                pending_entry_title = None
            cursor.setBlockFormat(block_format)
            if kind == BLOCK_BULLET:
                cursor.insertText("• ", self._character_format())
            self._insert_inline(cursor, content, section=kind == BLOCK_SECTION)

        self.setDocument(document)

    @staticmethod
    def _fragment_markdown(block, skip: int = 0, *, preserve_formatting: bool = True) -> str:
        result: list[str] = []
        remaining_skip = skip
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            iterator += 1
            if not fragment.isValid():
                continue
            text = fragment.text()
            if remaining_skip:
                removed = min(remaining_skip, len(text))
                text = text[removed:]
                remaining_skip -= removed
            if not text:
                continue
            character_format = fragment.charFormat()
            if not preserve_formatting:
                result.append(text)
                continue
            href = character_format.anchorHref() if character_format.isAnchor() else ""
            if href:
                text = f"[{text}]({href})"
            bold = character_format.fontWeight() >= QFont.Weight.Bold
            italic = character_format.fontItalic()
            if bold and italic:
                text = f"***{text}***"
            elif bold:
                text = f"**{text}**"
            elif italic:
                text = f"*{text}*"
            result.append(text)
        return "".join(result)

    def to_section_markdown(self) -> str:
        lines: list[str] = []
        block = self.document().begin()
        while block.isValid():
            kind = block.blockFormat().property(BLOCK_KIND_PROPERTY) or BLOCK_TEXT
            entry_starts = bool(block.blockFormat().property(ENTRY_START_PROPERTY))
            heading_visible = bool(
                block.blockFormat().property(ENTRY_HEADING_VISIBLE_PROPERTY)
            )
            if entry_starts and not heading_visible:
                lines.append(
                    f"## {block.blockFormat().property(ENTRY_TITLE_PROPERTY)}"
                )
            skip = 2 if kind == BLOCK_BULLET and block.text().startswith("• ") else 0
            content = self._fragment_markdown(
                block,
                skip=skip,
                preserve_formatting=kind != BLOCK_SECTION,
            ).strip()
            if kind == BLOCK_SECTION:
                lines.append(f"## {content}")
            elif kind == BLOCK_BULLET:
                lines.append(f"- {content}")
            elif kind == BLOCK_SPLIT:
                lines.append(content.replace("\t", " :: ", 1))
            else:
                lines.append(content)
            block = block.next()
        return "\n".join(lines).strip() + "\n"

    def keyPressEvent(self, event) -> None:
        if event.key() not in {Qt.Key.Key_Enter, Qt.Key.Key_Return}:
            super().keyPressEvent(event)
            return
        kind = self.textCursor().blockFormat().property(BLOCK_KIND_PROPERTY) or BLOCK_TEXT
        super().keyPressEvent(event)
        cursor = self.textCursor()
        if kind == BLOCK_BULLET:
            cursor.setBlockFormat(self._block_format(BLOCK_BULLET))
            cursor.insertText("• ", self._character_format())
        else:
            cursor.setBlockFormat(self._block_format(BLOCK_TEXT))
            cursor.setCharFormat(self._character_format())
        self.setTextCursor(cursor)

    def toggle_bold(self) -> None:
        character_format = QTextCharFormat()
        is_bold = self.currentCharFormat().fontWeight() >= QFont.Weight.Bold
        character_format.setFontWeight(QFont.Weight.Normal if is_bold else QFont.Weight.Bold)
        self.mergeCurrentCharFormat(character_format)

    def toggle_italic(self) -> None:
        character_format = QTextCharFormat()
        character_format.setFontItalic(not self.currentCharFormat().fontItalic())
        self.mergeCurrentCharFormat(character_format)

    def toggle_bullet(self) -> None:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        block = cursor.block()
        block_cursor = QTextCursor(block)
        block_cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        kind = block.blockFormat().property(BLOCK_KIND_PROPERTY) or BLOCK_TEXT
        if kind == BLOCK_BULLET:
            if block.text().startswith("• "):
                block_cursor.deleteChar()
                block_cursor.deleteChar()
            block_cursor.setBlockFormat(self._block_format(BLOCK_TEXT))
        else:
            block_cursor.insertText("• ", self._character_format())
            block_cursor.setBlockFormat(self._block_format(BLOCK_BULLET))
        cursor.endEditBlock()


class CVDocumentDialog(QDialog):
    """Synchronize a visual document editor with its Markdown source."""

    def __init__(self, cv: CV, parent=None):
        super().__init__(parent)
        self.cv = cv
        self._sections: list[dict] | None = None
        self._switching_tabs = False
        self.setWindowTitle(f"Edit document · {cv.name}")
        self.resize(960, 820)

        layout = QVBoxLayout(self)
        heading = QLabel("Edit CV document")
        heading.setProperty("pageTitle", True)
        layout.addWidget(heading)
        explanation = QLabel(
            "Document View approximates the final layout while editing the same supported "
            "Markdown shown in Markdown Source. The existing PDF exporter remains the final authority."
        )
        explanation.setWordWrap(True)
        explanation.setProperty("muted", True)
        layout.addWidget(explanation)

        self.tabs = QTabWidget()
        self.document_tab = self._document_page()
        self.markdown_tab = self._markdown_page()
        self.tabs.addTab(self.document_tab, "Document View")
        self.tabs.addTab(self.markdown_tab, "Markdown Source")
        self.tabs.currentChanged.connect(self._change_tab)
        layout.addWidget(self.tabs, 1)

        note = QLabel(
            "Text edits keep every existing Entry Library link. Personal details remain "
            "managed from Personal Details."
        )
        note.setProperty("muted", True)
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save && generate PDF")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        source = sections_markdown(cv.sections)
        self.document_editor.set_section_markdown(source)
        self.markdown_editor.setPlainText(source)

    def _document_page(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        for text, tooltip, shortcut, action in (
            ("Undo", "Undo", QKeySequence.StandardKey.Undo, self._undo),
            ("Redo", "Redo", QKeySequence.StandardKey.Redo, self._redo),
            ("B", "Bold", QKeySequence.StandardKey.Bold, self._bold),
            ("I", "Italic", QKeySequence.StandardKey.Italic, self._italic),
            ("• List", "Toggle bullet", None, self._bullet),
        ):
            button = QPushButton(text)
            button.setProperty("secondary", True)
            button.setToolTip(tooltip)
            if shortcut is not None:
                button.setShortcut(QKeySequence(shortcut))
            button.clicked.connect(action)
            toolbar.addWidget(button)
        toolbar.addStretch()
        toolbar.addWidget(QLabel("Approximate editing view · PDF output is unchanged"))
        layout.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: #e2e8f0; border: 0; }")
        surround = QWidget()
        surround_layout = QHBoxLayout(surround)
        surround_layout.setContentsMargins(24, 24, 24, 24)
        surround_layout.addStretch()
        page = QFrame()
        page.setFixedWidth(760)
        page.setMinimumHeight(984)
        page.setStyleSheet("QFrame { background: white; border: 1px solid #cbd5e1; }")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(45, 14, 47, 22)
        page_layout.setSpacing(3)

        profile = DEFAULT_PROFILE | self.cv.profile
        name = QLabel(profile["name"].upper())
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setStyleSheet(
            "border: 0; font-family: 'Times New Roman'; font-size: 24pt; font-weight: bold;"
        )
        page_layout.addWidget(name)
        contact = " | ".join(
            profile[key]
            for key in ("phone", "email", "github", "website", "linkedin")
            if profile[key]
        )
        contact_label = QLabel(contact)
        contact_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        contact_label.setWordWrap(True)
        contact_label.setStyleSheet(
            "border: 0; font-family: 'Times New Roman'; font-size: 10pt;"
        )
        page_layout.addWidget(contact_label)

        self.document_editor = RichMarkdownEdit()
        self.document_editor.setStyleSheet(
            "QTextEdit { border: 0; background: white; color: black; padding: 0; }"
        )
        self.document_editor.setMinimumHeight(760)
        self.document_editor.document().documentLayout().documentSizeChanged.connect(
            lambda size: self.document_editor.setMinimumHeight(max(760, int(size.height()) + 20))
        )
        page_layout.addWidget(self.document_editor)
        page_layout.addStretch()
        surround_layout.addWidget(page)
        surround_layout.addStretch()
        scroll.setWidget(surround)
        layout.addWidget(scroll, 1)
        return tab

    def _markdown_page(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        hint = QLabel(
            "Edit CV sections with ## headings, **bold**, *italic*, links, - bullets, "
            "and left :: right rows. Repeated adjacent headings preserve linked-entry "
            "boundaries and appear only once in the final PDF. The name and contact line "
            "are added from Personal Details."
        )
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        layout.addWidget(hint)
        self.markdown_editor = QPlainTextEdit()
        self.markdown_editor.setStyleSheet(
            "QPlainTextEdit { font-family: Menlo, Monaco, monospace; font-size: 13px; padding: 14px; }"
        )
        layout.addWidget(self.markdown_editor, 1)
        return tab

    def _undo(self) -> None:
        self.document_editor.undo()

    def _redo(self) -> None:
        self.document_editor.redo()

    def _bold(self) -> None:
        self.document_editor.toggle_bold()

    def _italic(self) -> None:
        self.document_editor.toggle_italic()

    def _bullet(self) -> None:
        self.document_editor.toggle_bullet()

    def _change_tab(self, index: int) -> None:
        if self._switching_tabs:
            return
        if self.tabs.widget(index) is self.markdown_tab:
            self.markdown_editor.setPlainText(self.document_editor.to_section_markdown())
            return
        try:
            source = self.markdown_editor.toPlainText()
            parse_sections_markdown(source, self.cv.sections)
            self.document_editor.set_section_markdown(source)
        except ValueError as error:
            QMessageBox.warning(self, "Markdown needs attention", str(error))
            self._switching_tabs = True
            self.tabs.setCurrentWidget(self.markdown_tab)
            self._switching_tabs = False

    def edited_sections(self) -> list[dict]:
        if self._sections is None:
            raise RuntimeError("The document editor has not been accepted.")
        return self._sections

    def accept(self) -> None:
        if self.tabs.currentWidget() is self.document_tab:
            self.markdown_editor.setPlainText(self.document_editor.to_section_markdown())
        try:
            self._sections = parse_sections_markdown(
                self.markdown_editor.toPlainText(), self.cv.sections
            )
        except ValueError as error:
            QMessageBox.warning(self, "Markdown needs attention", str(error))
            self.tabs.setCurrentWidget(self.markdown_tab)
            return
        super().accept()
