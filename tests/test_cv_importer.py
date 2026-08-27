import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from cv_importer import _extract_pdf_links, _restore_pdf_links, parse_cv_text


SAMPLE_CV = """ADA LOVELACE
(555) 010-1234 | ada@example.com | github.com/example-user | portfolio.example.com
EDUCATION
Example University, Toronto, ON                    Sep 2025 - Jun 2028
B.Sc. Honours Computer Science
EXPERIENCE
Data Engineering Intern                            May 2026 - Present
Example Company                                         Montreal, QC
• Built reliable data pipelines.
  Used by analytics teams.
Software Engineer                                  Feb 2026 - July 2026
• Built an import workflow.
SKILLS
Technical: Python, SQL
"""


class CVImporterTests(unittest.TestCase):
    def test_imports_profile_and_reusable_sections(self):
        result = parse_cv_text(SAMPLE_CV)
        self.assertEqual(result.profile["name"], "Ada Lovelace")
        self.assertEqual(result.profile["email"], "ada@example.com")
        self.assertEqual(result.profile["phone"], "(555) 010-1234")
        self.assertEqual(result.profile["website"], "portfolio.example.com")
        self.assertEqual([section.category for section in result.sections], ["Education", "Experience", "Skills"])

    def test_preserves_metadata_columns_and_wrapped_bullets(self):
        result = parse_cv_text(SAMPLE_CV)
        experience = result.sections[1].content
        self.assertIn("**Data Engineering Intern** :: *May 2026 - Present*", experience)
        self.assertIn("*Example Company* :: *Montreal, QC*", experience)
        self.assertIn("- Built reliable data pipelines. Used by analytics teams.", experience)
        self.assertIn("**Software Engineer** :: *Feb 2026 - July 2026*", experience)

    def test_preserves_links_in_entry_titles_and_bullets(self):
        result = parse_cv_text(
            "PROJECTS\n"
            "[My Project](https://example.com/project)      Jan 2026 - Present\n"
            "- Used [OpenLineage](https://example.com/openlineage) for lineage.\n"
        )

        self.assertEqual(
            result.sections[0].content,
            "**[My Project](https://example.com/project)** :: *Jan 2026 - Present*\n"
            "- Used [OpenLineage](https://example.com/openlineage) for lineage.",
        )

    def test_restores_pdf_links_only_in_section_content(self):
        text = (
            "My Project\n"
            "PROJECTS\n"
            "My Project                         2026\n"
            "- Used OpenLineage for lineage.\n"
        )

        restored = _restore_pdf_links(
            text,
            [
                ("My Project", "https://example.com/project"),
                ("OpenLineage", "https://example.com/openlineage"),
            ],
        )

        self.assertEqual(
            restored,
            "My Project\n"
            "PROJECTS\n"
            "[My Project](https://example.com/project)                         2026\n"
            "- Used [OpenLineage](https://example.com/openlineage) for lineage.\n",
        )

    @patch("cv_importer.subprocess.run")
    def test_extracts_project_title_links_from_quartz_pdf_xml(self, run):
        run.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout="""<?xml version="1.0" encoding="UTF-8"?>
<pdf2xml>
  <page number="1">
    <text top="737" left="54"><a href="https://example.com/remote"><b>Remote Desktop Control</b></a></text>
    <text top="737" left="220"><a href="https://example.com/remote"><b> </b></a></text>
    <text top="951" left="54"><a href="https://example.com/model"><b>Fine-tuned Transformers Model</b></a></text>
  </page>
</pdf2xml>""",
        )

        self.assertEqual(
            _extract_pdf_links(Path("resume.pdf")),
            [
                ("Remote Desktop Control", "https://example.com/remote"),
                ("Fine-tuned Transformers Model", "https://example.com/model"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
