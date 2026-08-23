import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "safari-extension"


class SafariExtensionAssetsTests(unittest.TestCase):
    def test_manifest_declares_popup_and_required_permissions(self):
        manifest = json.loads((ROOT / "manifest.json").read_text())
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["action"]["default_popup"], "popup.html")
        self.assertTrue({"activeTab", "storage", "tabs"}.issubset(manifest["permissions"]))

    def test_popup_uses_authenticated_desktop_bridge(self):
        script = (ROOT / "popup.js").read_text()
        self.assertIn("X-CV-Manager-Token", script)
        self.assertIn("posting_url", script)
        self.assertIn("fetch(endpoint", script)


if __name__ == "__main__":
    unittest.main()
