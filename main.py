"""CV Manager: a polished local macOS application tracker and CV builder."""
from __future__ import annotations

import csv
import json
import logging
import platform
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QDate, Signal, QSize, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QBrush, QColor, QCursor, QDesktopServices, QDrag
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
    QFileDialog, QPlainTextEdit, QStackedWidget, QTableWidget, QTableWidgetItem,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from cv_export import CVOverflowError, export_cv, export_stem, pdf_filename, render_markdown
from cv_importer import ImportResult, import_cv
from database import Application, CV, CVDatabase, CVHistory, DEFAULT_PROFILE, STATUSES, Section, SectionHistory
from safari_bridge_store import SafariBridgeStore


APP_STYLESHEET = """
    QWidget { background: #f8fafc; color: #1e293b; font-family: -apple-system, 'Helvetica Neue', sans-serif; font-size: 13px; }
    QMainWindow, QDialog { background: #f8fafc; }
    QListWidget { background: #102a43; color: #eaf2f8; border: 0; border-radius: 12px; padding: 10px 7px; outline: none; }
    QListWidget::item { padding: 11px 13px; border-radius: 7px; margin: 2px 0; }
    QListWidget::item:selected { background: #1d4ed8; color: white; font-weight: 600; }
    QListWidget::item:hover { background: #243b53; }
    QTableWidget { background: white; alternate-background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; gridline-color: #edf2f7; selection-background-color: #dbeafe; selection-color: #0f172a; }
    QTreeWidget { background: white; alternate-background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; selection-background-color: #dbeafe; selection-color: #0f172a; outline: none; }
    QTreeWidget::item { padding: 5px 4px; border-bottom: 1px solid #f1f5f9; }
    QTreeWidget::item:hover { background: #eff6ff; }
    QHeaderView::section { background: #f1f5f9; color: #475569; border: 0; border-bottom: 1px solid #e2e8f0; padding: 9px; font-weight: 600; }
    QPushButton { background: #1d4ed8; color: white; border: 0; border-radius: 7px; padding: 8px 13px; font-weight: 600; }
    QPushButton:hover { background: #1e40af; }
    QPushButton[secondary="true"] { background: white; color: #334155; border: 1px solid #cbd5e1; }
    QPushButton[danger="true"] { color: #b91c1c; background: #fee2e2; }
    QLineEdit, QPlainTextEdit, QComboBox, QDateEdit { background: white; border: 1px solid #cbd5e1; border-radius: 7px; padding: 7px; selection-background-color: #bfdbfe; }
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus { border: 2px solid #60a5fa; }
    QLabel[pageTitle="true"] { font-size: 26px; font-weight: 700; color: #0f172a; }
    QLabel[muted="true"] { color: #64748b; }
    QLabel[attribution="true"] { color: #94a3b8; font-size: 11px; padding: 5px 8px 0; }
    QFrame[card="true"] { background: white; border: 1px solid #e2e8f0; border-radius: 12px; }
"""

TREE_KIND_ROLE = int(Qt.ItemDataRole.UserRole)
TREE_DATA_ROLE = TREE_KIND_ROLE + 1
TREE_EDIT_MODE_ROLE = TREE_DATA_ROLE + 1
LOGGER = logging.getLogger("cv_manager")
COMMON_CV_SECTIONS = ("Experience", "Projects", "Skills", "Education", "Profile", "Other")


def is_bullet_item(item: QTreeWidgetItem | None) -> bool:
    """Return whether a tree row is a Markdown bullet."""
    return bool(
        item
        and item.data(0, TREE_KIND_ROLE) == "content"
        and item.text(1).startswith(("- ", "* "))
    )


class LibraryTreeWidget(QTreeWidget):
    """Allow bullets to be dragged only within their current entry."""

    bulletMoved = Signal(object)

    def startDrag(self, _supported_actions) -> None:
        current = self.currentItem()
        if not is_bullet_item(current):
            return
        self.clearSelection()
        current.setSelected(True)
        drag = QDrag(self)
        drag.setMimeData(self.mimeData([current]))
        drag.exec(Qt.DropAction.MoveAction)

    def _valid_bullet_drop(self, event) -> tuple[QTreeWidgetItem, QTreeWidgetItem] | None:
        source = self.currentItem()
        target = self.itemAt(event.position().toPoint())
        indicator = self.dropIndicatorPosition()
        if (
            not is_bullet_item(source)
            or not is_bullet_item(target)
            or source is target
            or source.parent() is None
            or source.parent() is not target.parent()
            or indicator not in {
                QAbstractItemView.DropIndicatorPosition.AboveItem,
                QAbstractItemView.DropIndicatorPosition.BelowItem,
            }
        ):
            return None
        return source, target

    def dragMoveEvent(self, event) -> None:
        super().dragMoveEvent(event)
        if self._valid_bullet_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        items = self._valid_bullet_drop(event)
        if not items:
            event.ignore()
            return
        source, target = items
        parent = source.parent()
        source_row = parent.indexOfChild(source)
        insertion_row = parent.indexOfChild(target)
        if self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.BelowItem:
            insertion_row += 1
        if source_row < insertion_row:
            insertion_row -= 1
        if insertion_row == source_row:
            event.ignore()
            return
        parent.takeChild(source_row)
        parent.insertChild(insertion_row, source)
        self.setCurrentItem(source)
        event.acceptProposedAction()
        self.bulletMoved.emit(source)


def configure_logging(data_dir: Path) -> Path:
    """Write durable, size-limited diagnostics without requiring a console."""
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "cv-manager.log"
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    LOGGER.setLevel(logging.INFO)
    for existing_handler in LOGGER.handlers:
        existing_handler.close()
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.propagate = False
    LOGGER.info("Starting CV Manager %s on macOS %s (%s)", "0.1.0", platform.mac_ver()[0], platform.machine())
    return log_path


def install_exception_handler(log_path: Path) -> None:
    """Record uncaught failures and give GUI users a useful recovery path."""
    previous_hook = sys.excepthook

    def handle_exception(exception_type, exception, traceback) -> None:
        if issubclass(exception_type, KeyboardInterrupt):
            previous_hook(exception_type, exception, traceback)
            return
        LOGGER.critical("Unhandled exception", exc_info=(exception_type, exception, traceback))
        if QApplication.instance() is not None:
            QMessageBox.critical(
                None,
                "CV Manager encountered an error",
                "An unexpected error occurred. Your data remains stored locally.\n\n"
                f"Diagnostic log:\n{log_path}",
            )

    sys.excepthook = handle_exception


class WrappingLineEditor(QPlainTextEdit):
    """Edit one persisted line while wrapping it to the available width."""

    editingFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)

    def keyPressEvent(self, event):
        if event.key() in {Qt.Key.Key_Enter, Qt.Key.Key_Return}:
            self.editingFinished.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ExistingTextEditDelegate(QStyledItemDelegate):
    """Open tree values with their existing text and a normal caret."""

    _WRAPPED_TEXT_FLAGS = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if index.column() != 1:
            return size

        wrapped_option = QStyleOptionViewItem(option)
        self.initStyleOption(wrapped_option, index)
        tree = self.parent()
        available_width = tree.columnWidth(index.column()) if isinstance(tree, QTreeWidget) else option.rect.width()
        text_width = max(1, available_width - 16)
        bounds = wrapped_option.fontMetrics.boundingRect(
            0, 0, text_width, 100_000,
            int(self._WRAPPED_TEXT_FLAGS),
            wrapped_option.text,
        )
        return QSize(size.width(), max(size.height(), bounds.height() + 16))

    def paint(self, painter, option, index):
        if index.column() != 1:
            super().paint(painter, option, index)
            return

        wrapped_option = QStyleOptionViewItem(option)
        self.initStyleOption(wrapped_option, index)
        text = wrapped_option.text
        wrapped_option.text = ""
        style = wrapped_option.widget.style() if wrapped_option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, wrapped_option, painter, wrapped_option.widget)

        text_rect = option.rect.adjusted(8, 5, -8, -5)
        color_role = (
            wrapped_option.palette.ColorRole.HighlightedText
            if option.state & QStyle.StateFlag.State_Selected
            else wrapped_option.palette.ColorRole.Text
        )
        painter.save()
        painter.setClipRect(text_rect)
        painter.setFont(wrapped_option.font)
        painter.setPen(wrapped_option.palette.color(color_role))
        painter.drawText(text_rect, int(self._WRAPPED_TEXT_FLAGS), text)
        painter.restore()

    def createEditor(self, parent, option, index):
        if index.column() == 1:
            editor = WrappingLineEditor(parent)
            editor.editingFinished.connect(lambda: self.commit_and_close(editor))
            editor.setStyleSheet(
                "QPlainTextEdit { color: #0f172a; background-color: #ffffff; "
                "border: 2px solid #3b82f6; border-radius: 5px; padding: 3px 6px; "
                "selection-color: #0f172a; selection-background-color: #bfdbfe; }"
            )
            return editor
        return super().createEditor(parent, option, index)

    def commit_and_close(self, editor):
        self.commitData.emit(editor)
        self.closeEditor.emit(editor)

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.DisplayRole)
        if isinstance(editor, WrappingLineEditor):
            editor.setPlainText("" if value is None else str(value))

            def position_wrapped_caret():
                mouse_position = editor.viewport().mapFromGlobal(QCursor.pos())
                if editor.viewport().rect().contains(mouse_position):
                    editor.setTextCursor(editor.cursorForPosition(mouse_position))
                else:
                    cursor = editor.textCursor()
                    cursor.setPosition(len(editor.toPlainText()))
                    editor.setTextCursor(cursor)

            QTimer.singleShot(0, position_wrapped_caret)
            return
        if not isinstance(editor, QLineEdit):
            super().setEditorData(editor, index)
            return
        editor.setText("" if value is None else str(value))

        def position_caret():
            if not editor:
                return
            mouse_position = editor.mapFromGlobal(QCursor.pos())
            if editor.rect().contains(mouse_position):
                editor.setCursorPosition(editor.cursorPositionAt(mouse_position))
            else:
                editor.setCursorPosition(len(editor.text()))
            editor.deselect()

        QTimer.singleShot(0, position_caret)

    def setModelData(self, editor, model, index):
        if isinstance(editor, WrappingLineEditor):
            model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)
            return
        super().setModelData(editor, model, index)

    def updateEditorGeometry(self, editor, option, index):
        geometry = option.rect
        height = max(34, geometry.height())
        geometry.setHeight(height)
        geometry.moveCenter(option.rect.center())
        editor.setGeometry(geometry)


class TreeEditDelegate(ExistingTextEditDelegate):
    """Allow editing only the tree cells that are persisted."""

    def createEditor(self, parent, option, index):
        kind = index.siblingAtColumn(0).data(TREE_KIND_ROLE)
        editable_columns = {"cv": {1}, "profile_field": {1}, "section": {0, 1}, "entry": {1}, "details": {1}, "content": {1}}
        if index.column() not in editable_columns.get(kind, set()):
            return None
        return super().createEditor(parent, option, index)


class LibraryTreeEditDelegate(ExistingTextEditDelegate):
    """Allow direct edits to the values displayed in the section tree."""

    def createEditor(self, parent, option, index):
        kind = index.siblingAtColumn(0).data(TREE_KIND_ROLE)
        editable_columns = {"section": {0, 1, 2, 3}, "entry": {1}, "details": {1}, "content": {1}}
        if index.column() not in editable_columns.get(kind, set()):
            return None
        return super().createEditor(parent, option, index)


def app_data_dir() -> Path:
    path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) or Path.home() / "Library/Application Support/CV Manager")
    path.mkdir(parents=True, exist_ok=True)
    return path


def title(text: str, subtitle: str | None = None) -> QWidget:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 8)
    heading = QLabel(text); heading.setProperty("pageTitle", True)
    layout.addWidget(heading)
    if subtitle:
        label = QLabel(subtitle); label.setProperty("muted", True); layout.addWidget(label)
    return container


def secondary_button(text: str) -> QPushButton:
    button = QPushButton(text); button.setProperty("secondary", True); return button


class ProfileDialog(QDialog):
    def __init__(self, profile: dict[str, str], parent=None, first_run: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Welcome to CV Manager" if first_run else "Personal details")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        subtitle = (
            "Add the contact details to use in new CVs. You can change them later."
            if first_run else
            "These details are copied into each new CV and do not change afterward."
        )
        layout.addWidget(title(self.windowTitle(), subtitle))
        form = QFormLayout()
        self.fields = {}
        labels = {"name": "Full name", "phone": "Phone", "email": "Email", "github": "GitHub display URL", "website": "Website display URL"}
        for key, label in labels.items():
            field = QLineEdit(profile.get(key, DEFAULT_PROFILE[key])); self.fields[key] = field; form.addRow(label, field)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def values(self) -> dict[str, str]:
        return {key: field.text().strip() for key, field in self.fields.items()}

    def accept(self) -> None:
        if not self.fields["name"].text().strip() or not self.fields["email"].text().strip():
            QMessageBox.warning(self, "Missing details", "Your name and email are required.")
            return
        super().accept()


