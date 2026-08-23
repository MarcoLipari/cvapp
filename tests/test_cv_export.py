import unittest

from cv_export import _inline


class CVExportTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
