import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from database import Section
from entry_consolidation import (
    ConsolidationDialog, entry_sources, matching_groups,
    merged_content, merged_keywords, title_key,
)
from main import MainWindow


class EntryConsolidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def section(self, id=1, heading="**Data Engineering Intern** :: *May 2026 — Present,&#x20;*", body="*Example Company*\n- Built Python pipelines.", labels="Python"):
        return Section(id, "Experience", "Experience", heading + "\n" + body, id, labels, f"Version {id}")

    def test_title_matching_ignores_dates_formatting_entities_and_case(self):
        self.assertEqual(title_key("**Data Engineering Intern** :: *May 2026 — Present,&#x20;*"), title_key("__data&#x20;engineering  intern__ :: *2025*"))
        self.assertEqual(title_key("**[Data Engineering Intern](https://example.com)**"), "data engineering intern")
        self.assertNotEqual(title_key("**Data Engineer**"), title_key("**Data Engineering Intern**"))

    def test_multi_role_entries_only_supply_matching_role_content(self):
        first = self.section(body="*Example Company*\n- Built pipelines.\n\n**Other Role** :: *2024*\n- Other achievement.")
        second = self.section(2, body="*Example Company*\n- Led migration.")
        groups = matching_groups([first, second])
        self.assertEqual(len(groups), 1)
        result = merged_content(groups[0])
        self.assertIn("Led migration", result)
        self.assertNotIn("Other achievement", result)
        self.assertNotIn("Other Role", result)

    def test_deduplicates_bullets_but_preserves_distinct_wording_and_links(self):
        first = self.section(body="*Example Company*\n- Built APIs.\n- Reduced latency 20%.\n- See [work](https://one.example).")
        second = self.section(2, body="*Example Company*\n* Built APIs.\n- Reduced latency 30%.\n- See [work](https://two.example).")
        content = merged_content(entry_sources([first, second]))
        self.assertEqual(content.count("Built APIs."), 1)
        self.assertEqual(content.count("*Example Company*"), 1)
        self.assertIn("20%", content)
        self.assertIn("30%", content)
        self.assertIn("https://one.example", content)
        self.assertIn("https://two.example", content)
        self.assertEqual(content.count("Data Engineering Intern"), 1)

    def test_multiline_bullet_and_bold_inside_bullet_are_preserved(self):
        sources = entry_sources([self.section(body="- Built **Python** pipelines.\n  Including [lineage](https://example.com).")])
        self.assertEqual(len(sources), 1)
        self.assertIn("\n  Including [lineage]", merged_content(sources))

    def test_keywords_are_combined_without_case_duplicates(self):
        sources = entry_sources([self.section(labels="Python, SQL"), self.section(2, labels="python, Leadership")])
        self.assertEqual(merged_keywords(sources), "Python, SQL, Leadership")

    def test_dialog_can_exclude_source_and_edit_draft(self):
        dialog = ConsolidationDialog(matching_groups([self.section(), self.section(2, body="- Led migration.")]))
        self.addCleanup(dialog.close)
        self.assertIn("Led migration", dialog.content.toPlainText())
        dialog.sources.item(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertNotIn("Led migration", dialog.content.toPlainText())
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled())
        dialog.sources.item(1).setCheckState(Qt.CheckState.Checked)
        dialog.content.setPlainText("**Data Engineering Intern**\n- Reviewed combined content.")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_creating_mega_entry_preserves_sources_cv_links_and_history(self):
        with tempfile.TemporaryDirectory() as path:
            with patch('main.app_data_dir', return_value=Path(path)), patch('safari_bridge_store.default_bridge_dir', return_value=Path(path)/'bridge'), patch.object(MainWindow, 'prompt_for_initial_profile', new=lambda self: None):
                window = MainWindow()
                try:
                    for section in (self.section(), self.section(2, body="- Led migration.")):
                        window.db.create_section(section.title, section.category, section.content, section.labels, section.internal_name)
                    originals = window.db.list_sections()
                    cv = window.db.create_cv("Existing", originals, {"name": "Example Person"})
                    histories = [window.db.list_section_history(section.id) for section in originals]
                    window.refresh_all()
                    window.nav.setCurrentRow(4)
                    with patch.object(ConsolidationDialog, 'exec', new=lambda self: QDialog.DialogCode.Accepted):
                        window.consolidate_library_entries()
                    entries = window.db.list_sections()
                    self.assertEqual(len(entries), 3)
                    self.assertEqual(entries[:2], originals)
                    self.assertEqual(window.db.get_cv(cv.id), cv)
                    self.assertEqual([window.db.list_section_history(section.id) for section in originals], histories)
                    mega = window.db.get_section(window.selected_section_id())
                    self.assertIn("Mega", mega.internal_name)
                    self.assertIn("Built Python", mega.content)
                    self.assertIn("Led migration", mega.content)
                    self.assertEqual(len(window.db.list_section_history(mega.id)), 1)
                finally:
                    window.close()

    def test_cancel_creates_nothing(self):
        with tempfile.TemporaryDirectory() as path:
            with patch('main.app_data_dir', return_value=Path(path)), patch('safari_bridge_store.default_bridge_dir', return_value=Path(path)/'bridge'), patch.object(MainWindow, 'prompt_for_initial_profile', new=lambda self: None):
                window = MainWindow()
                try:
                    for section in (self.section(), self.section(2)):
                        window.db.create_section(section.title, section.category, section.content)
                    window.refresh_all(); window.nav.setCurrentRow(4)
                    with patch.object(ConsolidationDialog, 'exec', new=lambda self: QDialog.DialogCode.Rejected):
                        window.consolidate_library_entries()
                    self.assertEqual(len(window.db.list_sections()), 2)
                finally:
                    window.close()
