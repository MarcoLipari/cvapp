"""SQLite persistence for CV Manager.

CVs snapshot their profile and selected content. Sections sourced from the
library retain a link so later library edits can be propagated to those CVs.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

STATUSES = ("Applied", "Interviewing", "Offer", "Rejected", "Withdrawn")
DEFAULT_PROFILE = {
    "name": "CV Manager User",
    "phone": "(555) 010-1234",
    "email": "user@example.com",
    "github": "github.com/example-user",
    "website": "portfolio.example.com",
}


@dataclass(frozen=True)
class Section:
    id: int
    title: str
    category: str
    content: str
    sort_order: int
    labels: str = ""


@dataclass(frozen=True)
class CV:
    id: int
    name: str
    created_at: str
    sections: list[dict]
    profile: dict[str, str]
    markdown_path: str | None
    pdf_path: str | None


@dataclass(frozen=True)
class Application:
    id: int
    company: str
    role: str
    location: str
    application_date: str
    status: str
    cv_id: int | None
    notes: str
    posting_url: str


class CVDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sections (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    labels TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS cvs (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sections_json TEXT NOT NULL,
                    profile_json TEXT NOT NULL DEFAULT '{}',
                    markdown_path TEXT,
                    pdf_path TEXT
                );
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY,
                    company TEXT NOT NULL,
                    role TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    application_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cv_id INTEGER REFERENCES cvs(id),
                    notes TEXT NOT NULL DEFAULT '',
                    posting_url TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(cvs)")}
            if "profile_json" not in columns:
                db.execute("ALTER TABLE cvs ADD COLUMN profile_json TEXT NOT NULL DEFAULT '{}'")
            application_columns = {row["name"] for row in db.execute("PRAGMA table_info(applications)")}
            if "posting_url" not in application_columns:
                db.execute("ALTER TABLE applications ADD COLUMN posting_url TEXT NOT NULL DEFAULT ''")
            section_columns = {row["name"] for row in db.execute("PRAGMA table_info(sections)")}
            if "labels" not in section_columns:
                db.execute("ALTER TABLE sections ADD COLUMN labels TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _section(row: sqlite3.Row) -> Section:
        return Section(row["id"], row["title"], row["category"], row["content"], row["sort_order"], row["labels"])

    @staticmethod
    def _cv(row: sqlite3.Row) -> CV:
        profile = DEFAULT_PROFILE | json.loads(row["profile_json"] or "{}")
        return CV(row["id"], row["name"], row["created_at"], json.loads(row["sections_json"]), profile, row["markdown_path"], row["pdf_path"])

    @staticmethod
    def _application(row: sqlite3.Row) -> Application:
        return Application(row["id"], row["company"], row["role"], row["location"], row["application_date"], row["status"], row["cv_id"], row["notes"], row["posting_url"])

    def get_profile(self) -> dict[str, str]:
        with self._connect() as db:
            rows = db.execute("SELECT key, value FROM settings WHERE key LIKE 'profile.%'").fetchall()
        saved = {row["key"].removeprefix("profile."): row["value"] for row in rows}
        return DEFAULT_PROFILE | saved

    def update_profile(self, profile: dict[str, str]) -> None:
        values = {key: str(profile.get(key, "")).strip() for key in DEFAULT_PROFILE}
        with self._connect() as db:
            db.executemany(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(f"profile.{key}", value) for key, value in values.items()],
            )

    def list_sections(self) -> list[Section]:
        with self._connect() as db:
            return [self._section(row) for row in db.execute("SELECT * FROM sections ORDER BY sort_order, id")]

    def get_section(self, section_id: int) -> Section | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM sections WHERE id = ?", (section_id,)).fetchone()
        return self._section(row) if row else None

    def create_section(self, title: str, category: str, content: str, labels: str = "") -> int:
        with self._connect() as db:
            order = db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM sections").fetchone()[0]
            return db.execute(
                "INSERT INTO sections(title, category, content, sort_order, labels) VALUES (?, ?, ?, ?, ?)",
                (title, category, content, order, labels),
            ).lastrowid

    def update_section(self, section_id: int, title: str, category: str, content: str, labels: str = "") -> list[int]:
        affected_cv_ids = []
        with self._connect() as db:
            previous = db.execute("SELECT title, category, content FROM sections WHERE id=?", (section_id,)).fetchone()
            if not previous:
                raise ValueError("Section not found")
            db.execute(
                "UPDATE sections SET title=?, category=?, content=?, labels=? WHERE id=?",
                (title, category, content, labels, section_id),
            )
            if all(value == previous[key] for key, value in (("title", title), ("category", category), ("content", content))):
                return affected_cv_ids
            rows = db.execute("SELECT id, sections_json FROM cvs").fetchall()
            for row in rows:
                sections = json.loads(row["sections_json"])
                changed = False
                for section in sections:
                    linked = section.get("source_section_id") == section_id
                    legacy_match = "source_section_id" not in section and all(
                        section.get(key, "") == previous[key] for key in ("title", "category", "content")
                    )
                    if linked or legacy_match:
                        section.update({
                            "title": title,
                            "category": category,
                            "content": content,
                            "source_section_id": section_id,
                        })
                        changed = True
                if changed:
                    db.execute(
                        "UPDATE cvs SET sections_json=?, markdown_path=NULL, pdf_path=NULL WHERE id=?",
                        (json.dumps(sections), row["id"]),
                    )
                    affected_cv_ids.append(row["id"])
        return affected_cv_ids

    def delete_section(self, section_id: int) -> None:
        self.delete_sections([section_id])

    def delete_sections(self, section_ids: list[int]) -> None:
        section_ids = list(dict.fromkeys(section_ids))
        if not section_ids:
            return
        placeholders = ",".join("?" for _ in section_ids)
        with self._connect() as db:
            db.execute(f"DELETE FROM sections WHERE id IN ({placeholders})", section_ids)

    def list_cvs(self) -> list[CV]:
        with self._connect() as db:
            return [self._cv(row) for row in db.execute("SELECT * FROM cvs ORDER BY created_at DESC, id DESC")]

    def get_cv(self, cv_id: int) -> CV | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cvs WHERE id=?", (cv_id,)).fetchone()
        return self._cv(row) if row else None

    @staticmethod
    def _section_snapshot(section: Section | dict) -> dict:
        """Return the portable fields stored in a CV snapshot."""
        if isinstance(section, Section):
            return {"title": section.title, "category": section.category, "content": section.content, "source_section_id": section.id}
        snapshot = {key: str(section.get(key, "")) for key in ("title", "category", "content")}
        if section.get("source_section_id") is not None:
            snapshot["source_section_id"] = int(section["source_section_id"])
        return snapshot

    def create_cv(self, name: str, sections: list[Section | dict], profile: dict[str, str] | None = None) -> CV:
        snapshot = [self._section_snapshot(section) for section in sections]
        profile_snapshot = DEFAULT_PROFILE | (profile or self.get_profile())
        with self._connect() as db:
            cv_id = db.execute(
                "INSERT INTO cvs(name, created_at, sections_json, profile_json) VALUES (?, ?, ?, ?)",
                (name, datetime.now().isoformat(timespec="seconds"), json.dumps(snapshot), json.dumps(profile_snapshot)),
            ).lastrowid
        return self.get_cv(cv_id)

    def update_cv(self, cv_id: int, name: str, sections: list[Section | dict], profile: dict[str, str] | None = None) -> CV:
        """Update a CV snapshot while retaining its identity and creation date."""
        if not name.strip() or not sections:
            raise ValueError("A CV needs a name and at least one section")
        snapshot = [self._section_snapshot(section) for section in sections]
        with self._connect() as db:
            current = db.execute("SELECT profile_json FROM cvs WHERE id=?", (cv_id,)).fetchone()
            if not current:
                raise ValueError("CV not found")
            saved_profile = json.loads(current["profile_json"] or "{}")
            profile_snapshot = DEFAULT_PROFILE | (profile if profile is not None else saved_profile)
            db.execute(
                "UPDATE cvs SET name=?, sections_json=?, profile_json=?, markdown_path=NULL, pdf_path=NULL WHERE id=?",
                (name.strip(), json.dumps(snapshot), json.dumps(profile_snapshot), cv_id),
            )
        return self.get_cv(cv_id)

    def update_cv_exports(self, cv_id: int, markdown_path: str | Path, pdf_path: str | Path) -> None:
        with self._connect() as db:
            db.execute("UPDATE cvs SET markdown_path=?, pdf_path=? WHERE id=?", (str(markdown_path), str(pdf_path), cv_id))

    def delete_cv(self, cv_id: int) -> None:
        self.delete_cvs([cv_id])

    def delete_cvs(self, cv_ids: list[int]) -> None:
        cv_ids = list(dict.fromkeys(cv_ids))
        if not cv_ids:
            return
        placeholders = ",".join("?" for _ in cv_ids)
        with self._connect() as db:
            db.execute(f"UPDATE applications SET cv_id=NULL WHERE cv_id IN ({placeholders})", cv_ids)
            db.execute(f"DELETE FROM cvs WHERE id IN ({placeholders})", cv_ids)

    def list_applications(self) -> list[Application]:
        with self._connect() as db:
            return [self._application(row) for row in db.execute("SELECT * FROM applications ORDER BY application_date DESC, id DESC")]

    def get_application(self, application_id: int) -> Application | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM applications WHERE id=?", (application_id,)).fetchone()
        return self._application(row) if row else None

    def create_application(self, **values) -> int:
        self._validate_application(values)
        columns = ("company", "role", "location", "application_date", "status", "cv_id", "notes", "posting_url")
        with self._connect() as db:
            return db.execute(
                f"INSERT INTO applications({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(values.get(column, "") for column in columns),
            ).lastrowid

    def update_application(self, application_id: int, **values) -> None:
        self._validate_application(values)
        columns = ("company", "role", "location", "application_date", "status", "cv_id", "notes", "posting_url")
        with self._connect() as db:
            db.execute(
                f"UPDATE applications SET {','.join(f'{column}=?' for column in columns)} WHERE id=?",
                tuple(values.get(column, "") for column in columns) + (application_id,),
            )

    def delete_application(self, application_id: int) -> None:
        self.delete_applications([application_id])

    def delete_applications(self, application_ids: list[int]) -> None:
        application_ids = list(dict.fromkeys(application_ids))
        if not application_ids:
            return
        placeholders = ",".join("?" for _ in application_ids)
        with self._connect() as db:
            db.execute(f"DELETE FROM applications WHERE id IN ({placeholders})", application_ids)

    def status_counts(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute("SELECT status, COUNT(*) AS count FROM applications GROUP BY status").fetchall()
        return {row["status"]: row["count"] for row in rows}

    def backup_data(self) -> dict:
        """Return a portable, read-only JSON representation of all user data."""
        return {
            "format": "cv-manager-backup",
            "version": 1,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "profile": self.get_profile(),
            "sections": [asdict(section) for section in self.list_sections()],
            "cvs": [asdict(cv) for cv in self.list_cvs()],
            "applications": [asdict(application) for application in self.list_applications()],
        }

    @staticmethod
    def _validate_application(values: dict) -> None:
        if not values.get("company") or not values.get("role"):
            raise ValueError("Company and role are required")
        if values.get("status") not in STATUSES:
            raise ValueError("Unknown application status")
