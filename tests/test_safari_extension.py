import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "safari-extension"


class SafariExtensionAssetsTests(unittest.TestCase):
    def test_manifest_declares_popup_and_required_permissions(self):
        manifest = json.loads((ROOT / "manifest.json").read_text())
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["action"]["default_popup"], "popup.html")
        self.assertTrue({"activeTab", "storage", "tabs", "nativeMessaging"}.issubset(manifest["permissions"]))
        self.assertEqual(manifest["background"]["service_worker"], "background.js")
        self.assertIn("content.js", manifest["content_scripts"][0]["js"])

    def test_extension_uses_native_bridge_and_direct_cv_attachment(self):
        background = (ROOT / "background.js").read_text()
        content = (ROOT / "content.js").read_text()
        popup = (ROOT / "popup.js").read_text()
        self.assertIn("sendNativeMessage", background)
        self.assertIn('operation: "write_event"', background)
        self.assertIn("new DataTransfer", content)
        self.assertIn("cv.upload_filename || cv.name", content)
        self.assertIn("Choose from CV Manager", content)
        self.assertIn("Don’t log", content)
        self.assertIn('type: "attachCv"', popup)
        self.assertNotIn("X-CV-Manager-Token", popup)

    def test_workday_application_submissions_are_detected(self):
        content = (ROOT / "content.js").read_text()
        self.assertIn('url.hostname.endsWith(".myworkdayjobs.com")', content)
        self.assertIn('/\\/job\\/.*\\/apply(?:\\/|$)/i', content)
        self.assertIn('armed_url: location.href', content)
        self.assertIn('/\\bcongratulations\\b/i', content)


if __name__ == "__main__":
    unittest.main()
