"""Filesystem handoff shared by CV Manager and its native Safari extension."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

from database import CVDatabase


APP_GROUP_ID = "group.com.cvmanager.app"


def default_bridge_dir() -> Path:
    override = os.environ.get("CV_MANAGER_SAFARI_BRIDGE_DIR")
    if override:
        return Path(override).expanduser()
    return (
        Path.home()
        / "Library"
        / "Group Containers"
        / APP_GROUP_ID
        / "Library"
        / "Application Support"
        / "CV Manager"
    )


class SafariBridgeStore:
    """Publish CV files and consume append-only requests from Safari."""

    def __init__(self, database: CVDatabase, root: str | Path | None = None):
        self.database = database
        self.root = Path(root) if root else default_bridge_dir()
        self.cv_dir = self.root / "cvs"
        self.request_dir = self.root / "requests"
        self.failed_dir = self.root / "failed"
        for directory in (self.cv_dir, self.request_dir, self.failed_dir):
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def catalog_path(self) -> Path:
        return self.root / "catalog.json"

    @property
    def pending_count(self) -> int:
        return sum(1 for _ in self.request_dir.glob("*.json"))

    @property
    def failed_count(self) -> int:
        return sum(1 for _ in self.failed_dir.glob("*.json"))

    def sync_cvs(self) -> list[dict]:
        """Copy available PDFs and atomically publish the extension catalog."""
        catalog = []
        referenced_files: set[str] = set()
        for cv in self.database.list_cvs():
            if not cv.pdf_path:
                continue
            source = Path(cv.pdf_path)
            if not source.is_file():
                continue
            digest = self._sha256(source)
            filename = f"{cv.id}-{digest[:16]}.pdf"
            destination = self.cv_dir / filename
            if not destination.exists() or destination.stat().st_size != source.stat().st_size:
                self._copy_atomic(source, destination)
            referenced_files.add(filename)
            catalog.append({
                "id": cv.id,
                "name": cv.name,
                # Keep the user-facing export filename when the extension
                # constructs a browser File; the hashed filename is internal
                # to the App Group store.
                "upload_filename": source.name,
                "created_at": cv.created_at,
                "filename": filename,
                "sha256": digest,
                "size": destination.stat().st_size,
            })

        for existing in self.cv_dir.glob("*.pdf"):
            if existing.name not in referenced_files:
                existing.unlink(missing_ok=True)

        document = {
            "version": 1,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "cvs": catalog,
        }
        self._write_json_atomic(self.catalog_path, document)
        return catalog

    def process_requests(self) -> list[dict]:
        """Apply queued create/edit/cancel requests and remove successful files."""
        results = []
        for path in sorted(self.request_dir.glob("*.json"), key=lambda item: (item.stat().st_mtime_ns, item.name)):
            try:
                request = json.loads(path.read_text(encoding="utf-8"))
                result = self._apply_request(request)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
                failed = self.failed_dir / path.name
                path.replace(failed)
                results.append({"action": "failed", "error": str(error), "request": path.name})
                continue
            path.unlink(missing_ok=True)
            results.append(result)
        return results

    def _apply_request(self, request: object) -> dict:
        if not isinstance(request, dict) or request.get("version") != 1:
            raise ValueError("Unsupported Safari request")
        event_id = str(request.get("event_id", "")).strip()
        if not event_id or len(event_id) > 100:
            raise ValueError("Missing Safari event identifier")
        state = request.get("state")
        existing = self.database.get_application_by_capture_event(event_id)
        if state == "cancelled":
            if existing:
                self.database.delete_application(existing.id)
            return {"action": "cancelled", "event_id": event_id, "application_id": existing.id if existing else None}
        if state != "active":
            raise ValueError("Unknown Safari event state")

        payload = request.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Missing application payload")
        company = str(payload.get("company", "")).strip()
        role = str(payload.get("role", "")).strip()
        if not company or not role:
            raise ValueError("Company and role are required")
        cv_id = payload.get("cv_id")
        if cv_id in (None, ""):
            cv_id = None
        else:
            cv_id = int(cv_id)
            if not self.database.get_cv(cv_id):
                cv_id = None

        snapshot = payload.get("snapshot", {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot["bridge_revision"] = int(request.get("revision", 1))
        values = {
            "company": company,
            "role": role,
            "location": str(payload.get("location", "")).strip(),
            "posting_url": str(payload.get("posting_url", "")).strip(),
            "application_date": str(payload.get("application_date", date.today().isoformat())).strip() or date.today().isoformat(),
            "status": existing.status if existing else "Applied",
            "cv_id": cv_id,
            "notes": str(payload.get("notes", "")).strip(),
            "capture_event_id": event_id,
            "posting_snapshot_json": json.dumps(snapshot, ensure_ascii=False),
        }
        if existing:
            self.database.update_application(existing.id, **values)
            application_id = existing.id
            action = "updated"
        else:
            application_id = self.database.create_application(**values)
            action = "created"
        return {"action": action, "event_id": event_id, "application_id": application_id, "company": company, "role": role}

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(128 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _copy_atomic(source: Path, destination: Path) -> None:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".cv-", delete=False) as stream:
            temporary = Path(stream.name)
        try:
            shutil.copy2(source, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_json_atomic(path: Path, value: object) -> None:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".json-", encoding="utf-8", delete=False) as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            temporary = Path(stream.name)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
