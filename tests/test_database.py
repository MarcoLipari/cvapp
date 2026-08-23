import tempfile
import unittest
import sqlite3
from pathlib import Path

from cv_export import render_markdown
from database import CVDatabase

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db = CVDatabase(Path(self.temp.name) / "test.sqlite3")
    def tearDown(self): self.temp.cleanup()
    def test_section_edit_does_not_change_saved_cv_snapshot(self):
        section_id = self.db.create_section("Skills", "Skills", "Python and SQL")
        cv = self.db.create_cv("Target role", [self.db.get_section(section_id)])
        self.db.update_section(section_id, "Skills", "Skills", "Swift and Rust")
        saved = self.db.get_cv(cv.id)
        self.assertEqual(saved.sections[0]["content"], "Python and SQL")
        self.assertIn("Python and SQL", render_markdown(saved))
        self.assertTrue(render_markdown(saved).startswith("# CV MANAGER USER"))

    def test_cv_snapshots_current_profile(self):
        self.db.update_profile({"name": "Test Person", "phone": "1", "email": "test@example.com", "github": "example.com/a", "website": "example.com"})
        section_id = self.db.create_section("Skills", "Skills", "Python")
        cv = self.db.create_cv("Role", [self.db.get_section(section_id)])
        self.db.update_profile({"name": "Changed Person", "phone": "2", "email": "changed@example.com", "github": "example.com/b", "website": "changed.example"})
        self.assertIn("# TEST PERSON", render_markdown(self.db.get_cv(cv.id)))
    def test_application_lifecycle_and_counts(self):
        application_id = self.db.create_application(company="Acme", role="Designer", location="Toronto", application_date="2026-08-22", status="Applied", cv_id=None, notes="Sent", posting_url="example.com/job")
        self.assertEqual(self.db.status_counts(), {"Applied": 1})
        self.db.update_application(application_id, company="Acme", role="Designer", location="Toronto", application_date="2026-08-22", status="Interviewing", cv_id=None, notes="Screen booked", posting_url="example.com/job")
        self.assertEqual(self.db.get_application(application_id).status, "Interviewing")
        self.assertEqual(self.db.get_application(application_id).posting_url, "example.com/job")
        self.assertEqual(self.db.status_counts(), {"Interviewing": 1})
        self.db.delete_application(application_id)
        self.assertEqual(self.db.list_applications(), [])

    def test_multiple_sections_can_be_deleted(self):
        first_id = self.db.create_section("First", "Skills", "Python")
        second_id = self.db.create_section("Second", "Skills", "SQL")
        remaining_id = self.db.create_section("Remaining", "Skills", "Swift")

        self.db.delete_sections([first_id, second_id])

        self.assertEqual([section.id for section in self.db.list_sections()], [remaining_id])

    def test_multiple_applications_can_be_deleted(self):
        values = {"location": "Toronto", "application_date": "2026-08-22", "status": "Applied", "cv_id": None, "notes": "", "posting_url": ""}
        first_id = self.db.create_application(company="Acme", role="Engineer", **values)
        second_id = self.db.create_application(company="Example", role="Designer", **values)
        remaining_id = self.db.create_application(company="Keep", role="Analyst", **values)

        self.db.delete_applications([first_id, second_id])

        self.assertEqual([application.id for application in self.db.list_applications()], [remaining_id])

    def test_deleting_cvs_keeps_linked_applications(self):
        section_id = self.db.create_section("Skills", "Skills", "Python")
        first_cv = self.db.create_cv("First role", [self.db.get_section(section_id)])
        second_cv = self.db.create_cv("Second role", [self.db.get_section(section_id)])
        first_application_id = self.db.create_application(company="Acme", role="Engineer", location="Toronto", application_date="2026-08-22", status="Applied", cv_id=first_cv.id, notes="", posting_url="")
        second_application_id = self.db.create_application(company="Example", role="Designer", location="Montreal", application_date="2026-08-22", status="Applied", cv_id=second_cv.id, notes="", posting_url="")

        self.db.delete_cvs([first_cv.id, second_cv.id])

        self.assertEqual(self.db.list_cvs(), [])
        self.assertIsNone(self.db.get_application(first_application_id).cv_id)
        self.assertIsNone(self.db.get_application(second_application_id).cv_id)

    def test_existing_database_is_migrated_without_losing_records(self):
        legacy_path = Path(self.temp.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as legacy:
            legacy.executescript("""
                CREATE TABLE cvs (id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, sections_json TEXT NOT NULL, markdown_path TEXT, pdf_path TEXT);
                CREATE TABLE applications (id INTEGER PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', application_date TEXT NOT NULL, status TEXT NOT NULL, cv_id INTEGER, notes TEXT NOT NULL DEFAULT '');
                INSERT INTO cvs VALUES (1, 'Old CV', '2026-01-01T00:00:00', '[]', NULL, NULL);
                INSERT INTO applications VALUES (1, 'Acme', 'Engineer', '', '2026-01-01', 'Applied', 1, '');
            """)
        migrated = CVDatabase(legacy_path)
        self.assertEqual(migrated.get_cv(1).profile["name"], "CV Manager User")
        self.assertEqual(migrated.get_application(1).posting_url, "")

    def test_backup_contains_every_user_record(self):
        section_id = self.db.create_section("Skills", "Skills", "Python")
        cv = self.db.create_cv("Role", [self.db.get_section(section_id)])
        self.db.create_application(company="Acme", role="Engineer", location="Toronto", application_date="2026-08-22", status="Applied", cv_id=cv.id, notes="", posting_url="")
        backup = self.db.backup_data()
        self.assertEqual(backup["format"], "cv-manager-backup")
        self.assertEqual(len(backup["sections"]), 1)
        self.assertEqual(len(backup["cvs"]), 1)
        self.assertEqual(len(backup["applications"]), 1)

if __name__ == "__main__": unittest.main()
