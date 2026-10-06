"""Serve a local translation editor; only localhost can write project files."""

from __future__ import annotations

import argparse
from bisect import bisect_right
import csv
from datetime import date, datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import os
import tempfile
import textwrap
import time
import xml.etree.ElementTree as ET
import urllib.error
from urllib.parse import parse_qs, urlsplit
import webbrowser
from xml.sax.saxutils import escape
import zipfile

import build_localization_catalog  # Included explicitly in the frozen editor.
import audit_primary_localization
import build_shipped_localization
import manage
import sync_primary_english
from merge_translation_handoff import MAX_ARCHIVE, merge_handoff
from merge_localization import review_merge, english_base, candidate_sources
from lekmod_localization.drafts import DraftStore
from lekmod_localization.common import (
    CatalogError, DEFAULT_EDITOR_OUTPUT, REPO_ROOT, WORKSPACE,
    PLACEHOLDER_RE, KEY_RE, character_count, token_counts,
)
from lekmod_localization.connections import (
    APP_HOME, TEAM_SNAPSHOT_URL, apply_game, detect_game, inspect_game, game_diagnostics, release_version,
    save_settings, settings, validate_project,
    release_catalog, download_compatible_source, editor_manifest, DownloadCancelled,
)
from lekmod_localization.editor_update import (
    latest_release, stage_release, launch_update, verify_installation, editor_instance, _pid_alive,
)
from lekmod_localization.release_feed import catalog as source_catalog
from lekmod_localization.english_dates import read_dates
from lekmod_localization.shipped import read_approvals
from lekmod_localization.vanilla_snapshot import read_snapshot
from lekmod_localization.vanilla_reference import verify_snapshot_reference
from lekmod_localization.vanilla_reference import read_reference, adopt_reference_extension, value_hash
from lekmod_localization.workspace import EDITOR_FIELDNAMES, editor_source_fingerprint
from lekmod_localization.version_history import (
    change_map, synchronize as sync_history, carry_translations, read_history, between,
)
from snapshot_cloud import decrypt_snapshot, download_encrypted, encrypt_snapshot


PAGE = APP_HOME / "localization" / "editor" / "index.html"
SCRIPT = APP_HOME / "localization" / "editor" / "app.js"
FAVICON = (Path(sys._MEIPASS) / "favicon.svg" if getattr(sys, "frozen", False)
           else APP_HOME / "localization" / "editor" / "favicon.svg")
LOG = logging.getLogger("lekmod.editor")
TRANSLATIONS = REPO_ROOT / "localization" / "translations"
APPROVAL_FIELDS = ("key", "source_fingerprint", "text", "gender", "plurality", "translator_note", "updated_at")
OPERATIONS = re.compile(
    r"(?ms)^(?P<indent>[ \t]+)<(?P<kind>Row|Replace)\b(?P<attrs>[^>]*)>"
    r"(?P<body>.*?)</(?P=kind)>"
)
TAG = re.compile(r'\bTag="(TXT_KEY_[A-Za-z0-9_]+)"')
TEXT = re.compile(r"<Text>(.*?)</Text>", re.DOTALL)


def safe_ui_event_detail(detail: str) -> str:
    """Keep useful warning context while excluding links, paths and secrets."""
    if not isinstance(detail, str) or len(detail) > 500:
        raise CatalogError("invalid interface event detail")
    detail = re.sub(r"[\r\n\t]+", " ", detail)
    detail = re.sub(r"(?i)\b(?:https?://|file://)\S+", "[link]", detail)
    detail = re.sub(r"(?i)\b[A-Z]:[\\/]\S+|(?<!\w)/(?:[^\s/]+/)+[^\s]*", "[path]", detail)
    detail = re.sub(r"(?i)\b(password|secret|token)\s*[:=]\s*\S+", r"\1=[redacted]", detail)
    return detail[:240]


def csv_rows(path: Path) -> list[dict[str, str]]:
    """Load one generated editor CSV without accepting changed columns."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(EDITOR_FIELDNAMES):
            raise CatalogError(f"outdated editor CSV: {path}; rebuild the workspace")
        rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise CatalogError(f"invalid CSV record: {path}")
    return rows


def atomic_bytes(path: Path, data: bytes) -> None:
    """Write a file in its own directory, then replace it atomically."""
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".editor.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def require_fresh_translation_files(data: dict, source: Path, approvals: Path) -> None:
    """Stop an editor save when an IDE or another editor changed its inputs."""
    for name, path in (("english_source_sha256", source),
                       ("approved_sha256", approvals)):
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        if not isinstance(data.get(name), str) or data[name] != actual:
            raise CatalogError("project files changed since this row was loaded; "
                               "copy your draft and reload the table before saving")


def encoded_csv(rows: list[dict[str, str]], fields: tuple[str, ...] | list[str]) -> bytes:
    """Keep a stable UTF-8 CSV format for both browser and Git."""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def primary_operations(document: str) -> list[dict]:
    """Index editable Text elements without reformatting the large XML file."""
    result = []
    for match in OPERATIONS.finditer(document):
        tag = TAG.search(match["attrs"])
        value = TEXT.search(match["body"])
        if tag is None or value is None:
            continue
        result.append({
            "index": len(result), "key": tag.group(1), "kind": match["kind"],
            "text": value.group(1), "start": match.start(),
            "text_start": match.start("body") + value.start(1),
            "text_end": match.start("body") + value.end(1),
        })
    return result


def primary_creation_info(document: str) -> dict[str, str | int]:
    """Describe the exact insertion point for a new English Row."""
    closing = "\t</Language_en_US>"
    if document.count(closing) != 1:
        raise CatalogError("cannot locate the end of the primary English table")
    return {"source_file": "localization/en_US/primary.xml",
            "line": document.count("\n", 0, document.index(closing)) + 1,
            "operation": "Row"}


def primary_text(value: str) -> str:
    """Hide XML indentation while retaining intentional text line breaks."""
    value = ET.fromstring("<Text>" + value + "</Text>").text or ""
    if "\n" not in value:
        return value
    lines = value.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return textwrap.dedent("\n".join(lines))


def formatted_primary_text(raw: str, text: str) -> str:
    """Keep an existing Text element's multiline XML layout when editing it."""
    if "\n" not in raw:
        return escape(text)
    first = re.search(r"\n([ \t]*)\S", raw)
    last = re.search(r"\n([ \t]*)$", raw)
    if first is None or last is None:
        return escape(text)
    indent, closing = first.group(1), last.group(1)
    return "\n" + indent + ("\n" + indent).join(
        escape(line) for line in text.split("\n")) + "\n" + closing


PAGE_SIZES = {25, 50, 100, 250, 500, 1000}


def page_slice(items: list[dict], offset: int, limit: str) -> list[dict]:
    """Bound normal pages; allow an explicit complete view for a category."""
    if limit == "all":
        return items
    count = int(limit)
    if count not in PAGE_SIZES or offset < 0:
        raise CatalogError("invalid page size or offset")
    return items[offset:offset + count]


def matches_filters(row: dict, filters: dict, *, primary: bool) -> bool:
    """Use the viewer's local day boundaries; old clients retain UTC day filters."""
    field = "kind" if primary else "classification"
    if filters.get("kind") and row.get(field) != filters["kind"]:
        return False
    if not primary and filters.get("status") and row.get("translation_status") != filters["status"]:
        return False
    column = filters.get("date_field") or "english_edited_at"
    if column not in ({"english_edited_at"} if primary else
                      {"english_edited_at", "translation_updated_at"}):
        raise CatalogError("invalid date field")
    if filters.get('date_start_utc') or filters.get('date_end_utc'):
        boundaries = {}
        for name in ('date_start_utc', 'date_end_utc'):
            if filters.get(name):
                try:
                    boundary = datetime.fromisoformat(filters[name].replace('Z', '+00:00'))
                    if boundary.tzinfo is None: raise ValueError('missing time zone')
                    boundaries[name] = boundary
                except (ValueError, TypeError) as error:
                    raise CatalogError('invalid local date boundary') from error
        try:
            current = datetime.fromisoformat(row.get(column, '').replace('Z', '+00:00'))
        except ValueError:
            return False
        if current.tzinfo is not None:
            return not ((boundaries.get('date_start_utc') and current < boundaries['date_start_utc']) or
                        (boundaries.get('date_end_utc') and current >= boundaries['date_end_utc']))
        # Legacy calendar-only records have no instant to convert; retain their date.
    for param in ("date_from", "date_to"):
        boundary = filters.get(param, "")
        if boundary:
            try:
                date.fromisoformat(boundary)
            except ValueError as error:
                raise CatalogError("invalid date filter") from error
            current = row.get(column, "")[:10]
            if not current or (param == "date_from" and current < boundary) or (
                param == "date_to" and current > boundary
            ):
                return False
    return True


