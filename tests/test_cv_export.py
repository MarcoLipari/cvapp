import tempfile
import unittest

from PySide6.QtGui import QGuiApplication
from PySide6.QtPdf import QPdfDocument

from cv_export import _inline, export_cv
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

    def test_reference_links_are_clickable_without_rewriting_saved_snapshots(self):
        rendered = _inline("Integrated OpenLineage and Marquez")

        self.assertIn('href="https://github.com/OpenLineage/OpenLineage/tree/main"', rendered)
        self.assertIn('href="https://github.com/MarquezProject/marquez"', rendered)

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
                "website": "ada.example.com",
            },
            markdown_path=None,
            pdf_path=None,
        )

        with tempfile.TemporaryDirectory() as directory:
            _, pdf_path = export_cv(cv, directory)
            self.assertEqual(pdf_path.name, "AdaLovelaceCV.pdf")
            document = QPdfDocument()
            self.assertEqual(document.load(str(pdf_path)), QPdfDocument.Error.None_)
            text = document.getAllText(0).text()

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


if __name__ == "__main__":
    unittest.main()
