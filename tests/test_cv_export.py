import tempfile
import unittest

from PySide6.QtGui import QGuiApplication
from PySide6.QtPdf import QPdfDocument

from cv_export import CVOverflowError, _inline, export_cv, render_markdown
from database import CV


class CVExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_renders_markdown_links_as_html_anchors(self):
        rendered = _inline("**[Project](https://example.com/project)**")

        self.assertEqual(
            rendered,
            '<b><a href="https://example.com/project">Project</a></b>',
        )

    def test_renders_combined_bold_and_italic_text(self):
        self.assertEqual(_inline("***Emphasis***"), "<b><i>Emphasis</i></b>")

    def test_users_can_add_their_own_links(self):
        rendered = _inline("Built [My Project](https://example.com/my-project)")

        self.assertEqual(
            rendered,
            'Built <a href="https://example.com/my-project">My Project</a>',
        )

    def test_plain_text_is_not_replaced_with_a_hardcoded_link(self):
        self.assertEqual(_inline("Integrated OpenLineage and Marquez"), "Integrated OpenLineage and Marquez")

    def test_adjacent_reusable_entries_share_one_cv_section_heading(self):
        cv = CV(
            id=7,
            name="Backend role",
            created_at="2026-08-23T12:00:00",
            sections=[
                {"title": "Projects", "category": "Projects", "content": "**Project A**\n- First."},
                {"title": "Projects", "category": "Projects", "content": "**Project B**\n- Second."},
                {"title": "Skills", "category": "Skills", "content": "Python"},
            ],
            profile={"name": "Ada Lovelace", "email": "ada@example.com"},
            markdown_path=None,
            pdf_path=None,
        )

        markdown = render_markdown(cv)

        self.assertEqual(markdown.count("## Projects"), 1)
        self.assertLess(markdown.index("**Project A**"), markdown.index("**Project B**"))
        self.assertIn("## Skills", markdown)

        with tempfile.TemporaryDirectory() as directory:
            _, pdf_path = export_cv(cv, directory)
            self.assertTrue(pdf_path.exists())

    def test_export_has_ats_readable_text_in_logical_order_and_metadata(self):
        cv = CV(
            id=7,
            name="Backend role",
            created_at="2026-08-23T12:00:00",
            sections=[
                {
                    "title": "Experience",
                    "category": "Experience",
                    "content": "**Software Engineer** :: *2024 - Present*\n- Built Python services.",
                },
                {"title": "Skills", "category": "Skills", "content": "Python, SQL"},
            ],
            profile={
                "name": "Ada Lovelace",
                "phone": "555-0100",
                "email": "ada@example.com",
                "github": "github.com/ada",
                "linkedin": "linkedin.com/in/ada",
                "website": "ada.example.com",
            },
            markdown_path=None,
            pdf_path=None,
        )

        self.assertEqual(
            render_markdown(cv).splitlines()[1],
            "555-0100 | ada@example.com | github.com/ada | ada.example.com | linkedin.com/in/ada",
        )

        with tempfile.TemporaryDirectory() as directory:
            _, pdf_path = export_cv(cv, directory)
            self.assertEqual(pdf_path.name, "AdaLovelaceCV.pdf")
            self.assertEqual(pdf_path.parent.name, "Backend-role-7")
            document = QPdfDocument()
            self.assertEqual(document.load(str(pdf_path)), QPdfDocument.Error.None_)
            text = document.getAllText(0).text()

            self.assertIn("linkedin.com/in/ada", text)

            expected_order = [
                "ADA LOVELACE",
                "EXPERIENCE",
                "Software Engineer",
                "2024 - Present",
                "Built Python services.",
                "SKILLS",
                "Python, SQL",
            ]
            offsets = [text.index(value) for value in expected_order]
            self.assertEqual(offsets, sorted(offsets))
            self.assertEqual(
                document.metaData(QPdfDocument.MetaDataField.Title),
                "AdaLovelaceCV",
            )
            self.assertFalse(document.metaData(QPdfDocument.MetaDataField.Author))
            self.assertFalse(document.metaData(QPdfDocument.MetaDataField.Creator))

    def test_cvs_for_the_same_person_keep_separate_pdf_exports(self):
        profile = {"name": "Ada Lovelace", "email": "ada@example.com"}
        backend_cv = CV(
            id=7,
            name="Backend role",
            created_at="2026-08-23T12:00:00",
            sections=[{"title": "Skills", "category": "Skills", "content": "Python services"}],
            profile=profile,
            markdown_path=None,
            pdf_path=None,
        )
        frontend_cv = CV(
            id=8,
            name="Frontend role",
            created_at="2026-08-24T12:00:00",
            sections=[{"title": "Skills", "category": "Skills", "content": "TypeScript interfaces"}],
            profile=profile,
            markdown_path=None,
            pdf_path=None,
        )

        with tempfile.TemporaryDirectory() as directory:
            _, backend_path = export_cv(backend_cv, directory)
            _, frontend_path = export_cv(frontend_cv, directory)

            self.assertNotEqual(backend_path, frontend_path)
            self.assertEqual(backend_path.name, "AdaLovelaceCV.pdf")
            self.assertEqual(frontend_path.name, "AdaLovelaceCV.pdf")
            backend_document = QPdfDocument()
            self.assertEqual(backend_document.load(str(backend_path)), QPdfDocument.Error.None_)
            self.assertIn("Python services", backend_document.getAllText(0).text())
            self.assertNotIn("TypeScript interfaces", backend_document.getAllText(0).text())

    def test_export_rejects_overflow_instead_of_shrinking_reference_sizes(self):
        cv = CV(
            id=8,
            name="Overfull CV",
            created_at="2026-09-03T00:00:00",
            sections=[
                {
                    "title": "Experience",
                    "category": "Experience",
                    "content": "**Software Engineer** :: *2020 - Present*\n"
                    + "\n".join(
                        f"- Delivered measurable result number {index}."
                        for index in range(50)
                    ),
                }
            ],
            profile={"name": "Ada Lovelace", "email": "ada@example.com"},
            markdown_path=None,
            pdf_path=None,
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CVOverflowError, "standard reference sizes"):
                export_cv(cv, directory)

            _, pdf_path = export_cv(cv, directory, allow_multipage=True)
            document = QPdfDocument()
            self.assertEqual(document.load(str(pdf_path)), QPdfDocument.Error.None_)
            self.assertGreater(document.pageCount(), 1)
            extracted = "\n".join(
                document.getAllText(page).text()
                for page in range(document.pageCount())
            )
            self.assertIn("Delivered measurable result number 49.", extracted)

            _, pdf_path = export_cv(cv, directory, shrink_to_fit=True)
            self.assertEqual(document.load(str(pdf_path)), QPdfDocument.Error.None_)
            self.assertEqual(document.pageCount(), 1)
            self.assertIn(
                "Delivered measurable result number 49.",
                document.getAllText(0).text(),
            )


if __name__ == "__main__":
    unittest.main()