class Editor:
    """Validate and persist browser edits before touching the shipped XML."""

    def __init__(self, snapshot: Path = manage.SNAPSHOT):
        # Initialize diagnostics and recovery controls before costly project work.
        self.instance_id = getattr(self, 'instance_id', secrets.token_hex(16))
        self.actions, self.events = [], []
        self.cursor = 0
        self.log_lock = threading.Lock()
        self.log_path = APP_HOME / "localization/workspace/editor-actions.jsonl"
        self.download_state = {"state": "idle"}
        self.update_state = {"state": "idle"}
        self.integrity_state = {"state": "idle"}
        self.save_state = {"state": "idle"}
        self.apply_state = {"state": "idle"}
        self.project_lock = threading.Lock()
        self.row_cache = {}
        self.download_cancel = threading.Event()
        self.handoff_data = self.handoff_preview = None
        self.handoff_id = ""
        self.handoff_packages = []
        self.handoff_choices = {}
        self.last_encrypted_archive = None
        self.ready = False
        self.connection_error = ""
        self.project_info = None
        self.snapshot = None
        self.snapshot_error = ""
        self.vanilla_counts = {}
        self.english_dates = self.local_dates = {}
        self.version_changes = {}
        self.version_info = {'available': [], 'synced': [], 'upgrade_versions': []}
        manage.migrate_workspace()
        try:
            self.project_info = validate_project(REPO_ROOT, full=True)
            self.ready = True
        except ValueError as error:
            self.project_info = None
            self.connection_error = str(error)
        if self.ready:
            bundled_reference = (Path(sys._MEIPASS) / "vanilla-fingerprints.json.gz"
                if getattr(sys, "frozen", False) else
                APP_HOME / "localization/reference/vanilla-fingerprints.json.gz")
            if adopt_reference_extension(REPO_ROOT, bundled_reference):
                self.record_event("vanilla-reference-migration", "success")
        home_snapshot = APP_HOME / "localization/workspace/vanilla-snapshot.json.gz"
        self.snapshot = next((path for path in (snapshot, home_snapshot) if path.is_file()), None)
        self.snapshot_error = ""
        if self.snapshot is not None:
            try:
                verify_snapshot_reference(self.snapshot)
            except CatalogError:
                # A newly reviewed team reference may supersede the private old
                # file. Keep it for recovery while allowing the editor to open.
                self.snapshot = None
                self.snapshot_error = "The saved vanilla snapshot belongs to an older team baseline. Download the current encrypted reference in Settings."
        self.vanilla_counts = (
            {locale: len(rows) for locale, rows in read_snapshot(self.snapshot)[0].items()}
            if self.snapshot is not None else {}
        )
        if self.ready:
            try:
                manage.prepare(manage.read_config(), self.snapshot)
                transfer = REPO_ROOT / 'localization/workspace/pending-transfer.json'
                if transfer.is_file():
                    request = json.loads(transfer.read_text(encoding='utf-8'))
                    paths = [build_shipped_localization.DEFAULT_SOURCE, *TRANSLATIONS.glob('*.csv')]
                    originals = {path: path.read_bytes() for path in paths}
                    try:
                        previous = Path(request['from'])
                        if request.get('carry_translations', True):
                            result = carry_translations(previous, REPO_ROOT)
                            old_drafts = previous / 'localization/workspace/editor-drafts.sqlite3'
                            if old_drafts.is_file():
                                result['drafts_carried'] = DraftStore(WORKSPACE / 'editor-drafts.sqlite3').carry_from(DraftStore(old_drafts))
                        else:
                            old_version, current_version = release_version(previous), release_version(REPO_ROOT)
                            sync_history(REPO_ROOT, between(read_history(REPO_ROOT), old_version, current_version),
                                         since=old_version)
                            result = {'from': old_version, 'to': current_version,
                                      'copied': 0, 'conflicts': 0, 'archived': 0}
                        manage.prepare(manage.read_config(), self.snapshot)
                        atomic_bytes(transfer.with_name('last-transfer.json'), json.dumps(result).encode())
                        transfer.unlink()
                    except Exception:
                        for path, data in originals.items(): atomic_bytes(path, data)
                        raise
            except Exception as error:
                self.ready = False
                self.connection_error = 'Project preparation failed: ' + str(error)
                self.record_event('project-prepare', 'error:' + type(error).__name__ + ': ' +
                                  safe_ui_event_detail(str(error)[:500]))
                LOG.exception('Project preparation failed; Settings remain available')
        if self.ready:
            source = sync_primary_english.DEFAULT_ENGLISH
            self.english_dates = read_dates(source, primary_operations(
                source.read_text(encoding="utf-8")), REPO_ROOT)
        else:
            self.english_dates = {}
        self.local_date_path = REPO_ROOT / "localization/workspace/english-edit-dates.json"
        self.local_dates = {}
        if self.ready and self.local_date_path.is_file():
            try:
                local = json.loads(self.local_date_path.read_text(encoding="utf-8"))
                if local.get("source_sha256") == hashlib.sha256(
                    sync_primary_english.DEFAULT_ENGLISH.read_bytes()).hexdigest():
                    self.local_dates = local.get("dates", {})
                    self.english_dates.update(self.local_dates)
            except (OSError, ValueError):
                pass
        if self.ready:
            english_base(REPO_ROOT)
            self.drafts = DraftStore(WORKSPACE / 'editor-drafts.sqlite3')
            self.vanilla_index = read_reference()['english']
        self.version_changes, self.version_info = change_map(REPO_ROOT, release_version(REPO_ROOT)) if self.ready else ({}, {'available': [], 'synced': [], 'upgrade_versions': []})
        self.read_events()
        if self.ready:
            path = WORKSPACE / 'pending-handoff/manifest.json'
            if path.is_file():
                try:
                    saved = json.loads(path.read_text(encoding='utf-8'))
                    names = saved.get('files', [])
                    if not isinstance(names, list) or not 1 <= len(names) <= 20 or any(
                        not isinstance(name, str) or not re.fullmatch(r'[0-9a-f]{64}\.zip', name) for name in names):
                        raise CatalogError('Invalid pending merge package list')
                    packages = [(path.parent / name).read_bytes() for name in names]
                    if sum(len(data) for data in packages) > MAX_ARCHIVE:
                        raise CatalogError('Pending merge review is too large')
                    self.handoff_packages = packages
                    self.handoff_choices = saved.get('choices', {})
                    if not isinstance(self.handoff_choices, dict) or any(
                        not isinstance(key, str) or value not in ('incoming', 'keep', 'review')
                        for key, value in self.handoff_choices.items()):
                        raise CatalogError('Invalid pending merge choices')
                    self.handoff_preview = review_merge(packages, REPO_ROOT,
                        choices={key: value for key, value in self.handoff_choices.items() if key.startswith('en_US:')})
                    self.handoff_data = packages[-1]
                    self.handoff_id = secrets.token_urlsafe(16)
                    self.handoff_choices = {item['id']: self.handoff_choices.get(item['id'],
                                            'keep' if item['status'] in ('stale', 'blocked') else 'incoming')
                                            for item in self.handoff_preview['items']}
                    for item in self.handoff_preview['items']:
                        if item['status'] in ('stale', 'blocked'): self.handoff_choices[item['id']] = 'keep'
                except (OSError, ValueError) as error:
                    self.handoff_packages = []
                    self.handoff_preview = self.handoff_data = None
                    self.record_event('handoff-restore', 'error:' + safe_ui_event_detail(str(error)[:500]))

    def record_event(self, name: str, result: str) -> None:
        """Keep a bounded, text-free local action journal for troubleshooting."""
        if not re.fullmatch(r"[a-z0-9/_-]{1,60}", name):
            return
        event = {"at": datetime.now(timezone.utc).isoformat(), "action": name, "result": result}
        with self.log_lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(event) + '\n')
            self.events = [*self.events[-499:], event]

    def read_events(self) -> list[dict]:
        """Include diagnostics appended by the independent update helper."""
        self.events = []
        if self.log_path.is_file():
            with self.log_path.open('rb') as handle:
                size = self.log_path.stat().st_size
                if size > 512 * 1024:
                    handle.seek(size - 512 * 1024)
                    handle.readline()  # Skip a partial first record in the bounded tail.
                lines = handle.read().decode('utf-8', errors='replace').splitlines()[-500:]
            for line in lines:
                try: self.events.append(json.loads(line))
                except ValueError: continue
        return self.events

    def start_download(self, version: str) -> dict:
        """Fetch a reviewed source release in the background without replacing files."""
        if self.download_state["state"] in ("running", "canceling"):
            raise CatalogError("a source download is already running")
        if version not in {row["version"] for row in release_catalog() if row["supported"]}:
            raise CatalogError("this release needs a reviewed localization migration")
        destination = APP_HOME / "localization/workspace/projects" / version
        self.download_cancel = threading.Event()
        self.download_state = {"state": "running", "version": version, "phase": "connecting",
                               "bytes": 0, "total": None, "destination": str(destination)}

        def progress(phase: str, done: int, total: int | None) -> None:
            self.download_state = {**self.download_state, "phase": phase,
                                   "bytes": done, "total": total}

        def run() -> None:
            try:
                existed = destination.exists()
                path = download_compatible_source(version, progress=progress,
                                                  cancelled=self.download_cancel.is_set)
                if self.download_cancel.is_set() and not existed:
                    raise DownloadCancelled("download canceled; temporary files removed")
                self.download_state = {**self.download_state, "state": "complete",
                                       "phase": "ready", "path": str(path),
                                       "reused": existed}
            except DownloadCancelled as error:
                self.download_state = {**self.download_state, "state": "canceled",
                                       "error": str(error)}
            except Exception as error:
                self.download_state = {**self.download_state, "state": "error",
                                       "error": str(error)}

        threading.Thread(target=run, daemon=True).start()
        return self.download_state

    def cancel_download(self) -> dict:
        """Stop a current transfer; its temporary directory is then removed."""
        if self.download_state["state"] in ("running", "canceling"):
            self.download_cancel.set()
            self.download_state = {**self.download_state, "state": "canceling"}
        return self.download_state

    def start_translation_save(self, data: dict) -> dict:
        """Run the costly XML rebuild off the HTTP loop, one save at a time."""
        if self.save_state.get("state") == "running":
            raise CatalogError("wait for the previous row to finish saving")
        job_id = secrets.token_urlsafe(12)
        self.save_state = {"state": "running", "id": job_id,
                           "locale": str(data.get("locale", "")),
                           "key": str(data.get("key", ""))}

        def work() -> None:
            try:
                result = self.save_translation(data)
                self.save_state = {"state": "complete", "id": job_id, **result}
                self.record_event("translate-background", "success")
            except Exception as error:
                self.save_state = {"state": "error", "id": job_id, "error": str(error)}
                self.record_event("translate-background",
                                  "error:" + type(error).__name__ + ": " +
                                  safe_ui_event_detail(str(error)))
                LOG.exception("Background translation save failed")

        threading.Thread(target=work, daemon=True).start()
        return self.save_state.copy()

    def history_state(self) -> dict[str, bool]:
        """Report whether saved edits can be undone or redone in this session."""
        return {"undo_available": self.cursor > 0,
                "redo_available": self.cursor < len(self.actions)}

    def remember(self, action: dict) -> None:
        """Keep a short session journal and discard redo after a new save."""
        del self.actions[self.cursor:]
        self.actions.append(action)
        if len(self.actions) > 20:
            del self.actions[0]
        self.cursor = len(self.actions)

    def mark_english_edit(self, key: str) -> None:
        """Retain portable-source edit dates across editor restarts."""
        self.mark_english_edits({key: datetime.now(timezone.utc).isoformat()})

    def mark_english_edits(self, dates: dict[str, str]) -> None:
        """Persist a batch's actual local edit dates with one source hash and disk write."""
        self.english_dates.update(dates)
        self.local_dates.update(dates)
        payload = {"source_sha256": hashlib.sha256(
            sync_primary_english.DEFAULT_ENGLISH.read_bytes()).hexdigest(),
                   "dates": self.local_dates}
        atomic_bytes(self.local_date_path, json.dumps(payload).encode("utf-8"))

    def manifest(self) -> dict:
        """Expose only known language and category filenames."""
        return json.loads((DEFAULT_EDITOR_OUTPUT / "manifest.json").read_text(encoding="utf-8"))

    def path(self, locale: str, category: str) -> Path:
        """Resolve a CSV through the manifest, rejecting path traversal."""
        files = self.manifest().get("locales", {}).get(locale, {}).get("files", {})
        name = category + ".csv"
        if name not in files or not re.fullmatch(r"[A-Za-z0-9_-]+", category):
            raise CatalogError("unknown language or category")
        return DEFAULT_EDITOR_OUTPUT / locale / name

    def metadata(self) -> dict:
        """Return selector choices and current feature switches."""
        self.read_events()
        manifest = self.manifest() if self.ready else {"locales": {}}
        prefs = settings()
        game_path = prefs["game_path"] or detect_game()
        game = (inspect_game(Path(game_path), REPO_ROOT if self.ready else None)
                if game_path else {"path": "", "mods": [], "state": "missing_game", "error": ""})
        game["selected_mod"] = (game["mods"][0]["name"]
                                if game["state"] == "installed" else "")
        return {
            "locales": {locale: [name.removesuffix(".csv") for name in details["files"]]
                        for locale, details in manifest["locales"].items()},
            "config": manage.read_config() if self.ready else {},
            "vanilla_counts": self.vanilla_counts,
            "snapshot_error": self.snapshot_error,
            "team_snapshot_url": TEAM_SNAPSHOT_URL,
            "ready": self.ready,
            "version_history": self.version_info,
            "handoff": ({'handoff_id': self.handoff_id, **self.handoff_preview,
                         'choices': self.handoff_choices} if self.handoff_preview else None),
            "transfer": (json.loads((WORKSPACE / 'last-transfer.json').read_text())
                         if (WORKSPACE / 'last-transfer.json').is_file() else None),
            "connection_error": self.connection_error,
            "project": self.project_info,
            "editor_version": editor_manifest()["version"],
            "server_instance": self.instance_id,
            "update_notice": next((item for item in reversed(self.events)
                                   if item.get("action") == "editor-update-install" and
                                   item.get("result") in ("success", "failure")), None),
            "included_source": REPO_ROOT == APP_HOME,
            "release": release_version(REPO_ROOT) if self.ready else "",
            "game": game,
            "preferences": prefs,
            **(self.drafts.status() if hasattr(self, 'drafts') else {"draft_count": 0}),
            "apply_state": getattr(self, 'apply_state', {"state": "idle"}),
            **self.history_state(),
        }

    def connect(self, data: dict) -> dict:
        """Validate selected folders before persisting; a project switch restarts."""
        project = str(data.get("project_path", "")).strip()
        game = str(data.get("game_path", "")).strip()
        source_root = Path(project) if project else APP_HOME
        try:
            validate_project(source_root, full=True)
        except ValueError as error:
            raise CatalogError("Source: " + str(error)) from error
        mod = ""
        if game:
            inspected = inspect_game(Path(game), source_root)
            if inspected["state"] not in ("installed", "vanilla", "mismatch"):
                raise CatalogError("Game: " + inspected["error"])
            if inspected["state"] == "installed":
                mod = inspected["mods"][0]["name"]
        changed = (Path(project).resolve() if project else APP_HOME) != REPO_ROOT
        if changed and self.ready:
            target = source_root / 'localization/workspace/pending-transfer.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and json.loads(target.read_text(encoding='utf-8')).get('from') != str(REPO_ROOT):
                raise CatalogError('This destination already has a pending translation transfer; finish it first')
            atomic_bytes(target, json.dumps({'from': str(REPO_ROOT),
                          'carry_translations': data.get('carry_translations', True) is True}).encode())
        elif not changed and not self.ready:
            # Retry a project whose files have been repaired through an IDE.
            changed = True
        saved = save_settings({"project_path": project, "game_path": game,
                               "game_mod": mod, "onboarded": True})
        return {"restart": changed, "preferences": saved}

    def import_snapshot(self, data: bytes) -> dict:
        """Accept a verified local upload; no vanilla text leaves this computer."""
        if not self.ready:
            raise CatalogError("connect a complete compatible project before importing a snapshot")
        destination = APP_HOME / "localization/workspace/vanilla-snapshot.json.gz"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".snapshot.",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        try:
            verify_snapshot_reference(temporary)
            if destination.is_file() and destination.read_bytes() != data:
                backup = destination.parent / ("vanilla-snapshot-previous-" +
                    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json.gz")
                shutil.copy2(destination, backup)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        self.snapshot = destination
        self.snapshot_error = ""
        self.vanilla_counts = {locale: len(rows) for locale, rows in
                               read_snapshot(destination)[0].items()}
        if self.ready:
            manage.prepare(manage.read_config(), self.snapshot)
        return {"vanilla_counts": self.vanilla_counts}

    def import_cloud_snapshot(self, url: str, password: str) -> dict:
        """Download encrypted text; keep only its verified local copy and URL."""
        if not self.ready or not isinstance(url, str) or not isinstance(password, str):
            raise CatalogError("connect a complete project and enter a snapshot link and password")
        if len(password) < 16:
            raise CatalogError("Enter the snapshot encryption password (at least 16 characters), not your Dropbox account password.")
        reference = REPO_ROOT / "localization/reference/vanilla-fingerprints.json.gz"
        data = decrypt_snapshot(download_encrypted(url), password, reference)
        result = self.import_snapshot(data)
        save_settings({"snapshot_url": url})
        return result

    def encrypt_local_snapshot(self, password: str) -> dict:
        """Create a verified encrypted copy in the private editor workspace."""
        if not self.ready or self.snapshot is None or not self.snapshot.is_file():
            raise CatalogError("connect a project and import a matching vanilla snapshot first")
        if not isinstance(password, str):
            raise CatalogError("enter a snapshot password")
        destination = APP_HOME / "localization/workspace" / (
            "vanilla-snapshot-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + "-" + secrets.token_hex(4) + ".enc"
        )
        reference = REPO_ROOT / "localization/reference/vanilla-fingerprints.json.gz"
        encrypt_snapshot(self.snapshot, password, reference, destination)
        self.last_encrypted_archive = destination
        return {"path": str(destination), "filename": destination.name}

    def apply_to_game(self) -> dict:
        """Copy the generated XML only after checking the connected DLC copy."""
        if not self.ready:
            raise CatalogError("connect a compatible Lekmod project first")
        prefs = settings()
        game = prefs["game_path"] or detect_game()
        if not game:
            raise CatalogError("connect a Civilization V installation in Settings")
        inspected = inspect_game(Path(game), REPO_ROOT)
        if inspected["state"] != "installed":
            raise CatalogError(inspected["error"] or "Install exactly one matching Lekmod DLC in this game.")
        name = inspected["mods"][0]["name"]
        candidate, _ = build_shipped_localization.build_candidate(self.snapshot)
        current = build_shipped_localization.DEFAULT_SOURCE.read_text(encoding="utf-8")
        if candidate != current:
            raise CatalogError("generated localization is out of date; save or prepare the project first")
        try:
            return apply_game(REPO_ROOT, Path(game), name)
        except (ValueError, OSError) as error:
            raise CatalogError(str(error)) from error

    def check_project(self) -> dict:
        """Run the full configured suite when available, or name skipped gates."""
        self.require_developer()
        python = sys.executable if not getattr(sys, "frozen", False) else (
            shutil.which("python") or shutil.which("py"))
        git = shutil.which("git")
        if python and git and (REPO_ROOT / ".git").exists():
            command = [python, "-B", str(REPO_ROOT / "localization/tools/manage.py"), "check"]
            result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True,
                                    text=True, timeout=180)
            if result.returncode:
                raise CatalogError((result.stderr or result.stdout)[-1500:])
            return {"summary": "All enabled project checks passed, including tests and inventory."}
        enabled = manage.read_config()["checks"]
        passed, skipped = [], []
        if enabled["primary"]:
            report = audit_primary_localization.parse_source(
                sync_primary_english.DEFAULT_ENGLISH, "en_US")
            if report["summary"]["errors"]:
                raise CatalogError("canonical English audit failed")
            passed.append("English source audit")
        if enabled["english_sync"]:
            sync_primary_english.synchronize(sync_primary_english.DEFAULT_ENGLISH,
                                            build_shipped_localization.DEFAULT_SOURCE,
                                            write=False)
            passed.append("English sync")
        if enabled["shipped"]:
            candidate, _ = build_shipped_localization.build_candidate(self.snapshot)
            if candidate != build_shipped_localization.DEFAULT_SOURCE.read_text(encoding="utf-8"):
                raise CatalogError("generated XML differs from approved translations")
            passed.append("generated XML")
        if enabled["art"]:
            for path in (REPO_ROOT / "LEKMOD/Art").rglob("*.xml"):
                ET.parse(path)
            passed.append("Art XML parsing")
        for name in ("inventory", "unit_tests"):
            if enabled[name]:
                skipped.append(name)
        return {"summary": "Passed: " + ", ".join(passed or ["none enabled"]) +
                (". Skipped outside a Git checkout with Python and Git: " + ", ".join(skipped) +
                 ". A developer runs the complete suite on a checkout or in CI."
                 if skipped else ".")}

    def export_locale(self, locale: str) -> bytes:
        """Keep the single-language API for older editor callers."""
        return self.export_locales([locale])

    def export_locales(self, locales: list[str]) -> bytes:
        """Export exactly the checked language CSVs, with no private vanilla text."""
        if not self.ready:
            raise CatalogError("connect a complete compatible Lekmod project first")
        available = self.manifest().get("locales", {})
        if (not isinstance(locales, list) or not 1 <= len(locales) <= len(available)
                or len(set(locales)) != len(locales)
                or any(locale not in available for locale in locales)):
            raise CatalogError("select at least one known language without duplicates")
        read_approvals(TRANSLATIONS)
        payloads = {locale: (TRANSLATIONS / f"{locale}.csv").read_bytes()
                    for locale in sorted(locales)}
        entries = ([entry for entry in self.drafts.entries()
                    if entry['payload']['mode'] == 'developer' or entry['payload']['locale'] in locales]
                   if hasattr(self, 'drafts') else [])
        plan = self.draft_plan(entries) if entries else None
        if plan:
            payloads = {locale: plan['writes'].get(TRANSLATIONS / f'{locale}.csv', content)
                        for locale, content in payloads.items()}
        english_content = (sync_primary_english.encoded_text(sync_primary_english.DEFAULT_ENGLISH, plan['document'])
                           if plan else sync_primary_english.DEFAULT_ENGLISH.read_bytes())
        reference = manage.DEFAULT_REFERENCE.read_bytes()
        revision = None
        if (REPO_ROOT / ".git").exists():
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
                capture_output=True, check=True,
            ).stdout.strip()
        metadata = {
            "schema_version": 2,
            "repository_commit": revision,
            "vanilla_reference_sha256": hashlib.sha256(reference).hexdigest(),
            "english_sha256": hashlib.sha256(english_content).hexdigest(),
            "locales": {locale: hashlib.sha256(content).hexdigest()
                        for locale, content in payloads.items()},
        }
        includes_english = any(entry['payload']['mode'] == 'developer' for entry in entries)
        if includes_english:
            metadata['english_base'] = english_base(REPO_ROOT)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for locale, content in payloads.items():
                archive.writestr(f"translations/{locale}.csv", content)
            if includes_english:
                archive.writestr('localization/en_US/primary.xml', english_content)
            archive.writestr("manifest.json", json.dumps(metadata, indent=2) + "\n")
            archive.writestr("README.txt", (
                "Lekmod localization handoff\n\n"
                "Send this ZIP to a Lekmod developer or attach it to a review.\n"
                "Local saved drafts are included. If they depend on English edits,\n"
                "the English source is included and must be reviewed first.\n"
                "Each selected language CSV contains all saved rows, not just recent\n"
                "edits. Do not replace a developer's CSV with these files.\n"
                "From the repository root, run localization/tools/manage.py prepare,\n"
                "then localization/tools/merge_translation_handoff.py <this ZIP>\n"
                "to preview additions, stale rows and conflicts by language and key.\n"
                "Resolve conflicts with --use-incoming LOCALE:TXT_KEY or\n"
                "--keep LOCALE:TXT_KEY, then run it again with --apply.\n"
                "Or import and review this ZIP in another Localization Editor.\n"
                "Then run localization/tools/manage.py prepare and check, review\n"
                "the generated game XML, and commit the reviewed files.\n"
                "This package contains no private vanilla snapshot.\n"
            ))
        return stream.getvalue()

    def preview_handoff(self, data: bytes) -> dict:
        """Hold an imported ZIP only in memory until this editor session ends."""
        if not self.ready or not isinstance(data, bytes) or not 0 < len(data) <= MAX_ARCHIVE:
            raise CatalogError("connect a project and choose a valid handoff ZIP")
        packages = [*self.handoff_packages, data]
        if len(packages) > 20 or sum(map(len, packages)) > MAX_ARCHIVE:
            raise CatalogError('Finish or clear this review before importing more ZIPs')
        report = review_merge(packages, REPO_ROOT)
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            contains_english = 'localization/en_US/primary.xml' in package.namelist()
        if contains_english and settings()['mode'] != 'developer':
            raise CatalogError('Import English source in Developer mode first')
        self.handoff_packages = packages
        self.handoff_data = data
        self.handoff_id = secrets.token_urlsafe(16)
        self.handoff_preview = report
        self.handoff_choices = {key: value for key, value in getattr(self, 'handoff_choices', {}).items()
                                if key in {item['id'] for item in report['items']}}
        for item in report['items']:
            if item['status'] in ('stale', 'blocked'): self.handoff_choices[item['id']] = 'keep'
        self.persist_handoff()
        return {"handoff_id": self.handoff_id, **report}

    def persist_handoff(self) -> None:
        """Preserve the project-bound pending review through editor upgrades."""
        folder = WORKSPACE / 'pending-handoff'
        names = []
        for data in self.handoff_packages:
            name = hashlib.sha256(data).hexdigest() + '.zip'
            if not (folder / name).is_file(): atomic_bytes(folder / name, data)
            names.append(name)
        atomic_bytes(folder / 'manifest.json', json.dumps(
            {'files': names, 'choices': self.handoff_choices}).encode())

    def clear_handoff(self) -> None:
        """Remove only our private pending review, leaving saved source untouched."""
        folder = WORKSPACE / 'pending-handoff'
        (folder / 'manifest.json').unlink(missing_ok=True)
        if folder.is_dir():
            for path in folder.glob('*.zip'):
                if re.fullmatch(r'[0-9a-f]{64}\.zip', path.name): path.unlink()
        self.handoff_packages, self.handoff_choices = [], {}
        self.handoff_preview = self.handoff_data = None
        self.handoff_id = ''

    def save_handoff_choices(self, choices: dict) -> dict:
        """Persist checkbox choices without rebuilding the source catalog."""
        if not self.handoff_preview or not isinstance(choices, dict):
            raise CatalogError('Open a pending review before choosing rows')
        items = {item['id']: item for item in self.handoff_preview['items']}
        for key, value in choices.items():
            if key not in items or value not in ('incoming', 'keep', 'review') or (
                value == 'incoming' and items[key]['status'] in ('stale', 'blocked')):
                raise CatalogError('Invalid pending merge choice')
        self.handoff_choices = choices
        self.persist_handoff()
        return {'saved': True}

    def review_handoff(self, choices: dict) -> dict:
        """Recompute dependent translations when an English checkbox changes."""
        if not self.handoff_preview or not isinstance(choices, dict):
            raise CatalogError('Import a ZIP before reviewing its choices')
        report = review_merge(self.handoff_packages, REPO_ROOT, choices=choices,
                              expected=self.handoff_preview['target_sha256'])
        self.handoff_preview = report
        self.handoff_choices.update(choices)
        valid = {item['id'] for item in report['items']}
        self.handoff_choices = {key: value for key, value in self.handoff_choices.items() if key in valid}
        for item in report['items']:
            if item['status'] in ('stale', 'blocked'): self.handoff_choices[item['id']] = 'keep'
        self.persist_handoff()
        return {'handoff_id': self.handoff_id, **report}

    def apply_handoff(self, data: dict) -> dict:
        """Recheck the preview, merge selected rows, and rebuild the project XML."""
        if hasattr(self, 'drafts') and self.drafts.status()['draft_count']:
            raise CatalogError('Apply your local drafts to the project before applying this merge. The imported review and local drafts are both retained')
        if (self.handoff_data is None or self.handoff_preview is None or
                data.get("handoff_id") != self.handoff_id):
            raise CatalogError("import a handoff ZIP before applying it")
        choices = data.get("choices")
        if not isinstance(choices, dict) or len(choices) > 100000:
            raise CatalogError("invalid handoff decisions")
        destination = data.get('destination', 'project')
        if destination not in ('project', 'project_game'):
            raise CatalogError('invalid merge destination')
        if destination == 'project_game':
            prefs = settings()
            game_info = inspect_game(Path(prefs['game_path']), REPO_ROOT)
            if game_info['state'] != 'installed':
                raise CatalogError('Game is not connected to one matching Lekmod installation')
        report = review_merge(self.handoff_packages, REPO_ROOT, apply=True, choices=choices,
                               expected=self.handoff_preview["target_sha256"])
        if report["applied"]:
            try:
                manage.prepare(manage.read_config(), self.snapshot)
            except Exception:
                for name, path in report["backups"].items():
                    atomic_bytes(REPO_ROOT / name, Path(path).read_bytes())
                manage.prepare(manage.read_config(), self.snapshot)
                raise
            self.actions = []
            self.cursor = 0
        if destination == 'project_game':
            try:
                prefs = settings()
                report['game_result'] = apply_game(REPO_ROOT, Path(prefs['game_path']),
                                                   game_info['mods'][0]['name'], APP_HOME)
            except (ValueError, OSError) as error:
                # The reviewed source stays saved even if the external game copy is locked.
                report['game_error'] = str(error)
        self.clear_handoff()
        return {**report, **self.history_state()}

    def export_english(self) -> bytes:
        """Hand off a changed canonical English file without private vanilla text."""
        self.require_developer()
        source = sync_primary_english.DEFAULT_ENGLISH
        entries = ([entry for entry in self.drafts.entries() if entry['payload']['mode'] == 'developer']
                   if hasattr(self, 'drafts') else [])
        content = (sync_primary_english.encoded_text(source, self.draft_plan(entries)['document'])
                   if entries else source.read_bytes())
        reference = manage.DEFAULT_REFERENCE.read_bytes()
        revision = None
        if (REPO_ROOT / ".git").exists():
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                                      text=True, capture_output=True, check=True).stdout.strip()
        manifest = {
            "repository_commit": revision,
            "vanilla_reference_sha256": hashlib.sha256(reference).hexdigest(),
            "english_sha256": hashlib.sha256(content).hexdigest(),
            "english_base": english_base(REPO_ROOT),
        }
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("localization/en_US/primary.xml", content)
            archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
            archive.writestr("README.txt", (
                "Lekmod English source handoff\n\n"
                "Compare the manifest and review the diff against your checkout.\n"
                "Merge the intended primary.xml rows; do not replace newer team edits.\n"
                "Run localization/tools/manage.py prepare and check, review the generated\n"
                "XML and translation fallbacks, then commit and push the reviewed files.\n"
                "New TXT_KEY names also need a reference in gameplay XML, SQL, or Lua.\n"
                "No private vanilla snapshot is included.\n"
            ))
        return stream.getvalue()

    def rows(self, locale: str, category: str, query: str, offset: int,
             limit: str = "100", filters: dict | None = None) -> dict:
        """Search a category and return one page with approval state."""
        categories = ([name.removesuffix('.csv') for name in self.manifest().get('locales', {}).get(locale, {}).get('files', {})]
                      if category == 'all' else [category])
        if not categories:
            raise CatalogError('unknown language or category')
        cache = getattr(self, 'row_cache', {})
        rows = []
        for name in categories:
            path = self.path(locale, name)
            stamp = (path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None
            cached = cache.get(path)
            if cached is None or (cached[0] != stamp and getattr(self, 'save_state', {}).get('state') != 'running'):
                cached = cache[path] = (stamp, csv_rows(path))
            rows.extend({**row, 'category': name} for row in cached[1])
        self.row_cache = cache
        approval_path = TRANSLATIONS / f"{locale}.csv"
        approval_bytes = self.view_bytes(approval_path)
        approved_sha = hashlib.sha256(approval_bytes).hexdigest() if approval_bytes is not None else ''
        english_sha = hashlib.sha256(self.view_bytes(sync_primary_english.DEFAULT_ENGLISH)).hexdigest()
        approved_details = {}
        if approval_bytes is not None:
            approved_details = {record['key']: record for record in csv.DictReader(
                io.StringIO(approval_bytes.decode('utf-8-sig')))}
        approved = approved_details
        draft_records = self.drafts.records() if hasattr(self, 'drafts') else {}
        drafts = [record for record in draft_records.values() if record['payload']]
        translations = {entry['payload']['key']: entry for entry in drafts
                        if entry['payload']['mode'] == 'translator' and entry['payload']['locale'] == locale}
        english_drafts = {entry['payload']['key']: entry for entry in drafts
                          if entry['payload']['mode'] == 'developer' and not entry['payload']['create']}
        query = query.casefold()
        filters = filters or {}
        selected = []
        shipped = manage.read_config()["build"]["shipped"]
        for row in rows:
            item = dict(row)
            item['changed_in'] = getattr(self, 'version_changes', {}).get(row['key'], [])
            selected_version = (filters or {}).get('version', '')
            if selected_version and not (set(item['changed_in']) & set(self.version_info['upgrade_versions']) if selected_version == 'upgrade' else selected_version in item['changed_in']):
                continue
            item["approved_sha256"] = approved_sha
            item["english_source_sha256"] = english_sha
            item["english_edited_at"] = self.english_dates.get(row["key"], "")
            item["translation_updated_at"] = approved_details.get(row["key"], {}).get("updated_at", "")
            saved = approved.get(row["key"])
            if saved:
                item["translation_status"] = (
                    "needs_source_review" if row.get('classification') == 'source_conflict' else
                    ("applied" if shipped else "saved")
                    if saved["source_fingerprint"] == row["source_fingerprint"]
                    else "stale"
                )
                item["translation"] = saved["text"]
                item["translation_characters"] = str(character_count(saved["text"]))
                item["translation_source_fingerprint"] = saved["source_fingerprint"]
                item["translation_gender"] = saved.get("gender", "")
                item["translation_plurality"] = saved.get("plurality", "")
                item["translator_note"] = approved_details[row["key"]]["translator_note"]
            if row.get('classification') == 'source_conflict':
                item['translation_status'] = 'needs_source_review'
            english_draft = english_drafts.get(row['key'])
            if english_draft and item['classification'] != 'source_conflict':
                edit = english_draft['payload']['edit']
                if edit['identifier'] == row['key']:
                    item['lekmod_en_US'] = edit['text']
                    item['lekmod_en_US_characters'] = str(character_count(edit['text']))
                    tokens = token_counts(edit['text'])
                    reference = getattr(self, 'vanilla_index', {}).get(row['key'])
                    if reference:
                        item['classification'] = ('vanilla_unchanged' if value_hash(edit['text']) == reference['Text']
                            else 'vanilla_modified')
                    item['source_fingerprint'] = editor_source_fingerprint(row['key'], {
                        'classification': item['classification'], 'lekmod_en_US': {
                            'text': edit['text'], 'gender': row.get('lekmod_en_US_gender') or None,
                            'plurality': row.get('lekmod_en_US_plurality') or None, 'format_tokens': tokens}})
                    item['required_format_tokens'] = json.dumps(tokens)
                    if saved and saved['source_fingerprint'] != item['source_fingerprint']:
                        item['translation_status'] = 'stale'
            slot = f'T:{locale}:{row["key"]}'
            item['draft_slot'] = slot
            record = draft_records.get(slot)
            item['draft_revision'] = record['revision'] if record else 0
            item['draft_base'] = {'approval': approved_details.get(row['key']), 'source_fingerprint': item['source_fingerprint']}
            item['applied_edit'] = {'text': item.get('translation') or item.get('lekmod_target') or '',
                'gender': item.get('translation_gender', ''), 'plurality': item.get('translation_plurality', ''),
                'note': item.get('translator_note', ''), 'identifier': ''}
            draft = translations.get(row['key'])
            if draft:
                edit = draft['payload']['edit']
                reviewed = draft['payload'].get('reviewed_source')
                if reviewed and reviewed['primary_sha256'] == english_sha and not english_draft:
                    item.update(lekmod_en_US=reviewed['text'], source_fingerprint=reviewed['source_fingerprint'],
                                required_format_tokens=reviewed['tokens'],
                                lekmod_en_US_characters=str(character_count(reviewed['text'])))
                item.update(translation=edit['text'], translation_gender=edit['gender'],
                    translation_plurality=edit['plurality'], translator_note=edit['note'],
                    translation_characters=str(character_count(edit['text'])), translation_status='draft',
                    translation_updated_at=draft['updated_at'], draft_base=draft['payload']['base'], has_local_draft=True)
            if (filters or {}).get('needs_translation') == 'true' and item.get('translation_status') not in ('missing', 'stale'):
                continue
            if (not query or any(query in item[field].casefold()
                                 for field in ("key", "lekmod_en_US", "vanilla_en_US",
                                               "vanilla_target", "translation"))) and matches_filters(
                                                   item, filters, primary=False):
                selected.append(item)
        return {"total": len(selected), "rows": page_slice(selected, offset, limit)}

    def primary(self, query: str, offset: int, limit: str = "100",
                filters: dict | None = None) -> dict:
        """Search the canonical English XML as a separate editor category."""
        document = self.view_bytes(sync_primary_english.DEFAULT_ENGLISH).decode('utf-8-sig')
        query = query.casefold()
        starts = [0] + [match.end() for match in re.finditer("\n", document)]
        selected = []
        draft_records = self.drafts.records() if hasattr(self, 'drafts') else {}
        drafts = [record for record in draft_records.values() if record['payload']]
        english_drafts = [entry for entry in drafts if entry['payload']['mode'] == 'developer']
        draft_map = {(entry['payload']['key'], entry['payload']['index']): entry for entry in english_drafts
                     if not entry['payload']['create']}
        for row in primary_operations(document):
            value = primary_text(row["text"])
            item = {"index": row["index"], "key": row["key"], "kind": row["kind"],
                    "text": value, "characters": character_count(value),
                    "source_file": "localization/en_US/primary.xml",
                    "source_line": bisect_right(starts, row["start"]),
                    "english_edited_at": self.english_dates.get(row["key"], "")}
            item['changed_in'] = getattr(self, 'version_changes', {}).get(row['key'], [])
            item['required_format_tokens'] = token_counts(value)
            entry = draft_map.get((row['key'], row['index']))
            if entry is None:
                matches = [draft for draft in english_drafts if draft['payload']['key'] == row['key'] and not draft['payload']['create']]
                if len(matches) == 1: entry = matches[0]
            slot = entry['slot'] if entry else f'P:{row["index"]}:{row["key"]}'
            record = draft_records.get(slot)
            item.update(draft_slot=slot, draft_revision=record['revision'] if record else 0,
                draft_base={'key': row['key'], 'kind': row['kind'], 'text': value},
                applied_edit={'text': value, 'gender': '', 'plurality': '', 'note': '', 'identifier': row['key']})
            if entry:
                item.update(text=entry['payload']['edit']['text'], key=entry['payload']['edit']['identifier'],
                    draft_key=entry['payload']['key'], draft_base=entry['payload']['base'], has_local_draft=True,
                    english_edited_at=entry['updated_at'])
                item['characters'] = character_count(item['text'])
                item['required_format_tokens'] = token_counts(item['text'])
            if query and query not in item['key'].casefold() and query not in item['text'].casefold():
                continue
            version = (filters or {}).get('version', '')
            if version and not (set(item['changed_in']) & set(self.version_info['upgrade_versions']) if version == 'upgrade' else version in item['changed_in']):
                continue
            if matches_filters(item, filters or {}, primary=True):
                selected.append(item)
        for entry in english_drafts:
            draft = entry['payload']
            if not draft['create']: continue
            edit = draft['edit']
            item = {'index': -1, 'key': edit['identifier'], 'kind': 'Row', 'text': edit['text'],
                'characters': character_count(edit['text']), 'source_file': 'localization/en_US/primary.xml',
                'source_line': 'Appended on Apply', 'english_edited_at': entry['updated_at'], 'changed_in': [],
                'required_format_tokens': token_counts(edit['text']), 'draft_slot': entry['slot'],
                'draft_revision': entry['revision'], 'draft_key': draft['key'], 'draft_base': draft['base'],
                'has_local_draft': True, 'draft_create': True,
                'applied_edit': {'text': '', 'gender': '', 'plurality': '', 'note': '', 'identifier': draft['key']}}
            if (not query or query in item['key'].casefold() or query in item['text'].casefold()) and matches_filters(item, filters or {}, primary=True):
                selected.append(item)
        return {"total": len(selected), "rows": page_slice(selected, offset, limit)}

    def history(self, index: int | None = None, key: str | None = None) -> dict:
        """Show the last committed edit date for a selected English row."""
        source = sync_primary_english.DEFAULT_ENGLISH
        document = source.read_text(encoding="utf-8")
        rows = primary_operations(document)
        if key is not None:
            matches = [row for row in rows if row["key"] == key]
            if not matches:
                raise CatalogError("unknown English key")
            selected = matches[-1]
        elif index is not None and 0 <= index < len(rows):
            selected = rows[index]
        else:
            raise CatalogError("unknown primary row")
        line = document.count("\n", 0, selected["text_start"]) + 1
        if not (REPO_ROOT / ".git").exists():
            return {"committed_at": None, "commit": None,
                    "history_available": False}
        result = subprocess.run(
            ["git", "blame", "--line-porcelain", "-L", f"{line},{line}",
             "--", str(source.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT, text=True, capture_output=True, check=True,
        )
        timestamp = re.search(r"^author-time (\d+)$", result.stdout, re.MULTILINE)
        commit = result.stdout.split(" ", 1)[0]
        return {
            "history_available": True,
            "committed_at": (
                datetime.fromtimestamp(int(timestamp.group(1)), timezone.utc).isoformat()
                if timestamp and commit.strip("0") else None
            ),
            "commit": commit if commit.strip("0") else None,
        }

    def view_bytes(self, path: Path) -> bytes | None:
        """Keep source baselines stable while a batch rebuild replaces canonical files."""
        originals = getattr(self, 'apply_originals', {})
        if getattr(self, 'save_state', {}).get('state') == 'running' and path in originals:
            return originals[path]
        return path.read_bytes() if path.is_file() else None

    def save_draft(self, data: dict) -> dict:
        """Persist one form in milliseconds; incomplete translations remain editable."""
        if not self.ready or not hasattr(self, 'drafts'):
            raise CatalogError('Connect a compatible project before saving')
        mode, key, locale = data.get('mode'), data.get('key'), data.get('locale', '')
        if mode not in ('translator', 'developer'):
            raise CatalogError('Choose Translator or Developer mode for this draft')
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise CatalogError('Choose a valid TXT_KEY row')
        if mode == 'translator' and locale not in self.manifest()['locales']:
            raise CatalogError('Unknown translation language')
        edit, base = data.get('edit'), data.get('base')
        if (not isinstance(edit, dict) or set(edit) != {'text', 'gender', 'plurality', 'note', 'identifier'}
                or any(not isinstance(v, str) or len(v) > 200000 for v in edit.values())
                or not isinstance(base, dict) or type(data.get('revision')) is not int):
            raise CatalogError('Invalid local draft')
        index = data.get('index', -1)
        if type(index) is not int or index < -1:
            raise CatalogError('Invalid English row')
        slot = data.get('slot') or (f'T:{locale}:{key}' if mode == 'translator' else f'P:{index}:{key}')
        if not isinstance(slot, str) or len(slot) > 300 or not re.fullmatch(r'[A-Za-z0-9_:-]+', slot):
            raise CatalogError('Invalid draft identifier')
        previous = self.drafts.get(slot)
        if data['revision'] != (previous['revision'] if previous else 0):
            applied = getattr(self, 'last_applied_drafts', {}).get(slot)
            return {'conflict': True, 'entry': previous, 'applied': applied}
        if previous and previous['payload']:
            base = previous['payload']['base']
        payload = {'mode': mode, 'locale': locale, 'key': key, 'index': index,
                   'create': data.get('create') is True, 'base': base, 'edit': edit}
        if mode == 'translator':
            fingerprint = data.get('source_fingerprint')
            if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
                raise CatalogError('Missing English source fingerprint')
            payload['source_fingerprint'] = fingerprint
            if previous and previous['payload'] and previous['payload'].get('reviewed_source'):
                payload['reviewed_source'] = previous['payload']['reviewed_source']
        result = self.drafts.put(slot, None if data.get('reverted') and not payload['create'] and
                                self.save_state.get('state') != 'running' else payload,
                                data['revision'], str(data.get('group', ''))[:100])
        return {**result, 'saved_locally': True}

    def discard_draft(self, data: dict) -> dict:
        """Discard only the selected local draft, leaving canonical mod files untouched."""
        if self.save_state.get('state') == 'running':
            raise CatalogError('Wait for Apply to finish before discarding its local drafts')
        slot, revision = data.get('slot'), data.get('revision')
        if not isinstance(slot, str) or type(revision) is not int:
            raise CatalogError('Select a local draft to discard')
        return self.drafts.put(slot, None, revision)

    def draft_plan(self, entries: list[dict]) -> dict:
        """Validate a batch against current files; English always precedes translations."""
        source = sync_primary_english.DEFAULT_ENGLISH
        originals = {source: source.read_bytes()}
        document = originals[source].decode('utf-8-sig')
        operations = primary_operations(document)
        replacements, additions, new_keys, bases = [], [], set(), {}
        english = [entry for entry in entries if entry['payload']['mode'] == 'developer']
        translations = [entry for entry in entries if entry['payload']['mode'] == 'translator']
        approvals = {}

        def locale_records(locale):
            if locale not in approvals:
                path = TRANSLATIONS / (locale + '.csv')
                originals[path] = path.read_bytes() if path.is_file() else None
                raw = (originals[path] or encoded_csv([], APPROVAL_FIELDS)).decode('utf-8-sig')
                approvals[locale] = {row['key']: row for row in csv.DictReader(io.StringIO(raw))}
            return approvals[locale]

        for entry in english:
            draft = entry['payload']
            key, edit, base = draft['key'], draft['edit'], draft['base']
            identifier = edit['identifier']
            if not KEY_RE.fullmatch(identifier) or (draft['create'] and not edit['text'].strip()):
                raise CatalogError(f'{key}: enter a valid TXT_KEY identifier and non-empty English text before Apply')
            if identifier in new_keys:
                raise CatalogError(f'{identifier}: two local English drafts use the same identifier')
            new_keys.add(identifier)
            target = [row for row in operations if row['key'] == identifier]
            kind = 'Row' if draft['create'] else base.get('kind')
            already_applied = (len(target) == 1 and target[0]['kind'] == kind and
                primary_text(target[0]['text']) == edit['text'] and
                (identifier == key or not any(row['key'] == key for row in operations) or draft['create']))
            if already_applied:
                bases[entry['slot']] = {'key': identifier, 'kind': kind, 'text': edit['text']}
                continue  # Recover an interrupted Apply without treating our own write as an IDE conflict.
            if draft['create'] or identifier != key:
                if identifier in read_reference()['english'] or any(row['key'] == identifier for row in operations):
                    raise CatalogError(f'{identifier}: this key already exists')
            if draft['create']:
                for path in (REPO_ROOT / 'LEKMOD/Art').rglob('*'):
                    if path.is_file() and path.suffix.lower() in ('.xml', '.sql') and identifier in path.read_text(encoding='utf-8', errors='ignore'):
                        raise CatalogError(f'{identifier}: key exists in {path.relative_to(REPO_ROOT)}')
                additions.append(f'\t\t<Row Tag="{identifier}">\n\t\t\t<Text>{escape(edit["text"])}</Text>\n\t\t</Row>\n')
            else:
                matches = [row for row in operations if row['key'] == base.get('key') and row['kind'] == base.get('kind')]
                row = next((row for row in matches if row['index'] == draft['index']), None)
                if row is None and len(matches) == 1:
                    row = matches[0]
                if row is None or primary_text(row['text']) != base.get('text'):
                    raise CatalogError(f'{key}: English was changed in the project. Your local draft is retained; review the current source before Apply')
                if identifier != key:
                    if len(matches) != 1 or any(key in locale_records(locale) for locale in self.manifest()['locales']):
                        raise CatalogError(f'{key}: a referenced or translated identifier needs an IDE migration')
                    for path in (REPO_ROOT / 'LEKMOD').rglob('*'):
                        if (path.is_file() and path != build_shipped_localization.DEFAULT_SOURCE and
                                path.suffix.lower() in ('.xml', '.sql', '.lua', '.modinfo') and
                                path.stat().st_size < 8 * 1024 * 1024 and key in path.read_text(encoding='utf-8', errors='ignore')):
                            raise CatalogError(f'{key}: update its gameplay reference in {path.relative_to(REPO_ROOT)} through an IDE migration')
                body = document[row['start']:row['text_start']] + formatted_primary_text(row['text'], edit['text'])
                body = body.replace(f'Tag="{key}"', f'Tag="{identifier}"', 1)
                replacements.append((row['start'], row['text_end'], body))
            bases[entry['slot']] = {'key': identifier, 'kind': 'Row' if draft['create'] else base['kind'], 'text': edit['text']}
        for start, end, replacement in sorted(replacements, reverse=True):
            document = document[:start] + replacement + document[end:]
        if additions:
            primary_creation_info(document)
            document = document.replace('\t</Language_en_US>', ''.join(additions) + '\t</Language_en_US>', 1)
        if english:
            sync_primary_english.validate_source(document)
        sources = candidate_sources(REPO_ROOT, document) if translations else {}
        for entry in translations:
            draft = entry['payload']
            key, locale, edit = draft['key'], draft['locale'], draft['edit']
            rows = locale_records(locale)
            current = rows.get(key)
            source_row = sources.get(key)
            if (not source_row or source_row.get('classification') == 'source_conflict' or
                    source_row['source_fingerprint'] != draft['source_fingerprint']):
                raise CatalogError(f'{locale}:{key}: English changed since this translation was written. Review the current English and save the draft again before Apply')
            if edit['text'] and (PLACEHOLDER_RE.search(edit['text']) or
                    token_counts(edit['text']) != json.loads(source_row['required_format_tokens'])):
                raise CatalogError(f'{locale}:{key}: the draft is saved, but Apply requires every formatting token to match English')
            expected = ({'key': key, 'source_fingerprint': draft['source_fingerprint'],
                             'text': edit['text'], 'gender': edit['gender'], 'plurality': edit['plurality'],
                             'translator_note': edit['note'], 'updated_at': entry['updated_at']}
                        if edit['text'] else None)
            if current != draft['base'].get('approval') and current != expected:
                raise CatalogError(f'{locale}:{key}: the project translation changed in an IDE or merge. Your local draft is retained; review it before Apply')
            if expected:
                rows[key] = expected
            else:
                rows.pop(key, None)
            bases[entry['slot']] = {'approval': rows.get(key), 'source_fingerprint': draft['source_fingerprint']}
        writes = {path: encoded_csv([rows[key] for key in sorted(rows)], APPROVAL_FIELDS)
                  for locale, rows in approvals.items() if (path := TRANSLATIONS / (locale + '.csv')) in originals}
        if english:
            writes[source] = sync_primary_english.encoded_text(source, document)
        writes = {path: value for path, value in writes.items() if value != originals[path]}
        return {'writes': writes, 'originals': originals, 'document': document, 'bases': bases}

    def review_draft(self, slot: str) -> dict:
        """Show the current source before an explicit rebase of a conflicting local edit."""
        entry = self.drafts.get(slot)
        if not entry or not entry['payload']:
            raise CatalogError('This draft is no longer pending')
        draft = entry['payload']
        document = sync_primary_english.DEFAULT_ENGLISH.read_text(encoding='utf-8-sig')
        if draft['mode'] == 'developer':
            if draft['create']:
                raise CatalogError('A new key has no existing source to rebase; choose another identifier if it conflicts')
            matches = [row for row in primary_operations(document) if row['key'] == draft['base'].get('key')]
            if len(matches) != 1:
                raise CatalogError('This key was removed or has multiple operations. Keep a draft backup and review the source in an IDE')
            row = matches[0]
            text = primary_text(row['text'])
            return {'entry': entry, 'project_text': text, 'english_text': text,
                    'base': {'key': row['key'], 'kind': row['kind'], 'text': text}, 'index': row['index']}
        locale, key = draft['locale'], draft['key']
        path = TRANSLATIONS / (locale + '.csv')
        with path.open(encoding='utf-8-sig', newline='') as handle:
            approval = next((row for row in csv.DictReader(handle) if row['key'] == key), None)
        english_entries = [entry for entry in self.drafts.entries() if entry['payload']['mode'] == 'developer']
        if english_entries:
            document = self.draft_plan(english_entries)['document']
        source = candidate_sources(REPO_ROOT, document).get(key)
        if not source:
            raise CatalogError('The current English source is missing or conflicted. Keep this draft until a developer resolves it')
        return {'entry': entry, 'project_text': approval['text'] if approval else '',
                'english_text': source['lekmod_en_US'], 'source_fingerprint': source['source_fingerprint'],
                'reviewed_source': {'text': source['lekmod_en_US'], 'tokens': source['required_format_tokens'],
                    'source_fingerprint': source['source_fingerprint'], 'primary_sha256': hashlib.sha256(
                        sync_primary_english.DEFAULT_ENGLISH.read_bytes()).hexdigest()},
                'base': {'approval': approval, 'source_fingerprint': source['source_fingerprint']}}

    def rebase_draft(self, data: dict) -> dict:
        """Adopt a reviewed baseline without writing the project or changing draft text."""
        if self.save_state.get('state') == 'running':
            raise CatalogError('Wait until Apply finishes before reviewing a conflict')
        review = self.review_draft(data.get('slot', ''))
        if review['entry']['revision'] != data.get('revision') or review['base'] != data.get('base'):
            raise CatalogError('The draft or project changed since review. Review it again')
        payload = review['entry']['payload']
        payload['base'] = review['base']
        if payload['mode'] == 'translator':
            payload['source_fingerprint'] = review['source_fingerprint']
            payload['reviewed_source'] = review['reviewed_source']
        else: payload['index'] = review['index']
        return self.drafts.put(review['entry']['slot'], payload, review['entry']['revision'])

    def start_apply(self, *, game: bool = False) -> dict:
        """Build one immutable draft batch in the background while editing stays available."""
        if not self.ready or self.save_state.get('state') == 'running':
            raise CatalogError('Wait for the current Apply to finish')
        if game:
            prefs = settings()
            inspected = inspect_game(Path(prefs['game_path']), REPO_ROOT)
            if inspected['state'] != 'installed':
                raise CatalogError(inspected.get('error') or 'Connect one matching Lekmod game installation first')
        entries = self.drafts.entries()
        if not entries and not game:
            return {'state': 'complete', 'applied_count': 0, **self.drafts.status()}
        self.apply_state = {'state': 'running', 'id': secrets.token_hex(12), 'count': len(entries), 'phase': 'Checking saved drafts'}
        self.save_state = {'state': 'running', 'kind': 'apply'}

        def work():
            try:
                if entries:
                    plan = self.draft_plan(entries)
                    originals, writes = plan['originals'], plan['writes']
                    self.apply_originals = originals
                    for path, original in originals.items():
                        if (path.read_bytes() if path.is_file() else None) != original:
                            raise CatalogError('Project files changed during Apply. Local drafts are retained; retry after reviewing the IDE changes')
                    game_path = build_shipped_localization.DEFAULT_SOURCE
                    old_game = game_path.read_bytes()
                    backup = WORKSPACE / 'draft-apply-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                    for path in writes:
                        if originals[path] is not None:
                            atomic_bytes(backup / path.relative_to(REPO_ROOT), originals[path])
                    atomic_bytes(backup / game_path.relative_to(REPO_ROOT), old_game)
                    written = []
                    try:
                        for path, value in writes.items():
                            atomic_bytes(path, value); written.append(path)
                        self.apply_state = {**self.apply_state, 'phase': 'Rebuilding project XML once'}
                        manage.prepare(manage.read_config(), self.snapshot)
                        for path, original in originals.items():
                            expected = writes.get(path, original)
                            if (path.read_bytes() if path.is_file() else None) != expected:
                                raise CatalogError('Project files changed during the rebuild. Your local drafts and external IDE edits are retained; review them before retrying Apply')
                    except Exception:
                        for path in written:
                            if path.read_bytes() == writes[path]:
                                if originals[path] is None: path.unlink()
                                else: atomic_bytes(path, originals[path])
                        atomic_bytes(game_path, old_game)
                        self.row_cache = {}
                        raise
                    self.drafts.finish(entries, plan['bases'])
                    self.last_applied_drafts = {entry['slot']: {'base': plan['bases'][entry['slot']],
                        'edit': entry['payload']['edit']} for entry in entries}
                    self.row_cache = {}
                    dates = {entry['payload']['edit']['identifier']: entry['updated_at'] for entry in entries
                             if entry['payload']['mode'] == 'developer'}
                    if dates: self.mark_english_edits(dates)
                result = {'applied_count': len(entries), **self.drafts.status()}
                if game:
                    self.apply_state = {**self.apply_state, 'phase': 'Copying to the verified game installation'}
                    result['game_result'] = self.apply_to_game()
                self.apply_state = {**self.apply_state, 'state': 'complete', **result}
                self.record_event('draft-apply', 'success')
            except Exception as error:
                self.apply_state = {**self.apply_state, 'state': 'error', 'error': str(error), **self.drafts.status()}
                self.record_event('draft-apply', 'error:' + type(error).__name__ + ': ' + safe_ui_event_detail(str(error)[:500]))
                LOG.exception('Draft Apply failed; local work retained')
            finally:
                self.save_state = {'state': 'idle'}
                self.apply_originals = {}
        threading.Thread(target=work, daemon=True).start()
        return self.apply_state.copy()

    def save_translation(self, data: dict) -> dict:
        """Approve one CSV row, update the tracked language CSV and game XML."""
        if not self.ready:
            raise CatalogError("connect a complete compatible Lekmod project first")
        if settings()["mode"] != "translator":
            raise CatalogError("switch to Translator mode to edit a translation")
        locale = str(data.get("locale", ""))
        category = str(data.get("category", ""))
        path = self.path(locale, category)
        rows = csv_rows(path)
        key = data.get("key")
        row = next((candidate for candidate in rows if candidate["key"] == key), None)
        if row is None or row["source_fingerprint"] != data.get("source_fingerprint"):
            raise CatalogError("the English source changed; reload this row")
        if row.get('classification') == 'source_conflict':
            raise CatalogError('Conflicting English sources need a developer review before this key can be translated')
        for name in ("translation", "translation_gender", "translation_plurality", "translator_note"):
            if not isinstance(data.get(name), str) or len(data[name]) > 200000:
                raise CatalogError(f"invalid {name}")
        translation = data["translation"]
        if translation and (
            PLACEHOLDER_RE.search(translation)
            or token_counts(translation) != json.loads(row["required_format_tokens"])
        ):
            raise CatalogError("translation must preserve all formatting tokens")
        approved_path = TRANSLATIONS / f"{locale}.csv"
        require_fresh_translation_files(data, sync_primary_english.DEFAULT_ENGLISH,
                                        approved_path)
        game = build_shipped_localization.DEFAULT_SOURCE
        old_approved = approved_path.read_bytes() if approved_path.exists() else None
        old_editor = path.read_bytes()
        old_game_hash = hashlib.sha256(game.read_bytes()).hexdigest()
        existing = read_approvals(TRANSLATIONS).get(locale, {})
        notes, timestamps = {}, {}
        if approved_path.is_file():
            with approved_path.open(encoding="utf-8-sig", newline="") as handle:
                for entry in csv.DictReader(handle):
                    notes[entry["key"]] = entry["translator_note"]
                    timestamps[entry["key"]] = entry.get("updated_at", "")
        if translation:
            existing[key] = {
                "source_fingerprint": row["source_fingerprint"], "text": translation,
                **{field: data["translation_" + field] for field in ("gender", "plurality")
                   if data["translation_" + field]},
            }
            notes[key] = data["translator_note"]
            timestamps[key] = datetime.now(timezone.utc).isoformat()
        else:
            existing.pop(key, None)
            notes.pop(key, None)
            timestamps.pop(key, None)
        approved_rows = [
            {"key": selected, "source_fingerprint": value["source_fingerprint"],
             "text": value["text"], "gender": value.get("gender", ""),
             "plurality": value.get("plurality", ""),
             "translator_note": notes.get(selected, ""),
             "updated_at": timestamps.get(selected, "")}
            for selected, value in sorted(existing.items())
        ]
        atomic_bytes(approved_path, encoded_csv(approved_rows, APPROVAL_FIELDS))
        try:
            apply_to_game = manage.read_config()["build"]["shipped"]
            if apply_to_game:
                candidate, summary = build_shipped_localization.build_candidate(
                    self.snapshot, approvals=TRANSLATIONS,
                )
            row.update({
                "translation": translation,
                "translation_gender": data["translation_gender"],
                "translation_plurality": data["translation_plurality"],
                "translator_note": data["translator_note"],
                "translation_source_fingerprint": row["source_fingerprint"],
                "translation_characters": str(character_count(translation)) if translation else "",
                "translation_status": ("applied" if apply_to_game else "saved")
                if translation else "missing",
            })
            atomic_bytes(path, encoded_csv(rows, list(EDITOR_FIELDNAMES)))
            if apply_to_game:
                sync_primary_english.atomic_text(game, candidate)
        except Exception:
            if old_approved is None:
                approved_path.unlink(missing_ok=True)
            else:
                atomic_bytes(approved_path, old_approved)
            atomic_bytes(path, old_editor)
            raise
        new_approved = approved_path.read_bytes()
        new_editor = path.read_bytes()
        new_game_hash = hashlib.sha256(game.read_bytes()).hexdigest()
        if (old_approved, old_editor, old_game_hash) != (
            new_approved, new_editor, new_game_hash
        ):
            self.remember({
                "kind": "translation", "approved_path": approved_path, "editor_path": path,
                "before_approved": old_approved, "after_approved": new_approved,
                "before_editor": old_editor, "after_editor": new_editor,
                "before_game_hash": old_game_hash, "after_game_hash": new_game_hash,
                "build": manage.read_config()["build"],
            })
        return {"applied_to_game": apply_to_game,
                "approved_sha256": hashlib.sha256(new_approved).hexdigest(),
                "translation_updated_at": timestamps.get(key, ""),
                **self.history_state()}

    def save_primary(self, data: dict, *, record: bool = True) -> dict:
        """Change one English text, then refresh generated XML and draft statuses."""
        self.require_developer()
        source = sync_primary_english.DEFAULT_ENGLISH
        game = build_shipped_localization.DEFAULT_SOURCE
        before = source.read_text(encoding="utf-8")
        old_source = source.read_bytes()
        index = data.get("index")
        if not isinstance(index, int):
            raise CatalogError("invalid primary row")
        rows = primary_operations(before)
        if index < 0 or index >= len(rows):
            raise CatalogError("unknown primary row")
        row = rows[index]
        new_text = data.get("text")
        if not isinstance(new_text, str) or len(new_text) > 200000 or (
            row["key"] != data.get("key")
            or primary_text(row["text"]) != data.get("old_text")
        ):
            raise CatalogError("primary row changed; reload before saving")
        replacement = (before[:row["text_start"]] +
                       formatted_primary_text(row["text"], new_text) +
                       before[row["text_end"]:])
        sync_primary_english.validate_source(replacement)
        old_game = game.read_bytes()
        old_game_hash = hashlib.sha256(old_game).hexdigest()
        sync_primary_english.atomic_text(source, replacement)
        try:
            manage.prepare(manage.read_config(), self.snapshot)
        except Exception:
            atomic_bytes(source, old_source)
            atomic_bytes(game, old_game)
            raise
        config = manage.read_config()["build"]
        new_game_hash = hashlib.sha256(game.read_bytes()).hexdigest()
        if before != replacement or old_game_hash != new_game_hash:
            if record:
                self.remember({
                "kind": "primary", "index": index, "key": row["key"],
                "before_text": primary_text(row["text"]), "after_text": new_text,
                "before_game_hash": old_game_hash, "after_game_hash": new_game_hash,
                "build": config,
                })
            self.mark_english_edit(row["key"])
        return {"key": row["key"], "updated": True,
                "applied_to_game": config["english"] and config["shipped"],
                **self.history_state()}

    def require_developer(self) -> None:
        """Keep canonical English changes in a full checkout for review."""
        if settings()["mode"] != "developer":
            raise CatalogError("switch to Developer mode to edit English source")
        try:
            validate_project(REPO_ROOT, full=True)
        except ValueError as error:
            raise CatalogError(str(error)) from error

    def create_primary(self, data: dict) -> dict:
        """Add a text key; gameplay entity references remain a developer task."""
        self.require_developer()
        key, value = data.get("key"), data.get("text")
        if data.get("operation", "Row") != "Row":
            raise CatalogError("new English keys use Row; Replace is for an existing game key")
        if (not isinstance(key, str) or not KEY_RE.fullmatch(key) or
            not isinstance(value, str) or not value.strip()):
            raise CatalogError("enter a TXT_KEY_* identifier and English text")
        if len(value) > 200000:
            raise CatalogError("English text is too large for this editor")
        source = sync_primary_english.DEFAULT_ENGLISH
        before = source.read_text(encoding="utf-8")
        if key in read_reference()["english"] or any(row["key"] == key for row in primary_operations(before)):
            raise CatalogError("key already exists in the English or vanilla source")
        for path in (REPO_ROOT / "LEKMOD/Art").rglob("*"):
            if path.is_file() and path.suffix.lower() in (".xml", ".sql") and (
                key in path.read_text(encoding="utf-8", errors="ignore")
            ):
                raise CatalogError(f"key already exists in {path.relative_to(REPO_ROOT)}")
        primary_creation_info(before)
        closing = "\t</Language_en_US>"
        operation = (f'\t\t<Row Tag="{key}">\n\t\t\t<Text>{escape(value)}</Text>\n'
                     "\t\t</Row>\n")
        return self._save_structure(before, before.replace(closing, operation + closing, 1), key)

    def creation_info(self) -> dict[str, str | int]:
        """Show the destination computed from the current source, not stale UI rows."""
        self.require_developer()
        source = sync_primary_english.DEFAULT_ENGLISH
        return primary_creation_info(source.read_text(encoding="utf-8"))

    def rename_primary(self, data: dict) -> dict:
        """Rename only an unreferenced key; changing a live ID needs a migration."""
        self.require_developer()
        index, old, new = data.get("index"), data.get("key"), data.get("new_key")
        if not isinstance(index, int) or not isinstance(new, str) or not KEY_RE.fullmatch(new):
            raise CatalogError("enter a valid replacement TXT_KEY_* identifier")
        source = sync_primary_english.DEFAULT_ENGLISH
        before = source.read_text(encoding="utf-8")
        rows = primary_operations(before)
        if index < 0 or index >= len(rows) or rows[index]["key"] != old:
            raise CatalogError("selected English row changed; reload")
        if any(row["key"] == new for row in rows) or new in read_reference()["english"]:
            raise CatalogError("replacement key already exists")
        if sum(row["key"] == old for row in rows) != 1:
            raise CatalogError("key has multiple English operations; review them in the XML")
        if any(old in read_approvals(TRANSLATIONS).get(locale, {})
               for locale in self.manifest()["locales"]):
            raise CatalogError("key has approved translations; migrate them with a developer")
        for path in (REPO_ROOT / "LEKMOD").rglob("*"):
            if (path.is_file() and path != build_shipped_localization.DEFAULT_SOURCE and
                path.suffix.lower() in (".xml", ".sql", ".lua", ".modinfo") and
                path.stat().st_size < 8 * 1024 * 1024 and
                old in path.read_text(encoding="utf-8", errors="ignore")):
                raise CatalogError(f"key is referenced in {path.relative_to(REPO_ROOT)}; "
                                   "update gameplay references in a reviewed migration")
        row = rows[index]
        if "text" in data:
            new_text = data["text"]
            if (not isinstance(new_text, str) or len(new_text) > 200000 or
                data.get("old_text") != primary_text(row["text"])):
                raise CatalogError("primary row changed; reload before saving")
        else:
            new_text = primary_text(row["text"])
        prefix = before[:row["start"]]
        body = before[row["start"]:row["text_end"]]
        if body.count(f'Tag="{old}"') != 1:
            raise CatalogError("cannot identify a unique Tag attribute")
        start = row["text_start"] - row["start"]
        end = row["text_end"] - row["start"]
        body = body[:start] + formatted_primary_text(row["text"], new_text) + body[end:]
        after = prefix + body.replace(f'Tag="{old}"', f'Tag="{new}"', 1) + before[row["text_end"]:]
        return self._save_structure(before, after, new)

    def _save_structure(self, before: str, after: str, key: str) -> dict:
        """Rebuild after an English key edit and restore files on failure."""
        source = sync_primary_english.DEFAULT_ENGLISH
        game = build_shipped_localization.DEFAULT_SOURCE
        sync_primary_english.validate_source(after)
        old_source, old_game = source.read_bytes(), game.read_bytes()
        sync_primary_english.atomic_text(source, after)
        try:
            manage.prepare(manage.read_config(), self.snapshot)
        except Exception:
            atomic_bytes(source, old_source)
            atomic_bytes(game, old_game)
            raise
        self.remember({"kind": "structure", "before_source": old_source,
                       "after_source": source.read_bytes(),
                       "before_game_hash": hashlib.sha256(old_game).hexdigest(),
                       "after_game_hash": hashlib.sha256(game.read_bytes()).hexdigest(),
                       "build": manage.read_config()["build"]})
        self.mark_english_edit(key)
        return {"key": key, "updated": True, **self.history_state()}

    def replay(self, *, undo: bool) -> dict:
        """Reverse or reapply one saved edit, refusing unrelated file changes."""
        position = self.cursor - 1 if undo else self.cursor
        if position < 0 or position >= len(self.actions):
            raise CatalogError("no saved edit to undo" if undo else "no saved edit to redo")
        action = self.actions[position]
        if manage.read_config()["build"] != action["build"]:
            raise CatalogError("build settings changed; restart the editor before undo or redo")
        game = build_shipped_localization.DEFAULT_SOURCE
        side = "after" if undo else "before"
        target = "before" if undo else "after"
        if hashlib.sha256(game.read_bytes()).hexdigest() != action[f"{side}_game_hash"]:
            raise CatalogError("game XML changed outside this editor; saved edit was not replayed")
        if action["kind"] == "structure":
            self.require_developer()
            source = sync_primary_english.DEFAULT_ENGLISH
            if source.read_bytes() != action[f"{side}_source"]:
                raise CatalogError("English source changed outside this editor; cannot replay")
            previous = source.read_bytes()
            atomic_bytes(source, action[f"{target}_source"])
            try:
                manage.prepare(manage.read_config(), self.snapshot)
                if hashlib.sha256(game.read_bytes()).hexdigest() != action[f"{target}_game_hash"]:
                    raise CatalogError("game build changed since this key edit")
            except Exception:
                atomic_bytes(source, previous)
                manage.prepare(manage.read_config(), self.snapshot)
                raise
            result = {"applied_to_game": action["build"]["shipped"]}
        elif action["kind"] == "primary":
            result = self.save_primary({
                "index": action["index"], "key": action["key"],
                "old_text": action[f"{side}_text"], "text": action[f"{target}_text"],
            }, record=False)
        else:
            approved_path = action["approved_path"]
            editor_path = action["editor_path"]
            current_approved = approved_path.read_bytes() if approved_path.exists() else None
            current_editor = editor_path.read_bytes()
            if (current_approved, current_editor) != (
                action[f"{side}_approved"], action[f"{side}_editor"]
            ):
                raise CatalogError("translation CSV changed outside this editor; saved edit was not replayed")
            try:
                desired = action[f"{target}_approved"]
                if desired is None:
                    approved_path.unlink(missing_ok=True)
                else:
                    atomic_bytes(approved_path, desired)
                atomic_bytes(editor_path, action[f"{target}_editor"])
                if action["build"]["shipped"]:
                    candidate, _ = build_shipped_localization.build_candidate(
                        self.snapshot, approvals=TRANSLATIONS,
                    )
                    if hashlib.sha256(sync_primary_english.encoded_text(game, candidate)).hexdigest() != action[f"{target}_game_hash"]:
                        raise CatalogError("game build changed since this save; saved edit was not replayed")
                    sync_primary_english.atomic_text(game, candidate)
            except Exception:
                if current_approved is None:
                    approved_path.unlink(missing_ok=True)
                else:
                    atomic_bytes(approved_path, current_approved)
                atomic_bytes(editor_path, current_editor)
                raise
            result = {"applied_to_game": action["build"]["shipped"]}
        if hashlib.sha256(game.read_bytes()).hexdigest() != action[f"{target}_game_hash"]:
            raise CatalogError("generated game XML differs from the saved edit; restart the editor")
        self.cursor += -1 if undo else 1
        return {**result, **self.history_state()}


def make_handler(editor: Editor, token: str, port: int):
    """Bind HTTP endpoints to this one private editor instance."""
    class Handler(BaseHTTPRequestHandler):
        def download_inline_svg(self, data: bytes) -> None:
            """Serve the static favicon from the packaged editor folder."""
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def respond(self, status: int, payload: object) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except ConnectionError:
                LOG.debug("Browser disconnected after the request was handled")

        def download(self, name: str, data: bytes, content_type: str) -> None:
            """Send a named local export with no private snapshot attached."""
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            args = parse_qs(url.query)
            one = lambda key, default="": args.get(key, [default])[0]
            try:
                if getattr(editor, 'initializing', False) is True and url.path.startswith('/api/') and url.path != '/api/health':
                    if url.path == '/api/meta':
                        self.respond(200, {'initializing': True, 'editor_version': editor_manifest()['version'],
                                           'server_instance': editor.instance_id})
                    else: self.respond(503, {'error': 'Preparing the connected project; please wait'})
                    return
                if url.path in {'/api/drafts', '/api/draft-backup', '/api/apply-status'} and not editor.ready:
                    raise CatalogError('Connect a compatible project first')
                if url.path == "/":
                    html = PAGE.read_text(encoding="utf-8").replace("{{TOKEN}}", token).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
                    self.send_header("Content-Length", str(len(html)))
                    self.end_headers()
                    self.wfile.write(html)
                elif url.path == "/app.js":
                    data = SCRIPT.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/javascript; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif url.path == "/favicon.svg":
                    self.download_inline_svg(FAVICON.read_bytes())
                elif url.path == "/api/health":
                    # The updater must not wait on metadata's game XML inspection.
                    self.respond(200, {"editor_version": editor_manifest()["version"],
                                       "server_instance": editor.instance_id})
                elif url.path == "/api/meta":
                    self.respond(200, editor.metadata())
                elif url.path == "/api/logs":
                    self.respond(200, {"events": editor.read_events()})
                elif url.path == "/api/logs/download":
                    self.download("lekmod-editor-actions.jsonl",
                                  editor.log_path.read_bytes() if editor.log_path.exists() else b"",
                                  "application/x-ndjson")
                elif url.path == '/api/game-diagnostics':
                    if not editor.ready:
                        raise CatalogError('Connect a compatible Lekmod project first')
                    game = settings()['game_path'] or detect_game()
                    if not game:
                        raise CatalogError('Connect a Civilization V installation in Settings first')
                    report = game_diagnostics(REPO_ROOT, Path(game))
                    editor.record_event('game-diagnostics', 'success')
                    self.download('lekmod-game-diagnostics.json',
                                  json.dumps(report, indent=2).encode('utf-8'), 'application/json')
                elif url.path == "/api/versions":
                    self.respond(200, source_catalog(APP_HOME, refresh=args.get('refresh') == ['1']))
                elif url.path == "/api/game-status":
                    path = settings()['game_path'] or detect_game()
                    game = (inspect_game(Path(path), REPO_ROOT if editor.ready else None)
                            if path else {'path': '', 'mods': [], 'state': 'missing_game', 'error': ''})
                    game['selected_mod'] = game['mods'][0]['name'] if game['state'] == 'installed' else ''
                    self.respond(200, game)
                elif url.path == "/api/editor-latest":
                    self.respond(200, latest_release())
                elif url.path == "/api/editor-update-status":
                    self.respond(200, editor.update_state.copy())
                elif url.path == "/api/editor-verify-status":
                    self.respond(200, editor.integrity_state.copy())
                elif url.path == "/api/encrypted-snapshot":
                    archive = editor.last_encrypted_archive
                    if archive is None or not archive.is_file():
                        raise CatalogError("encrypt a verified local snapshot first")
                    self.download(archive.name, archive.read_bytes(), "application/octet-stream")
                elif url.path == "/api/download-status":
                    self.respond(200, editor.download_state)
                elif url.path == "/api/save-status":
                    self.respond(200, editor.save_state.copy())
                elif url.path == '/api/apply-status':
                    self.respond(200, {**editor.apply_state, **editor.drafts.status()})
                elif url.path == '/api/drafts':
                    self.respond(200, {'entries': editor.drafts.entries(), **editor.drafts.status()})
                elif url.path == '/api/draft-backup':
                    self.download('lekmod-local-drafts.json', json.dumps({
                        'schema_version': 1, 'project': str(REPO_ROOT), 'release': release_version(REPO_ROOT),
                        'entries': editor.drafts.entries()}, ensure_ascii=False, indent=2).encode('utf-8'),
                        'application/json')
                elif url.path == "/api/rows":
                    if not editor.ready:
                        raise CatalogError("connect a compatible Lekmod project in Settings")
                    filters = {name: one(name) for name in
                               ("kind", "status", "date_field", "date_from", "date_to", "date_start_utc", "date_end_utc", "version", "needs_translation")}
                    self.respond(200, editor.rows(one("locale"), one("category"), one("q"),
                                                  int(one("offset", "0")), one("limit", "100"), filters))
                elif url.path == "/api/primary":
                    if not editor.ready:
                        raise CatalogError("connect a compatible Lekmod project in Settings")
                    filters = {name: one(name) for name in
                               ("kind", "date_field", "date_from", "date_to", "date_start_utc", "date_end_utc", "version")}
                    self.respond(200, editor.primary(one("q"), int(one("offset", "0")),
                                                     one("limit", "100"), filters))
                elif url.path == "/api/primary-create-info":
                    self.respond(200, editor.creation_info())
                elif url.path == "/api/history":
                    self.respond(200, editor.history(
                        key=one("key") if "key" in args else None,
                        index=int(one("index")) if "index" in args else None,
                    ))
                elif url.path == "/api/export":
                    locales = one("locales").split(",") if "locales" in args else [one("locale")]
                    data = editor.export_locales(locales)
                    label = locales[0] if len(locales) == 1 else "selected"
                    self.download(f"lekmod-{label}-translations.zip", data, "application/zip")
                elif url.path == "/api/export-english":
                    self.download("lekmod-english-source.zip", editor.export_english(),
                                  "application/zip")
                else:
                    self.respond(404, {"error": "not found"})
            except (CatalogError, OSError, ValueError, urllib.error.URLError,
                    subprocess.CalledProcessError) as error:
                self.respond(400, {"error": str(error)})

        def do_POST(self) -> None:
            if self.headers.get("Origin") != f"http://127.0.0.1:{port}" or (
                self.headers.get("X-Editor-Token") != token
            ):
                self.respond(403, {"error": "editor request rejected"})
                return
            action_lock = None
            try:
                if getattr(editor, 'initializing', False) is True:
                    self.respond(503, {'error': 'Preparing the connected project; please wait'}); return
                if self.path in {'/api/apply-project', '/api/translate', '/api/translate-async',
                        '/api/primary', '/api/create-primary', '/api/rename-primary', '/api/connect',
                        '/api/snapshot', '/api/snapshot-cloud', '/api/check', '/api/apply-game',
                        '/api/undo', '/api/redo', '/api/handoff-apply', '/api/handoff-review',
                        '/api/history-sync', '/api/editor-update', '/api/stop'}:
                    action_lock = getattr(editor, 'project_lock', None)
                    if action_lock is not None and not action_lock.acquire(blocking=False):
                        action_lock = None
                        raise CatalogError('Another project operation is running. Local editing remains available')
                length = int(self.headers.get("Content-Length", "0"))
                if self.path == "/api/snapshot":
                    if editor.save_state.get("state") == "running":
                        raise CatalogError("wait until the current row finishes saving")
                    if not 0 < length <= 16 * 1024 * 1024:
                        raise CatalogError("invalid snapshot upload size")
                    self.respond(200, editor.import_snapshot(self.rfile.read(length)))
                    editor.record_event("snapshot-import", "success")
                    return
                if self.path == "/api/handoff-preview":
                    if editor.save_state.get("state") == "running":
                        raise CatalogError("wait until the current row is saved")
                    if not 0 < length <= MAX_ARCHIVE:
                        raise CatalogError("invalid handoff ZIP size")
                    self.respond(200, editor.preview_handoff(self.rfile.read(length)))
                    editor.record_event("handoff-preview", "success")
                    return
                max_body = 8 * 1024 * 1024 if self.path in (
                    '/api/handoff-apply', '/api/handoff-review', '/api/handoff-choices') else 512 * 1024
                if not 0 < length <= max_body:
                    raise CatalogError("invalid request size")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise CatalogError("invalid request")
                if (self.path.startswith('/api/draft') or self.path == '/api/apply-project') and not editor.ready:
                    raise CatalogError('Connect a compatible project first')
                if editor.save_state.get("state") == "running" and self.path in {
                    "/api/translate", "/api/translate-async", "/api/primary",
                    "/api/create-primary", "/api/rename-primary", "/api/connect",
                    "/api/snapshot", "/api/snapshot-cloud", "/api/editor-update",
                    "/api/apply-game", "/api/check", "/api/undo", "/api/redo",
                    "/api/handoff-apply", "/api/handoff-review", "/api/history-sync", "/api/stop", '/api/apply-project',
                }:
                    raise CatalogError("wait until the current row finishes saving")
                if self.path == '/api/draft':
                    result = editor.save_draft(data)
                elif self.path == '/api/draft-discard':
                    result = editor.discard_draft(data)
                elif self.path == '/api/draft-review':
                    result = editor.review_draft(data.get('slot', ''))
                elif self.path == '/api/draft-rebase':
                    result = editor.rebase_draft(data)
                elif self.path in ('/api/draft-undo', '/api/draft-redo'):
                    if editor.save_state.get('state') == 'running':
                        raise CatalogError('Wait until Apply finishes before undoing its batch')
                    result = editor.drafts.replay(undo=self.path == '/api/draft-undo')
                elif self.path == '/api/apply-project':
                    result = editor.start_apply(game=data.get('game') is True)
                elif self.path == "/api/translate-async":
                    result = editor.start_translation_save(data)
                elif self.path == "/api/handoff-apply":
                    result = editor.apply_handoff(data)
                elif self.path == '/api/handoff-review':
                    result = editor.review_handoff(data.get('choices', {}))
                elif self.path == '/api/handoff-clear':
                    editor.clear_handoff()
                    result = {'cleared': True}
                elif self.path == '/api/handoff-choices':
                    result = editor.save_handoff_choices(data.get('choices'))
                elif self.path == '/api/history-sync':
                    sync_history(REPO_ROOT, [data.get('version', '')])
                    editor.version_changes, editor.version_info = change_map(REPO_ROOT, release_version(REPO_ROOT))
                    result = {'version_history': editor.version_info}
                elif self.path == "/api/translate":
                    result = editor.save_translation(data)
                elif self.path == "/api/primary":
                    result = editor.save_primary(data)
                elif self.path == "/api/create-primary":
                    result = editor.create_primary(data)
                elif self.path == "/api/rename-primary":
                    result = editor.rename_primary(data)
                elif self.path == "/api/connect":
                    result = editor.connect(data)
                    self.respond(200, result)
                    editor.record_event("connect", "success")
                    if result["restart"]:
                        self.server.restart_requested = True
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                elif self.path == "/api/download-project":
                    result = editor.start_download(str(data.get("version", "")))
                elif self.path == "/api/cancel-download":
                    result = editor.cancel_download()
                elif self.path == "/api/snapshot-cloud":
                    result = editor.import_cloud_snapshot(data.get("url"), data.get("password"))
                elif self.path == "/api/snapshot-encrypt":
                    result = editor.encrypt_local_snapshot(data.get("password"))
                elif self.path == "/api/editor-verify":
                    if editor.integrity_state["state"] == "running":
                        raise CatalogError("an editor file check is already running")
                    if editor.update_state["state"] not in ("idle", "error"):
                        raise CatalogError("wait until the editor update finishes")
                    editor.integrity_state = {"state": "running", "bytes": 0, "total": None}
                    self.respond(202, editor.integrity_state)

                    def verify_in_background() -> None:
                        try:
                            def progress(done: int, total: int | None) -> None:
                                editor.integrity_state = {"state": "running", "bytes": done,
                                                          "total": total}

                            result = verify_installation(progress=progress)
                            editor.integrity_state = {"state": "complete", **result}
                            editor.record_event("editor-verify", "damaged" if result["damaged_files"]
                                                else "success")
                        except Exception as error:
                            editor.integrity_state = {"state": "error", "error": str(error)}
                            editor.record_event("editor-verify", "error:" + type(error).__name__ +
                                                ": " + safe_ui_event_detail(str(error)))
                            LOG.exception("Editor file check failed")

                    threading.Thread(target=verify_in_background, daemon=True).start()
                    return
                elif self.path == "/api/editor-update":
                    if getattr(editor, 'download_state', {}).get('state') in ('running', 'canceling'):
                        raise CatalogError('Finish or cancel the Lekmod source download before updating the editor')
                    if editor.update_state["state"] not in ("idle", "error"):
                        raise CatalogError("an editor update is already running")
                    if editor.integrity_state["state"] == "running":
                        raise CatalogError("wait until the editor file check finishes")
                    repair = data.get("repair") is True
                    report = editor.integrity_state
                    if repair and (report.get("state") != "complete" or
                                   report.get("current") != editor_manifest()["version"] or
                                   not report.get("damaged_files")):
                        raise CatalogError("check editor files before requesting a repair")
                    editor.update_state = {"state": "checking"}
                    self.respond(202, editor.update_state)
                    server = self.server

                    def update_in_background() -> None:
                        """Keep the localhost API responsive during a slow download."""
                        try:
                            release = latest_release()
                            if not release["available"] and not repair:
                                raise ValueError("this editor is already up to date")
                            editor.update_state = {"state": "downloading", "version": release["latest"],
                                                   "bytes": 0, "total": None}

                            def progress(done: int, total: int | None) -> None:
                                editor.update_state = {"state": "downloading", "version": release["latest"],
                                                       "bytes": done, "total": total}

                            stage = stage_release(release, progress=progress, repair=repair)
                            editor.update_state = {"state": "installing", "version": release["latest"]}
                            if repair:
                                launch_update(stage, port=server.server_port, repair=True)
                            else:
                                launch_update(stage, port=server.server_port)
                            editor.record_event("editor-update", "installer-started")
                            threading.Thread(target=server.shutdown, daemon=True).start()
                        except Exception as error:
                            editor.update_state = {"state": "error", "error": str(error)}
                            editor.record_event("editor-update", "error:" + type(error).__name__ +
                                                ": " + safe_ui_event_detail(str(error)))
                            LOG.exception("Editor update could not start")

                    threading.Thread(target=update_in_background, daemon=True).start()
                    return
                elif self.path == "/api/preferences":
                    allowed = {"mode", "prefill", "wrap", "panel_expanded", "locale", "category",
                               "visible_columns", "translator_visible_columns",
                               "developer_visible_columns", "column_widths", "onboarded", "page_size",
                               "snapshot_url", "translator_filters", "developer_filters",
                               "translator_column_order", "developer_column_order"}
                    if set(data) - allowed:
                        raise CatalogError("unknown editor preference")
                    if editor.save_state.get("state") == "running" and editor.save_state.get('kind') != 'apply' and (
                            "mode" in data or "project_path" in data):
                        raise CatalogError("wait until the current row finishes saving")
                    if data.get("mode") == "developer":
                        validate_project(REPO_ROOT, full=True)
                    result = {"preferences": save_settings(data)}
                elif self.path == "/api/event":
                    name = data.get("name")
                    if name not in {"row-selected", "mode-switch", "settings-open",
                                    "columns-changed", "page-changed", "prefill-changed",
                                    "logs-open", "logs-download", "copy", "wrap-changed",
                                    "translation-export", "english-export", "update-check",
                                    "update-verify",
                                    "filter-changed", "page-size-changed", "discard",
                                    "ui-error", "ui-warning", "project-reconnect", "key-dialog"}:
                        raise CatalogError("unknown interface action")
                    detail = safe_ui_event_detail(data.get("detail", ""))
                    editor.record_event(name, "ui" + (": " + detail if detail else ""))
                    result = {"logged": True}
                elif self.path == "/api/browse":
                    if data.get("kind") not in ("project", "game"):
                        raise CatalogError("unknown folder kind")
                    from tkinter import Tk, filedialog
                    dialog = Tk()
                    dialog.withdraw()
                    try:
                        # The request comes from a browser window, which may otherwise
                        # cover the native folder chooser on Windows.
                        dialog.attributes("-topmost", True)
                        dialog.update()
                        result = {"path": filedialog.askdirectory(
                            title="Select Lekmod project" if data["kind"] == "project"
                            else "Select Civilization V installation", parent=dialog,
                            initialdir=str(APP_HOME.parent if data["kind"] == "project"
                                           else (Path("C:/") if Path("C:/").is_dir()
                                                 else APP_HOME)))}
                    finally:
                        dialog.destroy()
                elif self.path == "/api/apply-game":
                    result = editor.apply_to_game()
                elif self.path == "/api/check":
                    result = editor.check_project()
                elif self.path == "/api/detect-game":
                    path = str(data.get("path", "")).strip() or detect_game()
                    if not path:
                        result = {"path": "", "mods": [], "state": "missing_game"}
                    else:
                        try:
                            result = inspect_game(Path(path), REPO_ROOT if editor.ready else None)
                        except (OSError, ValueError) as error:
                            result = {"path": path, "mods": [], "state": "missing_game",
                                      "error": str(error)}
                elif self.path == "/api/undo":
                    result = editor.replay(undo=True)
                elif self.path == "/api/redo":
                    result = editor.replay(undo=False)
                elif self.path == "/api/stop":
                    if (editor.download_state.get('state') in ('running', 'canceling') or
                            editor.update_state.get('state') not in ('idle', 'error')):
                        raise CatalogError('finish the current download/update before closing the editor')
                    self.respond(200, {"stopping": True})
                    editor.record_event("stop", "success")
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                else:
                    self.respond(404, {"error": "not found"})
                    return
                self.respond(200, result)
                if self.path == "/api/detect-game":
                    editor.record_event("detect-game", result["state"])
                elif self.path != "/api/event":
                    editor.record_event(self.path.removeprefix("/api/"), "success")
            except (CatalogError, OSError, ValueError, ET.ParseError, urllib.error.URLError,
                    subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                self.respond(400, {"error": str(error)})
                detail = (": " + safe_ui_event_detail(str(error)[:500])) if self.path in (
                    "/api/check", "/api/preferences", "/api/handoff-preview",
                    "/api/handoff-apply", "/api/handoff-review", "/api/connect", "/api/history-sync",
                    "/api/translate-async") else ""
                editor.record_event(self.path.removeprefix("/api/"),
                                    "error:" + type(error).__name__ + detail)
            finally:
                if action_lock is not None:
                    action_lock.release()

        def log_message(self, format: str, *args: object) -> None:
            """Log one local request without exposing the private token."""
            LOG.info(format, *args)

    return Handler


def main() -> int:
    """Start the local-only editor and print its browser address."""
    if not LOG.handlers:
        path = APP_HOME / "localization/workspace/editor-startup.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        LOG.addHandler(RotatingFileHandler(path, maxBytes=1024 * 1024,
                                           backupCount=2, encoding="utf-8"))
        LOG.setLevel(logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0,
                        help="Local port; 0 chooses an available port")
    parser.add_argument("--no-browser", action="store_true",
                        help="Start the server without opening a browser")
    parser.add_argument("--update-ticket", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.update_ticket and not re.fullmatch(r"[0-9a-f]{32}", args.update_ticket):
        parser.error("invalid update ticket")
    if not 0 <= args.port <= 65535 or 0 < args.port < 1024:
        parser.error("choose port 0 or a port from 1024 to 65535")
    if not args.update_ticket:
        address = active_update_address()
        if address:
            if not args.no_browser:
                webbrowser.open(address)
            LOG.info('Reopened the active update at %s', address)
            return 0
    try:
        with editor_instance(APP_HOME) as owner:
            if not owner:
                session = running_editor_session()
                if session is None:
                    raise RuntimeError("Another editor is still starting. Wait a moment and retry.")
                publish_startup_marker(args.update_ticket, session)
                if not args.no_browser:
                    webbrowser.open(f"http://127.0.0.1:{session['port']}/")
                LOG.info("Reopened existing editor process %s", session['pid'])
                return 0
            restart_port = serve_editor(args)
    except (CatalogError, OSError, RuntimeError) as error:
        LOG.error("Editor could not start: %s", error)
        parser.exit(1, f"Editor failed: {error}\n")
    if restart_port is not None:
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--port", str(restart_port), "--no-browser"]
            environment = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        else:
            command = [sys.executable, "-B", str(APP_HOME / "localization/tools/editor_server.py"),
                       "--port", str(restart_port), "--no-browser"]
            environment = os.environ.copy()
            flags = 0
        subprocess.Popen(command, cwd=APP_HOME, env=environment, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    return 0


def active_update_address() -> str:
    """A double-click during installation reopens progress without starting a rival server."""
    path = APP_HOME / 'localization/workspace/editor-updates/helper-active.json'
    try:
        if path.stat().st_size > 4096:
            return ''
        session = json.loads(path.read_text(encoding='utf-8'))
        if (type(session.get('pid')) is int and type(session.get('port')) is int and
                1024 <= session['port'] <= 65535 and _pid_alive(session['pid'])):
            return f"http://127.0.0.1:{session['port']}/"
    except (OSError, ValueError, TypeError):
        pass
    return ''


def running_editor_session() -> dict | None:
    """Reuse only a live localhost server matching this installation's marker."""
    path = APP_HOME / 'localization/workspace/editor-session.json'
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if path.stat().st_size > 4096:
                return None
            session = json.loads(path.read_text(encoding='utf-8'))
            if (type(session['port']) is int and 1024 <= session['port'] <= 65535 and
                    type(session['pid']) is int and _pid_alive(session['pid'])):
                base = f"http://127.0.0.1:{session['port']}"
                with urllib.request.urlopen(base + '/api/health', timeout=.5) as response:
                    health = json.load(response)
                if health.get('server_instance') == session.get('server_instance'):
                    return session
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
            pass
        time.sleep(.1)
    return None


def publish_startup_marker(ticket: str, session: dict) -> None:
    """Let update helpers find a server started now or reused by a second launch."""
    if ticket:
        ready = APP_HOME / "localization/workspace/editor-updates" / ("ready-" + ticket + ".json")
        atomic_bytes(ready, json.dumps({'ticket': ticket, 'pid': session['pid'],
                                       'port': session['port']}).encode('utf-8'))


def serve_editor(args: argparse.Namespace) -> int | None:
    """Serve one process and release its session before a requested reconnect."""
    started_at = time.monotonic()
    LOG.info("Initializing editor process %s", os.getpid())
    try:
        editor = object.__new__(Editor)
        editor.instance_id = secrets.token_hex(16)
        editor.initializing = True
        editor.initialization_error = ''
        token = secrets.token_urlsafe(32)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(editor, token, args.port))
        # The handler compares the browser Origin with the actual assigned port.
        server.RequestHandlerClass = make_handler(editor, token, server.server_port)
    except (CatalogError, OSError) as error:
        LOG.error("Editor could not start: %s", error)
        raise
    url = f"http://127.0.0.1:{server.server_port}/"
    if sys.stdout is not None:
        print(f"Open {url} on this computer; stop with Ctrl+C.", flush=True)
    LOG.info("Editor started at %s; version %s; elapsed %.1fs", url,
             editor_manifest()["version"], time.monotonic() - started_at)
    session = {'pid': os.getpid(), 'port': server.server_port,
               'server_instance': editor.instance_id}
    session_path = APP_HOME / 'localization/workspace/editor-session.json'
    packaged = os.name == 'nt' and getattr(sys, 'frozen', False)
    if packaged:
        atomic_bytes(session_path, json.dumps(session).encode('utf-8'))
    publish_startup_marker(args.update_ticket, session)
    if not args.no_browser:
        webbrowser.open(url)
    def initialize_project() -> None:
        """Serve health and a loading screen while a slow project is prepared."""
        try:
            Editor.__init__(editor)
            LOG.info('Project prepared in %.1fs', time.monotonic() - started_at)
        except Exception as error:
            editor.ready = False
            editor.connection_error = 'Project preparation failed: ' + str(error)
            editor.record_event('project-prepare', 'error:' + type(error).__name__ + ': ' +
                                safe_ui_event_detail(str(error)[:500]))
            LOG.exception('Project preparation failed')
        finally:
            editor.initializing = False
    threading.Thread(target=initialize_project, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if packaged:
            try:
                session_path.unlink(missing_ok=True)
            except OSError:
                LOG.warning('Could not remove the closed editor session marker')
    return server.server_port if getattr(server, 'restart_requested', False) else None


if __name__ == "__main__":
    raise SystemExit(main())
