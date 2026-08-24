"""CV Manager: a polished local macOS application tracker and CV builder."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from PySide6.QtCore import QDate, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QFileDialog, QPlainTextEdit, QStackedWidget, QTableWidget, QTableWidgetItem,
    QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from cv_export import export_cv, render_markdown
from capture_bridge import CaptureBridge
from cv_importer import ImportResult, import_cv
from database import Application, CVDatabase, DEFAULT_PROFILE, STATUSES, Section


APP_STYLESHEET = """
    QWidget { background: #f8fafc; color: #1e293b; font-family: -apple-system, 'Helvetica Neue', sans-serif; font-size: 13px; }
    QMainWindow, QDialog { background: #f8fafc; }
    QListWidget { background: #102a43; color: #eaf2f8; border: 0; border-radius: 12px; padding: 10px 7px; outline: none; }
    QListWidget::item { padding: 11px 13px; border-radius: 7px; margin: 2px 0; }
    QListWidget::item:selected { background: #1d4ed8; color: white; font-weight: 600; }
    QListWidget::item:hover { background: #243b53; }
    QTableWidget { background: white; alternate-background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; gridline-color: #edf2f7; selection-background-color: #dbeafe; selection-color: #0f172a; }
    QTreeWidget { background: white; border: 1px solid #e2e8f0; border-radius: 10px; selection-background-color: #dbeafe; selection-color: #0f172a; }
    QHeaderView::section { background: #f1f5f9; color: #475569; border: 0; border-bottom: 1px solid #e2e8f0; padding: 9px; font-weight: 600; }
    QPushButton { background: #1d4ed8; color: white; border: 0; border-radius: 7px; padding: 8px 13px; font-weight: 600; }
    QPushButton:hover { background: #1e40af; }
    QPushButton[secondary="true"] { background: white; color: #334155; border: 1px solid #cbd5e1; }
    QPushButton[danger="true"] { color: #b91c1c; background: #fee2e2; }
    QLineEdit, QPlainTextEdit, QComboBox, QDateEdit { background: white; border: 1px solid #cbd5e1; border-radius: 7px; padding: 7px; selection-background-color: #bfdbfe; }
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus { border: 2px solid #60a5fa; }
    QLabel[pageTitle="true"] { font-size: 26px; font-weight: 700; color: #0f172a; }
    QLabel[muted="true"] { color: #64748b; }
    QFrame[card="true"] { background: white; border: 1px solid #e2e8f0; border-radius: 12px; }
"""

TREE_KIND_ROLE = int(Qt.ItemDataRole.UserRole)
TREE_DATA_ROLE = TREE_KIND_ROLE + 1


class TreeEditDelegate(QStyledItemDelegate):
    """Allow editing only the tree cells that are persisted."""

    def createEditor(self, parent, option, index):
        kind = index.siblingAtColumn(0).data(TREE_KIND_ROLE)
        editable_columns = {"cv": {1}, "profile_field": {1}, "section": {0, 1}, "content": {1}}
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
    def __init__(self, profile: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Personal details")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.addWidget(title("Personal details", "These details are snapshotted whenever you create a CV."))
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
    def __init__(self, section: Section | None = None, parent=None, show_labels: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Edit section" if section else "New section")
        self.resize(660, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(title(self.windowTitle(), "Use Markdown: **bold**, *italic*, and - bullets."))
        form = QFormLayout()
        self.title = QLineEdit(section.title if section else "")
        self.category = QComboBox(); self.category.addItems(["Profile", "Experience", "Skills", "Education", "Projects", "Other"])
        if section:
            self.category.setCurrentText(section.category)
        self.labels = QLineEdit(section.labels if section else "")
        self.labels.setPlaceholderText("e.g. backend, data engineering, fintech")
        form.addRow("Section title", self.title); form.addRow("Category", self.category)
        if show_labels:
            form.addRow("Job labels", self.labels)
        layout.addLayout(form)
        self.content = QPlainTextEdit(section.content if section else "")
        self.content.setPlaceholderText("Example:\n**Data Engineering Intern** :: *May 2026 - Present*\n*Example Company* :: *Montreal, QC*\n- Built reliable data pipelines...\n- Improved reporting...")
        layout.addWidget(self.content, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def values(self) -> tuple[str, str, str, str]:
        return self.title.text().strip(), self.category.currentText(), self.content.toPlainText().strip(), self.labels.text().strip()

    def accept(self) -> None:
        if not self.title.text().strip() or not self.content.toPlainText().strip():
            QMessageBox.warning(self, "Missing content", "A section needs both a title and content.")
            return
        super().accept()


class CVImportDialog(QDialog):
    def __init__(self, result: ImportResult, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import CV sections")
        self.resize(600, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(title("Review imported CV", "Choose which sections to add to your reusable section library."))
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
        layout.addWidget(title(self.windowTitle(), "Keep each submission tied to the exact CV you used."))
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
            "Change the saved name, wording, sections, or order. Editing wording here detaches that block from future library updates."
            if cv else f"This CV will snapshot {profile['name']}'s current details and selected sections."
        )
        outer.addWidget(title(self.windowTitle(), explanation))
        form = QFormLayout(); self.name = QLineEdit(cv.name if cv else ""); self.name.setPlaceholderText("e.g. Product data role - Acme"); form.addRow("Internal CV name", self.name); outer.addLayout(form)
        body = QHBoxLayout(); outer.addLayout(body, 1)
        self.available = QListWidget(); self.available.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        for section in sections:
            labels = f"  ·  {section.labels}" if section.labels else ""
            item = QListWidgetItem(f"{section.category}  ·  {section.title}{labels}"); item.setData(Qt.ItemDataRole.UserRole, section); self.available.addItem(item)
        self.selected = QListWidget()
        if cv:
            for section in cv.sections:
                item = QListWidgetItem(f"{section.get('category', 'Other')}  ·  {section.get('title', 'Untitled')}")
                item.setData(Qt.ItemDataRole.UserRole, dict(section))
                self.selected.addItem(item)
        self.selected.itemDoubleClicked.connect(lambda _: self.edit_selected_section())
        controls = QVBoxLayout()
        for text, action in [("Add →", self.add_sections), ("← Remove", self.remove_sections), ("Edit content", self.edit_selected_section), ("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1))]:
            button = secondary_button(text); button.clicked.connect(action); controls.addWidget(button)
        controls.addStretch()
        body.addWidget(self.available, 1); body.addLayout(controls); body.addWidget(self.selected, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); outer.addWidget(buttons)

    def add_sections(self) -> None:
        selected_library_ids = {
            value.id for value in (self.selected.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.selected.count()))
            if isinstance(value, Section)
        }
        for item in self.available.selectedItems():
            section = item.data(Qt.ItemDataRole.UserRole)
            if section.id not in selected_library_ids:
                clone = QListWidgetItem(item.text()); clone.setData(Qt.ItemDataRole.UserRole, section); self.selected.addItem(clone)
                selected_library_ids.add(section.id)

    def remove_sections(self) -> None:
        for item in self.selected.selectedItems():
            self.selected.takeItem(self.selected.row(item))

    def edit_selected_section(self) -> None:
        item = self.selected.currentItem()
        if not item:
            QMessageBox.information(self, "Select CV content", "Select a section from the right-hand list to edit its content for this CV.")
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
            item.setText(f"{category}  ·  {section_title}")
            item.setData(Qt.ItemDataRole.UserRole, {"title": section_title, "category": category, "content": content})

    def move(self, offset: int) -> None:
        row = self.selected.currentRow(); target = row + offset
        if 0 <= row < self.selected.count() and 0 <= target < self.selected.count():
            item = self.selected.takeItem(row); self.selected.insertItem(target, item); self.selected.setCurrentRow(target)

    def chosen_sections(self) -> list[Section | dict]:
        return [self.selected.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.selected.count())]

    def accept(self) -> None:
        if not self.name.text().strip() or not self.chosen_sections():
            QMessageBox.warning(self, "Incomplete CV", "Provide an internal name and add at least one section.")
            return
        super().accept()


class MainWindow(QMainWindow):
    capture_received = Signal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CV Manager")
        self.resize(1200, 780)
        self.data_dir = app_data_dir()
        self.db = CVDatabase(self.data_dir / "cv_manager.sqlite3")
        self.capture_bridge = CaptureBridge(self.capture_received.emit)
        self.capture_received.connect(self.receive_capture)

        self.nav = QListWidget(); self.nav.setFixedWidth(205)
        self.nav.addItems(["Overview", "Applications", "CVs", "Tree View", "Section Library", "Personal Details", "Safari Capture"])
        self.pages = QStackedWidget()
        for page in (self.overview_page(), self.applications_page(), self.cvs_page(), self.tree_page(), self.sections_page(), self.profile_page(), self.capture_page()):
            self.pages.addWidget(page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        shell = QWidget(); layout = QHBoxLayout(shell); layout.setContentsMargins(18, 18, 18, 18); layout.setSpacing(18); layout.addWidget(self.nav); layout.addWidget(self.pages, 1); self.setCentralWidget(shell)
        refresh = QAction("Refresh", self); refresh.setShortcut("Cmd+R"); refresh.triggered.connect(self.refresh_all); self.menuBar().addAction(refresh)
        self.nav.setCurrentRow(0); self.refresh_all()

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
        layout.addWidget(title("Your application pipeline", "Stay deliberate about each application and the CV that went with it."))
        grid = QGridLayout(); grid.setSpacing(12); self.status_cards = {}
        for index, status in enumerate(STATUSES):
            self.status_cards[status] = self.card(status); grid.addWidget(self.status_cards[status], index // 3, index % 3)
        layout.addLayout(grid)
        actions = QHBoxLayout(); add_application = QPushButton("Add application"); add_cv = secondary_button("Build tailored CV"); add_application.clicked.connect(self.new_application); add_cv.clicked.connect(self.new_cv); actions.addWidget(add_application); actions.addWidget(add_cv); actions.addStretch(); layout.addLayout(actions)
        layout.addWidget(title("Recent applications")); self.recent_table = self.table(["Company", "Role", "Applied", "Status"]); layout.addWidget(self.recent_table, 1)
        return page

    def applications_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        header = QHBoxLayout(); header.addWidget(title("Applications", "Search every saved field, update progress, and review the exact CV, posting, and notes for each submission.")); header.addStretch(); add = QPushButton("Add application"); edit = secondary_button("Edit"); open_posting = secondary_button("Open posting"); export_csv = secondary_button("Export CSV"); delete = secondary_button("Delete"); delete.setProperty("danger", True); add.clicked.connect(self.new_application); edit.clicked.connect(self.edit_application); open_posting.clicked.connect(self.open_selected_posting); export_csv.clicked.connect(self.export_applications_csv); delete.clicked.connect(self.delete_application); header.addWidget(add); header.addWidget(edit); header.addWidget(open_posting); header.addWidget(export_csv); header.addWidget(delete); layout.addLayout(header)
        self.application_search = QLineEdit(); self.application_search.setPlaceholderText("Search company, role, location, or status…"); self.application_search.textChanged.connect(self.refresh_applications); layout.addWidget(self.application_search)
        self.application_table = self.table(["Company", "Role", "Location", "Applied", "Status", "CV used"]); self.application_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection); self.application_table.itemDoubleClicked.connect(lambda _: self.edit_application()); self.application_table.itemSelectionChanged.connect(self.refresh_application_details); layout.addWidget(self.application_table, 1)
        application_card, self.application_detail_labels = self.detail_card([
            ("job", "Selected job"), ("timeline", "Application"), ("cv", "CV snapshot"),
            ("posting", "Job posting"), ("notes", "Notes"),
        ])
        layout.addWidget(application_card)
        return page

    def cvs_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        header = QHBoxLayout(); header.addWidget(title("Tailored CVs", "Every CV keeps its own contact details and section content, so submitted versions stay reproducible.")); header.addStretch(); new = QPushButton("Build CV"); edit = secondary_button("Edit CV"); preview = secondary_button("Preview Markdown"); regenerate = secondary_button("Regenerate PDF"); open_pdf = secondary_button("Open PDF"); open_folder = secondary_button("Exports"); delete = secondary_button("Delete"); delete.setProperty("danger", True); new.clicked.connect(self.new_cv); edit.clicked.connect(self.edit_cv); preview.clicked.connect(self.preview_cv); regenerate.clicked.connect(self.regenerate_selected_cv); open_pdf.clicked.connect(self.open_selected_pdf); open_folder.clicked.connect(self.open_export_folder); delete.clicked.connect(self.delete_cv); [header.addWidget(button) for button in (new, edit, preview, regenerate, open_pdf, open_folder, delete)]; layout.addLayout(header)
        self.cv_table = self.table(["Name", "Created", "Sections", "PDF export"]); self.cv_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection); self.cv_table.itemDoubleClicked.connect(lambda _: self.edit_cv()); self.cv_table.itemSelectionChanged.connect(self.refresh_cv_details); layout.addWidget(self.cv_table, 1)
        cv_card, self.cv_detail_labels = self.detail_card([
            ("identity", "Snapshot"), ("contact", "Contact details"), ("sections", "Section order"),
            ("applications", "Linked applications"), ("exports", "Export files"),
        ])
        layout.addWidget(cv_card)
        return page

    def tree_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(title("CV tree", "Edit a CV as a hierarchy: CV → sections → lines and bullet points."))
        header.addStretch()
        self.tree_cv_picker = QComboBox(); self.tree_cv_picker.setMinimumWidth(240)
        self.tree_cv_picker.currentIndexChanged.connect(self.load_cv_tree)
        header.addWidget(QLabel("CV")); header.addWidget(self.tree_cv_picker)
        layout.addLayout(header)

        self.cv_tree = QTreeWidget()
        self.cv_tree.setColumnCount(3); self.cv_tree.setHeaderLabels(["Node", "Value / category", "Job labels"])
        self.cv_tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.cv_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.cv_tree.setItemDelegate(TreeEditDelegate(self.cv_tree))
        self.cv_tree.header().setStretchLastSection(True)
        layout.addWidget(self.cv_tree, 1)

        actions = QHBoxLayout()
        for text, action, primary in [
            ("Add section", self.add_tree_section, False),
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
        hint = QLabel("Double-click a value to edit it. Job labels come from linked library sections and are read-only here. Markdown is preserved, including **bold**, *italic*, links, and the leading - on bullets.")
        hint.setProperty("muted", True); hint.setWordWrap(True); layout.addWidget(hint)
        return page

    @staticmethod
    def tree_item(kind: str, node: str, value: str = "", data=None, editable: bool = True, labels: str = "") -> QTreeWidgetItem:
        item = QTreeWidgetItem([node, value, labels])
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

    def refresh_tree_picker(self, cvs: list) -> None:
        if not hasattr(self, "tree_cv_picker"):
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

    def load_cv_tree(self) -> None:
        if not hasattr(self, "cv_tree"):
            return
        self.cv_tree.clear()
        cv_id = self.tree_cv_picker.currentData()
        cv = self.db.get_cv(cv_id) if cv_id else None
        if not cv:
            placeholder = self.tree_item("placeholder", "Build a CV to customize it here", editable=False)
            self.cv_tree.addTopLevelItem(placeholder)
            return

        root = self.tree_item("cv", "CV", cv.name)
        self.cv_tree.addTopLevelItem(root)
        profile = self.tree_item("profile", "Personal details", editable=False)
        root.addChild(profile)
        profile_labels = {"name": "Name", "phone": "Phone", "email": "Email", "github": "GitHub", "website": "Website"}
        for key, label in profile_labels.items():
            profile.addChild(self.tree_item("profile_field", label, cv.profile.get(key, ""), key))
        library_labels = {section.id: section.labels for section in self.db.list_sections()}
        for section in cv.sections:
            section_item = self.tree_item(
                "section",
                section.get("title", "Untitled"),
                section.get("category", "Other"),
                dict(section),
                labels=library_labels.get(section.get("source_section_id"), ""),
            )
            root.addChild(section_item)
            for line in section.get("content", "").splitlines():
                section_item.addChild(self.tree_item("content", self.content_node_label(line), line))
        root.setExpanded(True); profile.setExpanded(True)
        for index in range(root.childCount()):
            root.child(index).setExpanded(True)
        self.cv_tree.resizeColumnToContents(0); self.cv_tree.resizeColumnToContents(1); self.cv_tree.setCurrentItem(root)

    def add_tree_section(self) -> None:
        root = self.cv_tree.topLevelItem(0) if self.cv_tree.topLevelItemCount() else None
        if not root or root.data(0, TREE_KIND_ROLE) != "cv":
            QMessageBox.information(self, "No CV selected", "Build or select a CV before adding a section.")
            return
        section = self.tree_item("section", "New section", "Other", {})
        section.addChild(self.tree_item("content", "Bullet", "- New bullet point"))
        root.addChild(section); root.setExpanded(True); section.setExpanded(True)
        self.cv_tree.setCurrentItem(section); self.cv_tree.editItem(section, 0)

    def add_tree_content(self, line: str) -> None:
        item = self.cv_tree.currentItem()
        if item and item.data(0, TREE_KIND_ROLE) == "content":
            item = item.parent()
        if not item or item.data(0, TREE_KIND_ROLE) != "section":
            QMessageBox.information(self, "Select a section", "Select a section or one of its content nodes first.")
            return
        content = self.tree_item("content", self.content_node_label(line), line)
        item.addChild(content); item.setExpanded(True); self.cv_tree.setCurrentItem(content); self.cv_tree.editItem(content, 1)

    def remove_tree_node(self) -> None:
        item = self.cv_tree.currentItem()
        if not item or item.data(0, TREE_KIND_ROLE) not in {"section", "content"}:
            QMessageBox.information(self, "Select content", "Only sections, lines, and bullet points can be removed.")
            return
        parent = item.parent()
        parent.takeChild(parent.indexOfChild(item))

    def move_tree_node(self, offset: int) -> None:
        item = self.cv_tree.currentItem()
        if not item or item.data(0, TREE_KIND_ROLE) not in {"section", "content"}:
            return
        parent = item.parent(); row = parent.indexOfChild(item); target = row + offset
        if 0 <= target < parent.childCount() and parent.child(target).data(0, TREE_KIND_ROLE) == item.data(0, TREE_KIND_ROLE):
            parent.takeChild(row); parent.insertChild(target, item); self.cv_tree.setCurrentItem(item)

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
                content = "\n".join(item.child(line).text(1) for line in range(item.childCount())).strip()
                section = {"title": item.text(0).strip(), "category": item.text(1).strip() or "Other", "content": content}
                original = item.data(0, TREE_DATA_ROLE) or {}
                if original.get("source_section_id") is not None and all(section[key] == original.get(key, "") for key in ("title", "category", "content")):
                    section["source_section_id"] = original["source_section_id"]
                sections.append(section)
        return name, sections, profile

    def save_cv_tree(self) -> None:
        cv_id = self.tree_cv_picker.currentData()
        if not cv_id:
            QMessageBox.information(self, "No CV selected", "Build or select a CV before saving.")
            return
        name, sections, profile = self.tree_values()
        if not name or not sections or any(not section["title"] or not section["content"] for section in sections):
            QMessageBox.warning(self, "Incomplete CV", "A CV needs a name and at least one titled section with content.")
            return
        updated = self.db.update_cv(cv_id, name, sections, profile)
        try:
            markdown_path, pdf_path = export_cv(updated, self.data_dir / "exports")
            self.db.update_cv_exports(updated.id, markdown_path, pdf_path)
            message = f'Saved and exported CV: {updated.name}'
        except Exception as error:
            message = f'Saved CV, but export failed: {error}'
            QMessageBox.warning(self, "CV saved, export failed", message)
        self.refresh_all(); self.statusBar().showMessage(message, 6000)

    def sections_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        header = QHBoxLayout(); header.addWidget(title("Section library", "Compose each CV from reusable, tailored content blocks.")); header.addStretch(); importer = secondary_button("Import CV…"); add = QPushButton("New section"); edit = secondary_button("Edit"); delete = secondary_button("Delete"); delete.setProperty("danger", True); importer.clicked.connect(self.import_existing_cv); add.clicked.connect(self.new_section); edit.clicked.connect(self.edit_section); delete.clicked.connect(self.delete_section); header.addWidget(importer); header.addWidget(add); header.addWidget(edit); header.addWidget(delete); layout.addLayout(header)
        self.section_table = self.table(["Title", "Category", "Job labels", "Words", "Content preview"]); self.section_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection); self.section_table.itemDoubleClicked.connect(lambda _: self.edit_section()); self.section_table.itemSelectionChanged.connect(self.refresh_section_details); layout.addWidget(self.section_table, 1)
        section_card, self.section_detail_labels = self.detail_card([
            ("identity", "Selected section"), ("labels", "Job labels"), ("content", "Full content"),
        ])
        layout.addWidget(section_card)
        return page

    def profile_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(16)
        layout.addWidget(title("Personal details", "Shown in the masthead of every newly created CV."))
        card = QFrame(); card.setProperty("card", True); form = QFormLayout(card); form.setContentsMargins(22, 22, 22, 22); self.profile_labels = {}
        for key, label in [("name", "Name"), ("phone", "Phone"), ("email", "Email"), ("github", "GitHub"), ("website", "Website")]:
            value = QLabel(); value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); self.profile_labels[key] = value; form.addRow(label, value)
        layout.addWidget(card); actions = QHBoxLayout(); edit = QPushButton("Edit personal details"); backup = secondary_button("Export full backup"); open_data = secondary_button("Open data folder"); edit.clicked.connect(self.edit_profile); backup.clicked.connect(self.export_backup); open_data.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_dir)))); actions.addWidget(edit); actions.addWidget(backup); actions.addWidget(open_data); actions.addStretch(); layout.addLayout(actions); layout.addStretch()
        return page

    def capture_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(16)
        layout.addWidget(title("Safari capture", "Receive a completed application from a companion Safari Web Extension on this Mac."))
        card = QFrame(); card.setProperty("card", True); form = QFormLayout(card); form.setContentsMargins(22, 22, 22, 22)
        self.capture_status = QLabel(); self.capture_endpoint = QLineEdit(); self.capture_endpoint.setReadOnly(True); self.capture_token = QLineEdit(); self.capture_token.setReadOnly(True)
        form.addRow("Status", self.capture_status); form.addRow("Local endpoint", self.capture_endpoint); form.addRow("Extension token", self.capture_token)
        layout.addWidget(card)
        actions = QHBoxLayout(); self.capture_toggle = QPushButton(); copy_endpoint = secondary_button("Copy endpoint"); copy_token = secondary_button("Copy token"); self.capture_toggle.clicked.connect(self.toggle_capture_bridge); copy_endpoint.clicked.connect(lambda: self.copy_capture_value(self.capture_endpoint.text())); copy_token.clicked.connect(lambda: self.copy_capture_value(self.capture_token.text())); actions.addWidget(self.capture_toggle); actions.addWidget(copy_endpoint); actions.addWidget(copy_token); actions.addStretch(); layout.addLayout(actions)
        guide = QLabel("The extension sends a POST request to the local endpoint with the X-CV-Manager-Token header. The app accepts only loopback requests, requires the token, and saves captured applications as Applied for review.")
        guide.setWordWrap(True); guide.setProperty("muted", True); layout.addWidget(guide); layout.addStretch(); self.refresh_capture_status()
        return page

    def fill_table(self, table: QTableWidget, records: list, values) -> None:
        table.setSortingEnabled(False); table.setRowCount(0)
        for record in records:
            row = table.rowCount(); table.insertRow(row)
            for col, value in enumerate(values(record)):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, record.id); table.setItem(row, col, item)
        table.resizeColumnsToContents(); table.setSortingEnabled(True)

    def refresh_all(self) -> None:
        applications, cvs, sections, counts = self.db.list_applications(), self.db.list_cvs(), self.db.list_sections(), self.db.status_counts()
        for status, card in self.status_cards.items():
            card.setText(f"<span style='font-size:25px; color:#1d4ed8'>{counts.get(status, 0)}</span><br><span style='color:#64748b'>{status}</span>")
        self.fill_table(self.recent_table, applications[:8], lambda a: [a.company, a.role, a.application_date, a.status])
        self._applications = applications; self._cvs = cvs; self.refresh_applications()
        cv_names = {cv.id: cv.name for cv in cvs}
        self.fill_table(self.cv_table, cvs, lambda cv: [cv.name, cv.created_at[:10], len(cv.sections), "Ready" if cv.pdf_path else "Not exported"])
        self.refresh_tree_picker(cvs)
        self.fill_table(self.section_table, sections, lambda s: [s.title, s.category, s.labels or "—", len(s.content.split()), s.content.replace("\n", " ")[:140]])
        profile = self.db.get_profile()
        for key, label in self.profile_labels.items(): label.setText(profile[key] or "—")
        self.refresh_cv_details(); self.refresh_section_details()
        self.refresh_capture_status()

    def refresh_applications(self) -> None:
        needle = self.application_search.text().strip().casefold() if hasattr(self, "application_search") else ""
        matches = [record for record in self._applications if needle in " ".join([record.company, record.role, record.location, record.status, record.notes, record.posting_url]).casefold()]
        cv_names = {cv.id: cv.name for cv in self._cvs}
        self.fill_table(self.application_table, matches, lambda a: [a.company, a.role, a.location or "—", a.application_date, a.status, cv_names.get(a.cv_id, "—")])
        self.refresh_application_details()

    def refresh_application_details(self) -> None:
        if not hasattr(self, "application_detail_labels"):
            return
        application_id = self.selected_id(self.application_table)
        application = self.db.get_application(application_id) if application_id else None
        if not application:
            values = {"job": "Select an application to see all of its details.", "timeline": "—", "cv": "—", "posting": "—", "notes": "—"}
        else:
            cv = self.db.get_cv(application.cv_id) if application.cv_id else None
            values = {
                "job": f"{application.role} at {application.company}" + (f" · {application.location}" if application.location else ""),
                "timeline": f"Applied {application.application_date} · {application.status}",
                "cv": cv.name if cv else "No CV linked",
                "posting": application.posting_url or "No URL saved",
                "notes": application.notes or "No notes saved",
            }
        for key, value in values.items(): self.application_detail_labels[key].setText(value)

    def refresh_cv_details(self) -> None:
        if not hasattr(self, "cv_detail_labels"):
            return
        cv_id = self.selected_id(self.cv_table)
        cv = self.db.get_cv(cv_id) if cv_id else None
        if not cv:
            values = {"identity": "Select a CV to inspect its saved snapshot.", "contact": "—", "sections": "—", "applications": "—", "exports": "—"}
        else:
            contact = " · ".join(value for value in (cv.profile.get("phone"), cv.profile.get("email"), cv.profile.get("github"), cv.profile.get("website")) if value)
            linked = [f"{item.role} at {item.company}" for item in self.db.list_applications() if item.cv_id == cv.id]
            exports = " · ".join(path for path in (cv.markdown_path, cv.pdf_path) if path)
            values = {
                "identity": f"{cv.name} · created {cv.created_at.replace('T', ' ')} · {cv.profile.get('name', '')}",
                "contact": contact or "No contact details saved",
                "sections": " → ".join(section.get("title", "Untitled") for section in cv.sections),
                "applications": ", ".join(linked) if linked else "Not linked to an application",
                "exports": exports or "Not exported yet",
            }
        for key, value in values.items(): self.cv_detail_labels[key].setText(value)

    def refresh_section_details(self) -> None:
        if not hasattr(self, "section_detail_labels"):
            return
        section_id = self.selected_id(self.section_table)
        section = self.db.get_section(section_id) if section_id else None
        if section:
            identity = f"{section.title} · {section.category} · {len(section.content.split())} words"
            labels = section.labels or "No job labels"
            content = section.content
        else:
            identity, labels, content = "Select a section to read its complete reusable content.", "—", "—"
        self.section_detail_labels["identity"].setText(identity)
        self.section_detail_labels["labels"].setText(labels)
        self.section_detail_labels["content"].setText(content)

    @staticmethod
    def selected_id(table: QTableWidget) -> int | None:
        row = table.currentRow(); return table.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 and table.item(row, 0) else None

    @staticmethod
    def selected_ids(table: QTableWidget) -> list[int]:
        return [index.data(Qt.ItemDataRole.UserRole) for index in table.selectionModel().selectedRows(0)]

    def edit_profile(self) -> None:
        dialog = ProfileDialog(self.db.get_profile(), self)
        if dialog.exec(): self.db.update_profile(dialog.values()); self.refresh_all()

    def new_section(self) -> None:
        dialog = SectionDialog(parent=self)
        if dialog.exec(): self.db.create_section(*dialog.values()); self.refresh_all()

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

    def edit_section(self) -> None:
        section_id = self.selected_id(self.section_table)
        if not section_id: QMessageBox.information(self, "Select a section", "Select a section to edit."); return
        dialog = SectionDialog(self.db.get_section(section_id), self)
        if not dialog.exec():
            return
        affected_cv_ids = self.db.update_section(section_id, *dialog.values())
        failed_exports = []
        for cv_id in affected_cv_ids:
            cv = self.db.get_cv(cv_id)
            try:
                markdown_path, pdf_path = export_cv(cv, self.data_dir / "exports")
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
            self.statusBar().showMessage(f"Updated this section in {len(affected_cv_ids)} linked CV(s) and regenerated their exports.", 6000)

    def delete_section(self) -> None:
        section_ids = self.selected_ids(self.section_table)
        if not section_ids:
            QMessageBox.information(self, "Select sections", "Select one or more sections to delete.")
            return
        subject = "this reusable section" if len(section_ids) == 1 else f"these {len(section_ids)} reusable sections"
        message = f"Delete {subject}? Existing CV snapshots will remain unchanged."
        if QMessageBox.question(self, "Delete sections", message) == QMessageBox.StandardButton.Yes:
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
        if self.capture_bridge.is_running:
            self.capture_status.setText("<b style='color:#15803d'>Listening on this Mac</b>")
            self.capture_endpoint.setText(self.capture_bridge.endpoint or "")
            self.capture_token.setText(self.capture_bridge.token)
            self.capture_toggle.setText("Stop capture bridge")
        else:
            self.capture_status.setText("<b style='color:#64748b'>Not running</b>")
            self.capture_endpoint.clear(); self.capture_token.clear(); self.capture_toggle.setText("Start capture bridge")

    def toggle_capture_bridge(self) -> None:
        if self.capture_bridge.is_running:
            self.capture_bridge.stop()
        else:
            self.capture_bridge.start()
        self.refresh_capture_status()

    @staticmethod
    def copy_capture_value(value: str) -> None:
        if value:
            QApplication.clipboard().setText(value)

    def receive_capture(self, values: dict) -> None:
        self.db.create_application(**values)
        self.refresh_all()
        self.statusBar().showMessage(f"Captured application: {values['role']} at {values['company']}", 6000)

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
        QMessageBox.information(self, "Backup exported", "Your CVs, sections, applications, and profile were exported as JSON.")

    def new_cv(self) -> None:
        sections = self.db.list_sections()
        if not sections: QMessageBox.information(self, "Add content first", "Create at least one reusable section before building a CV."); return
        profile = self.db.get_profile(); dialog = CVDialog(sections, profile, parent=self)
        if dialog.exec():
            cv = self.db.create_cv(dialog.name.text().strip(), dialog.chosen_sections(), profile)
            try:
                markdown_path, pdf_path = export_cv(cv, self.data_dir / "exports")
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
        updated = self.db.update_cv(cv.id, dialog.name.text().strip(), dialog.chosen_sections(), cv.profile)
        try:
            markdown_path, pdf_path = export_cv(updated, self.data_dir / "exports")
            self.db.update_cv_exports(updated.id, markdown_path, pdf_path)
            self.statusBar().showMessage(f'Updated and exported CV: {updated.name}', 6000)
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
            markdown_path, pdf_path = export_cv(cv, self.data_dir / "exports")
            self.db.update_cv_exports(cv.id, markdown_path, pdf_path)
            self.refresh_all()
            QMessageBox.information(self, "Export regenerated", "The Markdown and PDF were regenerated from this CV's saved snapshot.")
        except Exception as error:
            QMessageBox.warning(self, "Export failed", f"Could not regenerate this CV:\n{error}")

    def open_selected_pdf(self) -> None:
        cv = self.selected_cv()
        if cv and cv.pdf_path and Path(cv.pdf_path).exists(): QDesktopServices.openUrl(QUrl.fromLocalFile(cv.pdf_path))
        elif cv: QMessageBox.information(self, "No PDF found", "The PDF export is not available. Regenerate this CV to create one.")

    def open_export_folder(self) -> None:
        export_dir = self.data_dir / "exports"; export_dir.mkdir(exist_ok=True); QDesktopServices.openUrl(QUrl.fromLocalFile(str(export_dir)))

    def closeEvent(self, event) -> None:
        self.capture_bridge.stop()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv); app.setOrganizationName("CV Manager"); app.setApplicationName("CV Manager"); app.setStyleSheet(APP_STYLESHEET)
    window = MainWindow(); window.show(); sys.exit(app.exec())
