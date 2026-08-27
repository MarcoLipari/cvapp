import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path

from cv_export import render_markdown
from database import CVDatabase

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db = CVDatabase(Path(self.temp.name) / "test.sqlite3")
    def tearDown(self): self.temp.cleanup()
    def test_section_edit_updates_linked_cv_and_invalidates_exports(self):
        section_id = self.db.create_section("Skills", "Skills", "Python and SQL")
        cv = self.db.create_cv("Target role", [self.db.get_section(section_id)])
        self.db.update_cv_exports(cv.id, "old.md", "old.pdf")
        self.db.update_section(section_id, "Skills", "Skills", "Swift and Rust")
        saved = self.db.get_cv(cv.id)
        self.assertEqual(saved.sections[0]["content"], "Swift and Rust")
        self.assertEqual(saved.sections[0]["source_section_id"], section_id)
        self.assertIn("Swift and Rust", render_markdown(saved))
        self.assertIsNone(saved.markdown_path)
        self.assertIsNone(saved.pdf_path)
        self.assertTrue(render_markdown(saved).startswith("# \n"))

    def test_new_profile_is_blank_until_onboarding_is_completed(self):
        self.assertEqual(
            self.db.get_profile(),
            {"name": "", "phone": "", "email": "", "github": "", "website": ""},
        )
        self.assertFalse(self.db.profile_is_configured())

        self.db.update_profile({"name": "Test Person", "email": "test@example.com"})

        self.assertTrue(self.db.profile_is_configured())

    def test_section_labels_are_library_only_metadata(self):
        section_id = self.db.create_section("Skills", "Skills", "Python and SQL", "data, backend")
        cv = self.db.create_cv("Target role", [self.db.get_section(section_id)])
        self.db.update_cv_exports(cv.id, "saved.md", "saved.pdf")

        affected_cv_ids = self.db.update_section(
            section_id, "Skills", "Skills", "Python and SQL", "machine learning"
        )

        section = self.db.get_section(section_id)
        saved_cv = self.db.get_cv(cv.id)
        self.assertEqual(section.labels, "machine learning")
        self.assertEqual(affected_cv_ids, [])
        self.assertNotIn("labels", saved_cv.sections[0])
        self.assertEqual(saved_cv.markdown_path, "saved.md")
        self.assertEqual(saved_cv.pdf_path, "saved.pdf")

    def test_cv_specific_section_edit_is_not_changed_by_library_update(self):
        section_id = self.db.create_section("Skills", "Skills", "Python")
        cv = self.db.create_cv("Target role", [self.db.get_section(section_id)])
        self.db.update_cv(cv.id, cv.name, [{"title": "Skills", "category": "Skills", "content": "Tailored Python"}], cv.profile)

        self.db.update_section(section_id, "Skills", "Skills", "Rust")

        self.assertEqual(self.db.get_cv(cv.id).sections[0]["content"], "Tailored Python")

    def test_cv_snapshots_current_profile(self):
        self.db.update_profile({"name": "Test Person", "phone": "1", "email": "test@example.com", "github": "example.com/a", "website": "example.com"})
        section_id = self.db.create_section("Skills", "Skills", "Python")
        cv = self.db.create_cv("Role", [self.db.get_section(section_id)])
        self.db.update_profile({"name": "Changed Person", "phone": "2", "email": "changed@example.com", "github": "example.com/b", "website": "changed.example"})
        self.assertIn("# TEST PERSON", render_markdown(self.db.get_cv(cv.id)))

    def test_cv_can_be_edited_without_losing_identity_or_profile_snapshot(self):
        self.db.update_profile({"name": "Original Person", "phone": "1", "email": "original@example.com", "github": "", "website": ""})
        first_id = self.db.create_section("Skills", "Skills", "Python")
        second_id = self.db.create_section("Projects", "Projects", "A useful project")
        cv = self.db.create_cv("First name", [self.db.get_section(first_id)])
        self.db.update_cv_exports(cv.id, "old.md", "old.pdf")

        updated = self.db.update_cv(cv.id, "Updated name", [self.db.get_section(second_id)])

        self.assertEqual(updated.id, cv.id)
        self.assertEqual(updated.created_at, cv.created_at)
        self.assertEqual(updated.name, "Updated name")
        self.assertEqual(updated.sections[0]["title"], "Projects")
        self.assertEqual(updated.profile["name"], "Original Person")
        self.assertIsNone(updated.markdown_path)
        self.assertIsNone(updated.pdf_path)
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
        with closing(sqlite3.connect(legacy_path)) as legacy:
            with legacy:
                legacy.executescript("""
                CREATE TABLE cvs (id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, sections_json TEXT NOT NULL, markdown_path TEXT, pdf_path TEXT);
                CREATE TABLE applications (id INTEGER PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', application_date TEXT NOT NULL, status TEXT NOT NULL, cv_id INTEGER, notes TEXT NOT NULL DEFAULT '');
                INSERT INTO cvs VALUES (1, 'Old CV', '2026-01-01T00:00:00', '[]', NULL, NULL);
                INSERT INTO applications VALUES (1, 'Acme', 'Engineer', '', '2026-01-01', 'Applied', 1, '');
                """)
        migrated = CVDatabase(legacy_path)
        self.assertEqual(migrated.get_cv(1).profile["name"], "")
        self.assertEqual(migrated.get_application(1).posting_url, "")
        self.assertEqual(migrated.get_application(1).capture_event_id, "")
        self.assertEqual(migrated.get_application(1).posting_snapshot_json, "")

    def test_existing_sections_gain_empty_labels(self):
        legacy_path = Path(self.temp.name) / "legacy-sections.sqlite3"
        with closing(sqlite3.connect(legacy_path)) as legacy:
            with legacy:
                legacy.executescript("""
                CREATE TABLE sections (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO sections VALUES (1, 'Skills', 'Skills', 'Python', 0);
                """)

        migrated = CVDatabase(legacy_path)

        self.assertEqual(migrated.get_section(1).labels, "")

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
