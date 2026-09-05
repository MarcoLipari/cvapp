"""CV-local tailoring helpers and a reversible bullet selector."""
from copy import deepcopy
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QLineEdit, QPushButton, QTextEdit, QVBoxLayout,
)


CONTENT_FIELDS = ("title", "category", "content")


def tailoring_snapshot(section: dict, origin: str = "Starting wording") -> dict:
    """Detach content, retaining an immutable comparison point for this variation."""
    result = deepcopy(section)
    source_id = result.pop("source_section_id", None)
    if source_id is not None:
        result["tailoring_source_id"] = source_id
    result["tailoring_base"] = {key: section.get(key, "") for key in CONTENT_FIELDS}
    result["tailoring_origin"] = origin
    return result


def is_tailored(section: dict) -> bool:
    base = section.get("tailoring_base")
    return bool(base and any(section.get(key, "") != base.get(key, "") for key in CONTENT_FIELDS))


def selected_content(rows: list[dict]) -> str:
    return "".join(row["text"] for row in rows if row["included"])


class BulletSelectionDialog(QDialog):
    """Select complete bullet blocks while preserving headings and Markdown."""

    def __init__(self, content: str, parent=None, saved_rows=None):
        super().__init__(parent)
        self.setWindowTitle("Choose bullets for this CV")
        self.resize(850, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Uncheck bullets to omit them. Double-click text to tailor wording. Move bullets within their group."))
        self.search = QLineEdit(); self.search.setPlaceholderText("Filter bullet wording by keyword…")
        self.search.textChanged.connect(self.filter_bullets); layout.addWidget(self.search)
        body = QHBoxLayout(); layout.addLayout(body)
        self.items = QListWidget(); self.items.setWordWrap(True); body.addWidget(self.items)
        self.preview = QTextEdit(); self.preview.setReadOnly(True); body.addWidget(self.preview)
        # Keep multiline bullet continuations together; never silently rewrite prose.
        blocks = []
        for line in content.splitlines(keepends=True):
            bullet = bool(re.match(r"^\s*[-*+]\s+", line))
            if not bullet and line[:1].isspace() and blocks and blocks[-1][0]:
                blocks[-1][1] += line
            else:
                blocks.append([bullet, line])
        rows = saved_rows if saved_rows and selected_content(saved_rows) == content else [
            {"bullet": bullet, "text": text, "included": True} for bullet, text in blocks
        ]
        for row in rows:
            bullet, text = row["bullet"], row["text"]
            item = QListWidgetItem(text.removesuffix("\n").removesuffix("\r"))
            item.setData(Qt.ItemDataRole.UserRole, text)
            item.setFlags((item.flags() | Qt.ItemFlag.ItemIsEditable) & ~Qt.ItemFlag.ItemIsUserCheckable)
            if bullet:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if row["included"] else Qt.CheckState.Unchecked)
            self.items.addItem(item)
        self.items.itemChanged.connect(self.refresh_preview)
        controls = QHBoxLayout(); layout.addLayout(controls)
        for caption, offset in (("Move up", -1), ("Move down", 1)):
            button = QPushButton(caption)
            button.clicked.connect(lambda _checked=False, step=offset: self.move(step))
            controls.addWidget(button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_preview()

    def rows(self) -> list[dict]:
        rows = []
        for row in range(self.items.count()):
            item = self.items.item(row)
            original = item.data(Qt.ItemDataRole.UserRole)
            ending = "\r\n" if original.endswith("\r\n") else "\n" if original.endswith("\n") else ""
            bullet = bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            rows.append({"text": item.text() + ending, "bullet": bullet,
                         "included": not bullet or item.checkState() == Qt.CheckState.Checked})
        return rows

    def content(self) -> str:
        return selected_content(self.rows())

    def filter_bullets(self, text):
        for index in range(self.items.count()):
            item = self.items.item(index)
            item.setHidden(bool(text.strip()) and text.strip().casefold() not in item.text().casefold())

    def refresh_preview(self, *_args):
        self.preview.setMarkdown(self.content())

    def move(self, offset):
        row = self.items.currentRow(); target = row + offset
        if not (0 <= target < self.items.count() and row >= 0):
            return
        if not all(self.items.item(index).flags() & Qt.ItemFlag.ItemIsUserCheckable for index in (row, target)):
            return
        # A final bullet without a newline must still be separated after moving.
        for index in (row, target):
            item = self.items.item(index)
            original = item.data(Qt.ItemDataRole.UserRole)
            if not original.endswith("\n"):
                item.setData(Qt.ItemDataRole.UserRole, original + "\n")
        self.items.insertItem(target, self.items.takeItem(row))
        self.items.setCurrentRow(target)
        self.refresh_preview()
