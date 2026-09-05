"""Build new reusable entries from matching library titles without changing sources."""
from dataclasses import dataclass
from html import unescape
import re
import unicodedata

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout,
)

from database import Section


@dataclass(frozen=True)
class EntrySource:
    section: Section
    heading: str
    body: str

    @property
    def title(self) -> str:
        return display_title(self.heading)


def display_title(heading: str) -> str:
    text = unescape(heading).split("::", 1)[0].strip()
    text = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", text)
    return re.sub(r"\s+", " ", text.replace("**", "").replace("__", "").strip(" *_"))


def title_key(heading: str) -> str:
    return unicodedata.normalize("NFKC", display_title(heading)).casefold()


def entry_sources(sections: list[Section]) -> list[EntrySource]:
    """Recognize the app's bold role/project headings, including multi-role entries.

    A plain first line is also a title. Indented continuation lines remain with
    their bullet; bold text inside a bullet never starts another entry.
    """
    sources = []
    for section in sections:
        heading = None
        body = []
        for line in section.content.splitlines():
            explicit_heading = bool(re.match(r"^(?:\*\*.+?\*\*|__.+?__)(?:\s*::.*)?\s*$", line))
            if explicit_heading or (heading is None and line.strip() and not re.match(r"^\s*[-*+]\s+", line)):
                if heading is not None:
                    sources.append(EntrySource(section, heading, "\n".join(body).strip()))
                heading, body = line, []
            else:
                body.append(line)
        if heading is not None:
            sources.append(EntrySource(section, heading, "\n".join(body).strip()))
        elif section.content.strip():
            # Bullet-only skills/other entries use their descriptive library name.
            sources.append(EntrySource(section, section.internal_name, section.content.strip()))
    return sources


def matching_groups(sections: list[Section]) -> list[list[EntrySource]]:
    grouped = {}
    for source in entry_sources(sections):
        key = title_key(source.heading)
        if key:
            grouped.setdefault(key, []).append(source)
    return sorted((sources for sources in grouped.values() if len(sources) > 1), key=lambda sources: sources[0].title.casefold())


def merged_content(sources: list[EntrySource]) -> str:
    """Keep one title, all distinct details and bullet blocks, in source order."""
    if not sources:
        return ""
    details, bullets = [], []
    seen = set()
    for source in sources:
        blocks = []
        for line in source.body.splitlines():
            is_bullet = bool(re.match(r"^\s*[-*+]\s+", line))
            if line.strip() and line[:1].isspace() and not is_bullet and blocks and blocks[-1][0]:
                blocks[-1][1] += "\n" + line
            elif line.strip():
                blocks.append([is_bullet, line])
        for bullet, text in blocks:
            normalized = unescape(text)
            if bullet:
                normalized = re.sub(r"^\s*[-*+]\s+", "", normalized)
            # Preserve wording, punctuation, links, and numbers: no fuzzy merging.
            key = (bullet, " ".join(normalized.split()))
            if key not in seen:
                seen.add(key)
                (bullets if bullet else details).append(text)
    return "\n".join([sources[0].heading, *details, *bullets]).strip()


def merged_keywords(sources: list[EntrySource]) -> str:
    result = []
    seen = set()
    for source in sources:
        for label in source.section.labels.split(","):
            label = label.strip()
            if label and label.casefold() not in seen:
                seen.add(label.casefold())
                result.append(label)
    return ", ".join(result)


class ConsolidationDialog(QDialog):
    def __init__(self, groups: list[list[EntrySource]], parent=None, preferred_title=""):
        super().__init__(parent)
        self.groups = groups
        self.setWindowTitle("Consolidate matching titles")
        self.resize(1000, 700)
        layout = QVBoxLayout(self)
        hint = QLabel("Create a new mega entry from matching library titles. Original entries and existing CV links are preserved.")
        hint.setWordWrap(True); layout.addWidget(hint)
        self.group = QComboBox()
        for sources in groups:
            self.group.addItem(f"{sources[0].title} · {len(sources)} sources")
        layout.addWidget(self.group)
        body = QHBoxLayout(); layout.addLayout(body, 1)
        left = QVBoxLayout(); body.addLayout(left, 1)
        left.addWidget(QLabel("Include sources (uncheck any that belong to a different role)"))
        self.sources = QListWidget(); self.sources.setWordWrap(True)
        left.addWidget(self.sources)
        self.warning = QLabel(); self.warning.setWordWrap(True); left.addWidget(self.warning)
        right = QVBoxLayout(); body.addLayout(right, 2)
        form = QFormLayout()
        self.name = QLineEdit(); self.section_title = QLineEdit(); self.labels = QLineEdit()
        form.addRow("New library name", self.name)
        form.addRow("CV section", self.section_title)
        form.addRow("Keywords", self.labels)
        right.addLayout(form)
        note = QLabel("Review and edit the combined Markdown below. Changing the sources rebuilds this draft.")
        note.setWordWrap(True); right.addWidget(note)
        self.content = QPlainTextEdit(); right.addWidget(self.content)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Create mega entry")
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.group.currentIndexChanged.connect(self.load_group)
        self.sources.itemChanged.connect(self.rebuild)
        for field in (self.name, self.section_title):
            field.textChanged.connect(self.validate)
        self.content.textChanged.connect(self.validate)
        index = next((i for i, sources in enumerate(groups) if title_key(sources[0].heading) == title_key(preferred_title)), 0)
        self.group.setCurrentIndex(index)
        self.load_group()

    def load_group(self, *_args):
        self.sources.blockSignals(True)
        self.sources.clear()
        for source in self.groups[self.group.currentIndex()]:
            text = f"{source.section.internal_name}\n{source.heading}"
            if source.body:
                text += "\n" + source.body
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.sources.addItem(item)
        self.sources.blockSignals(False)
        self.rebuild()

    def selected_sources(self) -> list[EntrySource]:
        return [source for index, source in enumerate(self.groups[self.group.currentIndex()])
                if self.sources.item(index).checkState() == Qt.CheckState.Checked]

    def rebuild(self, *_args):
        sources = self.selected_sources()
        self.name.setText(f"{sources[0].title} — Mega" if sources else "")
        self.section_title.setText(sources[0].section.title if sources else "")
        self.labels.setText(merged_keywords(sources))
        self.content.setPlainText(merged_content(sources))
        self.warning.setText(
            "The draft uses the first selected source's title and dates. Check dates and organization details before creating it; all source versions are shown above."
            if sources else "Select at least two sources to consolidate."
        )
        self.validate()

    def validate(self):
        enabled = len(self.selected_sources()) >= 2 and all((self.name.text().strip(), self.section_title.text().strip(), self.content.toPlainText().strip()))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(enabled)

    def accept(self):
        self.validate()
        if self.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled():
            super().accept()
