import json
import tempfile
import unittest
from pathlib import Path

from database import CVDatabase
from safari_bridge_store import SafariBridgeStore


class SafariBridgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = CVDatabase(self.root / "database.sqlite3")
        self.store = SafariBridgeStore(self.db, self.root / "bridge")

    def tearDown(self):
        self.temp.cleanup()

    def test_publishes_exported_cvs_with_hash_and_local_copy(self):
        section_id = self.db.create_section("Skills", "Skills", "Python")
        cv = self.db.create_cv("Data CV", [self.db.get_section(section_id)])
        pdf = self.root / "data.pdf"
        pdf.write_bytes(b"%PDF-test")
        self.db.update_cv_exports(cv.id, self.root / "data.md", pdf)

        catalog = self.store.sync_cvs()

        self.assertEqual(catalog[0]["name"], "Data CV")
        self.assertEqual(catalog[0]["upload_filename"], "data.pdf")
        self.assertEqual(len(catalog[0]["sha256"]), 64)
        self.assertEqual((self.store.cv_dir / catalog[0]["filename"]).read_bytes(), b"%PDF-test")
        self.assertEqual(json.loads(self.store.catalog_path.read_text())["version"], 1)

    def test_create_edit_and_cancel_are_reconciled_by_event_id(self):
        event_id = "event-1"
        self._request("01-create.json", event_id, 1, "active", company="Acme", role="Engineer")
        results = self.store.process_requests()

        self.assertEqual(results[0]["action"], "created")
        saved = self.db.get_application_by_capture_event(event_id)
        self.assertEqual(saved.company, "Acme")
        self.assertEqual(json.loads(saved.posting_snapshot_json)["description"], "Job text")

        self.db.update_application(
            saved.id, company="Acme", role="Engineer", location="", application_date="2026-08-26",
            status="Interviewing", cv_id=None, notes="", posting_url="https://example.com/job",
        )
        self._request("02-edit.json", event_id, 2, "active", company="Acme Corp", role="Engineer II")
        self.store.process_requests()
        updated = self.db.get_application_by_capture_event(event_id)
        self.assertEqual(updated.company, "Acme Corp")
        self.assertEqual(updated.status, "Interviewing")

        self._request("03-cancel.json", event_id, 3, "cancelled")
        self.store.process_requests()
        self.assertIsNone(self.db.get_application_by_capture_event(event_id))

    def test_invalid_request_is_quarantined(self):
        (self.store.request_dir / "bad.json").write_text("not json")
        result = self.store.process_requests()
        self.assertEqual(result[0]["action"], "failed")
        self.assertTrue((self.store.failed_dir / "bad.json").exists())

    def _request(self, filename, event_id, revision, state, company="", role=""):
        request = {
            "version": 1,
            "request_id": filename,
            "event_id": event_id,
            "revision": revision,
            "state": state,
            "payload": {
                "company": company,
                "role": role,
                "location": "Toronto",
                "posting_url": "https://example.com/job",
                "application_date": "2026-08-26",
                "cv_id": None,
                "notes": "",
                "snapshot": {"description": "Job text"},
            },
        }
        (self.store.request_dir / filename).write_text(json.dumps(request))


if __name__ == "__main__":
    unittest.main()