class SectionDialog(QDialog):
    def __init__(
        self,
        section: Section | None = None,
        parent=None,
        show_labels: bool = True,
        section_headings: list[str] | None = None,
        section_categories: dict[str, str] | None = None,
    ):
        super().__init__(parent)
        self.show_library_fields = show_labels
        self.setWindowTitle("Edit CV section" if section else "New reusable entry")
        self.resize(660, 520)
        layout = QVBoxLayout(self)
        subtitle = (
            "Formatting supported: **bold**, *italic*, links, and - bullets."
            if section else
            "Name this entry, then choose the CV section that should contain it."
        )
        layout.addWidget(title(self.windowTitle(), subtitle))
        form = QFormLayout()
        self.internal_name = QLineEdit(section.internal_name if section else "")
        self.internal_name.setPlaceholderText("e.g. Payments migration project")
        self.title = QComboBox()
        self.title.setEditable(True)
        seen_headings = set()
        for heading in [*(section_headings or []), *COMMON_CV_SECTIONS]:
            heading = heading.strip()
            normalized = heading.casefold()
            if heading and normalized not in seen_headings:
                self.title.addItem(heading)
                seen_headings.add(normalized)
        if section:
            self.title.setCurrentText(section.title)
        else:
            self.title.setCurrentIndex(-1)
        self.title.lineEdit().setPlaceholderText("Choose or type a CV section…")
        self.category = QComboBox(); self.category.addItems(["Profile", "Experience", "Skills", "Education", "Projects", "Other"])
        if section:
            self.category.setCurrentText(section.category)
        else:
            self.category.setCurrentText("Other")
        self.section_categories = {
            heading.casefold(): category
            for heading, category in {
                **{heading: heading for heading in COMMON_CV_SECTIONS},
                **(section_categories or {}),
            }.items()
        }
        self.title.currentTextChanged.connect(self.suggest_category)
        self.labels = QLineEdit(section.labels if section else "")
        self.labels.setPlaceholderText("e.g. backend, data engineering, fintech")
        if show_labels:
            form.addRow("Library entry name", self.internal_name)
        form.addRow("CV section heading", self.title); form.addRow("Category", self.category)
        if show_labels:
            form.addRow("Entry keywords (optional)", self.labels)
        layout.addLayout(form)
        if show_labels:
            section_hint = QLabel(
                "Entries assigned to the same CV section are kept together under one heading."
            )
            section_hint.setProperty("muted", True)
            section_hint.setWordWrap(True)
            layout.addWidget(section_hint)
        self.content = QPlainTextEdit(section.content if section else "")
        self.content.setPlaceholderText("Example:\n**Data Engineering Intern** :: *May 2026 - Present*\n*Example Company* :: *Montreal, QC*\n- Built reliable data pipelines...\n- Improved reporting...")
        layout.addWidget(self.content, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def values(self) -> tuple[str, str, str, str]:
        return self.title.currentText().strip(), self.category.currentText(), self.content.toPlainText().strip(), self.labels.text().strip()

    def library_name(self) -> str:
        return self.internal_name.text().strip()

    def suggest_category(self, heading: str) -> None:
        category = self.section_categories.get(heading.strip().casefold())
        self.category.setCurrentText(category or "Other")

    def accept(self) -> None:
        if self.show_library_fields and not self.internal_name.text().strip():
            QMessageBox.warning(self, "Missing name", "Give this reusable entry a library name.")
            return
        if not self.title.currentText().strip() or not self.content.toPlainText().strip():
            QMessageBox.warning(self, "Missing content", "An entry needs a CV section heading and content.")
            return
        super().accept()


class CVImportDialog(QDialog):
    def __init__(self, result: ImportResult, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import CV sections")
        self.resize(600, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(title("Review imported CV", "Choose which content to add to your reusable entry library."))
        self.sections = QListWidget()
        for section in result.sections:
            item = QListWidgetItem(f"{section.category}  ·  {section.title}")
            item.setData(Qt.ItemDataRole.UserRole, section)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.sections.addItem(item)
        layout.addWidget(self.sections, 1)
        self.import_profile = QCheckBox("Update Personal Details using the contact information found in this CV")
        details = ", ".join(result.profile)
        self.import_profile.setEnabled(bool(result.profile))
        self.import_profile.setToolTip(f"Detected: {details}" if details else "No contact information was detected.")
        layout.addWidget(self.import_profile)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def selected_sections(self):
        return [self.sections.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.sections.count()) if self.sections.item(index).checkState() == Qt.CheckState.Checked]

    def accept(self) -> None:
        if not self.selected_sections():
            QMessageBox.warning(self, "No sections selected", "Select at least one section to import.")
            return
        super().accept()


class ApplicationDialog(QDialog):
    def __init__(self, cvs: list, application: Application | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit application" if application else "Add application")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        layout.addWidget(title(self.windowTitle(), "Record the role, progress, posting, notes, and CV used for each application."))
        form = QFormLayout()
        self.company = QLineEdit(application.company if application else "")
        self.role = QLineEdit(application.role if application else "")
        self.location = QLineEdit(application.location if application else "")
        self.date = QDateEdit(QDate.fromString(application.application_date, "yyyy-MM-dd") if application else QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.status = QComboBox(); self.status.addItems(STATUSES); self.status.setCurrentText(application.status if application else "Applied")
        self.posting_url = QLineEdit(application.posting_url if application else "")
        self.posting_url.setPlaceholderText("https://company.example/jobs/role")
        self.cv = QComboBox(); self.cv.addItem("No CV linked", None)
        for record in cvs:
            self.cv.addItem(record.name, record.id)
        if application and application.cv_id:
            self.cv.setCurrentIndex(max(self.cv.findData(application.cv_id), 0))
        self.notes = QPlainTextEdit(application.notes if application else ""); self.notes.setPlaceholderText("Interview notes, next steps, or reminders…"); self.notes.setFixedHeight(100)
        for label, field in [("Company", self.company), ("Role", self.role), ("Location", self.location), ("Job posting URL", self.posting_url), ("Applied on", self.date), ("Status", self.status), ("CV used", self.cv), ("Notes", self.notes)]:
            form.addRow(label, field)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def values(self) -> dict:
        return {"company": self.company.text().strip(), "role": self.role.text().strip(), "location": self.location.text().strip(), "posting_url": self.posting_url.text().strip(), "application_date": self.date.date().toString("yyyy-MM-dd"), "status": self.status.currentText(), "cv_id": self.cv.currentData(), "notes": self.notes.toPlainText().strip()}

    def accept(self) -> None:
        if not self.company.text().strip() or not self.role.text().strip():
            QMessageBox.warning(self, "Missing details", "Company and role are required.")
            return
        super().accept()


class CVDialog(QDialog):
    def __init__(self, sections: list[Section], profile: dict[str, str], cv=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit CV" if cv else "Build tailored CV")
        self.resize(840, 540)
        outer = QVBoxLayout(self)
        explanation = (
            "Change the name, entries, or order. Editing an entry here stops future library updates from changing it."
            if cv else f"Choose reusable entries for {profile['name']}. Entries with the same heading stay together as one CV section."
        )
        outer.addWidget(title(self.windowTitle(), explanation))
        form = QFormLayout(); self.name = QLineEdit(cv.name if cv else ""); self.name.setPlaceholderText("e.g. Product data role - Acme"); form.addRow("Internal CV name", self.name)
        self.keywords = QLineEdit(cv.keywords if cv else "")
        self.keywords.setPlaceholderText("e.g. backend, Python, platform engineering")
        form.addRow("Job keywords", self.keywords); outer.addLayout(form)
        body = QHBoxLayout(); outer.addLayout(body, 1)
        self.available = QListWidget(); self.available.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        for section in sections:
            labels = f"  ·  {section.labels}" if section.labels else ""
            item = QListWidgetItem(
                f"{section.internal_name}  →  {section.title}{labels}"
            )
            item.setData(Qt.ItemDataRole.UserRole, section); self.available.addItem(item)
        self.selected = QListWidget()
        if cv:
            library_sections = {section.id: section for section in sections}
            for section in cv.sections:
                source = library_sections.get(section.get("source_section_id"))
                entry_name = source.internal_name if source else "Customized entry"
                item = QListWidgetItem(f"{entry_name}  →  {section.get('title', 'Untitled')}")
                item.setData(Qt.ItemDataRole.UserRole, dict(section))
                self.selected.addItem(item)
        self.selected.itemDoubleClicked.connect(lambda _: self.edit_selected_section())
        controls = QVBoxLayout()
        for text, action in [("Add entries →", self.add_sections), ("← Remove", self.remove_sections), ("Edit entry", self.edit_selected_section), ("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1))]:
            button = secondary_button(text); button.clicked.connect(action); controls.addWidget(button)
        controls.addStretch()
        self.entry_search = QLineEdit(); self.entry_search.setPlaceholderText("Search entry names, CV sections, or keywords…")
        self.entry_search.textChanged.connect(self.filter_available_entries)
        available_panel = QVBoxLayout(); available_panel.addWidget(QLabel("Reusable entries")); available_panel.addWidget(self.entry_search); available_panel.addWidget(self.available, 1)
        selected_panel = QVBoxLayout(); selected_panel.addWidget(QLabel("CV content (in order)")); selected_panel.addWidget(self.selected, 1)
        body.addLayout(available_panel, 1); body.addLayout(controls); body.addLayout(selected_panel, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); outer.addWidget(buttons)

    def add_sections(self) -> None:
        selected_library_ids = set()
        for index in range(self.selected.count()):
            value = self.selected.item(index).data(Qt.ItemDataRole.UserRole)
            if isinstance(value, Section):
                selected_library_ids.add(value.id)
            elif value.get("source_section_id") is not None:
                selected_library_ids.add(value["source_section_id"])
        for item in self.available.selectedItems():
            section = item.data(Qt.ItemDataRole.UserRole)
            if section.id not in selected_library_ids:
                clone = QListWidgetItem(item.text()); clone.setData(Qt.ItemDataRole.UserRole, section); self.selected.addItem(clone)
                matching_rows = []
                for row in range(self.selected.count() - 1):
                    selected = self.selected.item(row).data(Qt.ItemDataRole.UserRole)
                    selected_title = selected.title if isinstance(selected, Section) else selected.get("title")
                    if selected_title == section.title:
                        matching_rows.append(row)
                if matching_rows:
                    self.selected.takeItem(self.selected.row(clone))
                    self.selected.insertItem(matching_rows[-1] + 1, clone)
                selected_library_ids.add(section.id)

    def filter_available_entries(self, text: str) -> None:
        needle = text.strip().casefold()
        self.available.clearSelection()
        for index in range(self.available.count()):
            item = self.available.item(index)
            section = item.data(Qt.ItemDataRole.UserRole)
            searchable = " ".join((
                section.internal_name,
                section.title,
                section.category,
                section.labels,
                section.content,
            )).casefold()
            item.setHidden(bool(needle) and needle not in searchable)

    def remove_sections(self) -> None:
        for item in self.selected.selectedItems():
            self.selected.takeItem(self.selected.row(item))

    def edit_selected_section(self) -> None:
        item = self.selected.currentItem()
        if not item:
            QMessageBox.information(self, "Select CV content", "Select an entry from the right-hand list to edit it for this CV.")
            return
        value = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(value, Section):
            section = value
        else:
            section = Section(0, value.get("title", ""), value.get("category", "Other"), value.get("content", ""), 0)
        dialog = SectionDialog(section, self, show_labels=False)
        dialog.setWindowTitle("Edit CV section")
        if dialog.exec():
            section_title, category, content, _ = dialog.values()
            item.setText(f"Customized entry  →  {section_title}")
            item.setData(Qt.ItemDataRole.UserRole, {"title": section_title, "category": category, "content": content})

    def move(self, offset: int) -> None:
        row = self.selected.currentRow(); target = row + offset
        if 0 <= row < self.selected.count() and 0 <= target < self.selected.count():
            item = self.selected.takeItem(row); self.selected.insertItem(target, item); self.selected.setCurrentRow(target)

    def chosen_sections(self) -> list[Section | dict]:
        return [self.selected.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.selected.count())]

    def accept(self) -> None:
        if not self.name.text().strip() or not self.chosen_sections():
            QMessageBox.warning(self, "Incomplete CV", "Provide an internal name and add at least one entry.")
            return
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CV Manager")
        self.resize(1200, 780)
        self.data_dir = app_data_dir()
        self.db = CVDatabase(self.data_dir / "cv_manager.sqlite3")
        self.safari_bridge = SafariBridgeStore(self.db)
        self.safari_bridge_error = ""
        self._current_page_index = -1
        self._changing_page = False
        self._loading_cv_tree = False
        self._tree_cv_id = None
        self._tree_dirty = False
        self._dirty_section_ids: set[int] = set()
        self._autosaved_linked_cv_ids: set[int] = set()

        self.nav = QListWidget(); self.nav.setFixedWidth(205)
        self.nav.addItems(["Overview", "Applications", "CVs", "Tree View", "Entry Library", "Personal Details", "Safari Integration"])
        nav_panel = QWidget(); nav_layout = QVBoxLayout(nav_panel); nav_layout.setContentsMargins(0, 0, 0, 0); nav_layout.setSpacing(0)
        nav_layout.addWidget(self.nav, 1)
        self.pages = QStackedWidget()
        for page in (self.overview_page(), self.applications_page(), self.cvs_page(), self.tree_page(), self.sections_page(), self.profile_page(), self.capture_page()):
            self.pages.addWidget(page)
        self.nav.currentRowChanged.connect(self.change_page)
        shell = QWidget(); layout = QHBoxLayout(shell); layout.setContentsMargins(18, 18, 18, 18); layout.setSpacing(18); layout.addWidget(nav_panel); layout.addWidget(self.pages, 1); self.setCentralWidget(shell)
        refresh = QAction("Refresh", self); refresh.setShortcut("Cmd+R"); refresh.triggered.connect(self.refresh_all); self.menuBar().addAction(refresh)
        self.safari_timer = QTimer(self); self.safari_timer.timeout.connect(self.poll_safari_bridge); self.safari_timer.start(1000)
        self.nav.setCurrentRow(0); self.refresh_all(); self.poll_safari_bridge()
        QTimer.singleShot(0, self.prompt_for_initial_profile)

    def change_page(self, index: int) -> None:
        """Commit an editor session before displaying another navigation page."""
        if self._changing_page:
            return
        previous = self._current_page_index
        self.commit_active_editor(previous)
        changed = False
        if previous == 3 and index != previous and self._tree_dirty:
            if not self.save_cv_tree(self._tree_cv_id, refresh=False):
                self._changing_page = True
                self.nav.setCurrentRow(previous)
                self._changing_page = False
                return
            changed = True
        if previous == 4 and index != previous and self._dirty_section_ids:
            self.commit_library_edits()
            changed = True
        self._current_page_index = index
        self.pages.setCurrentIndex(index)
        if changed:
            self.refresh_all()

    def commit_active_editor(self, page_index: int) -> None:
        tree = self.cv_tree if page_index == 3 else self.section_tree if page_index == 4 else None
        focused = QApplication.focusWidget()
        if tree and focused and tree.isAncestorOf(focused):
            focused.clearFocus()

    def closeEvent(self, event) -> None:
        self.commit_active_editor(self._current_page_index)
        if self._tree_dirty and not self.save_cv_tree(self._tree_cv_id, refresh=False):
            event.ignore()
            return
        if self._dirty_section_ids:
            self.commit_library_edits()
        event.accept()

    def card(self, caption: str) -> QLabel:
        card = QLabel(caption); card.setProperty("card", True); card.setMinimumHeight(86); card.setAlignment(Qt.AlignmentFlag.AlignCenter); card.setStyleSheet("font-size: 15px; font-weight: 600; padding: 8px;")
        return card

    def table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers)); table.setHorizontalHeaderLabels(headers); table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); table.setAlternatingRowColors(True); table.setSortingEnabled(False); table.horizontalHeader().setStretchLastSection(True); table.verticalHeader().hide(); return table

    def detail_card(self, fields: list[tuple[str, str]]) -> tuple[QFrame, dict[str, QLabel]]:
        card = QFrame(); card.setProperty("card", True)
        form = QFormLayout(card); form.setContentsMargins(18, 14, 18, 14)
        labels = {}
        for key, caption in fields:
            value = QLabel("—"); value.setWordWrap(True); value.setTextFormat(Qt.TextFormat.PlainText)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            labels[key] = value; form.addRow(caption, value)
        return card, labels

    def overview_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(14)
        layout.addWidget(title("Application pipeline", "Track each role, its progress, and the CV you used."))
        grid = QGridLayout(); grid.setSpacing(12); self.status_cards = {}
        for index, status in enumerate(STATUSES):
            self.status_cards[status] = self.card(status); grid.addWidget(self.status_cards[status], index // 3, index % 3)
        layout.addLayout(grid)
        actions = QHBoxLayout(); add_application = QPushButton("Add application"); add_cv = secondary_button("Build tailored CV"); add_application.clicked.connect(self.new_application); add_cv.clicked.connect(self.new_cv); actions.addWidget(add_application); actions.addWidget(add_cv); actions.addStretch(); layout.addLayout(actions)
        layout.addWidget(title("Recent applications")); self.recent_table = self.table(["Company", "Role", "Applied", "Status"]); layout.addWidget(self.recent_table, 1)
        return page

    def applications_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        header = QHBoxLayout(); header.addWidget(title("Applications", "Update progress and review the saved CV, posting, and notes for each role.")); header.addStretch(); add = QPushButton("Add application"); edit = secondary_button("Edit"); open_posting = secondary_button("Open posting"); export_csv = secondary_button("Export CSV"); delete = secondary_button("Delete"); delete.setProperty("danger", True); add.clicked.connect(self.new_application); edit.clicked.connect(self.edit_application); open_posting.clicked.connect(self.open_selected_posting); export_csv.clicked.connect(self.export_applications_csv); delete.clicked.connect(self.delete_application); header.addWidget(add); header.addWidget(edit); header.addWidget(open_posting); header.addWidget(export_csv); header.addWidget(delete); layout.addLayout(header)
        self.application_search = QLineEdit(); self.application_search.setPlaceholderText("Search company, role, location, status, notes, or posting URL…"); self.application_search.textChanged.connect(self.refresh_applications); layout.addWidget(self.application_search)
        self.application_table = self.table(["Company", "Role", "Location", "Applied", "Status", "CV used"]); self.application_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection); self.application_table.itemDoubleClicked.connect(lambda _: self.edit_application()); self.application_table.itemSelectionChanged.connect(self.refresh_application_details); layout.addWidget(self.application_table, 1)
        application_card, self.application_detail_labels = self.detail_card([
            ("job", "Selected job"), ("timeline", "Application"), ("cv", "CV snapshot"),
            ("posting", "Job posting"), ("snapshot", "Saved posting snapshot"), ("notes", "Notes"),
        ])
        layout.addWidget(application_card)
        return page

    def cvs_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        header = QHBoxLayout(); header.addWidget(title("Tailored CVs", "Contact details are saved with each CV. Linked library entries update until you customize them.")); header.addStretch(); new = QPushButton("Build CV"); edit = secondary_button("Edit CV"); preview = secondary_button("Preview Markdown"); regenerate = secondary_button("Regenerate PDF"); open_pdf = secondary_button("Open PDF"); open_folder = secondary_button("Exports"); delete = secondary_button("Delete"); delete.setProperty("danger", True); new.clicked.connect(self.new_cv); edit.clicked.connect(self.edit_cv); preview.clicked.connect(self.preview_cv); regenerate.clicked.connect(self.regenerate_selected_cv); open_pdf.clicked.connect(self.open_selected_pdf); open_folder.clicked.connect(self.open_export_folder); delete.clicked.connect(self.delete_cv); [header.addWidget(button) for button in (new, edit, preview, regenerate, open_pdf, open_folder, delete)]; layout.addLayout(header)
        self.cv_table = self.table(["Name", "Job keywords", "Created", "Entries", "PDF export"]); self.cv_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection); self.cv_table.itemDoubleClicked.connect(lambda _: self.edit_cv()); self.cv_table.itemSelectionChanged.connect(self.refresh_cv_details); self.cv_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.cv_table.customContextMenuRequested.connect(self.show_cv_context_menu); layout.addWidget(self.cv_table, 1)
        cv_card, self.cv_detail_labels = self.detail_card([
            ("identity", "Snapshot"), ("keywords", "Best suited for"), ("contact", "Contact details"), ("sections", "Section order"),
            ("applications", "Linked applications"), ("exports", "Export files"),
        ])
        layout.addWidget(cv_card)
        return page

    def tree_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(title("CV tree", "Edit a CV by section, entry, and bullet point."))
        header.addStretch()
        self.tree_cv_picker = QComboBox(); self.tree_cv_picker.setMinimumWidth(240)
        self.tree_cv_picker.currentIndexChanged.connect(self.change_tree_cv)
        header.addWidget(QLabel("CV")); header.addWidget(self.tree_cv_picker)
        layout.addLayout(header)

        self.cv_tree = QTreeWidget()
        self.cv_tree.setColumnCount(4); self.cv_tree.setHeaderLabels(["Node", "Value / category", "Entry keywords", "Library link"])
        self.cv_tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.cv_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.cv_tree.setWordWrap(True)
        self.cv_tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.cv_tree.setItemDelegate(TreeEditDelegate(self.cv_tree))
        self.cv_tree.itemChanged.connect(self.mark_cv_tree_dirty)
        self.cv_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cv_tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        self.cv_tree.header().setStretchLastSection(False)
        self.cv_tree.header().setSectionResizeMode(0, self.cv_tree.header().ResizeMode.ResizeToContents)
        self.cv_tree.header().setSectionResizeMode(1, self.cv_tree.header().ResizeMode.Stretch)
        self.cv_tree.header().setSectionResizeMode(2, self.cv_tree.header().ResizeMode.ResizeToContents)
        self.cv_tree.header().setSectionResizeMode(3, self.cv_tree.header().ResizeMode.ResizeToContents)
        self.cv_tree.header().sectionResized.connect(
            lambda column, _old, _new: QTimer.singleShot(0, self.cv_tree.doItemsLayout) if column == 1 else None
        )
        layout.addWidget(self.cv_tree, 1)

        actions = QHBoxLayout()
        for text, action, primary in [
            ("Add section", self.add_tree_section, False),
            ("Add entry", self.add_tree_entry, False),
            ("Add organization", self.add_tree_details, False),
            ("Add bullet", lambda: self.add_tree_content("- New bullet point"), False),
            ("Add line", lambda: self.add_tree_content("New line"), False),
            ("Remove", self.remove_tree_node, False),
            ("Move up", lambda: self.move_tree_node(-1), False),
            ("Move down", lambda: self.move_tree_node(1), False),
            ("Save & export", self.save_cv_tree, True),
        ]:
            button = QPushButton(text) if primary else secondary_button(text)
            button.clicked.connect(action); actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        hint = QLabel("Double-click a value to edit it. Right-click a CV-specific entry to move it to linked entries. When you save changes to linked entry content, choose whether to create an entry copy or update every linked CV. Entry keywords are read-only here.")
        hint.setProperty("muted", True); hint.setWordWrap(True); layout.addWidget(hint)
        return page

    @staticmethod
    def tree_item(
        kind: str,
        node: str,
        value: str = "",
        data=None,
        editable: bool = True,
        labels: str = "",
        status: str = "",
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node, value, labels, status])
        item.setData(0, TREE_KIND_ROLE, kind); item.setData(0, TREE_DATA_ROLE, data)
        if editable:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        return item

    @staticmethod
    def content_node_label(line: str) -> str:
        if line.startswith(("- ", "* ")):
            return "Bullet"
        if " :: " in line:
            return "Entry"
        if not line:
            return "Spacing"
        return "Line"

    @staticmethod
    def section_uses_entries(category: str, title: str = "") -> bool:
        flat_sections = {"education", "personal details"}
        return not any(value.strip().casefold() in flat_sections for value in (category, title))

    def add_section_content_to_tree(
        self,
        section_item: QTreeWidgetItem,
        content: str,
        category: str | None = None,
        section_title: str | None = None,
    ) -> None:
        lines = content.splitlines()
        category = section_item.text(1) if category is None else category
        section_title = section_item.text(0) if section_title is None else section_title
        if not self.section_uses_entries(category, section_title):
            for line in lines:
                section_item.addChild(self.tree_item("content", self.content_node_label(line), line))
            return

        entry = None
        entry_has_bullet = False
        for line in lines:
            is_bullet = line.startswith(("- ", "* "))
            starts_entry = not is_bullet and bool(line) and (
                entry is None or entry_has_bullet or line.lstrip().startswith("**")
            )
            if starts_entry:
                entry = self.tree_item("entry", "Entry", line)
                section_item.addChild(entry)
                entry.setExpanded(True)
                entry_has_bullet = False
            elif entry is None:
                entry = self.tree_item("entry", "Entry", "")
                section_item.addChild(entry)
                entry.setExpanded(True)
                entry.addChild(self.tree_item("content", self.content_node_label(line), line))
            else:
                if not is_bullet and " :: " in line:
                    child = self.tree_item("details", "Organization / location", line)
                else:
                    child = self.tree_item("content", self.content_node_label(line), line)
                entry.addChild(child)
                entry_has_bullet = entry_has_bullet or is_bullet

    def refresh_tree_picker(self, cvs: list) -> None:
        if not hasattr(self, "tree_cv_picker"):
            return
        if self._tree_dirty:
            return
        selected_id = self.tree_cv_picker.currentData()
        self.tree_cv_picker.blockSignals(True); self.tree_cv_picker.clear()
        if cvs:
            for cv in cvs:
                self.tree_cv_picker.addItem(cv.name, cv.id)
            index = self.tree_cv_picker.findData(selected_id)
            self.tree_cv_picker.setCurrentIndex(index if index >= 0 else 0)
        else:
            self.tree_cv_picker.addItem("No saved CVs", None)
        self.tree_cv_picker.blockSignals(False)
        self.load_cv_tree()

    def change_tree_cv(self, _index: int = -1) -> None:
        selected_id = self.tree_cv_picker.currentData()
        if self._tree_dirty and self._tree_cv_id and selected_id != self._tree_cv_id:
            if not self.save_cv_tree(self._tree_cv_id, refresh=False):
                self.tree_cv_picker.blockSignals(True)
                self.tree_cv_picker.setCurrentIndex(
                    self.tree_cv_picker.findData(self._tree_cv_id)
                )
                self.tree_cv_picker.blockSignals(False)
                return
        self.load_cv_tree()

    def load_cv_tree(self) -> None:
        if not hasattr(self, "cv_tree"):
            return
        self._loading_cv_tree = True
        self.cv_tree.blockSignals(True)
        self.cv_tree.clear()
        cv_id = self.tree_cv_picker.currentData()
        cv = self.db.get_cv(cv_id) if cv_id else None
        if not cv:
            placeholder = self.tree_item("placeholder", "Build a CV to customize it here", editable=False)
            self.cv_tree.addTopLevelItem(placeholder)
            self._tree_cv_id = None
            self._tree_dirty = False
            self.cv_tree.blockSignals(False)
            self._loading_cv_tree = False
            return

        root = self.tree_item("cv", "CV", cv.name)
        self.cv_tree.addTopLevelItem(root)
        profile = self.tree_item("profile", "Personal details", editable=False)
        root.addChild(profile)
        profile_labels = {"name": "Name", "phone": "Phone", "email": "Email", "github": "GitHub", "website": "Website"}
        for key, label in profile_labels.items():
            profile.addChild(self.tree_item("profile_field", label, cv.profile.get(key, ""), key))
        library_sections = {section.id: section for section in self.db.list_sections()}
        for section in cv.sections:
            source = library_sections.get(section.get("source_section_id"))
            section_item = self.tree_item(
                "section",
                section.get("title", "Untitled"),
                section.get("category", "Other"),
                dict(section),
                labels=source.labels if source else "",
                status=f"Linked · {source.internal_name}" if source else "CV-specific",
            )
            root.addChild(section_item)
            self.add_section_content_to_tree(section_item, section.get("content", ""))
        root.setExpanded(True); profile.setExpanded(True)
        for index in range(root.childCount()):
            root.child(index).setExpanded(True)
        self.cv_tree.resizeColumnToContents(0); self.cv_tree.setCurrentItem(root)
        self._tree_cv_id = cv.id
        self._tree_dirty = False
        self.cv_tree.blockSignals(False)
        self._loading_cv_tree = False

    def mark_cv_tree_dirty(self, *_args) -> None:
        if not self._loading_cv_tree:
            self._tree_dirty = True

    @staticmethod
    def cv_tree_section_item(item: QTreeWidgetItem | None) -> QTreeWidgetItem | None:
        """Return the CV section that owns a selected tree row."""
        while item and item.data(0, TREE_KIND_ROLE) not in {"section", "cv"}:
            item = item.parent()
        return item if item and item.data(0, TREE_KIND_ROLE) == "section" else None

    def show_tree_context_menu(self, position) -> None:
        item = self.cv_tree.itemAt(position)
        section_item = self.cv_tree_section_item(item)
        if not section_item:
            return
        original = section_item.data(0, TREE_DATA_ROLE) or {}
        source_section_id = original.get("source_section_id")
        source = self.db.get_section(source_section_id) if source_section_id is not None else None
        if source or section_item.data(0, TREE_EDIT_MODE_ROLE) == "link":
            return
        self.cv_tree.setCurrentItem(section_item)
        menu = QMenu(self)
        link = menu.addAction("Move to linked entries")
        link.triggered.connect(lambda: self.move_tree_section_to_linked(section_item))
        menu.exec(self.cv_tree.viewport().mapToGlobal(position))

    def move_tree_section_to_linked(self, section_item: QTreeWidgetItem) -> None:
        """Mark a CV-specific section for creation in and linkage to the library."""
        original = section_item.data(0, TREE_DATA_ROLE) or {}
        source_section_id = original.get("source_section_id")
        if source_section_id is not None and self.db.get_section(source_section_id):
            return
        root = self.cv_tree.topLevelItem(0)
        cv_name = root.text(1).strip() if root else "CV"
        internal_name = f"{cv_name} | {section_item.text(0).strip()}"
        section_item.setData(0, TREE_EDIT_MODE_ROLE, "link")
        section_item.setText(3, f"Will link on save · {internal_name}")
        self.cv_tree.setCurrentItem(section_item)
        self._tree_dirty = True
        self.statusBar().showMessage("This entry will move to linked entries when the CV is saved.", 5000)

    def prompt_tree_section_action(self, section_item: QTreeWidgetItem) -> bool:
        """Choose how a changed linked section should be saved."""
        original = section_item.data(0, TREE_DATA_ROLE) or {}
        source_section_id = original.get("source_section_id")
        source = self.db.get_section(source_section_id) if source_section_id is not None else None
        if not source:
            return True

        linked_count = self.db.count_linked_cvs(source.id)
        cv_word = "CV" if linked_count == 1 else "CVs"
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("Edit linked section")
        dialog.setText(f'“{source.internal_name}” is a shared Entry Library item.')
        dialog.setInformativeText(
            "Create a new reusable entry for this CV, or edit the shared entry and update every CV linked to it."
        )
        copy_button = dialog.addButton("Create entry copy", QMessageBox.ButtonRole.AcceptRole)
        shared_button = dialog.addButton(
            f"Edit shared entry (updates {linked_count} {cv_word})",
            QMessageBox.ButtonRole.ActionRole,
        )
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(copy_button)
        dialog.exec()

        if dialog.clickedButton() is copy_button:
            section_item.setData(0, TREE_EDIT_MODE_ROLE, "copy")
            root = self.cv_tree.topLevelItem(0)
            cv_name = root.text(1).strip() if root else "CV"
            section_item.setText(3, f"New copy · {cv_name} | {source.internal_name}")
            return True
        if dialog.clickedButton() is shared_button:
            section_item.setData(0, TREE_EDIT_MODE_ROLE, "shared")
            section_item.setText(3, f"Shared edit · {source.internal_name}")
            return True
        return False

    def resolve_tree_section_actions(self) -> bool:
        """Prompt at save time for each linked section whose persisted content changed."""
        root = self.cv_tree.topLevelItem(0)
        chosen_items = []
        for child_index in range(root.childCount()):
            item = root.child(child_index)
            if item.data(0, TREE_KIND_ROLE) != "section":
                continue
            original = item.data(0, TREE_DATA_ROLE) or {}
            source_section_id = original.get("source_section_id")
            if item.data(0, TREE_EDIT_MODE_ROLE) == "link":
                continue
            if source_section_id is None:
                continue
            changed = any((
                item.text(0).strip() != original.get("title", ""),
                (item.text(1).strip() or "Other") != original.get("category", ""),
                self.section_item_content(item) != original.get("content", ""),
            ))
            if not changed:
                item.setData(0, TREE_EDIT_MODE_ROLE, None)
                source = self.db.get_section(source_section_id)
                item.setText(3, f"Linked · {source.internal_name}" if source else "CV-specific")
                continue
            if not item.data(0, TREE_EDIT_MODE_ROLE):
                if not self.prompt_tree_section_action(item):
                    for chosen_item in chosen_items:
                        chosen_item.setData(0, TREE_EDIT_MODE_ROLE, None)
                        chosen_original = chosen_item.data(0, TREE_DATA_ROLE) or {}
                        chosen_source = self.db.get_section(chosen_original.get("source_section_id"))
                        chosen_item.setText(
                            3,
                            f"Linked · {chosen_source.internal_name}" if chosen_source else "CV-specific",
                        )
                    return False
                chosen_items.append(item)
        return True

    def add_tree_section(self) -> None:
        root = self.cv_tree.topLevelItem(0) if self.cv_tree.topLevelItemCount() else None
        if not root or root.data(0, TREE_KIND_ROLE) != "cv":
            QMessageBox.information(self, "No CV selected", "Build or select a CV before adding a section.")
            return
        section = self.tree_item("section", "New section", "Other", {}, status="CV-specific")
        entry = self.tree_item("entry", "Entry", "New entry")
        entry.addChild(self.tree_item("content", "Bullet", "- New bullet point"))
        entry.setExpanded(True)
        section.addChild(entry)
        root.addChild(section); root.setExpanded(True); section.setExpanded(True)
        self._tree_dirty = True
        self.cv_tree.setCurrentItem(section); self.cv_tree.editItem(section, 0)

    def add_tree_entry(self) -> None:
        item = self.cv_tree.currentItem()
        while item and item.data(0, TREE_KIND_ROLE) not in {"section", "cv"}:
            item = item.parent()
        if not item or item.data(0, TREE_KIND_ROLE) != "section" or not self.section_uses_entries(item.text(1), item.text(0)):
            QMessageBox.information(self, "Select a section", "Select a section other than Education before adding an entry.")
            return
        entry = self.tree_item("entry", "Entry", "New entry")
        item.addChild(entry); item.setExpanded(True); entry.setExpanded(True); self.cv_tree.setCurrentItem(entry); self.cv_tree.editItem(entry, 1)
        self._tree_dirty = True

    def add_tree_details(self) -> None:
        item = self.cv_tree.currentItem()
        if item and item.data(0, TREE_KIND_ROLE) in {"details", "content"}:
            item = item.parent()
        if not item or item.data(0, TREE_KIND_ROLE) != "entry":
            QMessageBox.information(self, "Select an entry", "Select an entry before adding its organization and location.")
            return
        details = self.tree_item("details", "Organization / location", "*Organization* :: *Location*")
        item.insertChild(0, details); item.setExpanded(True); self.cv_tree.setCurrentItem(details); self.cv_tree.editItem(details, 1)
        self._tree_dirty = True

    def add_tree_content(self, line: str) -> None:
        item = self.cv_tree.currentItem()
        if item and item.data(0, TREE_KIND_ROLE) in {"details", "content"}:
            item = item.parent()
        if item and item.data(0, TREE_KIND_ROLE) == "entry":
            parent = item
        elif item and item.data(0, TREE_KIND_ROLE) == "section" and not self.section_uses_entries(item.text(1), item.text(0)):
            parent = item
        else:
            QMessageBox.information(self, "Select content", "Select an entry, or select Education to add a line directly.")
            return
        content = self.tree_item("content", self.content_node_label(line), line)
        parent.addChild(content); parent.setExpanded(True); self.cv_tree.setCurrentItem(content); self.cv_tree.editItem(content, 1)
        self._tree_dirty = True

    def remove_tree_node(self) -> None:
        item = self.cv_tree.currentItem()
        if not item or item.data(0, TREE_KIND_ROLE) not in {"section", "entry", "details", "content"}:
            QMessageBox.information(self, "Select content", "Only sections, entries, organization details, lines, and bullet points can be removed.")
            return
        parent = item.parent()
        parent.takeChild(parent.indexOfChild(item))
        self._tree_dirty = True

    def move_tree_node(self, offset: int) -> None:
        item = self.cv_tree.currentItem()
        if not item or item.data(0, TREE_KIND_ROLE) not in {"section", "entry", "details", "content"}:
            return
        parent = item.parent(); row = parent.indexOfChild(item); target = row + offset
        if 0 <= target < parent.childCount() and parent.child(target).data(0, TREE_KIND_ROLE) == item.data(0, TREE_KIND_ROLE):
            parent.takeChild(row); parent.insertChild(target, item); self.cv_tree.setCurrentItem(item)
            self._tree_dirty = True

    def tree_values(self) -> tuple[str, list[dict], dict[str, str]]:
        root = self.cv_tree.topLevelItem(0)
        name = root.text(1).strip()
        profile = {}
        sections = []
        for index in range(root.childCount()):
            item = root.child(index); kind = item.data(0, TREE_KIND_ROLE)
            if kind == "profile":
                for field_index in range(item.childCount()):
                    field = item.child(field_index)
                    profile[field.data(0, TREE_DATA_ROLE)] = field.text(1).strip()
            elif kind == "section":
                content = self.section_item_content(item)
                section = {"title": item.text(0).strip(), "category": item.text(1).strip() or "Other", "content": content}
                original = item.data(0, TREE_DATA_ROLE) or {}
                edit_mode = item.data(0, TREE_EDIT_MODE_ROLE)
                if original.get("source_section_id") is not None and edit_mode != "link" and (
                    edit_mode in {"copy", "shared"}
                    or all(section[key] == original.get(key, "") for key in ("title", "category", "content"))
                ):
                    section["source_section_id"] = original["source_section_id"]
                sections.append(section)
        return name, sections, profile

    def tree_section_actions(self) -> dict[int, str]:
        root = self.cv_tree.topLevelItem(0)
        actions = {}
        section_index = 0
        for child_index in range(root.childCount()):
            item = root.child(child_index)
            if item.data(0, TREE_KIND_ROLE) != "section":
                continue
            edit_mode = item.data(0, TREE_EDIT_MODE_ROLE)
            if edit_mode:
                actions[section_index] = edit_mode
            section_index += 1
        return actions

    def save_cv_tree(self, cv_id: int | None = None, *, refresh: bool = True) -> bool:
        cv_id = cv_id or self._tree_cv_id or self.tree_cv_picker.currentData()
        if not cv_id:
            QMessageBox.information(self, "No CV selected", "Build or select a CV before saving.")
            return False
        name, sections, profile = self.tree_values()
        if not name or not sections or any(not section["title"] or not section["content"] for section in sections):
            QMessageBox.warning(self, "Incomplete CV", "A CV needs a name and at least one titled section with content.")
            return False
        if not self.resolve_tree_section_actions():
            return False
        name, sections, profile = self.tree_values()
        try:
            updated, affected_cv_ids, created_section_ids = self.db.update_cv_from_tree(
                cv_id, name, sections, profile, self.tree_section_actions()
            )
        except ValueError as error:
            QMessageBox.warning(self, "CV not saved", str(error))
            return False

        failed_exports = []
        cancelled_exports = []
        export_ids = list(dict.fromkeys([updated.id, *affected_cv_ids]))
        for export_cv_id in export_ids:
            export_target = self.db.get_cv(export_cv_id)
            if not export_target:
                continue
            try:
                exported = self.export_cv_with_overflow_warning(export_target, self.data_dir / "exports")
                if exported is None:
                    cancelled_exports.append(export_target.name)
                    continue
                markdown_path, pdf_path = exported
                self.db.update_cv_exports(export_target.id, markdown_path, pdf_path)
            except Exception as error:
                failed_exports.append(f"{export_target.name}: {error}")

        copy_summary = ""
        if created_section_ids:
            noun = "entry" if len(created_section_ids) == 1 else "entries"
            copy_summary = f" Created {len(created_section_ids)} linked library {noun}."
        other_cv_count = len(set(affected_cv_ids) - {updated.id})
        shared_summary = f" Updated {other_cv_count} other linked CV(s)." if other_cv_count else ""
        message = f"Saved and exported CV: {updated.name}.{copy_summary}{shared_summary}"
        if failed_exports:
            QMessageBox.warning(
                self,
                "Changes saved, some exports failed",
                "Your changes were saved, but these exports need attention:\n\n" + "\n".join(failed_exports),
            )
            message = "Changes saved, but some exports need attention."
        elif cancelled_exports:
            message = "Changes saved, but some exports were cancelled."
        self._tree_dirty = False
        self._tree_cv_id = updated.id
        if refresh:
            self.refresh_all()
        self.statusBar().showMessage(message, 6000)
        return True

    def sections_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        header = QHBoxLayout(); header.addWidget(title("Entry library", "Build reusable projects, roles, skills, or other entries, then mix and match them in a CV.")); header.addStretch(); importer = secondary_button("Import CV…"); add = secondary_button("New entry"); self.section_preview_button = secondary_button("Show Markdown"); save = QPushButton("Save changes"); delete = secondary_button("Delete"); delete.setProperty("danger", True); importer.clicked.connect(self.import_existing_cv); add.clicked.connect(self.new_section); self.section_preview_button.clicked.connect(self.toggle_section_preview); save.clicked.connect(self.save_library_section); delete.clicked.connect(self.delete_section); header.addWidget(importer); header.addWidget(add); header.addWidget(self.section_preview_button); header.addWidget(save); header.addWidget(delete); layout.addLayout(header)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("CV section"))
        self.section_heading_filter = QComboBox()
        self.section_heading_filter.setMinimumWidth(220)
        self.section_heading_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.section_heading_filter.addItem("All CV sections", None)
        self.section_heading_filter.currentIndexChanged.connect(self.apply_section_heading_filter)
        filters.addWidget(self.section_heading_filter); filters.addStretch(); layout.addLayout(filters)
        self.section_tree = LibraryTreeWidget()
        self.section_tree.setColumnCount(5)
        self.section_tree.setHeaderLabels(["Entry name / node", "CV section / value", "Category", "Entry keywords", "Words"])
        self.section_tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.section_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.section_tree.setIndentation(24); self.section_tree.setAnimated(True)
        self.section_tree.setWordWrap(True)
        self.section_tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.section_tree.setItemDelegate(LibraryTreeEditDelegate(self.section_tree))
        self.section_tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.section_tree.setDropIndicatorShown(True)
        self.section_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.section_tree.customContextMenuRequested.connect(self.show_section_context_menu)
        self.section_tree.bulletMoved.connect(self.library_bullet_moved)
        self.section_tree.itemSelectionChanged.connect(self.refresh_section_details)
        self.section_tree.itemChanged.connect(self.refresh_section_preview)
        self.section_tree.itemChanged.connect(self.autosave_library_section)
        self.section_tree.header().setStretchLastSection(False)
        self.section_tree.header().setSectionResizeMode(0, self.section_tree.header().ResizeMode.ResizeToContents)
        self.section_tree.header().setSectionResizeMode(1, self.section_tree.header().ResizeMode.Stretch)
        self.section_tree.header().sectionResized.connect(
            lambda column, _old, _new: QTimer.singleShot(0, self.section_tree.doItemsLayout) if column == 1 else None
        )
        layout.addWidget(self.section_tree, 1)
        editor = QFrame(); editor.setProperty("card", True)
        editor_layout = QVBoxLayout(editor); editor_layout.setContentsMargins(18, 14, 18, 16); editor_layout.setSpacing(10)
        editor_heading = QLabel("Selected entry Markdown"); heading_font = editor_heading.font(); heading_font.setBold(True); editor_heading.setFont(heading_font); editor_layout.addWidget(editor_heading)
        fields = QHBoxLayout()
        self.section_editor_internal_name = QLineEdit(); self.section_editor_internal_name.setPlaceholderText("Entry name")
        self.section_editor_title = QLineEdit(); self.section_editor_title.setPlaceholderText("CV section")
        self.section_editor_category = QComboBox(); self.section_editor_category.addItems(["Profile", "Experience", "Skills", "Education", "Projects", "Other"]); self.section_editor_category.setEditable(True)
        self.section_editor_labels = QLineEdit(); self.section_editor_labels.setPlaceholderText("Entry keywords")
        self.section_editor_internal_name.setReadOnly(True); self.section_editor_title.setReadOnly(True); self.section_editor_category.setEnabled(False); self.section_editor_labels.setReadOnly(True)
        fields.addWidget(self.section_editor_internal_name, 2); fields.addWidget(self.section_editor_title, 2); fields.addWidget(self.section_editor_category, 1); fields.addWidget(self.section_editor_labels, 2)
        editor_layout.addLayout(fields)
        self.section_content_editor = QPlainTextEdit(); self.section_content_editor.setMinimumHeight(180)
        self.section_content_editor.setPlaceholderText("Select an entry, then edit its complete content here.")
        self.section_content_editor.setReadOnly(True)
        self.section_content_editor.setStyleSheet("QPlainTextEdit { font-family: Menlo, Monaco, monospace; font-size: 13px; padding: 14px; }")
        editor_layout.addWidget(self.section_content_editor)
        editor_hint = QLabel("Double-click the entry name to rename it without changing linked CVs. Adjacent entries with the same CV section are grouped under one heading when exported.")
        editor_hint.setProperty("muted", True); editor_layout.addWidget(editor_hint)
        self.section_preview_widget = editor; editor.hide(); layout.addWidget(editor)
        return page

    def toggle_section_preview(self) -> None:
        show_preview = self.section_preview_widget.isHidden()
        self.section_preview_widget.setVisible(show_preview)
        self.section_preview_button.setText("Hide Markdown" if show_preview else "Show Markdown")

    def profile_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(16)
        layout.addWidget(title("Personal details", "Used for new CVs; existing CVs keep the details saved with them."))
        card = QFrame(); card.setProperty("card", True); form = QFormLayout(card); form.setContentsMargins(22, 22, 22, 22); self.profile_labels = {}
        for key, label in [("name", "Name"), ("phone", "Phone"), ("email", "Email"), ("github", "GitHub"), ("website", "Website")]:
            value = QLabel(); value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); self.profile_labels[key] = value; form.addRow(label, value)
        layout.addWidget(card); actions = QHBoxLayout(); edit = QPushButton("Edit personal details"); backup = secondary_button("Export full backup"); open_data = secondary_button("Open data folder"); edit.clicked.connect(self.edit_profile); backup.clicked.connect(self.export_backup); open_data.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_dir)))); actions.addWidget(edit); actions.addWidget(backup); actions.addWidget(open_data); actions.addStretch(); layout.addLayout(actions); layout.addStretch()
        return page

    def capture_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(16)
        layout.addWidget(title("Safari integration", "Share exported CVs and receive applications through Safari's native extension bridge."))
        card = QFrame(); card.setProperty("card", True); form = QFormLayout(card); form.setContentsMargins(22, 22, 22, 22)
        self.capture_status = QLabel(); self.capture_cv_count = QLabel(); self.capture_pending = QLabel(); self.capture_folder = QLineEdit(str(self.safari_bridge.root)); self.capture_folder.setReadOnly(True)
        form.addRow("Status", self.capture_status); form.addRow("CVs available", self.capture_cv_count); form.addRow("Queued changes", self.capture_pending); form.addRow("Shared local storage", self.capture_folder)
        layout.addWidget(card)
        actions = QHBoxLayout(); sync = QPushButton("Sync now"); open_folder = secondary_button("Open shared folder"); sync.clicked.connect(self.sync_safari_bridge); open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.safari_bridge.root)))); actions.addWidget(sync); actions.addWidget(open_folder); actions.addStretch(); layout.addLayout(actions)
        guide = QLabel("No local web server or token is needed. The native Safari extension reads this private App Group storage, lets you attach an exported CV directly, and queues logged applications here when CV Manager is closed.")
        guide.setWordWrap(True); guide.setProperty("muted", True); layout.addWidget(guide); layout.addStretch(); self.refresh_capture_status()
        return page

    def fill_table(self, table: QTableWidget, records: list, values) -> None:
        table.setSortingEnabled(False); table.setRowCount(0)
        for record in records:
            row = table.rowCount(); table.insertRow(row)
            for col, value in enumerate(values(record)):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, record.id); table.setItem(row, col, item)
        table.resizeColumnsToContents(); table.setSortingEnabled(True)

    def fill_section_tree(self, sections: list[Section]) -> None:
        selected_id = self.selected_section_id()
        selected_heading = self.section_heading_filter.currentData()
        headings = sorted({section.title.strip() for section in sections if section.title.strip()}, key=str.casefold)
        self.section_heading_filter.blockSignals(True)
        self.section_heading_filter.clear()
        self.section_heading_filter.addItem("All CV sections", None)
        for heading in headings:
            self.section_heading_filter.addItem(heading, heading)
        filter_index = self.section_heading_filter.findData(selected_heading)
        self.section_heading_filter.setCurrentIndex(filter_index if filter_index >= 0 else 0)
        self.section_heading_filter.blockSignals(False)
        self.section_tree.blockSignals(True)
        self.section_tree.clear()
        selected_item = None
        for section in sections:
            item = QTreeWidgetItem([
                section.internal_name,
                section.title,
                section.category,
                section.labels or "—",
                str(len(section.content.split())),
            ])
            item.setData(0, TREE_KIND_ROLE, "section")
            item.setData(0, TREE_DATA_ROLE, section.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.section_tree.addTopLevelItem(item)
            self.add_section_content_to_tree(item, section.content, section.category, section.title)
            self.style_library_section(item)
            item.setExpanded(True)
            if section.id == selected_id:
                selected_item = item
        self.section_tree.resizeColumnToContents(0)
        self.section_tree.resizeColumnToContents(2)
        self.section_tree.resizeColumnToContents(3)
        self.section_tree.resizeColumnToContents(4)
        if selected_item:
            self.section_tree.setCurrentItem(selected_item)
        self.section_tree.blockSignals(False)
        self.apply_section_heading_filter()

    def apply_section_heading_filter(self, _index: int | None = None) -> None:
        """Show only library entries with the selected exported CV section."""
        if not hasattr(self, "section_tree"):
            return
        heading = self.section_heading_filter.currentData()
        current = self.library_section_item(self.section_tree.currentItem())
        for index in range(self.section_tree.topLevelItemCount()):
            item = self.section_tree.topLevelItem(index)
            item.setHidden(heading is not None and item.text(1) != heading)
        if current and current.isHidden():
            self.section_tree.setCurrentItem(None)
            self.refresh_section_details()

    @staticmethod
    def section_item_content(section_item: QTreeWidgetItem) -> str:
        lines = []
        for child_index in range(section_item.childCount()):
            child = section_item.child(child_index)
            if child.data(0, TREE_KIND_ROLE) == "entry":
                lines.append(child.text(1))
                lines.extend(child.child(line).text(1) for line in range(child.childCount()))
            else:
                lines.append(child.text(1))
        return "\n".join(lines).strip()

    @staticmethod
    def style_library_section(section_item: QTreeWidgetItem) -> None:
        section_background = QBrush(QColor("#eaf2ff"))
        entry_background = QBrush(QColor("#f8fafc"))
        primary = QBrush(QColor("#0f172a"))
        muted = QBrush(QColor("#64748b"))
        for column in range(5):
            section_item.setBackground(column, section_background)
            section_item.setForeground(column, primary if column < 3 else muted)
        section_item.setSizeHint(1, QSize(1, 38))
        for column in (0, 1, 2):
            font = section_item.font(column); font.setBold(True); section_item.setFont(column, font)

        for child_index in range(section_item.childCount()):
            entry = section_item.child(child_index)
            if entry.data(0, TREE_KIND_ROLE) != "entry":
                entry.setForeground(0, muted)
                entry.setSizeHint(1, QSize(1, 34))
                continue
            for column in range(5):
                entry.setBackground(column, entry_background)
            entry.setSizeHint(1, QSize(1, 36))
            font = entry.font(0); font.setBold(True); entry.setFont(0, font)
            for detail_index in range(entry.childCount()):
                detail = entry.child(detail_index)
                detail.setSizeHint(1, QSize(1, 34))
                if detail.data(0, TREE_KIND_ROLE) == "details":
                    detail.setForeground(0, muted)
                    detail.setForeground(1, muted)

    def refresh_all(self) -> None:
        self.commit_active_editor(self._current_page_index)
        applications, cvs, sections, counts = self.db.list_applications(), self.db.list_cvs(), self.db.list_sections(), self.db.status_counts()
        for status, card in self.status_cards.items():
            card.setText(f"<span style='font-size:25px; color:#1d4ed8'>{counts.get(status, 0)}</span><br><span style='color:#64748b'>{status}</span>")
        self.fill_table(self.recent_table, applications[:8], lambda a: [a.company, a.role, a.application_date, a.status])
        self._applications = applications; self._cvs = cvs; self.refresh_applications()
        cv_names = {cv.id: cv.name for cv in cvs}
        self.fill_table(self.cv_table, cvs, lambda cv: [cv.name, cv.keywords or "—", cv.created_at[:10], len(cv.sections), "Ready" if cv.pdf_path else "Not exported"])
        self.refresh_tree_picker(cvs)
        self.fill_section_tree(sections)
        profile = self.db.get_profile()
        for key, label in self.profile_labels.items(): label.setText(profile[key] or "—")
        self.refresh_cv_details(); self.refresh_section_details()
        self.sync_safari_bridge()
        self.refresh_capture_status()

    @staticmethod
    def history_change_label(change_type: str) -> str:
        return {
            "baseline": "History enabled",
            "created": "Created",
            "edited": "Edited",
            "linked_section_updated": "Linked section updated",
        }.get(change_type, change_type.replace("_", " ").title())

    @staticmethod
    def previous_versions(history: list) -> list:
        """History is newest first; its first entry represents the current content."""
        return history[1:]

    def history_version_label(self, entry) -> str:
        saved = entry.recorded_at.replace("T", " ")
        return f"Version {entry.version} · {saved} · {self.history_change_label(entry.change_type)}"

    def show_cv_context_menu(self, position) -> None:
        item = self.cv_table.itemAt(position)
        if not item:
            return
        self.cv_table.setCurrentCell(item.row(), 0)
        cv_id = self.selected_id(self.cv_table)
        history = self.previous_versions(self.db.list_cv_history(cv_id))

        menu = QMenu(self)
        menu.addAction("Open PDF", self.open_selected_pdf)
        menu.addSeparator()
        history_menu = menu.addMenu("See history")
        if not history:
            empty = history_menu.addAction("No previous versions")
            empty.setEnabled(False)
        for entry in history:
            action = history_menu.addAction(self.history_version_label(entry))
            action.triggered.connect(
                lambda _checked=False, selected=entry: self.open_cv_history_pdf(selected)
            )
        menu.exec(self.cv_table.viewport().mapToGlobal(position))

    @staticmethod
    def cv_from_history(entry: CVHistory) -> CV:
        snapshot = entry.snapshot
        return CV(
            snapshot.get("id", entry.cv_id),
            snapshot.get("name", "CV"),
            snapshot.get("created_at", entry.recorded_at),
            snapshot.get("sections", []),
            DEFAULT_PROFILE | snapshot.get("profile", {}),
            None,
            None,
            snapshot.get("keywords", ""),
        )

    def open_cv_history_pdf(self, entry: CVHistory) -> None:
        version_dir = self.data_dir / "exports" / "history" / f"cv-{entry.cv_id}" / f"version-{entry.version}"
        pdf_path = next(version_dir.rglob("*.pdf"), None) if version_dir.exists() else None
        try:
            if pdf_path is None:
                exported = self.export_cv_with_overflow_warning(
                    self.cv_from_history(entry),
                    version_dir,
                )
                if exported is None:
                    return
                _, pdf_path = exported
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf_path)))
        except Exception as error:
            QMessageBox.warning(self, "Historical PDF unavailable", f"Could not create this CV version's PDF:\n{error}")

    def show_section_context_menu(self, position) -> None:
        item = self.section_tree.itemAt(position)
        section_item = self.library_section_item(item)
        if not section_item:
            return
        self.section_tree.setCurrentItem(item)
        section_id = section_item.data(0, TREE_DATA_ROLE)
        history = self.previous_versions(self.db.list_section_history(section_id))

        menu = QMenu(self)
        insertion = self.library_bullet_insertion_point(item)
        if insertion:
            add_label = "Add bullet below" if item.data(0, TREE_KIND_ROLE) in {"content", "details"} else "Add bullet"
            add_bullet = menu.addAction(add_label)
            add_bullet.triggered.connect(lambda: self.add_library_bullet(item))
        if is_bullet_item(item):
            move_up = menu.addAction("Move bullet up")
            move_up.setEnabled(self.library_bullet_move_target(item, -1) is not None)
            move_up.triggered.connect(lambda: self.move_library_bullet(item, -1))
            move_down = menu.addAction("Move bullet down")
            move_down.setEnabled(self.library_bullet_move_target(item, 1) is not None)
            move_down.triggered.connect(lambda: self.move_library_bullet(item, 1))
        kind = item.data(0, TREE_KIND_ROLE)
        if insertion or is_bullet_item(item):
            menu.addSeparator()
        if self.library_entry_split_point(item):
            split = menu.addAction("Split into separate entries")
            split.triggered.connect(lambda: self.split_library_entry(item))
        if kind in {"entry", "details", "content"}:
            delete_labels = {
                "entry": "Delete sub-entry",
                "details": "Delete organization / location",
                "content": "Delete bullet" if is_bullet_item(item) else "Delete line",
            }
            delete_node = menu.addAction(delete_labels[kind])
            delete_node.triggered.connect(lambda: self.delete_library_node(item))
            menu.addSeparator()
        duplicate = menu.addAction("Duplicate entry")
        duplicate.triggered.connect(lambda: self.duplicate_library_section(section_id))
        delete = menu.addAction("Delete entry")
        delete.triggered.connect(lambda: self.delete_library_sections([section_id]))
        menu.addSeparator()
        history_menu = menu.addMenu("See history")
        if not history:
            empty = history_menu.addAction("No previous versions")
            empty.setEnabled(False)
        for entry in history:
            action = history_menu.addAction(self.history_version_label(entry))
            action.triggered.connect(
                lambda _checked=False, selected=entry: self.preview_section_history(selected)
            )
        menu.exec(self.section_tree.viewport().mapToGlobal(position))

    @staticmethod
    def library_entry_split_point(
        item: QTreeWidgetItem | None,
    ) -> tuple[QTreeWidgetItem, int] | None:
        """Return the section and child boundary selected by a split action."""
        if not item:
            return None
        kind = item.data(0, TREE_KIND_ROLE)
        section_item = item if kind == "section" else item.parent() if kind == "entry" else None
        if not section_item:
            return None
        entries = [
            section_item.child(index)
            for index in range(section_item.childCount())
            if section_item.child(index).data(0, TREE_KIND_ROLE) == "entry"
        ]
        if len(entries) < 2 or len(entries) != section_item.childCount():
            return None
        if kind == "section" or item is entries[0]:
            return section_item, section_item.indexOfChild(entries[1])
        return section_item, section_item.indexOfChild(item)

    @staticmethod
    def section_content_between(
        section_item: QTreeWidgetItem,
        start: int,
        end: int,
    ) -> str:
        """Serialize a contiguous range of a library entry's child rows."""
        lines = []
        for child_index in range(start, end):
            child = section_item.child(child_index)
            lines.append(child.text(1))
            if child.data(0, TREE_KIND_ROLE) == "entry":
                lines.extend(child.child(index).text(1) for index in range(child.childCount()))
        return "\n".join(lines).strip()

    @staticmethod
    def split_entry_internal_name(entry_text: str, fallback: str) -> str:
        """Turn a Markdown entry heading into a useful library name."""
        heading = entry_text.split(" :: ", 1)[0]
        heading = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", heading)
        heading = heading.replace("**", "").replace("*", "").replace("_", "").strip()
        return heading or f"{fallback} (split)"

    def split_library_entry(self, item: QTreeWidgetItem) -> None:
        """Split a multi-entry library item at the selected sub-entry boundary."""
        split_point = self.library_entry_split_point(item)
        if not split_point:
            return
        section_item, boundary = split_point
        first_content = self.section_content_between(section_item, 0, boundary)
        second_content = self.section_content_between(section_item, boundary, section_item.childCount())
        second_entry = section_item.child(boundary)
        second_name = self.split_entry_internal_name(second_entry.text(1), section_item.text(0))
        if QMessageBox.question(
            self,
            "Split reusable entry",
            f"Split this into two reusable entries at “{second_name}”? "
            "Linked CVs will keep both entries in the same order.",
        ) != QMessageBox.StandardButton.Yes:
            return
        if not self.autosave_library_section(section_item):
            return
        section_id = section_item.data(0, TREE_DATA_ROLE)
        try:
            new_section_id, affected_cv_ids = self.db.split_section(
                section_id,
                first_content,
                second_content,
                second_name,
                record_history=False,
            )
        except ValueError as error:
            QMessageBox.warning(self, "Entry not split", str(error))
            return
        self._dirty_section_ids.add(section_id)
        self._autosaved_linked_cv_ids.update(affected_cv_ids)
        self.refresh_all()
        for index in range(self.section_tree.topLevelItemCount()):
            candidate = self.section_tree.topLevelItem(index)
            if candidate.data(0, TREE_DATA_ROLE) == new_section_id:
                self.section_tree.setCurrentItem(candidate)
                break
        self.statusBar().showMessage(
            "Created a separate reusable entry; linked CVs still contain both in order.", 6000
        )

    def library_bullet_insertion_point(
        self, item: QTreeWidgetItem | None
    ) -> tuple[QTreeWidgetItem, int] | None:
        """Find the parent and row for a bullet created from a context-menu target."""
        if not item:
            return None
        kind = item.data(0, TREE_KIND_ROLE)
        if kind == "entry":
            return item, item.childCount()
        if kind in {"content", "details"}:
            parent = item.parent()
            if parent and parent.data(0, TREE_KIND_ROLE) in {"entry", "section"}:
                return parent, parent.indexOfChild(item) + 1
            return None
        if kind != "section":
            return None
        if not self.section_uses_entries(item.text(2), item.text(1)):
            return item, item.childCount()
        entries = [
            item.child(index)
            for index in range(item.childCount())
            if item.child(index).data(0, TREE_KIND_ROLE) == "entry"
        ]
        if len(entries) == 1:
            return entries[0], entries[0].childCount()
        return None

    @staticmethod
    def library_bullet_move_target(
        item: QTreeWidgetItem | None, offset: int
    ) -> QTreeWidgetItem | None:
        if not is_bullet_item(item) or offset not in {-1, 1}:
            return None
        parent = item.parent()
        if not parent:
            return None
        target_row = parent.indexOfChild(item) + offset
        if not 0 <= target_row < parent.childCount():
            return None
        target = parent.child(target_row)
        return target if is_bullet_item(target) else None

    def add_library_bullet(self, target: QTreeWidgetItem) -> None:
        insertion = self.library_bullet_insertion_point(target)
        if not insertion:
            return
        parent, row = insertion
        bullet = self.tree_item("content", "Bullet", "- New bullet point")
        parent.insertChild(row, bullet)
        parent.setExpanded(True)
        section_item = self.library_section_item(parent)
        if section_item:
            self.style_library_section(section_item)
            self.autosave_library_section(section_item)
            self.refresh_section_preview(section_item)
        self.section_tree.setCurrentItem(bullet)
        self.section_tree.editItem(bullet, 1)

    def move_library_bullet(self, item: QTreeWidgetItem, offset: int) -> None:
        target = self.library_bullet_move_target(item, offset)
        if not target:
            return
        parent = item.parent()
        source_row = parent.indexOfChild(item)
        target_row = parent.indexOfChild(target)
        parent.takeChild(source_row)
        parent.insertChild(target_row, item)
        self.section_tree.setCurrentItem(item)
        self.library_bullet_moved(item)

    def library_bullet_moved(self, item: QTreeWidgetItem) -> None:
        section_item = self.library_section_item(item)
        if not section_item:
            return
        self.autosave_library_section(section_item)
        self.refresh_section_preview(section_item)
        self.statusBar().showMessage("Bullet order autosaved.", 2500)

    def delete_library_node(self, item: QTreeWidgetItem) -> None:
        """Delete one nested entry or content line without deleting its library item."""
        kind = item.data(0, TREE_KIND_ROLE)
        if kind not in {"entry", "details", "content"}:
            return
        labels = {
            "entry": "this sub-entry and all of its content",
            "details": "this organization/location line",
            "content": "this bullet or line",
        }
        if QMessageBox.question(
            self,
            "Delete selected content",
            f"Delete {labels[kind]}? Linked CVs will be updated when library changes are committed.",
        ) != QMessageBox.StandardButton.Yes:
            return

        section_item = self.library_section_item(item)
        parent = item.parent()
        if not section_item or not parent:
            return
        row = parent.indexOfChild(item)
        removed = parent.takeChild(row)
        if not self.section_item_content(section_item):
            parent.insertChild(row, removed)
            QMessageBox.information(
                self,
                "Entry needs content",
                "A reusable entry cannot be empty. Delete the whole library entry instead.",
            )
            return
        if not self.autosave_library_section(section_item):
            parent.insertChild(row, removed)
            return
        self.style_library_section(section_item)
        self.section_tree.setCurrentItem(section_item)
        self.refresh_section_preview(section_item)
        self.statusBar().showMessage("Selected content deleted and autosaved.", 3000)

    def duplicate_library_section(self, section_id: int) -> None:
        current = self.library_section_item(self.section_tree.currentItem())
        if current and current.data(0, TREE_DATA_ROLE) == section_id:
            if not self.autosave_library_section(current):
                return
        try:
            duplicate_id = self.db.duplicate_section(section_id)
        except ValueError:
            QMessageBox.warning(self, "Entry unavailable", "This entry is no longer available to duplicate.")
            self.refresh_all()
            return
        self.refresh_all()
        for index in range(self.section_tree.topLevelItemCount()):
            item = self.section_tree.topLevelItem(index)
            if item.data(0, TREE_DATA_ROLE) == duplicate_id:
                self.section_tree.setCurrentItem(item)
                break
        self.statusBar().showMessage("Created an independent entry copy.", 6000)

    def preview_section_history(self, entry: SectionHistory) -> None:
        snapshot = entry.snapshot
        internal_name = snapshot.get("internal_name") or snapshot.get("title", "Section")
        cv_heading = snapshot.get("title", "Section")
        dialog = QDialog(self); dialog.setWindowTitle(f"{internal_name} · version {entry.version}"); dialog.resize(680, 560)
        layout = QVBoxLayout(dialog)
        metadata = snapshot.get("category", "Other")
        if snapshot.get("labels"):
            metadata += f" · {snapshot['labels']}"
        layout.addWidget(title(
            internal_name,
            f"CV section: {cv_heading} · Version {entry.version} · {entry.recorded_at.replace('T', ' ')} · {metadata}",
        ))
        content = QPlainTextEdit(snapshot.get("content", "")); content.setReadOnly(True)
        content.setStyleSheet("QPlainTextEdit { font-family: Menlo, Monaco, monospace; font-size: 13px; padding: 14px; }")
        layout.addWidget(content, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        dialog.exec()

    def refresh_applications(self) -> None:
        needle = self.application_search.text().strip().casefold() if hasattr(self, "application_search") else ""
        matches = [record for record in self._applications if needle in " ".join([record.company, record.role, record.location, record.status, record.notes, record.posting_url, record.posting_snapshot_json]).casefold()]
        cv_names = {cv.id: cv.name for cv in self._cvs}
        self.fill_table(self.application_table, matches, lambda a: [a.company, a.role, a.location or "—", a.application_date, a.status, cv_names.get(a.cv_id, "—")])
        self.refresh_application_details()

    def refresh_application_details(self) -> None:
        if not hasattr(self, "application_detail_labels"):
            return
        application_id = self.selected_id(self.application_table)
        application = self.db.get_application(application_id) if application_id else None
        if not application:
            values = {"job": "Select an application to see all of its details.", "timeline": "—", "cv": "—", "posting": "—", "snapshot": "—", "notes": "—"}
        else:
            cv = self.db.get_cv(application.cv_id) if application.cv_id else None
            try:
                description = json.loads(application.posting_snapshot_json or "{}").get("description", "")
            except json.JSONDecodeError:
                description = ""
            snapshot = description[:800] + ("…" if len(description) > 800 else "")
            values = {
                "job": f"{application.role} at {application.company}" + (f" · {application.location}" if application.location else ""),
                "timeline": f"Applied {application.application_date} · {application.status}",
                "cv": cv.name if cv else "No CV linked",
                "posting": application.posting_url or "No URL saved",
                "snapshot": snapshot or "No posting snapshot saved",
                "notes": application.notes or "No notes saved",
            }
        for key, value in values.items(): self.application_detail_labels[key].setText(value)

    def refresh_cv_details(self) -> None:
        if not hasattr(self, "cv_detail_labels"):
            return
        cv_id = self.selected_id(self.cv_table)
        cv = self.db.get_cv(cv_id) if cv_id else None
        if not cv:
            values = {"identity": "Select a CV to inspect its saved snapshot.", "keywords": "—", "contact": "—", "sections": "—", "applications": "—", "exports": "—"}
        else:
            contact = " · ".join(value for value in (cv.profile.get("phone"), cv.profile.get("email"), cv.profile.get("github"), cv.profile.get("website")) if value)
            linked = [f"{item.role} at {item.company}" for item in self.db.list_applications() if item.cv_id == cv.id]
            exports = " · ".join(path for path in (cv.markdown_path, cv.pdf_path) if path)
            values = {
                "identity": f"{cv.name} · created {cv.created_at.replace('T', ' ')} · {cv.profile.get('name', '')}",
                "keywords": cv.keywords or "No job keywords saved",
                "contact": contact or "No contact details saved",
                "sections": " → ".join(section.get("title", "Untitled") for section in cv.sections),
                "applications": ", ".join(linked) if linked else "Not linked to an application",
                "exports": exports or "Not exported yet",
            }
        for key, value in values.items(): self.cv_detail_labels[key].setText(value)

    def refresh_section_details(self) -> None:
        if not hasattr(self, "section_content_editor"):
            return
        item = self.library_section_item(self.section_tree.currentItem())
        enabled = item is not None
        self.section_preview_button.setEnabled(enabled)
        if not enabled:
            self.section_preview_widget.hide()
            self.section_preview_button.setText("Show Markdown")
        self.section_editor_internal_name.setEnabled(enabled)
        self.section_editor_title.setEnabled(enabled)
        self.section_editor_category.setEnabled(False)
        self.section_editor_labels.setEnabled(enabled)
        self.section_content_editor.setEnabled(enabled)
        self._section_editor_id = item.data(0, TREE_DATA_ROLE) if item else None
        self.refresh_section_preview(item)

    def refresh_section_preview(self, changed_item: QTreeWidgetItem | None = None, column: int = 0) -> None:
        if not hasattr(self, "section_content_editor"):
            return
        item = self.library_section_item(changed_item or self.section_tree.currentItem())
        current = self.library_section_item(self.section_tree.currentItem())
        if not item or (current and item is not current):
            return
        labels = item.text(3).strip()
        self.section_editor_internal_name.setText(item.text(0))
        self.section_editor_title.setText(item.text(1))
        self.section_editor_category.setCurrentText(item.text(2) or "Other")
        self.section_editor_labels.setText("" if labels == "—" else labels)
        content = self.section_item_content(item)
        self.section_content_editor.setPlainText(content)
        word_count = str(len(content.split()))
        if item.text(4) != word_count:
            item.setText(4, word_count)

    @staticmethod
    def selected_id(table: QTableWidget) -> int | None:
        row = table.currentRow(); return table.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 and table.item(row, 0) else None

    @staticmethod
    def selected_ids(table: QTableWidget) -> list[int]:
        return [index.data(Qt.ItemDataRole.UserRole) for index in table.selectionModel().selectedRows(0)]

    @staticmethod
    def library_section_item(item: QTreeWidgetItem | None) -> QTreeWidgetItem | None:
        while item and item.parent():
            item = item.parent()
        return item if item and item.data(0, TREE_KIND_ROLE) == "section" else None

    def selected_section_id(self) -> int | None:
        if not hasattr(self, "section_tree"):
            return None
        item = self.library_section_item(self.section_tree.currentItem())
        return item.data(0, TREE_DATA_ROLE) if item else None

    def selected_section_ids(self) -> list[int]:
        if not hasattr(self, "section_tree"):
            return []
        return list(dict.fromkeys(
            item.data(0, TREE_DATA_ROLE)
            for selected in self.section_tree.selectedItems()
            if (item := self.library_section_item(selected)) is not None
        ))

    def edit_profile(self) -> None:
        dialog = ProfileDialog(self.db.get_profile(), self)
        if dialog.exec(): self.db.update_profile(dialog.values()); self.refresh_all()

    def prompt_for_initial_profile(self) -> None:
        if self.db.profile_is_configured():
            return
        dialog = ProfileDialog(self.db.get_profile(), self, first_run=True)
        if dialog.exec():
            self.db.update_profile(dialog.values())
            self.refresh_all()
            self.statusBar().showMessage("Personal details saved. You can now build a CV.", 6000)
        else:
            self.statusBar().showMessage("Complete Personal Details before building a CV.", 10000)

    def new_section(self) -> None:
        sections = self.db.list_sections()
        section_categories = {
            section.title.strip(): section.category
            for section in sections
            if section.title.strip()
        }
        headings = sorted(section_categories, key=str.casefold)
        dialog = SectionDialog(
            parent=self,
            section_headings=headings,
            section_categories=section_categories,
        )
        if dialog.exec():
            section_title, category, content, labels = dialog.values()
            self.db.create_section(
                section_title,
                category,
                content,
                labels,
                internal_name=dialog.library_name(),
            )
            self.refresh_all()

    def import_existing_cv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import existing CV", "", "CV files (*.pdf *.md *.txt)")
        if not path:
            return
        try:
            result = import_cv(path)
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "Could not import CV", str(error))
            return
        if not result.sections:
            QMessageBox.warning(self, "No sections found", "No standard CV section headings were found. Try importing a text or Markdown version with headings such as EXPERIENCE or SKILLS.")
            return
        dialog = CVImportDialog(result, self)
        if not dialog.exec():
            return
        for section in dialog.selected_sections():
            self.db.create_section(section.title, section.category, section.content)
        if dialog.import_profile.isChecked():
            self.db.update_profile(self.db.get_profile() | result.profile)
        self.refresh_all()
        self.nav.setCurrentRow(4)
        self.statusBar().showMessage(f"Imported {len(dialog.selected_sections())} CV section(s).", 6000)

    def save_library_section(self) -> None:
        item = self.library_section_item(self.section_tree.currentItem())
        if not item:
            QMessageBox.information(self, "Select an entry", "Select an entry or one of its content nodes before saving.")
            return
        internal_name = item.text(0).strip()
        title = item.text(1).strip()
        category = item.text(2).strip() or "Other"
        labels = item.text(3).strip()
        if labels == "—":
            labels = ""
        content = self.section_item_content(item)
        if not internal_name or not title or not content:
            QMessageBox.warning(self, "Incomplete entry", "An entry needs a library name, CV section heading, and content.")
            return
        if self.autosave_library_section(item):
            self.commit_library_edits()
            self.refresh_all()

    def autosave_library_section(
        self,
        changed_item: QTreeWidgetItem | None = None,
        _column: int = 0,
    ) -> bool:
        """Persist one inline edit without creating a history entry per edit."""
        item = self.library_section_item(changed_item or self.section_tree.currentItem())
        if not item:
            return True
        section_id = item.data(0, TREE_DATA_ROLE)
        saved = self.db.get_section(section_id)
        if not saved:
            return False
        internal_name = item.text(0).strip()
        title = item.text(1).strip()
        category = item.text(2).strip() or "Other"
        labels = item.text(3).strip()
        if labels == "—":
            labels = ""
        content = self.section_item_content(item)
        values = (title, category, content, labels, internal_name)
        current = (saved.title, saved.category, saved.content, saved.labels, saved.internal_name)
        if values == current:
            return True
        if not internal_name or not title or not content:
            self.statusBar().showMessage(
                "Autosave paused: an entry needs a library name, CV section heading, and content.", 6000
            )
            return False
        try:
            affected_cv_ids = self.db.update_section(
                section_id,
                title,
                category,
                content,
                labels,
                internal_name,
                record_history=False,
            )
        except ValueError as error:
            self.statusBar().showMessage(f"Autosave paused: {error}", 6000)
            return False
        self._dirty_section_ids.add(section_id)
        self._autosaved_linked_cv_ids.update(affected_cv_ids)
        self.statusBar().showMessage("Entry changes autosaved.", 2500)
        return True

    def commit_library_edits(self) -> None:
        """Turn autosaved library edits into one version when the page is exited."""
        section_ids = set(self._dirty_section_ids)
        affected_cv_ids = set(self._autosaved_linked_cv_ids)
        self._dirty_section_ids.clear()
        self._autosaved_linked_cv_ids.clear()
        for section_id in section_ids:
            self.db.record_section_version(section_id)
        for cv_id in affected_cv_ids:
            self.db.record_cv_version(cv_id, "linked_section_updated")

        failed_exports = []
        cancelled_exports = []
        for cv_id in affected_cv_ids:
            cv = self.db.get_cv(cv_id)
            if not cv:
                continue
            try:
                exported = self.export_cv_with_overflow_warning(cv, self.data_dir / "exports")
                if exported is None:
                    cancelled_exports.append(cv.name)
                    continue
                markdown_path, pdf_path = exported
                self.db.update_cv_exports(cv.id, markdown_path, pdf_path)
            except Exception as error:
                failed_exports.append(f"{cv.name}: {error}")
        if failed_exports:
            QMessageBox.warning(
                self,
                "Sections saved, some exports failed",
                "The section versions were saved, but these exports need attention:\n\n"
                + "\n".join(failed_exports),
            )
        elif section_ids:
            message = "Saved Entry Library version."
            if cancelled_exports:
                message += " Some CV exports were cancelled."
            self.statusBar().showMessage(message, 6000)

    def update_library_section(
        self,
        section_id: int,
        internal_name: str,
        title: str,
        category: str,
        content: str,
        labels: str = "",
    ) -> None:
        affected_cv_ids = self.db.update_section(section_id, title, category, content, labels, internal_name)
        failed_exports = []
        cancelled_exports = []
        for cv_id in affected_cv_ids:
            cv = self.db.get_cv(cv_id)
            try:
                exported = self.export_cv_with_overflow_warning(cv, self.data_dir / "exports")
                if exported is None:
                    cancelled_exports.append(cv.name)
                    continue
                markdown_path, pdf_path = exported
                self.db.update_cv_exports(cv.id, markdown_path, pdf_path)
            except Exception as error:
                failed_exports.append(f"{cv.name}: {error}")
        self.refresh_all()
        if failed_exports:
            QMessageBox.warning(
                self,
                "Sections updated, some exports failed",
                "The linked CV content was updated, but these exports need attention:\n\n" + "\n".join(failed_exports),
            )
        elif affected_cv_ids:
            message = f"Updated this section in {len(affected_cv_ids)} linked CV(s)."
            if cancelled_exports:
                message += " Some CV exports were cancelled."
            else:
                message += " Regenerated their exports."
            self.statusBar().showMessage(message, 6000)
        else:
            self.statusBar().showMessage("Saved reusable entry changes.", 6000)

    def delete_section(self) -> None:
        selected_items = self.section_tree.selectedItems()
        nested_items = [
            item for item in selected_items
            if item.data(0, TREE_KIND_ROLE) in {"entry", "details", "content"}
        ]
        if nested_items:
            if len(selected_items) == 1:
                self.delete_library_node(nested_items[0])
            else:
                QMessageBox.information(
                    self,
                    "Select one content item",
                    "Delete nested entries, lines, or bullets one at a time.",
                )
            return
        section_ids = self.selected_section_ids()
        if not section_ids:
            QMessageBox.information(self, "Select entries", "Select one or more entries to delete.")
            return
        self.delete_library_sections(section_ids)

    def delete_library_sections(self, section_ids: list[int]) -> None:
        subject = "this reusable entry" if len(section_ids) == 1 else f"these {len(section_ids)} reusable entries"
        message = f"Delete {subject}? Existing CV snapshots will remain unchanged."
        if QMessageBox.question(self, "Delete entries", message) == QMessageBox.StandardButton.Yes:
            self.db.delete_sections(section_ids)
            self.refresh_all()

    def new_application(self) -> None:
        dialog = ApplicationDialog(self.db.list_cvs(), parent=self)
        if dialog.exec(): self.db.create_application(**dialog.values()); self.refresh_all()

    def edit_application(self) -> None:
        application_id = self.selected_id(self.application_table)
        if not application_id: QMessageBox.information(self, "Select an application", "Select an application to edit."); return
        dialog = ApplicationDialog(self.db.list_cvs(), self.db.get_application(application_id), self)
        if dialog.exec(): self.db.update_application(application_id, **dialog.values()); self.refresh_all()

    def delete_application(self) -> None:
        application_ids = self.selected_ids(self.application_table)
        if not application_ids:
            QMessageBox.information(self, "Select applications", "Select one or more applications to delete.")
            return
        subject = "this application record" if len(application_ids) == 1 else f"these {len(application_ids)} application records"
        if QMessageBox.question(self, "Delete applications", f"Delete {subject}?") == QMessageBox.StandardButton.Yes:
            self.db.delete_applications(application_ids)
            self.refresh_all()

    def open_selected_posting(self) -> None:
        application_id = self.selected_id(self.application_table)
        if not application_id: QMessageBox.information(self, "Select an application", "Select an application first."); return
        posting_url = self.db.get_application(application_id).posting_url
        if not posting_url: QMessageBox.information(self, "No job posting URL", "No posting URL was saved for this application."); return
        url = posting_url if posting_url.startswith(("https://", "http://")) else f"https://{posting_url}"
        QDesktopServices.openUrl(QUrl(url))

    def refresh_capture_status(self) -> None:
        if not hasattr(self, "capture_status"):
            return
        if self.safari_bridge_error:
            self.capture_status.setText(f"<b style='color:#b91c1c'>Needs attention: {self.safari_bridge_error}</b>")
        else:
            self.capture_status.setText("<b style='color:#15803d'>Local bridge ready</b>")
        try:
            catalog = json.loads(self.safari_bridge.catalog_path.read_text(encoding="utf-8"))
            cv_count = len(catalog.get("cvs", []))
        except (OSError, json.JSONDecodeError):
            cv_count = 0
        self.capture_cv_count.setText(str(cv_count))
        failed = self.safari_bridge.failed_count
        pending = self.safari_bridge.pending_count
        self.capture_pending.setText(f"{pending}" + (f" · {failed} failed" if failed else ""))

    def sync_safari_bridge(self) -> None:
        try:
            self.safari_bridge.sync_cvs()
            self.safari_bridge_error = ""
        except OSError as error:
            self.safari_bridge_error = str(error)
        self.refresh_capture_status()

    def poll_safari_bridge(self) -> None:
        try:
            results = self.safari_bridge.process_requests()
            self.safari_bridge_error = ""
        except OSError as error:
            self.safari_bridge_error = str(error)
            self.refresh_capture_status()
            return
        changed = [result for result in results if result["action"] in {"created", "updated", "cancelled"}]
        if changed:
            latest = changed[-1]
            if latest["action"] == "cancelled":
                self.statusBar().showMessage("Removed Safari application log", 6000)
            else:
                self.statusBar().showMessage(f"Safari logged: {latest['role']} at {latest['company']}", 6000)
            self.refresh_all()
        else:
            self.refresh_capture_status()

    def export_applications_csv(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Export applications", "applications.csv", "CSV files (*.csv)")
        if not filename: return
        cv_names = {cv.id: cv.name for cv in self.db.list_cvs()}
        with Path(filename).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["company", "role", "location", "application_date", "status", "cv_name", "posting_url", "notes"])
            writer.writeheader()
            for application in self.db.list_applications():
                writer.writerow({"company": application.company, "role": application.role, "location": application.location, "application_date": application.application_date, "status": application.status, "cv_name": cv_names.get(application.cv_id, ""), "posting_url": application.posting_url, "notes": application.notes})
        QMessageBox.information(self, "Applications exported", "Your application tracker was exported as CSV.")

    def export_backup(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Export CV Manager backup", "cv-manager-backup.json", "JSON files (*.json)")
        if not filename: return
        Path(filename).write_text(json.dumps(self.db.backup_data(), indent=2, ensure_ascii=False), encoding="utf-8")
        QMessageBox.information(self, "Backup exported", "Your CVs, sections, histories, applications, and profile were exported as JSON.")

    def export_cv_with_overflow_warning(
        self,
        cv: CV,
        output_dir: str | Path,
    ) -> tuple[Path, Path] | None:
        """Let the user choose how an overfull CV should be exported."""
        try:
            return export_cv(cv, output_dir)
        except CVOverflowError:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setWindowTitle("CV exceeds one page")
            dialog.setText(
                f'“{cv.name}” does not fit on one Letter page at the standard reference sizes.'
            )
            dialog.setInformativeText(
                "Shrink the formatting to keep a one-page PDF, use additional "
                "pages at the standard sizes, or cancel and shorten the CV."
            )
            shrink_button = dialog.addButton(
                "Shrink to one page",
                QMessageBox.ButtonRole.AcceptRole,
            )
            multipage_button = dialog.addButton(
                "Use multiple pages",
                QMessageBox.ButtonRole.ActionRole,
            )
            dialog.addButton(QMessageBox.StandardButton.Cancel)
            dialog.exec()
            if dialog.clickedButton() is shrink_button:
                return export_cv(cv, output_dir, shrink_to_fit=True)
            if dialog.clickedButton() is multipage_button:
                return export_cv(cv, output_dir, allow_multipage=True)
            return None

    def new_cv(self) -> None:
        if not self.db.profile_is_configured():
            self.prompt_for_initial_profile()
            if not self.db.profile_is_configured():
                return
        sections = self.db.list_sections()
        if not sections: QMessageBox.information(self, "Add content first", "Create at least one reusable entry before building a CV."); return
        profile = self.db.get_profile(); dialog = CVDialog(sections, profile, parent=self)
        if dialog.exec():
            cv = self.db.create_cv(
                dialog.name.text().strip(), dialog.chosen_sections(), profile,
                keywords=dialog.keywords.text(),
            )
            try:
                exported = self.export_cv_with_overflow_warning(cv, self.data_dir / "exports")
                if exported is not None:
                    markdown_path, pdf_path = exported
                    self.db.update_cv_exports(cv.id, markdown_path, pdf_path)
            except Exception as error:
                QMessageBox.warning(self, "CV saved, export failed", f"The CV snapshot was saved but could not be exported:\n{error}")
            self.refresh_all()

    def edit_cv(self) -> None:
        cv = self.selected_cv()
        if not cv:
            return
        dialog = CVDialog(self.db.list_sections(), cv.profile, cv=cv, parent=self)
        if not dialog.exec():
            return
        updated = self.db.update_cv(
            cv.id, dialog.name.text().strip(), dialog.chosen_sections(), cv.profile,
            keywords=dialog.keywords.text(),
        )
        try:
            exported = self.export_cv_with_overflow_warning(updated, self.data_dir / "exports")
            if exported is not None:
                markdown_path, pdf_path = exported
                self.db.update_cv_exports(updated.id, markdown_path, pdf_path)
                self.statusBar().showMessage(f'Updated and exported CV: {updated.name}', 6000)
            else:
                self.statusBar().showMessage(f'Updated CV; export cancelled: {updated.name}', 6000)
        except Exception as error:
            QMessageBox.warning(self, "CV updated, export failed", f"The CV snapshot was updated but could not be exported:\n{error}")
        self.refresh_all()

    def selected_cv(self):
        cv_id = self.selected_id(self.cv_table)
        if not cv_id: QMessageBox.information(self, "Select a CV", "Select a CV first."); return None
        return self.db.get_cv(cv_id)

    def preview_cv(self) -> None:
        cv = self.selected_cv()
        if not cv: return
        dialog = QDialog(self); dialog.setWindowTitle(cv.name); dialog.resize(760, 720); layout = QVBoxLayout(dialog); layout.addWidget(title(cv.name, "Markdown generated from this CV's saved snapshot")); text = QPlainTextEdit(render_markdown(cv)); text.setReadOnly(True); layout.addWidget(text); dialog.exec()

    def delete_cv(self) -> None:
        cv_ids = self.selected_ids(self.cv_table)
        if not cv_ids:
            QMessageBox.information(self, "Select CVs", "Select one or more CVs to delete.")
            return
        if len(cv_ids) == 1:
            cv = self.db.get_cv(cv_ids[0])
            subject = f'"{cv.name}"' if cv else "this CV"
        else:
            subject = f"these {len(cv_ids)} CVs"
        message = f"Delete {subject}? Applications linked to the selection will be kept but unlinked. Exported files will remain on disk."
        if QMessageBox.question(self, "Delete CVs", message) == QMessageBox.StandardButton.Yes:
            self.db.delete_cvs(cv_ids)
            self.refresh_all()

    def regenerate_selected_cv(self) -> None:
        cv = self.selected_cv()
        if not cv: return
        try:
            exported = self.export_cv_with_overflow_warning(cv, self.data_dir / "exports")
            if exported is None:
                return
            markdown_path, pdf_path = exported
            self.db.update_cv_exports(cv.id, markdown_path, pdf_path)
            self.refresh_all()
            QMessageBox.information(self, "Export regenerated", "The Markdown and PDF were regenerated from this CV's saved snapshot.")
        except Exception as error:
            QMessageBox.warning(self, "Export failed", f"Could not regenerate this CV:\n{error}")

    def open_selected_pdf(self) -> None:
        cv = self.selected_cv()
        if not cv:
            return
        pdf_path = Path(cv.pdf_path) if cv.pdf_path else None
        expected_path = self.data_dir / "exports" / export_stem(cv) / pdf_filename(cv)
        if pdf_path and pdf_path.exists() and pdf_path != expected_path:
            try:
                exported = self.export_cv_with_overflow_warning(cv, self.data_dir / "exports")
                if exported is None:
                    return
                markdown_path, pdf_path = exported
                self.db.update_cv_exports(cv.id, markdown_path, pdf_path)
                self.refresh_all()
            except Exception as error:
                QMessageBox.warning(self, "PDF unavailable", f"Could not refresh this CV's PDF:\n{error}")
                return
        if pdf_path and pdf_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf_path)))
        else:
            QMessageBox.information(self, "No PDF found", "The PDF export is not available. Regenerate this CV to create one.")

    def open_export_folder(self) -> None:
        export_dir = self.data_dir / "exports"; export_dir.mkdir(exist_ok=True); QDesktopServices.openUrl(QUrl.fromLocalFile(str(export_dir)))

def run() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("CV Manager")
    app.setOrganizationDomain("cvmanager.app")
    app.setApplicationName("CV Manager")
    app.setApplicationVersion("0.1.0")
    app.setStyleSheet(APP_STYLESHEET)
    log_path = configure_logging(app_data_dir())
    install_exception_handler(log_path)
    try:
        window = MainWindow()
        window.show()
        return app.exec()
    except Exception:
        sys.excepthook(*sys.exc_info())
        return 1


if __name__ == "__main__":
    sys.exit(run())
