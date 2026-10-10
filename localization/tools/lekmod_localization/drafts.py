"""Durable working versions, bounded Undo history and 50 explicit Saves."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

from .common import CatalogError

HISTORY_LIMIT = 100
SAVE_LIMIT = 50


class DraftStore:
    """Keep editor versions separate from project and installed-game writes."""

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
                CREATE TABLE IF NOT EXISTS versions (slot TEXT PRIMARY KEY,
                    current TEXT, baseline TEXT, initial TEXT,
                    signature TEXT NOT NULL, initial_signature TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoint (slot TEXT PRIMARY KEY,
                    payload TEXT, signature TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoint_state (id INTEGER PRIMARY KEY CHECK(id=1),
                    saved_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS saved_checkpoints (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    saved_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS saved_content (hash TEXT PRIMARY KEY, payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS saved_rows (save_id INTEGER NOT NULL,
                    slot TEXT NOT NULL, content_hash TEXT NOT NULL, PRIMARY KEY(save_id, slot));
                CREATE INDEX IF NOT EXISTS saved_rows_content ON saved_rows(content_hash);
                CREATE TABLE IF NOT EXISTS apply_issues (slot TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL);
            """)
            if not db.execute("SELECT 1 FROM migrations WHERE name='versions'").fetchone():
                for row in db.execute("SELECT * FROM drafts").fetchall():
                    value = self.decode(row['payload'])
                    seed = value
                    if seed is None:
                        for action in db.execute('SELECT before, after FROM history WHERE slot=? ORDER BY id DESC',
                                                 (row['slot'],)).fetchall():
                            seed = self.decode(action['after']) or self.decode(action['before'])
                            if seed is not None:
                                break
                    if seed is None:
                        continue
                    baseline = self.baseline(seed)
                    current = value if value is not None else baseline
                    db.execute('INSERT OR IGNORE INTO versions VALUES (?,?,?,?,?,?)',
                        (row['slot'], self.encode(current), self.encode(baseline), self.encode(baseline),
                         self.signature(current), self.signature(baseline)))
                for row in db.execute('SELECT * FROM history').fetchall():
                    version = db.execute('SELECT baseline FROM versions WHERE slot=?', (row['slot'],)).fetchone()
                    if version:
                        db.execute('UPDATE history SET before=coalesce(before,?), after=coalesce(after,?) WHERE id=?',
                                   (version[0], version[0], row['id']))
                db.execute("INSERT INTO migrations VALUES ('versions')")
            self.prune(db)
            if not db.execute("SELECT 1 FROM migrations WHERE name='saved-checkpoints'").fetchone():
                saved = db.execute('SELECT saved_at FROM checkpoint_state WHERE id=1').fetchone()
                if saved:
                    self.archive_save(db, saved[0], db.execute('SELECT slot, payload FROM checkpoint').fetchall())
                db.execute("INSERT INTO migrations VALUES ('saved-checkpoints')")

    @contextmanager
    def connection(self):
        """Close handles after successful and failed writes, including frozen Windows runs."""
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def encode(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else None

    @staticmethod
    def decode(value):
        return json.loads(value) if value else None

    @classmethod
    def signature(cls, value):
        if value and value.get('mode') in ('translator', 'developer'):
            accepted = value.get('formatting_approval', '')
            value = ({'absent': True} if value.get('delete') else
                {'edit': value['edit'], 'absent': False, **({'source_fingerprint': value.get('source_fingerprint')}
                 if value['mode'] == 'translator' and value['edit']['text'] else {}),
                 **({'formatting_approval': accepted} if accepted else {})})
        return cls.encode(value) or ''

    @staticmethod
    def baseline(value):
        """Capture original content as a version, including an absent new English key."""
        if not value or value.get('mode') not in ('translator', 'developer'):
            return None
        result = json.loads(json.dumps(value))
        base = value['base']
        if value['mode'] == 'translator':
            approved = base.get('approval')
            if approved:
                result['source_fingerprint'] = approved.get('source_fingerprint', result.get('source_fingerprint'))
            result.pop('formatting_approval', None)
            if approved and approved.get('formatting_approval'):
                result['formatting_approval'] = approved['formatting_approval']
            result['edit'] = ({'text': approved['text'], 'gender': approved.get('gender', ''),
                'plurality': approved.get('plurality', ''), 'note': approved.get('translator_note', ''),
                'identifier': ''} if approved else
                {'text': '', 'gender': '', 'plurality': '', 'note': '', 'identifier': ''})
        elif value.get('create'):
            result['delete'] = True
        else:
            result['edit'] = {'text': base['text'], 'identifier': base['key'],
                              'gender': '', 'plurality': '', 'note': ''}
        return result

    @classmethod
    def pending(cls, value, baseline):
        """Compare the desired version with today's project, independently of history."""
        if cls.signature(value) == cls.signature(baseline):
            return None
        if not value or not baseline or value.get('mode') not in ('translator', 'developer'):
            return value
        result = json.loads(json.dumps(value))
        result['base'] = baseline['base']
        if result['mode'] == 'developer':
            result['key'] = baseline['edit']['identifier']
            result['index'] = baseline['index']
            result['create'] = baseline.get('delete', False)
        return result

    @staticmethod
    def prune(db):
        db.execute('DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT ?)',
                   (HISTORY_LIMIT,))

    @classmethod
    def record(cls, row):
        """A removed draft still has a revision; it is not a never-edited row."""
        return ({"slot": row["slot"], "revision": row["revision"],
                 "payload": cls.decode(row["payload"]), "updated_at": row["updated_at"]} if row else None)

    def get(self, slot: str) -> dict | None:
        with self.connection() as db:
            return self.record(db.execute("SELECT * FROM drafts WHERE slot=?", (slot,)).fetchone())

    def entries(self) -> list[dict]:
        with self.connection() as db:
            return [self.record(row) for row in db.execute(
                "SELECT * FROM drafts WHERE payload IS NOT NULL ORDER BY updated_at, slot")]

    def records(self) -> dict[str, dict]:
        with self.connection() as db:
            return {row['slot']: self.record(row) for row in db.execute('SELECT * FROM drafts')}

    def baselines(self) -> dict:
        with self.connection() as db:
            return {row['slot']: self.decode(row['baseline']) for row in db.execute('SELECT slot, baseline FROM versions')}

    def status(self) -> dict:
        """Report history and Save state without loading texts into Python."""
        with self.connection() as db:
            cursor = db.execute("SELECT cursor FROM state WHERE id=1").fetchone()[0]
            count = db.execute("SELECT count(*) FROM drafts WHERE payload IS NOT NULL").fetchone()[0]
            saved = db.execute('SELECT saved_at FROM checkpoint_state WHERE id=1').fetchone()
            dirty = db.execute("""SELECT count(*) FROM versions v LEFT JOIN checkpoint c USING(slot)
                WHERE v.signature != coalesce(c.signature, v.initial_signature)""").fetchone()[0]
            return {"draft_count": count,
                    "draft_undo_available": bool(db.execute('SELECT 1 FROM history WHERE id<=? LIMIT 1', (cursor,)).fetchone()),
                    "draft_redo_available": bool(db.execute('SELECT 1 FROM history WHERE id>? LIMIT 1', (cursor,)).fetchone()),
                    "history_count": db.execute('SELECT count(*) FROM history').fetchone()[0],
                    "saved_version_count": db.execute('SELECT count(*) FROM saved_checkpoints').fetchone()[0],
                    "apply_issue_count": db.execute('SELECT count(*) FROM apply_issues i JOIN drafts d USING(slot) '
                                                    'WHERE d.payload IS NOT NULL').fetchone()[0],
                    "checkpoint_saved_at": saved[0] if saved else '',
                    "checkpoint_dirty": bool(dirty)}

    @staticmethod
    def write(db, slot, payload):
        revision = db.execute("SELECT revision FROM state WHERE id=1").fetchone()[0] + 1
        db.execute("UPDATE state SET revision=? WHERE id=1", (revision,))
        db.execute("INSERT OR REPLACE INTO drafts VALUES (?,?,?,?)",
                   (slot, revision, payload, datetime.now(timezone.utc).isoformat()))

    def put(self, slot: str, payload: dict | None, revision: int, group: str = "") -> dict:
        """Persist a meaningful edit and truncate the redo branch atomically."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM drafts WHERE slot=?", (slot,)).fetchone()
            if revision != (row["revision"] if row else 0):
                raise CatalogError("This local draft changed in another view. Your text is still in the form; reload that row before saving again.")
            version = db.execute('SELECT * FROM versions WHERE slot=?', (slot,)).fetchone()
            if version is None:
                baseline = self.baseline(payload)
                before = self.encode(baseline)
                db.execute('INSERT INTO versions VALUES (?,?,?,?,?,?)',
                    (slot, before, before, before, self.signature(baseline), self.signature(baseline)))
            else:
                before = version['current']
                baseline = self.decode(version['baseline'])
                if payload and (not row or not row['payload']) and baseline and payload.get('base') != baseline.get('base'):
                    baseline = self.baseline(payload)
                    db.execute('UPDATE versions SET baseline=? WHERE slot=?', (self.encode(baseline), slot))
            desired = payload if payload is not None else baseline
            after = self.encode(desired)
            encoded = self.encode(self.pending(desired, baseline))
            if before != after:
                cursor = db.execute("SELECT cursor FROM state WHERE id=1").fetchone()[0]
                branched = bool(db.execute('SELECT 1 FROM history WHERE id>? LIMIT 1', (cursor,)).fetchone())
                db.execute("DELETE FROM history WHERE id>?", (cursor,))
                previous = db.execute("SELECT * FROM history WHERE id=?", (cursor,)).fetchone()
                if not branched and group and previous and previous["slot"] == slot and previous["edit_group"] == group:
                    db.execute("UPDATE history SET after=? WHERE id=?", (after, cursor))
                else:
                    cursor += 1
                    db.execute("INSERT INTO history VALUES (?,?,?,?,?)", (cursor, slot, before, after, group))
                    db.execute("UPDATE state SET cursor=? WHERE id=1", (cursor,))
                db.execute('UPDATE versions SET current=?, signature=? WHERE slot=?',
                           (after, self.signature(desired), slot))
                self.write(db, slot, encoded)
                self.prune(db)
            elif row is None:
                self.write(db, slot, encoded)
            result = self.record(db.execute("SELECT * FROM drafts WHERE slot=?", (slot,)).fetchone())
        return {"entry": result, **self.status()}

    def replay(self, *, undo: bool) -> dict:
        """Restore a working version; target writes require a later Apply."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("SELECT cursor FROM state WHERE id=1").fetchone()[0]
            action = db.execute('SELECT * FROM history WHERE id<=? ORDER BY id DESC LIMIT 1' if undo else
                'SELECT * FROM history WHERE id>? ORDER BY id LIMIT 1', (cursor,)).fetchone()
            if not action:
                raise CatalogError("No local edit to undo" if undo else "No local edit to redo")
            desired = self.decode(action["before"] if undo else action["after"])
            baseline = self.decode(db.execute('SELECT baseline FROM versions WHERE slot=?', (action['slot'],)).fetchone()[0])
            self.write(db, action["slot"], self.encode(self.pending(desired, baseline)))
            db.execute('UPDATE versions SET current=?, signature=? WHERE slot=?',
                       (self.encode(desired), self.signature(desired), action['slot']))
            db.execute("UPDATE state SET cursor=? WHERE id=1", (action['id'] - 1 if undo else action['id'],))
        return {**self.status(), 'slot': action['slot'], 'version': desired}

    def save_checkpoint(self) -> dict:
        """Archive an explicit Save and make it the trash restore point."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            saved_at = datetime.now(timezone.utc).isoformat()
            db.execute('DELETE FROM checkpoint')
            db.execute('INSERT INTO checkpoint SELECT slot, current, signature FROM versions')
            db.execute('INSERT OR REPLACE INTO checkpoint_state VALUES (1,?)',
                       (saved_at,))
            save_id = self.archive_save(db, saved_at, db.execute('SELECT slot, payload FROM checkpoint').fetchall())
        return {'save_id': save_id, **self.status()}

    @staticmethod
    def archive_save(db, saved_at, rows):
        """Share unchanged compressed row content across up to 50 snapshots."""
        save_id = db.execute('INSERT INTO saved_checkpoints(saved_at) VALUES (?)', (saved_at,)).lastrowid
        for row in rows:
            data = (row['payload'] or 'null').encode('utf-8')
            digest = hashlib.sha256(data).hexdigest()
            db.execute('INSERT OR IGNORE INTO saved_content VALUES (?,?)', (digest, zlib.compress(data)))
            db.execute('INSERT INTO saved_rows VALUES (?,?,?)', (save_id, row['slot'], digest))
        db.execute('DELETE FROM saved_checkpoints WHERE id NOT IN '
                   '(SELECT id FROM saved_checkpoints ORDER BY id DESC LIMIT ?)', (SAVE_LIMIT,))
        db.execute('DELETE FROM saved_rows WHERE save_id NOT IN (SELECT id FROM saved_checkpoints)')
        db.execute('DELETE FROM saved_content WHERE hash NOT IN (SELECT content_hash FROM saved_rows)')
        return save_id

    def saved_versions(self) -> list[dict]:
        """List only metadata; loading the dialog does not fetch private texts."""
        with self.connection() as db:
            return [dict(row) for row in db.execute('''
                SELECT s.id, s.saved_at, count(r.slot) AS row_count
                FROM saved_checkpoints s LEFT JOIN saved_rows r ON r.save_id=s.id
                GROUP BY s.id ORDER BY s.id DESC''')]

    def save_apply_issues(self, issues: list[dict]) -> None:
        """Retain the last validation report across restarts, without altering drafts."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM apply_issues')
            db.executemany('INSERT INTO apply_issues VALUES (?,?,?)',
                           [(issue['slot'], issue['revision'], self.encode(issue)) for issue in issues])

    def apply_issues(self) -> list[dict]:
        """Show current draft text and flag edits made after the last validation."""
        with self.connection() as db:
            result = []
            for row in db.execute('SELECT i.payload AS issue, i.revision AS checked_revision, '
                                  'd.payload AS draft, d.revision FROM apply_issues i JOIN drafts d USING(slot) '
                                  'WHERE d.payload IS NOT NULL ORDER BY i.slot'):
                issue = self.decode(row['issue'])
                issue['draft_text'] = self.decode(row['draft'])['edit']['text']
                issue['needs_recheck'] = row['revision'] != row['checked_revision']
                issue['current_revision'] = row['revision']
                result.append(issue)
            return result

    def load_saved_version(self, save_id: int) -> dict:
        """Load one immutable Save locally, retaining the latest explicit Save."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            save = db.execute('SELECT * FROM saved_checkpoints WHERE id=?', (save_id,)).fetchone()
            if not save:
                raise CatalogError('This Save is no longer available. Open Saved versions again.')
            saved = {row['slot']: zlib.decompress(row['payload']).decode('utf-8') for row in db.execute(
                'SELECT r.slot, c.payload FROM saved_rows r JOIN saved_content c ON c.hash=r.content_hash '
                'WHERE r.save_id=?', (save_id,))}
            self.restore_versions(db, saved)
        return {'loaded_save_id': save_id, 'loaded_saved_at': save['saved_at'], **self.status()}

    def restore_versions(self, db, saved):
        """Rebase a restored local version against today's project, atomically."""
        for version in db.execute('SELECT * FROM versions').fetchall():
            desired = self.decode(saved[version['slot']] if version['slot'] in saved else version['initial'])
            self.write(db, version['slot'], self.encode(self.pending(desired, self.decode(version['baseline']))))
            db.execute('UPDATE versions SET current=?, signature=? WHERE slot=?',
                       (self.encode(desired), self.signature(desired), version['slot']))
        db.execute('DELETE FROM history')
        db.execute('DELETE FROM apply_issues')
        db.execute('UPDATE state SET cursor=0 WHERE id=1')

    def restore_checkpoint(self) -> dict:
        """Restore Save, or the original working version if Save has never been pressed."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.restore_versions(db, {row['slot']: row['payload'] for row in db.execute('SELECT * FROM checkpoint')})
        return self.status()

    def finish(self, applied: list[dict], bases: dict[str, dict]) -> dict:
        """Advance project baselines while retaining history, Save and concurrent typing."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for entry in applied:
                version = db.execute('SELECT * FROM versions WHERE slot=?', (entry['slot'],)).fetchone()
                if not version:
                    continue
                baseline = json.loads(json.dumps(entry['payload']))
                baseline['base'] = bases[entry['slot']]
                baseline['create'] = False
                if baseline['mode'] == 'developer':
                    baseline['key'] = baseline['base'].get('key', baseline['key'])
                desired = self.decode(version['current'])
                self.write(db, entry['slot'], self.encode(self.pending(desired, baseline)))
                db.execute('UPDATE versions SET baseline=? WHERE slot=?', (self.encode(baseline), entry['slot']))
        return self.status()

    def migrate_legacy(self, entries: list[tuple[str, dict]]) -> int:
        """Import CSV edits once without resurrecting already discarded SQLite rows."""
        with self.connection() as db:
            if db.execute("SELECT 1 FROM migrations WHERE name='legacy-csv'").fetchone():
                return 0
        count = 0
        for slot, payload in entries:
            if self.get(slot) is None:
                self.put(slot, payload, 0)
                count += 1
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO migrations VALUES ('legacy-csv')")
        return count

    def carry_from(self, previous: "DraftStore") -> int:
        """Keep both projects and destination edits when upgrading a source release."""
        count = 0
        for entry in previous.entries():
            if self.get(entry["slot"]) is None:
                self.put(entry["slot"], entry["payload"], 0)
                count += 1
        return count
