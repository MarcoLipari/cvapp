import unittest

from PySide6.QtWidgets import QApplication

from cv_document_editor import (
    RichMarkdownEdit,
    parse_sections_markdown,
    sections_markdown,
)


class CVDocumentEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_unchanged_markdown_preserves_entry_boundaries_and_library_links(self):
        sections = [
            {
                "title": "Projects",
                "category": "Projects",
                "content": "**Project A**\n- First result.",
                "source_section_id": 7,
            },
            {
                "title": "Projects",
                "category": "Projects",
                "content": "**Project B**\n- Second result.",
                "source_section_id": 8,
            },
        ]

        parsed = parse_sections_markdown(sections_markdown(sections), sections)

        self.assertEqual(parsed, sections)

    def test_changed_markdown_keeps_its_library_link_and_supported_syntax(self):
        original = [{
            "title": "Skills",
            "category": "Skills",
            "content": "Python",
            "source_section_id": 3,
        }]

        parsed = parse_sections_markdown(
            "## Skills\n**Python** and *SQL*\n", original
        )

        self.assertEqual(parsed, [{
            "title": "Skills",
            "category": "Skills",
            "content": "**Python** and *SQL*",
            "source_section_id": 3,
        }])

    def test_adjacent_entries_keep_separate_boundaries_and_all_library_links(self):
        original = [
            {
                "title": "Projects",
                "category": "Projects",
                "content": "**Project A**\n- First result.",
                "source_section_id": 7,
            },
            {
                "title": "Projects",
                "category": "Projects",
                "content": "**Project B**\n- Second result.",
                "source_section_id": 8,
            },
            {
                "title": "Skills",
                "category": "Skills",
                "content": "Python",
                "source_section_id": 9,
            },
        ]

        parsed = parse_sections_markdown(
            sections_markdown(original).replace("First result.", "Updated result."),
            original,
        )

        self.assertEqual(len(parsed), 3)
        self.assertEqual(
            [section["source_section_id"] for section in parsed],
            [7, 8, 9],
        )

    def test_document_editor_rejects_structural_entry_changes(self):
        original = [{
            "title": "Skills",
            "category": "Skills",
            "content": "Python",
            "source_section_id": 3,
        }]

        with self.assertRaisesRegex(ValueError, "cannot add or remove CV entries"):
            parse_sections_markdown(
                "## Skills\nPython\n\n## Projects\nNew project\n",
                original,
            )

    def test_markdown_requires_sections_and_content(self):
        with self.assertRaisesRegex(ValueError, "begin with a ## section heading"):
            parse_sections_markdown("Unheaded content", [])
        with self.assertRaisesRegex(ValueError, "needs some content"):
            parse_sections_markdown("## Empty", [])

    def test_rich_document_round_trip_preserves_cv_line_types_and_formatting(self):
        source = (
            "## Experience\n"
            "**Engineer** :: *2025 - Present*\n"
            "*Example Company* :: *Toronto*\n"
            "- Built **reliable** services.\n"
            "- Used *Python*.\n"
        )
        editor = RichMarkdownEdit()

        editor.set_section_markdown(source)

        self.assertEqual(editor.to_section_markdown(), source)

    def test_document_view_hides_repeated_heading_but_preserves_entry_boundary(self):
        source = (
            "## Projects\n"
            "**Project A**\n"
            "- First result.\n\n"
            "## Projects\n"
            "**Project B**\n"
            "- Second result.\n"
        )
        editor = RichMarkdownEdit()

        editor.set_section_markdown(source)

        self.assertEqual(editor.toPlainText().count("Projects"), 1)
        self.assertEqual(editor.to_section_markdown(), source)

    def test_bold_and_italic_commands_are_written_back_as_markdown(self):
        editor = RichMarkdownEdit()
        editor.set_section_markdown("## Skills\nPython and SQL\n")

        cursor = editor.document().find("Python")
        editor.setTextCursor(cursor)
        editor.toggle_bold()
        cursor = editor.document().find("SQL")
        editor.setTextCursor(cursor)
        editor.toggle_italic()

        self.assertEqual(
            editor.to_section_markdown(),
            "## Skills\n**Python** and *SQL*\n",
        )


if __name__ == "__main__":
    unittest.main()
