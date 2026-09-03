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

    def test_duplicate_section_creates_an_independent_copy(self):
        section_id = self.db.create_section(
            "Skills", "Skills", "Python and SQL", "backend", internal_name="Core skills"
        )

        duplicate_id = self.db.duplicate_section(section_id)

        original = self.db.get_section(section_id)
        duplicate = self.db.get_section(duplicate_id)
        self.assertNotEqual(duplicate.id, original.id)
        self.assertEqual(duplicate.title, original.title)
        self.assertEqual(duplicate.category, original.category)
        self.assertEqual(duplicate.content, original.content)
        self.assertEqual(duplicate.labels, original.labels)
        self.assertEqual(duplicate.internal_name, "Core skills (copy)")
        self.assertEqual(self.db.list_section_history(duplicate_id)[0].change_type, "created")

    def test_section_and_linked_cv_changes_create_independent_history(self):
        section_id = self.db.create_section("Skills", "Skills", "Python", "backend")
        cv = self.db.create_cv("Target role", [self.db.get_section(section_id)])

        self.db.update_section(section_id, "Skills", "Skills", "Python", "data")
        self.db.update_section(section_id, "Technical Skills", "Skills", "Python and SQL", "data")

        section_history = self.db.list_section_history(section_id)
        cv_history = self.db.list_cv_history(cv.id)
        self.assertEqual([entry.version for entry in section_history], [3, 2, 1])
        self.assertEqual(section_history[0].snapshot["content"], "Python and SQL")
        self.assertEqual(section_history[1].snapshot["labels"], "data")
        self.assertEqual(section_history[2].change_type, "created")
        self.assertEqual([entry.version for entry in cv_history], [2, 1])
        self.assertEqual(cv_history[0].change_type, "linked_section_updated")
        self.assertEqual(cv_history[0].snapshot["sections"][0]["title"], "Technical Skills")
        self.assertEqual(cv_history[1].snapshot["sections"][0]["content"], "Python")

    def test_direct_cv_history_records_only_meaningful_content_changes(self):
        section_id = self.db.create_section("Skills", "Skills", "Python")
        cv = self.db.create_cv("Original", [self.db.get_section(section_id)])

        updated = self.db.update_cv(cv.id, "Updated", cv.sections, cv.profile)
        self.db.update_cv(updated.id, updated.name, updated.sections, updated.profile)
        self.db.update_cv_exports(updated.id, "current.md", "current.pdf")

        history = self.db.list_cv_history(cv.id)
        self.assertEqual([entry.version for entry in history], [2, 1])
        self.assertEqual(history[0].change_type, "edited")
        self.assertEqual(history[0].snapshot["name"], "Updated")
        self.assertNotIn("pdf_path", history[0].snapshot)
        self.assertEqual(history[1].snapshot["name"], "Original")

    def test_deleting_an_item_removes_only_its_history(self):
        first_section_id = self.db.create_section("First", "Skills", "Python")
        second_section_id = self.db.create_section("Second", "Skills", "SQL")
        cv = self.db.create_cv("Role", [self.db.get_section(first_section_id)])

        self.db.delete_sections([first_section_id])

        self.assertEqual(self.db.list_section_history(first_section_id), [])
        self.assertEqual(len(self.db.list_section_history(second_section_id)), 1)
        self.assertEqual(len(self.db.list_cv_history(cv.id)), 1)

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

    def test_internal_name_is_library_only_metadata_and_preserves_links(self):
        section_id = self.db.create_section("Skills", "Skills", "Python", internal_name="Shared skills")
        cv = self.db.create_cv("Target role", [self.db.get_section(section_id)])
        self.db.update_cv_exports(cv.id, "saved.md", "saved.pdf")

        affected_cv_ids = self.db.update_section(
            section_id, "Skills", "Skills", "Python", internal_name="Backend skills"
        )

        section = self.db.get_section(section_id)
        saved_cv = self.db.get_cv(cv.id)
        self.assertEqual(section.internal_name, "Backend skills")
        self.assertEqual(affected_cv_ids, [])
        self.assertEqual(saved_cv.sections[0]["source_section_id"], section_id)
        self.assertEqual(saved_cv.sections[0]["title"], "Skills")
        self.assertEqual(saved_cv.markdown_path, "saved.md")
        self.assertEqual(saved_cv.pdf_path, "saved.pdf")

    def test_tree_section_copy_creates_library_item_and_relinks_only_current_cv(self):
        section_id = self.db.create_section(
            "Skills", "Skills", "Python", "backend", internal_name="Core skills"
        )
        current = self.db.create_cv("Acme role", [self.db.get_section(section_id)])
        other = self.db.create_cv("Other role", [self.db.get_section(section_id)])
        edited_sections = [{
            "title": "Skills",
            "category": "Skills",
            "content": "Python and Rust",
            "source_section_id": section_id,
        }]

        updated, affected_cv_ids, created_section_ids = self.db.update_cv_from_tree(
            current.id, current.name, edited_sections, current.profile, {0: "copy"}
        )

        self.assertEqual(affected_cv_ids, [])
        self.assertEqual(len(created_section_ids), 1)
        copied_id = created_section_ids[0]
        copied = self.db.get_section(copied_id)
        self.assertEqual(copied.internal_name, "Acme role | Core skills")
        self.assertEqual(copied.title, "Skills")
        self.assertEqual(copied.content, "Python and Rust")
        self.assertEqual(copied.labels, "backend")
        self.assertEqual(updated.sections[0]["source_section_id"], copied_id)
        self.assertEqual(self.db.get_cv(other.id).sections[0]["source_section_id"], section_id)
        self.assertEqual(self.db.get_cv(other.id).sections[0]["content"], "Python")

        self.db.update_section(
            copied_id, copied.title, copied.category, copied.content, copied.labels,
            internal_name="Acme tailored skills",
        )
        self.assertEqual(self.db.get_cv(current.id).sections[0]["source_section_id"], copied_id)
        self.assertEqual(self.db.get_cv(current.id).sections[0]["title"], "Skills")

    def test_tree_shared_edit_updates_every_linked_cv(self):
        section_id = self.db.create_section("Skills", "Skills", "Python", internal_name="Core skills")
        current = self.db.create_cv("Acme role", [self.db.get_section(section_id)])
        other = self.db.create_cv("Other role", [self.db.get_section(section_id)])
        self.db.update_cv_exports(current.id, "current.md", "current.pdf")
        self.db.update_cv_exports(other.id, "other.md", "other.pdf")
        edited_sections = [{
            "title": "Technical Skills",
            "category": "Skills",
            "content": "Python and Rust",
            "source_section_id": section_id,
        }]

        updated, affected_cv_ids, created_section_ids = self.db.update_cv_from_tree(
            current.id, current.name, edited_sections, current.profile, {0: "shared"}
        )

        self.assertEqual(set(affected_cv_ids), {current.id, other.id})
        self.assertEqual(created_section_ids, [])
        self.assertEqual(self.db.get_section(section_id).internal_name, "Core skills")
        self.assertEqual(updated.sections[0]["title"], "Technical Skills")
        linked = self.db.get_cv(other.id)
        self.assertEqual(linked.sections[0]["content"], "Python and Rust")
        self.assertEqual(linked.sections[0]["source_section_id"], section_id)
        self.assertIsNone(updated.pdf_path)
        self.assertIsNone(linked.pdf_path)

    def test_tree_section_actions_are_atomic(self):
        first_id = self.db.create_section("Skills", "Skills", "Python")
        second_id = self.db.create_section("Projects", "Projects", "Project")
        cv = self.db.create_cv("Acme role", [self.db.get_section(first_id), self.db.get_section(second_id)])
        edited_sections = [dict(section, content=f"{section['content']} changed") for section in cv.sections]

        with self.assertRaisesRegex(ValueError, "Unknown linked-section action"):
            self.db.update_cv_from_tree(
                cv.id, cv.name, edited_sections, cv.profile, {0: "copy", 1: "invalid"}
            )

        self.assertEqual(len(self.db.list_sections()), 2)
        self.assertEqual(self.db.get_cv(cv.id).sections, cv.sections)

    def test_linked_cv_count_counts_each_cv_once(self):
        section_id = self.db.create_section("Skills", "Skills", "Python")
        self.db.create_cv("First", [self.db.get_section(section_id)])
        self.db.create_cv("Second", [self.db.get_section(section_id)])

        self.assertEqual(self.db.count_linked_cvs(section_id), 2)

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

    def test_cv_job_keywords_are_saved_edited_and_recorded_in_history(self):
        section_id = self.db.create_section("Skills", "Skills", "Python")
        cv = self.db.create_cv(
            "Backend CV", [self.db.get_section(section_id)], keywords="backend, Python"
        )

        updated = self.db.update_cv(
            cv.id, cv.name, cv.sections, cv.profile, keywords="platform, distributed systems"
        )

        self.assertEqual(updated.keywords, "platform, distributed systems")
        self.assertEqual(self.db.list_cv_history(cv.id)[0].snapshot["keywords"], "platform, distributed systems")
        self.assertEqual(self.db.backup_data()["cvs"][0]["keywords"], "platform, distributed systems")
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
        self.assertEqual(migrated.get_cv(1).keywords, "")
        self.assertEqual(migrated.get_application(1).posting_url, "")
        self.assertEqual(migrated.get_application(1).capture_event_id, "")
        self.assertEqual(migrated.get_application(1).posting_snapshot_json, "")
        self.assertEqual(migrated.list_cv_history(1)[0].change_type, "baseline")
        self.assertEqual(migrated.list_cv_history(1)[0].snapshot["name"], "Old CV")

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
        self.assertEqual(migrated.get_section(1).internal_name, "Skills")

    def test_backup_contains_every_user_record(self):
        section_id = self.db.create_section("Skills", "Skills", "Python")
        cv = self.db.create_cv("Role", [self.db.get_section(section_id)])
        self.db.create_application(company="Acme", role="Engineer", location="Toronto", application_date="2026-08-22", status="Applied", cv_id=cv.id, notes="", posting_url="")
        backup = self.db.backup_data()
        self.assertEqual(backup["format"], "cv-manager-backup")
        self.assertEqual(len(backup["sections"]), 1)
        self.assertEqual(len(backup["cvs"]), 1)
        self.assertEqual(len(backup["applications"]), 1)
        self.assertEqual(backup["version"], 2)
        self.assertEqual(len(backup["section_history"]), 1)
        self.assertEqual(len(backup["cv_history"]), 1)

if __name__ == "__main__": unittest.main()
