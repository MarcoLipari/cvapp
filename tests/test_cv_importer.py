import unittest

from cv_importer import parse_cv_text


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


if __name__ == "__main__":
    unittest.main()
