import unittest

from database import CVHistory
from main import MainWindow, TREE_DATA_ROLE, TREE_EDIT_MODE_ROLE, TREE_KIND_ROLE


class CVTreeTests(unittest.TestCase):
    def tree_helper(self):
        helper = type("TreeHelper", (), {})()
        helper.tree_item = MainWindow.tree_item
        helper.content_node_label = MainWindow.content_node_label
        helper.section_uses_entries = MainWindow.section_uses_entries
        helper.section_item_content = MainWindow.section_item_content
        return helper

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


if __name__ == "__main__":
    unittest.main()
