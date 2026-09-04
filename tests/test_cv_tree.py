import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from PySide6.QtWidgets import QMessageBox, QTreeWidgetItem

from cv_export import CVExportMetrics, CVOverflowError
from database import CV, CVDatabase, CVHistory
from main import MainWindow, TREE_DATA_ROLE, TREE_EDIT_MODE_ROLE, TREE_KIND_ROLE, is_bullet_item


class CVTreeTests(unittest.TestCase):
    def tree_helper(self):
        helper = type("TreeHelper", (), {})()
        helper.tree_item = MainWindow.tree_item
        helper.content_node_label = MainWindow.content_node_label
        helper.section_uses_entries = MainWindow.section_uses_entries
        helper.section_item_content = MainWindow.section_item_content
        return helper

    def test_overflow_warning_can_continue_with_multipage_export(self):
        cv = CV(
            id=7,
            name="Long CV",
            created_at="2026-09-03T00:00:00",
            sections=[],
            profile={"name": "Ada Lovelace"},
            markdown_path=None,
            pdf_path=None,
        )
        expected = (Path("cv.md"), Path("cv.pdf"))
        shrink_button = object()
        multipage_button = object()

        with (
            patch("main.export_cv", side_effect=[CVOverflowError(), expected]) as exporter,
            patch("main.QMessageBox") as message_box,
        ):
            dialog = message_box.return_value
            dialog.addButton.side_effect = [shrink_button, multipage_button, None]
            dialog.clickedButton.return_value = multipage_button

            result = MainWindow.export_cv_with_overflow_warning(
                None,
                cv,
                Path("exports"),
            )

        self.assertEqual(result, expected)
        self.assertEqual(exporter.call_count, 2)
        exporter.assert_called_with(cv, Path("exports"), allow_multipage=True)

    def test_overflow_warning_can_shrink_to_one_page(self):
        cv = CV(
            id=7,
            name="Long CV",
            created_at="2026-09-03T00:00:00",
            sections=[],
            profile={"name": "Ada Lovelace"},
            markdown_path=None,
            pdf_path=None,
        )
        expected = (Path("cv.md"), Path("cv.pdf"))
        shrink_button = object()

        with (
            patch("main.export_cv", side_effect=[CVOverflowError(), expected]) as exporter,
            patch("main.QMessageBox") as message_box,
        ):
            dialog = message_box.return_value
            dialog.addButton.side_effect = [shrink_button, object(), None]
            dialog.clickedButton.return_value = shrink_button

            result = MainWindow.export_cv_with_overflow_warning(
                None,
                cv,
                Path("exports"),
            )

        self.assertEqual(result, expected)
        self.assertEqual(exporter.call_count, 2)
        exporter.assert_called_with(
            cv,
            Path("exports"),
            shrink_to_fit=True,
            metrics=ANY,
        )
        metrics = exporter.call_args.kwargs["metrics"]
        self.assertIsInstance(metrics, CVExportMetrics)
        message_box.information.assert_called_once_with(
            None,
            "CV shrunk to one page",
            "Final body font size: 11.0 pt\n"
            "Recommended body font size for standard CVs: 10-12 pt.",
        )

    def test_groups_entry_bullets_and_preserves_section_content(self):
        content = (
            "**Data Engineer** :: *2025 - Present*\n"
            "*Example Co* :: *Toronto*\n"
            "- Built pipelines.\n"
            "- Improved reliability.\n"
            "**Developer** :: *2023 - 2025*\n"
            "- Shipped features."
        )
        helper = self.tree_helper()
        root = MainWindow.tree_item("cv", "CV", "Test CV")
        helper.cv_tree = type("Tree", (), {"topLevelItem": lambda self, index: root})()
        section = MainWindow.tree_item(
            "section", "Experience", "Experience",
            {"title": "Experience", "category": "Experience", "content": content, "source_section_id": 7},
        )
        root.addChild(section)

        MainWindow.add_section_content_to_tree(helper, section, content)

        self.assertEqual(section.childCount(), 2)
        self.assertEqual(section.child(0).data(0, TREE_KIND_ROLE), "entry")
        self.assertEqual(section.child(0).childCount(), 3)
        self.assertEqual(section.child(0).child(0).data(0, TREE_KIND_ROLE), "details")
        self.assertEqual(section.child(0).child(0).text(0), "Organization / location")
        self.assertEqual(section.child(0).child(1).text(1), "- Built pipelines.")

        name, sections, profile = MainWindow.tree_values(helper)
        self.assertEqual(name, "Test CV")
        self.assertEqual(profile, {})
        self.assertEqual(sections[0]["content"], content)
        self.assertEqual(sections[0]["source_section_id"], 7)

    def test_education_lines_remain_direct_section_children(self):
        helper = self.tree_helper()
        section = MainWindow.tree_item("section", "Education", "Education")
        MainWindow.add_section_content_to_tree(helper, section, "**University** :: *2024*\n- Degree")

        self.assertEqual(section.childCount(), 2)
        self.assertEqual(section.child(0).data(0, TREE_KIND_ROLE), "content")
        self.assertEqual(section.child(1).data(0, TREE_KIND_ROLE), "content")

    def test_chosen_linked_section_action_preserves_source_for_transactional_save(self):
        helper = self.tree_helper()
        root = MainWindow.tree_item("cv", "CV", "Test CV")
        helper.cv_tree = type("Tree", (), {"topLevelItem": lambda self, index: root})()
        section = MainWindow.tree_item(
            "section", "Skills", "Skills",
            {"title": "Skills", "category": "Skills", "content": "Python", "source_section_id": 7},
        )
        section.setData(0, TREE_EDIT_MODE_ROLE, "copy")
        section.addChild(MainWindow.tree_item("content", "Line", "Python and Rust"))
        root.addChild(section)

        _, sections, _ = MainWindow.tree_values(helper)

        self.assertEqual(sections[0]["content"], "Python and Rust")
        self.assertEqual(sections[0]["source_section_id"], 7)

    def test_changed_linked_section_choice_is_deferred_until_save_resolution(self):
        helper = self.tree_helper()
        root = MainWindow.tree_item("cv", "CV", "Test CV")
        helper.cv_tree = type("Tree", (), {"topLevelItem": lambda self, index: root})()
        section = MainWindow.tree_item(
            "section", "Skills", "Skills",
            {"title": "Skills", "category": "Skills", "content": "Python", "source_section_id": 7},
        )
        section.addChild(MainWindow.tree_item("content", "Line", "Python and Rust"))
        root.addChild(section)
        prompted = []

        def choose(item):
            prompted.append(item)
            item.setData(0, TREE_EDIT_MODE_ROLE, "copy")
            return True

        helper.prompt_tree_section_action = choose

        self.assertIsNone(section.data(0, TREE_EDIT_MODE_ROLE))
        self.assertTrue(MainWindow.resolve_tree_section_actions(helper))
        self.assertEqual(prompted, [section])
        self.assertEqual(section.data(0, TREE_EDIT_MODE_ROLE), "copy")

    def test_cancelled_save_resolution_keeps_tree_edit_unsaved(self):
        helper = self.tree_helper()
        root = MainWindow.tree_item("cv", "CV", "Test CV")
        helper.cv_tree = type("Tree", (), {"topLevelItem": lambda self, index: root})()
        section = MainWindow.tree_item(
            "section", "Skills", "Skills",
            {"title": "Skills", "category": "Skills", "content": "Python", "source_section_id": 7},
        )
        section.addChild(MainWindow.tree_item("content", "Line", "Python and Rust"))
        root.addChild(section)
        helper.prompt_tree_section_action = lambda item: False

        self.assertFalse(MainWindow.resolve_tree_section_actions(helper))
        self.assertEqual(MainWindow.section_item_content(section), "Python and Rust")
        self.assertIsNone(section.data(0, TREE_EDIT_MODE_ROLE))

    def test_cv_specific_link_action_is_preserved_in_tree_values(self):
        helper = self.tree_helper()
        root = MainWindow.tree_item("cv", "CV", "Test CV")
        helper.cv_tree = type("Tree", (), {"topLevelItem": lambda self, index: root})()
        section = MainWindow.tree_item(
            "section",
            "Experience",
            "Experience",
            {"title": "Experience", "category": "Experience", "content": "Developer"},
        )
        section.setData(0, TREE_EDIT_MODE_ROLE, "link")
        section.addChild(MainWindow.tree_item("entry", "Entry", "Developer"))
        root.addChild(section)

        _, sections, _ = MainWindow.tree_values(helper)

        self.assertEqual(sections, [{
            "title": "Experience",
            "category": "Experience",
            "content": "Developer",
        }])

    def test_cv_specific_link_action_needs_no_shared_entry_prompt(self):
        helper = self.tree_helper()
        root = MainWindow.tree_item("cv", "CV", "Test CV")
        helper.cv_tree = type("Tree", (), {"topLevelItem": lambda self, index: root})()
        section = MainWindow.tree_item(
            "section",
            "Experience",
            "Experience",
            {"title": "Experience", "category": "Experience", "content": "Developer"},
        )
        section.setData(0, TREE_EDIT_MODE_ROLE, "link")
        section.addChild(MainWindow.tree_item("entry", "Entry", "Developer"))
        root.addChild(section)
        helper.prompt_tree_section_action = lambda _item: self.fail("Linking should not prompt")

        self.assertTrue(MainWindow.resolve_tree_section_actions(helper))
        self.assertEqual(section.data(0, TREE_EDIT_MODE_ROLE), "link")

    def test_link_action_drops_a_missing_legacy_source_before_save(self):
        helper = self.tree_helper()
        root = MainWindow.tree_item("cv", "CV", "Test CV")
        helper.cv_tree = type("Tree", (), {"topLevelItem": lambda self, index: root})()
        section = MainWindow.tree_item(
            "section",
            "Skills",
            "Skills",
            {"title": "Skills", "category": "Skills", "content": "Python", "source_section_id": 99},
        )
        section.setData(0, TREE_EDIT_MODE_ROLE, "link")
        section.addChild(MainWindow.tree_item("content", "Line", "Python"))
        root.addChild(section)

        _, sections, _ = MainWindow.tree_values(helper)

        self.assertNotIn("source_section_id", sections[0])

    def test_cv_tree_nested_row_resolves_to_owning_section(self):
        section = MainWindow.tree_item("section", "Experience", "Experience")
        entry = MainWindow.tree_item("entry", "Entry", "Developer")
        bullet = MainWindow.tree_item("content", "Bullet", "- Built systems")
        section.addChild(entry)
        entry.addChild(bullet)

        self.assertIs(MainWindow.cv_tree_section_item(bullet), section)

    def test_library_child_resolves_to_owning_section(self):
        section = MainWindow.tree_item("section", "Experience", "Experience", 42)
        entry = MainWindow.tree_item("entry", "Entry", "Developer")
        bullet = MainWindow.tree_item("content", "Bullet", "- Built software")
        section.addChild(entry)
        entry.addChild(bullet)

        owner = MainWindow.library_section_item(bullet)

        self.assertIs(owner, section)
        self.assertEqual(owner.data(0, TREE_KIND_ROLE), "section")
        self.assertEqual(owner.data(0, TREE_DATA_ROLE), 42)

    def test_library_bullet_can_move_between_adjacent_bullets(self):
        entry = MainWindow.tree_item("entry", "Entry", "Developer")
        first = MainWindow.tree_item("content", "Bullet", "- First")
        second = MainWindow.tree_item("content", "Bullet", "- Second")
        entry.addChildren([first, second])
        helper = type("LibraryHelper", (), {})()
        helper.library_bullet_move_target = MainWindow.library_bullet_move_target
        helper.library_bullet_moved = lambda _item: None
        helper.section_tree = type("Tree", (), {"setCurrentItem": lambda self, item: None})()

        MainWindow.move_library_bullet(helper, first, 1)

        self.assertEqual([entry.child(index).text(1) for index in range(2)], ["- Second", "- First"])
        self.assertTrue(is_bullet_item(first))

    def test_library_bullet_does_not_move_across_non_bullet_content(self):
        entry = MainWindow.tree_item("entry", "Entry", "Developer")
        details = MainWindow.tree_item("details", "Organization / location", "Example Co")
        bullet = MainWindow.tree_item("content", "Bullet", "- First")
        entry.addChildren([details, bullet])

        self.assertIsNone(MainWindow.library_bullet_move_target(bullet, -1))

    def test_library_bullet_inserts_below_clicked_content(self):
        helper = self.tree_helper()
        helper.section_uses_entries = MainWindow.section_uses_entries
        section = MainWindow.tree_item("section", "Experience", "Experience")
        entry = MainWindow.tree_item("entry", "Entry", "Developer")
        first = MainWindow.tree_item("content", "Bullet", "- First")
        entry.addChild(first)
        section.addChild(entry)

        parent, row = MainWindow.library_bullet_insertion_point(helper, first)

        self.assertIs(parent, entry)
        self.assertEqual(row, 1)

    def test_library_sub_entry_can_be_deleted_without_deleting_its_parent(self):
        section = MainWindow.tree_item("section", "Projects", "Projects", 42)
        first = MainWindow.tree_item("entry", "Entry", "Project A")
        first.addChild(MainWindow.tree_item("content", "Bullet", "- First"))
        second = MainWindow.tree_item("entry", "Entry", "Project B")
        second.addChild(MainWindow.tree_item("content", "Bullet", "- Second"))
        section.addChildren([first, second])

        helper = type("LibraryHelper", (), {})()
        helper.library_section_item = MainWindow.library_section_item
        helper.section_item_content = MainWindow.section_item_content
        helper.autosave_library_section = lambda item: True
        helper.style_library_section = lambda item: None
        helper.refresh_section_preview = lambda item: None
        helper.section_tree = type("Tree", (), {"setCurrentItem": lambda self, item: None})()
        helper.statusBar = lambda: type("Status", (), {"showMessage": lambda *args: None})()

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            MainWindow.delete_library_node(helper, first)

        self.assertEqual(section.childCount(), 1)
        self.assertEqual(section.child(0).text(1), "Project B")

    def test_library_entry_split_uses_clicked_sub_entry_as_boundary(self):
        section = MainWindow.tree_item("section", "Experience history", "Experience", 42)
        first = MainWindow.tree_item("entry", "Entry", "**Data Engineer** :: *2025 - Present*")
        first.addChild(MainWindow.tree_item("content", "Bullet", "- Built pipelines."))
        second = MainWindow.tree_item("entry", "Entry", "**Developer** :: *2023 - 2025*")
        second.addChild(MainWindow.tree_item("content", "Bullet", "- Shipped features."))
        third = MainWindow.tree_item("entry", "Entry", "**Analyst** :: *2022 - 2023*")
        third.addChild(MainWindow.tree_item("content", "Bullet", "- Analyzed data."))
        section.addChildren([first, second, third])

        split_section, boundary = MainWindow.library_entry_split_point(second)

        self.assertIs(split_section, section)
        self.assertEqual(boundary, 1)
        self.assertEqual(
            MainWindow.section_content_between(section, 0, boundary),
            "**Data Engineer** :: *2025 - Present*\n- Built pipelines.",
        )
        self.assertEqual(
            MainWindow.section_content_between(section, boundary, section.childCount()),
            "**Developer** :: *2023 - 2025*\n- Shipped features.\n"
            "**Analyst** :: *2022 - 2023*\n- Analyzed data.",
        )

    def test_library_entry_split_from_parent_or_first_entry_keeps_first_entry_first(self):
        section = MainWindow.tree_item("section", "Experience history", "Experience", 42)
        first = MainWindow.tree_item("entry", "Entry", "First")
        second = MainWindow.tree_item("entry", "Entry", "Second")
        section.addChildren([first, second])

        self.assertEqual(MainWindow.library_entry_split_point(section), (section, 1))
        self.assertEqual(MainWindow.library_entry_split_point(first), (section, 1))

    def test_library_entry_split_requires_multiple_sub_entries(self):
        section = MainWindow.tree_item("section", "Project", "Projects", 42)
        only = MainWindow.tree_item("entry", "Entry", "Only project")
        section.addChild(only)

        self.assertIsNone(MainWindow.library_entry_split_point(section))
        self.assertIsNone(MainWindow.library_entry_split_point(only))

    def test_split_entry_internal_name_removes_markdown_and_date(self):
        name = MainWindow.split_entry_internal_name(
            "**[Software Engineer](https://example.com)** :: *2024 - Present*",
            "Experience",
        )

        self.assertEqual(name, "Software Engineer")

    def test_library_edit_is_autosaved_before_a_section_is_duplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CVDatabase(Path(directory) / "test.sqlite3")
            section_id = db.create_section(
                "Skills", "Skills", "Python", "backend", internal_name="Core skills"
            )
            section = QTreeWidgetItem(["Core skills", "Skills", "Skills", "backend", "2"])
            section.setData(0, TREE_KIND_ROLE, "section")
            section.setData(0, TREE_DATA_ROLE, section_id)
            section.addChild(MainWindow.tree_item("content", "Line", "Python and SQL"))

            helper = type("LibraryHelper", (), {})()
            helper.db = db
            helper.section_tree = type("Tree", (), {"currentItem": lambda self: section})()
            helper.library_section_item = MainWindow.library_section_item
            helper.section_item_content = MainWindow.section_item_content
            helper._dirty_section_ids = set()
            helper._autosaved_linked_cv_ids = set()
            helper.statusBar = lambda: type("Status", (), {"showMessage": lambda *args: None})()

            self.assertTrue(MainWindow.autosave_library_section(helper, section))
            duplicate_id = db.duplicate_section(section_id)

            self.assertEqual(db.get_section(section_id).content, "Python and SQL")
            self.assertEqual(db.get_section(duplicate_id).content, "Python and SQL")
            self.assertEqual(len(db.list_section_history(section_id)), 1)

    def test_cv_history_snapshot_can_be_exported_without_becoming_current(self):
        entry = CVHistory(
            id=9,
            cv_id=42,
            version=1,
            recorded_at="2026-08-27T12:00:00",
            change_type="created",
            snapshot={
                "id": 42,
                "name": "Earlier CV",
                "created_at": "2026-08-20T10:00:00",
                "sections": [{"title": "Skills", "category": "Skills", "content": "Python"}],
                "profile": {"name": "Test Person", "email": "test@example.com"},
            },
        )

        cv = MainWindow.cv_from_history(entry)

        self.assertEqual(cv.id, 42)
        self.assertEqual(cv.name, "Earlier CV")
        self.assertEqual(cv.sections[0]["content"], "Python")
        self.assertEqual(cv.profile["email"], "test@example.com")
        self.assertIsNone(cv.markdown_path)
        self.assertIsNone(cv.pdf_path)

    def test_previous_versions_excludes_the_current_snapshot(self):
        history = ["current", "previous", "oldest"]

        self.assertEqual(MainWindow.previous_versions(history), ["previous", "oldest"])

    def test_opening_a_legacy_shared_pdf_regenerates_the_selected_cv(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            export_dir = data_dir / "exports"
            export_dir.mkdir()
            legacy_path = export_dir / "AdaLovelaceCV.pdf"
            legacy_path.write_bytes(b"legacy")
            current_markdown = export_dir / "Backend-role-7.md"
            current_pdf_dir = export_dir / "Backend-role-7"
            current_pdf_dir.mkdir()
            current_pdf = current_pdf_dir / "AdaLovelaceCV.pdf"
            current_pdf.write_bytes(b"current")
            cv = CV(
                id=7,
                name="Backend role",
                created_at="2026-08-23T12:00:00",
                sections=[],
                profile={"name": "Ada Lovelace"},
                markdown_path=None,
                pdf_path=str(legacy_path),
            )
            updates = []

            helper = type("OpenPDFHelper", (), {})()
            helper.data_dir = data_dir
            helper.selected_cv = lambda: cv
            helper.export_cv_with_overflow_warning = (
                lambda selected, output: (current_markdown, current_pdf)
            )
            helper.db = type("Database", (), {
                "update_cv_exports": lambda self, *args: updates.append(args),
            })()
            helper.refresh_all = lambda: None

            with patch("main.QDesktopServices.openUrl") as open_url:
                MainWindow.open_selected_pdf(helper)

            self.assertEqual(updates, [(7, current_markdown, current_pdf)])
            self.assertEqual(open_url.call_args.args[0].toLocalFile(), str(current_pdf))

    def test_section_heading_filter_hides_non_matches_and_clears_hidden_selection(self):
        class Item:
            def __init__(self, heading):
                self.heading = heading
                self.hidden = False

            def parent(self):
                return None

            def data(self, column, role):
                return "section" if role == TREE_KIND_ROLE else None

            def text(self, column):
                return self.heading if column == 1 else ""

            def setHidden(self, hidden):
                self.hidden = hidden

            def isHidden(self):
                return self.hidden

        skills = Item("Skills")
        experience = Item("Experience")

        class Tree:
            def __init__(self):
                self.items = [skills, experience]
                self.current = experience

            def topLevelItemCount(self):
                return len(self.items)

            def topLevelItem(self, index):
                return self.items[index]

            def currentItem(self):
                return self.current

            def setCurrentItem(self, item):
                self.current = item

        helper = type("FilterHelper", (), {})()
        helper.section_tree = Tree()
        helper.section_heading_filter = type("Filter", (), {"currentData": lambda self: "Skills"})()
        helper.library_section_item = MainWindow.library_section_item
        helper.refresh_section_details = lambda: None

        MainWindow.apply_section_heading_filter(helper)

        self.assertFalse(skills.isHidden())
        self.assertTrue(experience.isHidden())
        self.assertIsNone(helper.section_tree.currentItem())


if __name__ == "__main__":
    unittest.main()
