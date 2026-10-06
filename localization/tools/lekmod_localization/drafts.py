"""Durable, project-local edits which never rebuild or overwrite mod files."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .common import CatalogError


class DraftStore:
    """Commit a single row at a time, with revision checks and persistent undo."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1),
                    revision INTEGER NOT NULL, cursor INTEGER NOT NULL);
                INSERT OR IGNORE INTO state VALUES (1, 0, 0);
                CREATE TABLE IF NOT EXISTS drafts (slot TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL, payload TEXT, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY,
                    slot TEXT NOT NULL, before TEXT, after TEXT, edit_group TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        """Close every SQLite handle, including failed writes and frozen Windows runs."""
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def record(row):
        """Decode an entry without confusing an intentionally deleted draft with revision zero."""
        return ({"slot": row["slot"], "revision": row["revision"],
                 "payload": json.loads(row["payload"]) if row["payload"] else None,
                 "updated_at": row["updated_at"]} if row else None)

    def get(self, slot: str) -> dict | None:
        """Read one tiny entry for an optimistic browser save."""
        with self.connection() as db:
            return self.record(db.execute("SELECT * FROM drafts WHERE slot=?", (slot,)).fetchone())

    def entries(self) -> list[dict]:
        """Snapshot pending edits before an Apply, export or table refresh."""
        with self.connection() as db:
            return [self.record(row) for row in db.execute(
                "SELECT * FROM drafts WHERE payload IS NOT NULL ORDER BY updated_at, slot")]

    def records(self) -> dict[str, dict]:
        """Read all revisions once per table request, rather than opening SQLite per row."""
        with self.connection() as db:
            return {row['slot']: self.record(row) for row in db.execute('SELECT * FROM drafts')}

    def status(self) -> dict:
        """Expose a count and undo state without reading translation text."""
        with self.connection() as db:
            cursor = db.execute("SELECT cursor FROM state WHERE id=1").fetchone()[0]
            maximum = db.execute("SELECT coalesce(max(id),0) FROM history").fetchone()[0]
            count = db.execute("SELECT count(*) FROM drafts WHERE payload IS NOT NULL").fetchone()[0]
            return {"draft_count": count, "draft_undo_available": cursor > 0,
                    "draft_redo_available": cursor < maximum}

    @staticmethod
    def write(db, slot, payload):
        """Advance a global revision so undo cannot accidentally reuse an old browser revision."""
        revision = db.execute("SELECT revision FROM state WHERE id=1").fetchone()[0] + 1
        now = datetime.now(timezone.utc).isoformat()
        db.execute("UPDATE state SET revision=? WHERE id=1", (revision,))
        db.execute("INSERT OR REPLACE INTO drafts VALUES (?,?,?,?)", (slot, revision, payload, now))

    def put(self, slot: str, payload: dict | None, revision: int, group: str = "") -> dict:
        """Save incomplete text too; token and source checks belong to explicit Apply."""
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else None
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM drafts WHERE slot=?", (slot,)).fetchone()
            if revision != (row["revision"] if row else 0):
                raise CatalogError("This local draft changed in another view. Your text is still in the form; reload that row before saving again.")
            before = row["payload"] if row else None
            if before != encoded:
                cursor = db.execute("SELECT cursor FROM state WHERE id=1").fetchone()[0]
                db.execute("DELETE FROM history WHERE id>?", (cursor,))
                previous = db.execute("SELECT * FROM history WHERE id=?", (cursor,)).fetchone()
                if group and previous and previous["slot"] == slot and previous["edit_group"] == group:
                    db.execute("UPDATE history SET after=? WHERE id=?", (encoded, cursor))
                else:
                    cursor += 1
                    db.execute("INSERT INTO history VALUES (?,?,?,?,?)", (cursor, slot, before, encoded, group))
                    db.execute("UPDATE state SET cursor=? WHERE id=1", (cursor,))
                self.write(db, slot, encoded)
            result = self.record(db.execute("SELECT * FROM drafts WHERE slot=?", (slot,)).fetchone())
        return {"entry": result, **self.status()}

    def replay(self, *, undo: bool) -> dict:
        """Undo or redo local edits immediately, without changing CSV or game XML."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("SELECT cursor FROM state WHERE id=1").fetchone()[0]
            action = db.execute("SELECT * FROM history WHERE id=?", (cursor if undo else cursor + 1,)).fetchone()
            if not action:
                raise CatalogError("No local edit to undo" if undo else "No local edit to redo")
            self.write(db, action["slot"], action["before"] if undo else action["after"])
            db.execute("UPDATE state SET cursor=? WHERE id=1", (cursor - 1 if undo else cursor + 1,))
        return self.status()

    def finish(self, applied: list[dict], bases: dict[str, dict]) -> dict:
        """Clear only applied revisions; retain and rebase edits typed during the build."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for entry in applied:
                row = db.execute("SELECT * FROM drafts WHERE slot=?", (entry["slot"],)).fetchone()
                if not row or not row["payload"]:
                    continue
                if row["revision"] == entry["revision"]:
                    self.write(db, entry["slot"], None)
                else:
                    payload = json.loads(row["payload"])
                    payload["base"] = bases[entry["slot"]]
                    payload["create"] = False
                    if payload["mode"] == "developer":
                        payload["key"] = payload["base"]["key"]
                    self.write(db, entry["slot"], json.dumps(payload, ensure_ascii=False, sort_keys=True))
            # Undo concerns unapplied work. Applied source has its own Git/backups.
            db.execute("DELETE FROM history")
            db.execute("UPDATE state SET cursor=0 WHERE id=1")
        return self.status()

    def carry_from(self, previous: "DraftStore") -> int:
        """Copy drafts on a mod upgrade, keeping their old baseline for conflict detection."""
        count = 0
        for entry in previous.entries():
            if self.get(entry["slot"]) is None:
                self.put(entry["slot"], entry["payload"], 0)
                count += 1
        return count
