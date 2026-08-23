"""SQLite persistence for CV Manager.

CVs snapshot both their selected sections and profile so a submitted CV can
always be reproduced even when the library or contact details later change.
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
                    sort_order INTEGER NOT NULL DEFAULT 0
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

    @staticmethod
    def _section(row: sqlite3.Row) -> Section:
        return Section(row["id"], row["title"], row["category"], row["content"], row["sort_order"])

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

    def create_section(self, title: str, category: str, content: str) -> int:
        with self._connect() as db:
            order = db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM sections").fetchone()[0]
            return db.execute("INSERT INTO sections(title, category, content, sort_order) VALUES (?, ?, ?, ?)", (title, category, content, order)).lastrowid

    def update_section(self, section_id: int, title: str, category: str, content: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE sections SET title=?, category=?, content=? WHERE id=?", (title, category, content, section_id))

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

    def create_cv(self, name: str, sections: list[Section], profile: dict[str, str] | None = None) -> CV:
        snapshot = [{"title": section.title, "category": section.category, "content": section.content} for section in sections]
        profile_snapshot = DEFAULT_PROFILE | (profile or self.get_profile())
        with self._connect() as db:
            cv_id = db.execute(
                "INSERT INTO cvs(name, created_at, sections_json, profile_json) VALUES (?, ?, ?, ?)",
                (name, datetime.now().isoformat(timespec="seconds"), json.dumps(snapshot), json.dumps(profile_snapshot)),
            ).lastrowid
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
