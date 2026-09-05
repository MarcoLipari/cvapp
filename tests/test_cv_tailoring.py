import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from cv_document_editor import parse_sections_markdown, sections_markdown
from cv_export import render_markdown
from cv_tailoring import BulletSelectionDialog, is_tailored, tailoring_snapshot
from database import CVDatabase
from main import CVDialog


class CVTailoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = CVDatabase(Path(self.temp.name) / "cv.sqlite3")
        self.entry_id = self.db.create_section("Experience", "Experience", "**Engineer**\n- Built APIs.\n- Led delivery.")
        self.entry = self.db.get_section(self.entry_id)
        self.cv = self.db.create_cv("Backend", [self.entry], {"name": "Example Person"})

    def dialog(self, tailoring=True, cv=None):
        dialog = CVDialog([self.entry], self.cv.profile, cv=cv or self.cv, tailoring=tailoring)
        self.addCleanup(dialog.close)
        return dialog

    def test_variation_starts_identical_but_is_not_linked(self):
        dialog = self.dialog()
        section = dialog.chosen_sections()[0]
        self.assertNotIn("source_section_id", section)
        self.assertEqual(section["content"], self.entry.content)
        self.assertFalse(is_tailored(section))
        self.assertIn("Built APIs", dialog.preview.toPlainText())
        dialog.company.setText("Acme")
        dialog.role.setText("Platform Engineer")
        self.assertEqual(dialog.name.text(), "Acme — Platform Engineer")

    def test_inline_edit_is_local_and_persists_baseline_after_reopening(self):
        dialog = self.dialog(False)
        dialog.wording.setPlainText("**Engineer**\n- Built Python APIs.")
        section = dialog.chosen_sections()[0]
        self.assertTrue(is_tailored(section))
        self.assertIn("Tailored", dialog.selected.item(0).text())
        saved = self.db.create_cv("Acme", dialog.chosen_sections(), tailoring={"source_cv_id": self.cv.id})
        reopened = self.dialog(False, saved)
        self.assertEqual(reopened.chosen_sections()[0]["tailoring_base"]["content"], self.entry.content)
        self.assertEqual(self.db.get_cv(self.cv.id).sections[0]["content"], self.entry.content)
        self.assertEqual(self.db.get_section(self.entry_id).content, self.entry.content)
        self.assertEqual(saved.tailoring["source_cv_id"], self.cv.id)
        self.assertEqual(self.db.list_cv_history(saved.id)[0].snapshot["tailoring"], saved.tailoring)
        self.assertEqual(next(cv for cv in self.db.backup_data()["cvs"] if cv["id"] == saved.id)["tailoring"], saved.tailoring)

    def test_shared_updates_do_not_recapture_identical_variation(self):
        variation = self.db.create_cv("Acme", self.dialog().chosen_sections())
        self.db.update_section(self.entry_id, "Experience", "Experience", "**Engineer**\n- Updated library.", "")
        self.assertEqual(self.db.get_cv(variation.id).sections[0]["content"], self.entry.content)
        self.assertIn("Updated library", self.db.get_cv(self.cv.id).sections[0]["content"])
        self.assertEqual(self.db.count_linked_cvs(self.entry_id), 1)

    def test_reset_restores_baseline_without_linking(self):
        dialog = self.dialog()
        dialog.wording.setPlainText("Different wording")
        def reset(comparison):
            buttons = comparison.findChild(QDialogButtonBox)
            next(button for button in buttons.buttons() if button.text() == "Reset to starting wording").click()
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog, "exec", reset):
            dialog.compare_selected()
        section = dialog.chosen_sections()[0]
        self.assertEqual(section["content"], self.entry.content)
        self.assertFalse(is_tailored(section))
        self.assertNotIn("source_section_id", section)

    def test_duplicate_library_entry_is_not_added_to_variation(self):
        dialog = self.dialog()
        dialog.available.item(0).setSelected(True)
        dialog.add_sections()
        self.assertEqual(dialog.selected.count(), 1)

    def test_cancel_does_not_create_a_cv(self):
        dialog = self.dialog()
        dialog.wording.setPlainText("Unsaved edit")
        dialog.reject()
        self.assertEqual(len(self.db.list_cvs()), 1)
        self.assertEqual(self.db.get_cv(self.cv.id).sections[0]["content"], self.entry.content)

    def test_document_editor_preserves_tailoring_baseline(self):
        section = tailoring_snapshot(self.cv.sections[0])
        edited = parse_sections_markdown(sections_markdown([section]).replace("Built APIs.", "Built services."), [section])
        saved = self.db.create_cv("Edited", edited)
        self.assertEqual(saved.sections[0]["tailoring_base"], section["tailoring_base"])
        self.assertNotIn("tailoring_base", render_markdown(saved))

    def test_bullet_selection_preserves_markdown_and_continuations(self):
        content = "**Engineer** :: *2026*\n- Built APIs.\n  With Python.\n- Led delivery.\n\n**Project**\n+ Shipped it."
        dialog = BulletSelectionDialog(content)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.content(), content)
        dialog.items.item(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertNotIn("With Python", dialog.content())
        self.assertIn("**Project**", dialog.content())
        reopened = BulletSelectionDialog(dialog.content(), saved_rows=dialog.rows())
        self.addCleanup(reopened.close)
        reopened.items.item(1).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(reopened.content(), content)

    def test_bullet_order_preserves_separators_and_group_boundaries(self):
        dialog = BulletSelectionDialog("**Engineer**\n- First\n- Second")
        self.addCleanup(dialog.close)
        dialog.items.setCurrentRow(2)
        dialog.move(-1)
        self.assertEqual(dialog.content(), "**Engineer**\n- Second\n- First\n")
        dialog.move(-1)
        self.assertTrue(dialog.content().startswith("**Engineer**\n"))

    def test_stale_bullet_state_cannot_overwrite_document_edits(self):
        dialog = BulletSelectionDialog(self.entry.content)
        self.addCleanup(dialog.close)
        reopened = BulletSelectionDialog("- New wording", saved_rows=dialog.rows())
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.content(), "- New wording")

    def test_bullet_choices_survive_save_and_reopen(self):
        editor = self.dialog()
        def choose(dialog):
            dialog.items.item(1).setCheckState(Qt.CheckState.Unchecked)
            return QDialog.DialogCode.Accepted
        with patch.object(BulletSelectionDialog, "exec", choose):
            editor.choose_bullets()
        saved = self.db.create_cv("Acme", editor.chosen_sections())
        section = saved.sections[0]
        self.assertNotIn("Built APIs", section["content"])
        selector = BulletSelectionDialog(section["content"], saved_rows=section["tailoring_bullets"])
        self.addCleanup(selector.close)
        selector.items.item(1).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(selector.content(), self.entry.content)

    def test_filtering_bullets_keeps_hidden_selections(self):
        dialog = BulletSelectionDialog(self.entry.content)
        self.addCleanup(dialog.close)
        dialog.search.setText("delivery")
        self.assertTrue(dialog.items.item(1).isHidden())
        self.assertEqual(dialog.content(), self.entry.content)
        dialog.search.clear()
        self.assertFalse(dialog.items.item(1).isHidden())

    def test_reopened_variation_keeps_new_entries_independent(self):
        saved = self.db.create_cv("Acme", self.dialog().chosen_sections(), tailoring={"source_cv_id": self.cv.id})
        second_id = self.db.create_section("Skills", "Skills", "Python, SQL")
        second = self.db.get_section(second_id)
        dialog = CVDialog([second], saved.profile, cv=saved)
        self.addCleanup(dialog.close)
        dialog.available.item(0).setSelected(True)
        dialog.add_sections()
        self.assertNotIn("source_section_id", dialog.chosen_sections()[1])

    def test_update_keeps_job_context_and_full_comparison(self):
        context = {"source_cv_id": self.cv.id, "sections": self.cv.sections, "company": "Acme", "posting": "Platform role"}
        saved = self.db.create_cv("Acme", self.dialog().chosen_sections(), tailoring=context)
        updated = self.db.update_cv(saved.id, "Acme final", saved.sections)
        self.assertEqual(updated.tailoring, context)
        reopened = self.dialog(False, updated)
        self.assertEqual(reopened._starting_sections, self.cv.sections)
