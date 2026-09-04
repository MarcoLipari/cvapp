"""SQLite persistence for CV Manager.

CVs snapshot their profile and selected content. Sections sourced from the
library retain a link so later library edits can be propagated to those CVs.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

STATUSES = ("Applied", "Interviewing", "Offer", "Rejected", "Withdrawn")
CURRENT_CV_PROFILE_MIGRATION = "migration.current_cv_profiles_v1"
DEFAULT_PROFILE = {
    "name": "",
    "phone": "",
    "email": "",
    "github": "",
    "website": "",
    "linkedin": "",
}


@dataclass(frozen=True)
class Section:
    id: int
    title: str
    category: str
    content: str
    sort_order: int
    labels: str = ""
    internal_name: str = ""


@dataclass(frozen=True)
class CV:
    id: int
    name: str
    created_at: str
    sections: list[dict]
    profile: dict[str, str]
    markdown_path: str | None
    pdf_path: str | None
    keywords: str = ""


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
    capture_event_id: str = ""
    posting_snapshot_json: str = ""


@dataclass(frozen=True)
class SectionHistory:
    id: int
    section_id: int
    version: int
    recorded_at: str
    change_type: str
    snapshot: dict


@dataclass(frozen=True)
class CVHistory:
    id: int
    cv_id: int
    version: int
    recorded_at: str
    change_type: str
    snapshot: dict


class CVDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sections (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    labels TEXT NOT NULL DEFAULT '',
                    internal_name TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS cvs (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sections_json TEXT NOT NULL,
                    profile_json TEXT NOT NULL DEFAULT '{}',
                    markdown_path TEXT,
                    pdf_path TEXT,
                    keywords TEXT NOT NULL DEFAULT ''
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
                    posting_url TEXT NOT NULL DEFAULT '',
                    capture_event_id TEXT NOT NULL DEFAULT '',
                    posting_snapshot_json TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS section_history (
                    id INTEGER PRIMARY KEY,
                    section_id INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    UNIQUE(section_id, version)
                );
                CREATE TABLE IF NOT EXISTS cv_history (
                    id INTEGER PRIMARY KEY,
                    cv_id INTEGER NOT NULL REFERENCES cvs(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    UNIQUE(cv_id, version)
                );
                CREATE INDEX IF NOT EXISTS section_history_recorded_at_idx
                    ON section_history(recorded_at DESC);
                CREATE INDEX IF NOT EXISTS cv_history_recorded_at_idx
                    ON cv_history(recorded_at DESC);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(cvs)")}
            if "profile_json" not in columns:
                db.execute("ALTER TABLE cvs ADD COLUMN profile_json TEXT NOT NULL DEFAULT '{}'")
            if "keywords" not in columns:
                db.execute("ALTER TABLE cvs ADD COLUMN keywords TEXT NOT NULL DEFAULT ''")
            application_columns = {row["name"] for row in db.execute("PRAGMA table_info(applications)")}
            if "posting_url" not in application_columns:
                db.execute("ALTER TABLE applications ADD COLUMN posting_url TEXT NOT NULL DEFAULT ''")
            if "capture_event_id" not in application_columns:
                db.execute("ALTER TABLE applications ADD COLUMN capture_event_id TEXT NOT NULL DEFAULT ''")
            if "posting_snapshot_json" not in application_columns:
                db.execute("ALTER TABLE applications ADD COLUMN posting_snapshot_json TEXT NOT NULL DEFAULT ''")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS applications_capture_event_id_idx "
                "ON applications(capture_event_id) WHERE capture_event_id <> ''"
            )
            section_columns = {row["name"] for row in db.execute("PRAGMA table_info(sections)")}
            if "labels" not in section_columns:
                db.execute("ALTER TABLE sections ADD COLUMN labels TEXT NOT NULL DEFAULT ''")
            if "internal_name" not in section_columns:
                db.execute("ALTER TABLE sections ADD COLUMN internal_name TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE sections SET internal_name=title WHERE TRIM(internal_name)='' ")
            self._backfill_history(db)
            self._migrate_current_cv_profiles(db)

    @staticmethod
    def _section(row: sqlite3.Row) -> Section:
        return Section(
            row["id"], row["title"], row["category"], row["content"], row["sort_order"],
            row["labels"], row["internal_name"] or row["title"],
        )

    @staticmethod
    def _cv(row: sqlite3.Row) -> CV:
        profile = DEFAULT_PROFILE | json.loads(row["profile_json"] or "{}")
        return CV(
            row["id"], row["name"], row["created_at"], json.loads(row["sections_json"]),
            profile, row["markdown_path"], row["pdf_path"], row["keywords"],
        )

    @staticmethod
    def _application(row: sqlite3.Row) -> Application:
        return Application(
            row["id"], row["company"], row["role"], row["location"], row["application_date"],
            row["status"], row["cv_id"], row["notes"], row["posting_url"],
            row["capture_event_id"], row["posting_snapshot_json"],
        )

    @staticmethod
    def _section_history(row: sqlite3.Row) -> SectionHistory:
        return SectionHistory(
            row["id"], row["section_id"], row["version"], row["recorded_at"],
            row["change_type"], json.loads(row["snapshot_json"]),
        )

    @staticmethod
    def _cv_history(row: sqlite3.Row) -> CVHistory:
        return CVHistory(
            row["id"], row["cv_id"], row["version"], row["recorded_at"],
            row["change_type"], json.loads(row["snapshot_json"]),
        )

    @staticmethod
    def _history_timestamp() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @classmethod
    def _record_section_history(
        cls,
        db: sqlite3.Connection,
        section_id: int,
        change_type: str,
        *,
        only_if_changed: bool = False,
    ) -> bool:
        row = db.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
        if not row:
            return False
        snapshot = asdict(cls._section(row))
        if only_if_changed:
            latest = db.execute(
                "SELECT snapshot_json FROM section_history WHERE section_id=? ORDER BY version DESC LIMIT 1",
                (section_id,),
            ).fetchone()
            if latest and json.loads(latest["snapshot_json"]) == snapshot:
                return False
        version = db.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM section_history WHERE section_id=?",
            (section_id,),
        ).fetchone()[0]
        db.execute(
            "INSERT INTO section_history(section_id, version, recorded_at, change_type, snapshot_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (section_id, version, cls._history_timestamp(), change_type, json.dumps(snapshot)),
        )
        return True

    @classmethod
    def _record_cv_history(
        cls,
        db: sqlite3.Connection,
        cv_id: int,
        change_type: str,
        *,
        only_if_changed: bool = False,
    ) -> bool:
        row = db.execute("SELECT * FROM cvs WHERE id=?", (cv_id,)).fetchone()
        if not row:
            return False
        cv = cls._cv(row)
        snapshot = {
            "id": cv.id,
            "name": cv.name,
            "created_at": cv.created_at,
            "sections": cv.sections,
            "profile": cv.profile,
            "keywords": cv.keywords,
        }
        if only_if_changed:
            latest = db.execute(
                "SELECT snapshot_json FROM cv_history WHERE cv_id=? ORDER BY version DESC LIMIT 1",
                (cv_id,),
            ).fetchone()
            if latest and json.loads(latest["snapshot_json"]) == snapshot:
                return False
        version = db.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM cv_history WHERE cv_id=?",
            (cv_id,),
        ).fetchone()[0]
        db.execute(
            "INSERT INTO cv_history(cv_id, version, recorded_at, change_type, snapshot_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (cv_id, version, cls._history_timestamp(), change_type, json.dumps(snapshot)),
        )
        return True

    def record_section_version(self, section_id: int, change_type: str = "edited") -> bool:
        """Record the current section after an autosaved editing session."""
        with self._connect() as db:
            return self._record_section_history(
                db, section_id, change_type, only_if_changed=True
            )

    def record_cv_version(self, cv_id: int, change_type: str = "edited") -> bool:
        """Record the current CV after an autosaved editing session."""
        with self._connect() as db:
            return self._record_cv_history(db, cv_id, change_type, only_if_changed=True)

    @classmethod
    def _backfill_history(cls, db: sqlite3.Connection) -> None:
        """Give records created before history support a single current-state baseline."""
        section_ids = db.execute(
            "SELECT id FROM sections WHERE NOT EXISTS "
            "(SELECT 1 FROM section_history WHERE section_history.section_id=sections.id)"
        ).fetchall()
        for row in section_ids:
            cls._record_section_history(db, row["id"], "baseline")
        cv_ids = db.execute(
            "SELECT id FROM cvs WHERE NOT EXISTS "
            "(SELECT 1 FROM cv_history WHERE cv_history.cv_id=cvs.id)"
        ).fetchall()
        for row in cv_ids:
            cls._record_cv_history(db, row["id"], "baseline")

    def get_profile(self) -> dict[str, str]:
        with self._connect() as db:
            rows = db.execute("SELECT key, value FROM settings WHERE key LIKE 'profile.%'").fetchall()
        saved = {row["key"].removeprefix("profile."): row["value"] for row in rows}
        return DEFAULT_PROFILE | saved

    def profile_is_configured(self) -> bool:
        """Return whether the required first-run profile fields were saved."""
        profile = self.get_profile()
        return bool(profile["name"].strip() and profile["email"].strip())

    @classmethod
    def _apply_profile_to_current_cvs(
        cls,
        db: sqlite3.Connection,
        profile: dict[str, str],
    ) -> None:
        profile_json = json.dumps(profile)
        cvs = db.execute("SELECT id, profile_json FROM cvs").fetchall()
        for cv in cvs:
            saved_profile = DEFAULT_PROFILE | json.loads(cv["profile_json"] or "{}")
            if saved_profile == profile:
                continue
            db.execute(
                "UPDATE cvs SET profile_json=?, markdown_path=NULL, pdf_path=NULL WHERE id=?",
                (profile_json, cv["id"]),
            )
            cls._record_cv_history(db, cv["id"], "profile_updated")

    @classmethod
    def _migrate_current_cv_profiles(cls, db: sqlite3.Connection) -> None:
        """Apply a profile saved before current-CV propagation was introduced."""
        migrated = db.execute(
            "SELECT 1 FROM settings WHERE key=?",
            (CURRENT_CV_PROFILE_MIGRATION,),
        ).fetchone()
        if migrated:
            return
        rows = db.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'profile.%'"
        ).fetchall()
        if rows:
            saved = {row["key"].removeprefix("profile."): row["value"] for row in rows}
            profile = {
                key: str(saved.get(key, default)).strip()
                for key, default in DEFAULT_PROFILE.items()
            }
            cls._apply_profile_to_current_cvs(db, profile)
        db.execute(
            "INSERT INTO settings(key, value) VALUES (?, '1')",
            (CURRENT_CV_PROFILE_MIGRATION,),
        )

    def update_profile(self, profile: dict[str, str]) -> None:
        """Update personal details on the profile and every current CV.

        Existing CV history rows remain unchanged, while each affected CV gets
        a new current history version and must be exported again.
        """
        values = {key: str(profile.get(key, "")).strip() for key in DEFAULT_PROFILE}
        with self._connect() as db:
            db.executemany(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(f"profile.{key}", value) for key, value in values.items()],
            )
            self._apply_profile_to_current_cvs(db, values)

    def list_sections(self) -> list[Section]:
        with self._connect() as db:
            return [self._section(row) for row in db.execute("SELECT * FROM sections ORDER BY sort_order, id")]

    def get_section(self, section_id: int) -> Section | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM sections WHERE id = ?", (section_id,)).fetchone()
        return self._section(row) if row else None

    def create_section(
        self,
        title: str,
        category: str,
        content: str,
        labels: str = "",
        internal_name: str | None = None,
    ) -> int:
        with self._connect() as db:
            order = db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM sections").fetchone()[0]
            section_id = db.execute(
                "INSERT INTO sections(title, category, content, sort_order, labels, internal_name) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (title, category, content, order, labels, (internal_name or title).strip()),
            ).lastrowid
            self._record_section_history(db, section_id, "created")
            return section_id

    def duplicate_section(self, section_id: int) -> int:
        """Create an independent reusable copy of a library section."""
        section = self.get_section(section_id)
        if not section:
            raise ValueError("Section not found")
        return self.create_section(
            section.title,
            section.category,
            section.content,
            section.labels,
            internal_name=f"{section.internal_name} (copy)",
        )

    def split_section(
        self,
        section_id: int,
        first_content: str,
        second_content: str,
        second_internal_name: str,
        *,
        record_history: bool = True,
    ) -> tuple[int, list[int]]:
        """Split one reusable entry while preserving linked CV content and order."""
        first_content = first_content.strip()
        second_content = second_content.strip()
        second_internal_name = second_internal_name.strip()
        if not first_content or not second_content:
            raise ValueError("Both split entries need content")
        if not second_internal_name:
            raise ValueError("The new entry needs an internal name")

        affected_cv_ids: list[int] = []
        with self._connect() as db:
            source = db.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
            if not source:
                raise ValueError("Section not found")

            db.execute("UPDATE sections SET sort_order=sort_order+1 WHERE sort_order>?", (source["sort_order"],))
            db.execute("UPDATE sections SET content=? WHERE id=?", (first_content, section_id))
            new_section_id = db.execute(
                "INSERT INTO sections(title, category, content, sort_order, labels, internal_name) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    source["title"], source["category"], second_content,
                    source["sort_order"] + 1, source["labels"], second_internal_name,
                ),
            ).lastrowid

            if record_history:
                self._record_section_history(db, section_id, "split")
            self._record_section_history(db, new_section_id, "created")

            for row in db.execute("SELECT id, sections_json FROM cvs").fetchall():
                sections = json.loads(row["sections_json"])
                updated_sections = []
                changed = False
                for section in sections:
                    linked = section.get("source_section_id") == section_id
                    legacy_match = "source_section_id" not in section and all(
                        section.get(key, "") == source[key]
                        for key in ("title", "category", "content")
                    )
                    if not (linked or legacy_match):
                        updated_sections.append(section)
                        continue
                    updated_sections.extend([
                        {
                            "title": source["title"],
                            "category": source["category"],
                            "content": first_content,
                            "source_section_id": section_id,
                        },
                        {
                            "title": source["title"],
                            "category": source["category"],
                            "content": second_content,
                            "source_section_id": new_section_id,
                        },
                    ])
                    changed = True
                if changed:
                    db.execute(
                        "UPDATE cvs SET sections_json=?, markdown_path=NULL, pdf_path=NULL WHERE id=?",
                        (json.dumps(updated_sections), row["id"]),
                    )
                    if record_history:
                        self._record_cv_history(db, row["id"], "linked_section_split")
                    affected_cv_ids.append(row["id"])

        return new_section_id, affected_cv_ids

    @classmethod
    def _update_section(
        cls,
        db: sqlite3.Connection,
        section_id: int,
        title: str,
        category: str,
        content: str,
        labels: str,
        internal_name: str | None = None,
        skip_cv_id: int | None = None,
        record_history: bool = True,
    ) -> list[int]:
        previous = db.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
        if not previous:
            raise ValueError("Section not found")
        saved_internal_name = (internal_name if internal_name is not None else previous["internal_name"]).strip()
        if not saved_internal_name:
            raise ValueError("A section needs an internal name")
        db.execute(
            "UPDATE sections SET title=?, category=?, content=?, labels=?, internal_name=? WHERE id=?",
            (title, category, content, labels, saved_internal_name, section_id),
        )
        if record_history and any(value != previous[key] for key, value in (
            ("title", title), ("category", category), ("content", content), ("labels", labels),
            ("internal_name", saved_internal_name),
        )):
            cls._record_section_history(db, section_id, "edited")
        if all(value == previous[key] for key, value in (
            ("title", title), ("category", category), ("content", content),
        )):
            return []

        affected_cv_ids = []
        rows = db.execute("SELECT id, sections_json FROM cvs").fetchall()
        for row in rows:
            if row["id"] == skip_cv_id:
                continue
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
                if record_history:
                    cls._record_cv_history(db, row["id"], "linked_section_updated")
                affected_cv_ids.append(row["id"])
        return affected_cv_ids

    def update_section(
        self,
        section_id: int,
        title: str,
        category: str,
        content: str,
        labels: str = "",
        internal_name: str | None = None,
        *,
        record_history: bool = True,
    ) -> list[int]:
        with self._connect() as db:
            return self._update_section(
                db,
                section_id,
                title,
                category,
                content,
                labels,
                internal_name,
                record_history=record_history,
            )

    def count_linked_cvs(self, section_id: int) -> int:
        """Count CVs that would receive a shared edit to this section."""
        with self._connect() as db:
            source = db.execute("SELECT title, category, content FROM sections WHERE id=?", (section_id,)).fetchone()
            if not source:
                return 0
            count = 0
            for row in db.execute("SELECT sections_json FROM cvs"):
                sections = json.loads(row["sections_json"])
                if any(
                    section.get("source_section_id") == section_id
                    or (
                        "source_section_id" not in section
                        and all(section.get(key, "") == source[key] for key in ("title", "category", "content"))
                    )
                    for section in sections
                ):
                    count += 1
            return count

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

    def list_section_history(self, section_id: int | None = None) -> list[SectionHistory]:
        query = "SELECT * FROM section_history"
        parameters: tuple = ()
        if section_id is not None:
            query += " WHERE section_id=?"
            parameters = (section_id,)
        query += " ORDER BY recorded_at DESC, id DESC"
        with self._connect() as db:
            return [self._section_history(row) for row in db.execute(query, parameters)]

    def list_cv_history(self, cv_id: int | None = None) -> list[CVHistory]:
        query = "SELECT * FROM cv_history"
        parameters: tuple = ()
        if cv_id is not None:
            query += " WHERE cv_id=?"
            parameters = (cv_id,)
        query += " ORDER BY recorded_at DESC, id DESC"
        with self._connect() as db:
            return [self._cv_history(row) for row in db.execute(query, parameters)]

    @staticmethod
    def _section_snapshot(section: Section | dict) -> dict:
        """Return the portable fields stored in a CV snapshot."""
        if isinstance(section, Section):
            return {"title": section.title, "category": section.category, "content": section.content, "source_section_id": section.id}
        snapshot = {key: str(section.get(key, "")) for key in ("title", "category", "content")}
        if section.get("source_section_id") is not None:
            snapshot["source_section_id"] = int(section["source_section_id"])
        return snapshot

    def create_cv(
        self,
        name: str,
        sections: list[Section | dict],
        profile: dict[str, str] | None = None,
        keywords: str = "",
    ) -> CV:
        snapshot = [self._section_snapshot(section) for section in sections]
        profile_snapshot = DEFAULT_PROFILE | (profile or self.get_profile())
        with self._connect() as db:
            cv_id = db.execute(
                "INSERT INTO cvs(name, created_at, sections_json, profile_json, keywords) VALUES (?, ?, ?, ?, ?)",
                (
                    name, datetime.now().isoformat(timespec="seconds"), json.dumps(snapshot),
                    json.dumps(profile_snapshot), keywords.strip(),
                ),
            ).lastrowid
            self._record_cv_history(db, cv_id, "created")
        return self.get_cv(cv_id)

    def update_cv(
        self,
        cv_id: int,
        name: str,
        sections: list[Section | dict],
        profile: dict[str, str] | None = None,
        keywords: str | None = None,
    ) -> CV:
        """Update a CV snapshot while retaining its identity and creation date."""
        if not name.strip() or not sections:
            raise ValueError("A CV needs a name and at least one section")
        snapshot = [self._section_snapshot(section) for section in sections]
        with self._connect() as db:
            current = db.execute(
                "SELECT name, sections_json, profile_json, keywords FROM cvs WHERE id=?", (cv_id,)
            ).fetchone()
            if not current:
                raise ValueError("CV not found")
            saved_profile = json.loads(current["profile_json"] or "{}")
            profile_snapshot = DEFAULT_PROFILE | (profile if profile is not None else saved_profile)
            saved_keywords = current["keywords"] if keywords is None else keywords.strip()
            changed = (
                name.strip() != current["name"]
                or snapshot != json.loads(current["sections_json"])
                or profile_snapshot != (DEFAULT_PROFILE | saved_profile)
                or saved_keywords != current["keywords"]
            )
            db.execute(
                "UPDATE cvs SET name=?, sections_json=?, profile_json=?, keywords=?, markdown_path=NULL, pdf_path=NULL WHERE id=?",
                (name.strip(), json.dumps(snapshot), json.dumps(profile_snapshot), saved_keywords, cv_id),
            )
            if changed:
                self._record_cv_history(db, cv_id, "edited")
        return self.get_cv(cv_id)

    def update_cv_from_tree(
        self,
        cv_id: int,
        name: str,
        sections: list[dict],
        profile: dict[str, str],
        section_actions: dict[int, str],
    ) -> tuple[CV, list[int], list[int]]:
        """Apply Tree View section choices and save the CV in one transaction.

        Section actions are keyed by the section's position in ``sections`` and
        may be ``link``, ``copy``, or ``shared``. Link actions add CV-specific
        content to the reusable library. Copy actions create a reusable copy and
        link only this CV to it. Shared actions update the existing source
        section and every other linked CV.
        """
        saved_name = name.strip()
        if not saved_name or not sections:
            raise ValueError("A CV needs a name and at least one section")

        snapshot = [self._section_snapshot(section) for section in sections]
        affected_cv_ids: set[int] = set()
        created_section_ids: list[int] = []
        with self._connect() as db:
            current = db.execute("SELECT name, sections_json, profile_json FROM cvs WHERE id=?", (cv_id,)).fetchone()
            if not current:
                raise ValueError("CV not found")

            for section_index, action in sorted(section_actions.items()):
                if section_index < 0 or section_index >= len(snapshot):
                    raise ValueError("Unknown CV section")
                section = snapshot[section_index]
                source_section_id = section.get("source_section_id")
                if action == "link":
                    if source_section_id is not None:
                        raise ValueError("Only CV-specific entries can be moved to linked entries")
                    order = db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM sections").fetchone()[0]
                    internal_name = f"{saved_name} | {section['title']}"
                    linked_section_id = db.execute(
                        "INSERT INTO sections(title, category, content, sort_order, labels, internal_name) "
                        "VALUES (?, ?, ?, ?, '', ?)",
                        (
                            section["title"], section["category"], section["content"],
                            order, internal_name,
                        ),
                    ).lastrowid
                    self._record_section_history(db, linked_section_id, "created")
                    section["source_section_id"] = linked_section_id
                    created_section_ids.append(linked_section_id)
                    continue
                if source_section_id is None:
                    raise ValueError("Only linked sections can be copied or edited as shared")
                source = db.execute("SELECT * FROM sections WHERE id=?", (source_section_id,)).fetchone()
                if not source:
                    raise ValueError("The linked library section no longer exists")

                if action == "copy":
                    order = db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM sections").fetchone()[0]
                    internal_name = f"{saved_name} | {source['internal_name'] or source['title']}"
                    copied_section_id = db.execute(
                        "INSERT INTO sections(title, category, content, sort_order, labels, internal_name) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            section["title"], section["category"], section["content"], order,
                            source["labels"], internal_name,
                        ),
                    ).lastrowid
                    self._record_section_history(db, copied_section_id, "created")
                    section["source_section_id"] = copied_section_id
                    created_section_ids.append(copied_section_id)
                elif action == "shared":
                    affected_cv_ids.update(self._update_section(
                        db,
                        source_section_id,
                        section["title"],
                        section["category"],
                        section["content"],
                        source["labels"],
                        source["internal_name"],
                        skip_cv_id=cv_id,
                    ))
                    affected_cv_ids.add(cv_id)
                else:
                    raise ValueError("Unknown linked-section action")

            profile_snapshot = DEFAULT_PROFILE | profile
            changed = (
                saved_name != current["name"]
                or snapshot != json.loads(current["sections_json"])
                or profile_snapshot != (DEFAULT_PROFILE | json.loads(current["profile_json"] or "{}"))
            )
            db.execute(
                "UPDATE cvs SET name=?, sections_json=?, profile_json=?, markdown_path=NULL, pdf_path=NULL WHERE id=?",
                (saved_name, json.dumps(snapshot), json.dumps(profile_snapshot), cv_id),
            )
            if changed:
                self._record_cv_history(db, cv_id, "edited")

        updated = self.get_cv(cv_id)
        if not updated:
            raise ValueError("CV not found after update")
        return updated, sorted(affected_cv_ids), created_section_ids

    def update_cv_exports(self, cv_id: int, markdown_path: str | Path, pdf_path: str | Path) -> None:
        with self._connect() as db:
            db.execute("UPDATE cvs SET markdown_path=?, pdf_path=? WHERE id=?", (str(markdown_path), str(pdf_path), cv_id))

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

    def get_application_by_capture_event(self, event_id: str) -> Application | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM applications WHERE capture_event_id=?", (event_id,)).fetchone()
        return self._application(row) if row else None

    def create_application(self, **values) -> int:
        self._validate_application(values)
        columns = (
            "company", "role", "location", "application_date", "status", "cv_id", "notes",
            "posting_url", "capture_event_id", "posting_snapshot_json",
        )
        with self._connect() as db:
            return db.execute(
                f"INSERT INTO applications({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(values.get(column, "") for column in columns),
            ).lastrowid

    def update_application(self, application_id: int, **values) -> None:
        self._validate_application(values)
        columns = ["company", "role", "location", "application_date", "status", "cv_id", "notes", "posting_url"]
        columns.extend(column for column in ("capture_event_id", "posting_snapshot_json") if column in values)
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
            "version": 2,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "profile": self.get_profile(),
            "sections": [asdict(section) for section in self.list_sections()],
            "cvs": [asdict(cv) for cv in self.list_cvs()],
            "applications": [asdict(application) for application in self.list_applications()],
            "section_history": [asdict(entry) for entry in self.list_section_history()],
            "cv_history": [asdict(entry) for entry in self.list_cv_history()],
        }

    @staticmethod
    def _validate_application(values: dict) -> None:
        if not values.get("company") or not values.get("role"):
            raise ValueError("Company and role are required")
        if values.get("status") not in STATUSES:
            raise ValueError("Unknown application status")
