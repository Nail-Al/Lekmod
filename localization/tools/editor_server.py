"""Serve a local translation editor; only localhost can write project files."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import os
import tempfile
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
from lekmod_localization.common import (
    CatalogError, DEFAULT_EDITOR_OUTPUT, REPO_ROOT, WORKSPACE,
    PLACEHOLDER_RE, KEY_RE, character_count, token_counts,
)
from lekmod_localization.connections import (
    APP_HOME, apply_game, detect_game, installed_mods, release_version,
    save_settings, settings, validate_game, validate_project,
    release_catalog, download_compatible_source, editor_manifest,
)
from lekmod_localization.editor_update import (
    latest_release, stage_release, write_windows_updater,
)
from lekmod_localization.english_dates import read_dates
from lekmod_localization.shipped import read_approvals
from lekmod_localization.vanilla_snapshot import read_snapshot
from lekmod_localization.vanilla_reference import verify_snapshot_reference
from lekmod_localization.vanilla_reference import read_reference
from lekmod_localization.workspace import EDITOR_FIELDNAMES
from snapshot_cloud import decrypt_snapshot, download_encrypted


PAGE = APP_HOME / "localization" / "editor" / "index.html"
SCRIPT = APP_HOME / "localization" / "editor" / "app.js"
TRANSLATIONS = REPO_ROOT / "localization" / "translations"
APPROVAL_FIELDS = ("key", "source_fingerprint", "text", "gender", "plurality", "translator_note", "updated_at")
OPERATIONS = re.compile(
    r"(?ms)^(?P<indent>[ \t]+)<(?P<kind>Row|Replace)\b(?P<attrs>[^>]*)>"
    r"(?P<body>.*?)^[ \t]*</(?P=kind)>"
)
TAG = re.compile(r'\bTag="(TXT_KEY_[A-Za-z0-9_]+)"')
TEXT = re.compile(r"<Text>(.*?)</Text>", re.DOTALL)


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


def primary_text(value: str) -> str:
    """Interpret XML entities in one Text element, preserving line breaks."""
    import xml.etree.ElementTree as ET
    return ET.fromstring("<Text>" + value + "</Text>").text or ""


class Editor:
    """Validate and persist browser edits before touching the shipped XML."""

    def __init__(self, snapshot: Path = manage.SNAPSHOT):
        manage.migrate_workspace()
        self.ready = False
        self.connection_error = ""
        try:
            self.project_info = validate_project(REPO_ROOT, full=True)
            self.ready = True
        except ValueError as error:
            self.project_info = None
            self.connection_error = str(error)
        home_snapshot = APP_HOME / "localization/workspace/vanilla-snapshot.json.gz"
        self.snapshot = next((path for path in (snapshot, home_snapshot) if path.is_file()), None)
        self.vanilla_counts = (
            {locale: len(rows) for locale, rows in read_snapshot(self.snapshot)[0].items()}
            if self.snapshot is not None else {}
        )
        if self.ready:
            manage.prepare(manage.read_config(), self.snapshot)
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
        self.actions: list[dict] = []
        self.cursor = 0
        self.download_state: dict = {"state": "idle"}
        self.log_path = APP_HOME / "localization/workspace/editor-actions.jsonl"
        self.events: list[dict] = []
        if self.log_path.is_file():
            for line in self.log_path.read_text(encoding="utf-8").splitlines()[-500:]:
                try:
                    self.events.append(json.loads(line))
                except ValueError:
                    continue

    def record_event(self, name: str, result: str) -> None:
        """Keep a bounded, text-free local action journal for troubleshooting."""
        if not re.fullmatch(r"[a-z0-9/_-]{1,60}", name):
            return
        self.events.append({"at": datetime.now(timezone.utc).isoformat(),
                            "action": name, "result": result})
        self.events = self.events[-500:]
        atomic_bytes(self.log_path, ("\n".join(json.dumps(item) for item in self.events)
                                     + "\n").encode("utf-8"))

    def start_download(self, version: str) -> dict:
        """Fetch a reviewed source release in the background without replacing files."""
        if self.download_state["state"] == "running":
            raise CatalogError("a source download is already running")
        if version not in {row["version"] for row in release_catalog() if row["supported"]}:
            raise CatalogError("this release needs a reviewed localization migration")
        self.download_state = {"state": "running", "version": version}

        def run() -> None:
            try:
                path = download_compatible_source(version)
                self.download_state = {"state": "complete", "path": str(path)}
            except Exception as error:
                self.download_state = {"state": "error", "error": str(error)}

        threading.Thread(target=run, daemon=True).start()
        return self.download_state

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
        date = datetime.now(timezone.utc).isoformat()
        self.english_dates[key] = date
        self.local_dates[key] = date
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
        manifest = self.manifest() if self.ready else {"locales": {}}
        prefs = settings()
        game_path = prefs["game_path"] or detect_game()
        mods = []
        game_error = ""
        if game_path:
            try:
                mods = installed_mods(Path(game_path))
            except ValueError as error:
                game_error = str(error)
        selected_mod = prefs["game_mod"] or (mods[0]["name"] if len(mods) == 1 else "")
        return {
            "locales": {locale: [name.removesuffix(".csv") for name in details["files"]]
                        for locale, details in manifest["locales"].items()},
            "config": manage.read_config() if self.ready else {},
            "vanilla_counts": self.vanilla_counts,
            "ready": self.ready,
            "connection_error": self.connection_error,
            "project": self.project_info,
            "editor_version": editor_manifest()["version"],
            "included_source": REPO_ROOT == APP_HOME,
            "release": release_version(REPO_ROOT) if self.ready else "",
            "game": {"path": game_path, "mods": mods, "selected_mod": selected_mod,
                     "error": game_error},
            "preferences": prefs,
            **self.history_state(),
        }

    def connect(self, data: dict) -> dict:
        """Validate selected folders before persisting; a project switch restarts."""
        project = str(data.get("project_path", "")).strip()
        game = str(data.get("game_path", "")).strip()
        mod = str(data.get("game_mod", "")).strip()
        source_root = Path(project) if project else APP_HOME
        validate_project(source_root, full=True)
        if game:
            mods = installed_mods(validate_game(Path(game)))
            if mod and mod not in {entry["name"] for entry in mods}:
                raise CatalogError("selected Lekmod DLC folder is not installed in this game")
            if not mod and len(mods) == 1:
                mod = mods[0]["name"]
            if mod:
                selected = next(entry for entry in mods if entry["name"] == mod)
                if (selected["version"].casefold() != validate_project(
                    source_root, full=True)["version"].casefold() or
                    selected["release"].casefold() != release_version(source_root).casefold()):
                    raise CatalogError("The selected game's Lekmod version differs from "
                                       "the connected source. Choose a matching DLC release.")
        elif mod:
            raise CatalogError("select a game folder before selecting a DLC mod")
        changed = (Path(project).resolve() if project else APP_HOME) != REPO_ROOT
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
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        self.snapshot = destination
        self.vanilla_counts = {locale: len(rows) for locale, rows in
                               read_snapshot(destination)[0].items()}
        if self.ready:
            manage.prepare(manage.read_config(), self.snapshot)
        return {"vanilla_counts": self.vanilla_counts}

    def import_cloud_snapshot(self, url: str, password: str) -> dict:
        """Download encrypted text; keep only its verified local copy and URL."""
        if not self.ready or not isinstance(url, str) or not isinstance(password, str):
            raise CatalogError("connect a complete project and enter a snapshot link and password")
        reference = REPO_ROOT / "localization/reference/vanilla-fingerprints.json.gz"
        data = decrypt_snapshot(download_encrypted(url), password, reference)
        result = self.import_snapshot(data)
        save_settings({"snapshot_url": url})
        return result

    def apply_to_game(self) -> dict:
        """Copy the generated XML only after checking the connected DLC copy."""
        if not self.ready:
            raise CatalogError("connect a compatible Lekmod project first")
        prefs = settings()
        game = prefs["game_path"] or detect_game()
        if not game:
            raise CatalogError("connect a Civilization V installation in Settings")
        mods = installed_mods(Path(game))
        name = prefs["game_mod"] or (mods[0]["name"] if len(mods) == 1 else "")
        if not name:
            raise CatalogError("select the installed Lekmod version in Settings")
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
        python = shutil.which("python") or shutil.which("py")
        git = shutil.which("git")
        if python and git:
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
                (". Skipped without developer Python and Git: " + ", ".join(skipped) +
                 ". CI runs the complete suite on push." if skipped else ".")}

    def export_locale(self, locale: str) -> bytes:
        """Package one approved CSV for a developer without private vanilla text."""
        if not self.ready:
            raise CatalogError("connect a complete compatible Lekmod project first")
        if locale not in self.manifest().get("locales", {}):
            raise CatalogError("unknown language")
        path = TRANSLATIONS / f"{locale}.csv"
        read_approvals(TRANSLATIONS)
        reference = manage.DEFAULT_REFERENCE.read_bytes()
        revision = None
        if (REPO_ROOT / ".git").exists():
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
                capture_output=True, check=True,
            ).stdout.strip()
        metadata = {
            "locale": locale,
            "repository_commit": revision,
            "vanilla_reference_sha256": hashlib.sha256(reference).hexdigest(),
        }
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"translations/{locale}.csv", path.read_bytes())
            archive.writestr("manifest.json", json.dumps(metadata, indent=2) + "\n")
            archive.writestr("README.txt", (
                "Lekmod localization handoff\n\n"
                "Send this ZIP to a Lekmod developer or attach it to a review.\n"
                "The developer should compare the manifest, copy the CSV into\n"
                "localization/translations/, run localization/tools/manage.py prepare\n"
                "and check, then review and commit the generated game XML.\n"
                "This package contains no private vanilla snapshot.\n"
            ))
        return stream.getvalue()

    def rows(self, locale: str, category: str, query: str, offset: int) -> dict:
        """Search a category and return one page with approval state."""
        rows = csv_rows(self.path(locale, category))
        approved = read_approvals(TRANSLATIONS).get(locale, {})
        timestamps = {}
        approval_path = TRANSLATIONS / f"{locale}.csv"
        if approval_path.is_file():
            with approval_path.open(encoding="utf-8-sig", newline="") as handle:
                timestamps = {record["key"]: record.get("updated_at", "")
                              for record in csv.DictReader(handle)}
        query = query.casefold()
        selected = [
            row for row in rows
            if not query or any(query in row[field].casefold()
                                for field in ("key", "lekmod_en_US", "vanilla_en_US",
                                              "vanilla_target", "translation"))
        ]
        page = []
        for row in selected[max(0, offset):max(0, offset) + 60]:
            item = dict(row)
            item["english_edited_at"] = self.english_dates.get(row["key"], "")
            item["translation_updated_at"] = timestamps.get(row["key"], "")
            saved = approved.get(row["key"])
            if saved:
                item["translation_status"] = (
                    ("applied" if manage.read_config()["build"]["shipped"] else "saved")
                    if saved["source_fingerprint"] == row["source_fingerprint"]
                    else "stale"
                )
                item["translation"] = saved["text"]
                item["translation_characters"] = str(character_count(saved["text"]))
            page.append(item)
        return {"total": len(selected), "rows": page}

    def primary(self, query: str, offset: int) -> dict:
        """Search the canonical English XML as a separate editor category."""
        document = sync_primary_english.DEFAULT_ENGLISH.read_text(encoding="utf-8")
        query = query.casefold()
        selected = [
            {"index": row["index"], "key": row["key"], "kind": row["kind"],
             "text": primary_text(row["text"]),
             "characters": character_count(primary_text(row["text"])),
             "english_edited_at": self.english_dates.get(row["key"], "")}
            for row in primary_operations(document)
            if not query or query in row["key"].casefold()
            or query in primary_text(row["text"]).casefold()
        ]
        return {"total": len(selected), "rows": selected[max(0, offset):max(0, offset) + 60]}

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
        return {"applied_to_game": apply_to_game, **self.history_state()}

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
        replacement = before[:row["text_start"]] + escape(new_text) + before[row["text_end"]:]
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
        if not isinstance(key, str) or not KEY_RE.fullmatch(key) or not isinstance(value, str):
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
        closing = "\t</Language_en_US>"
        if before.count(closing) != 1:
            raise CatalogError("cannot locate the end of the primary English table")
        operation = (f'\t\t<Row Tag="{key}">\n\t\t\t<Text>{escape(value)}</Text>\n'
                     "\t\t</Row>\n")
        return self._save_structure(before, before.replace(closing, operation + closing, 1), key)

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
        prefix = before[:row["start"]]
        body = before[row["start"]:row["text_end"]]
        if body.count(f'Tag="{old}"') != 1:
            raise CatalogError("cannot identify a unique Tag attribute")
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
        def respond(self, status: int, payload: object) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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
                elif url.path == "/api/meta":
                    self.respond(200, editor.metadata())
                elif url.path == "/api/logs":
                    self.respond(200, {"events": editor.events})
                elif url.path == "/api/logs/download":
                    self.download("lekmod-editor-actions.jsonl",
                                  editor.log_path.read_bytes() if editor.log_path.exists() else b"",
                                  "application/x-ndjson")
                elif url.path == "/api/versions":
                    self.respond(200, {"versions": release_catalog()})
                elif url.path == "/api/editor-latest":
                    self.respond(200, latest_release())
                elif url.path == "/api/download-status":
                    self.respond(200, editor.download_state)
                elif url.path == "/api/rows":
                    if not editor.ready:
                        raise CatalogError("connect a compatible Lekmod project in Settings")
                    self.respond(200, editor.rows(one("locale"), one("category"), one("q"), int(one("offset", "0"))))
                elif url.path == "/api/primary":
                    if not editor.ready:
                        raise CatalogError("connect a compatible Lekmod project in Settings")
                    self.respond(200, editor.primary(one("q"), int(one("offset", "0"))))
                elif url.path == "/api/history":
                    self.respond(200, editor.history(
                        key=one("key") if "key" in args else None,
                        index=int(one("index")) if "index" in args else None,
                    ))
                elif url.path == "/api/export":
                    locale = one("locale")
                    data = editor.export_locale(locale)
                    self.download(f"lekmod-{locale}-translations.zip", data, "application/zip")
                elif url.path == "/api/game-xml":
                    game = build_shipped_localization.DEFAULT_SOURCE
                    self.download(game.name, game.read_bytes(), "application/xml")
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
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.path == "/api/snapshot":
                    if not 0 < length <= 16 * 1024 * 1024:
                        raise CatalogError("invalid snapshot upload size")
                    self.respond(200, editor.import_snapshot(self.rfile.read(length)))
                    editor.record_event("snapshot-import", "success")
                    return
                if not 0 < length <= 512 * 1024:
                    raise CatalogError("invalid request size")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise CatalogError("invalid request")
                if self.path == "/api/translate":
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
                elif self.path == "/api/snapshot-cloud":
                    result = editor.import_cloud_snapshot(data.get("url"), data.get("password"))
                elif self.path == "/api/editor-update":
                    latest = latest_release()
                    stage = stage_release(latest)
                    self.server.update_script = write_windows_updater(stage)
                    self.server.update_stage = stage
                    self.respond(200, {"updating": True, "version": latest["latest"]})
                    editor.record_event("editor-update", "success")
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                elif self.path == "/api/preferences":
                    allowed = {"mode", "prefill", "wrap", "locale", "category",
                               "visible_columns", "column_widths", "onboarded"}
                    if set(data) - allowed:
                        raise CatalogError("unknown editor preference")
                    if data.get("mode") == "developer":
                        validate_project(REPO_ROOT, full=True)
                    result = {"preferences": save_settings(data)}
                elif self.path == "/api/event":
                    name = data.get("name")
                    if name not in {"row-selected", "mode-switch", "settings-open",
                                    "columns-changed", "page-changed", "prefill-changed",
                                    "logs-open", "logs-download", "copy", "wrap-changed",
                                    "translation-export", "xml-export", "update-check"}:
                        raise CatalogError("unknown interface action")
                    editor.record_event(name, "ui")
                    result = {"logged": True}
                elif self.path == "/api/browse":
                    if data.get("kind") not in ("project", "game"):
                        raise CatalogError("unknown folder kind")
                    from tkinter import Tk, filedialog
                    dialog = Tk()
                    dialog.withdraw()
                    try:
                        result = {"path": filedialog.askdirectory(
                            title="Select Lekmod project" if data["kind"] == "project"
                            else "Select Civilization V installation")}
                    finally:
                        dialog.destroy()
                elif self.path == "/api/apply-game":
                    result = editor.apply_to_game()
                elif self.path == "/api/check":
                    result = editor.check_project()
                elif self.path == "/api/detect-game":
                    result = {"path": detect_game()}
                elif self.path == "/api/inspect-game":
                    result = {"mods": installed_mods(Path(str(data.get("path", ""))))}
                elif self.path == "/api/undo":
                    result = editor.replay(undo=True)
                elif self.path == "/api/redo":
                    result = editor.replay(undo=False)
                elif self.path == "/api/stop":
                    self.respond(200, {"stopping": True})
                    editor.record_event("stop", "success")
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                else:
                    self.respond(404, {"error": "not found"})
                    return
                self.respond(200, result)
                if self.path != "/api/event":
                    editor.record_event(self.path.removeprefix("/api/"), "success")
            except (CatalogError, OSError, ValueError, ET.ParseError, urllib.error.URLError,
                    subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                self.respond(400, {"error": str(error)})
                editor.record_event(self.path.removeprefix("/api/"),
                                    "error:" + type(error).__name__)

        def log_message(self, format: str, *args: object) -> None:
            """Log one local request without exposing the private token."""
            print(format % args, file=sys.stderr)

    return Handler


def main() -> int:
    """Start the local-only editor and print its browser address."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0,
                        help="Local port; 0 chooses an available port")
    parser.add_argument("--no-browser", action="store_true",
                        help="Start the server without opening a browser")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or 0 < args.port < 1024:
        parser.error("choose port 0 or a port from 1024 to 65535")
    try:
        editor = Editor()
        token = secrets.token_urlsafe(32)
        server = HTTPServer(("127.0.0.1", args.port), make_handler(editor, token, args.port))
        # The handler compares the browser Origin with the actual assigned port.
        server.RequestHandlerClass = make_handler(editor, token, server.server_port)
    except (CatalogError, OSError) as error:
        parser.exit(1, f"Editor failed: {error}\n")
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Open {url} on this computer; stop with Ctrl+C.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    if getattr(server, "update_script", None):
        subprocess.Popen([
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(server.update_script), "-OldPid", str(os.getpid()),
            "-Stage", str(server.update_stage), "-Home", str(APP_HOME),
        ], creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif getattr(server, "restart_requested", False):
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--port", str(server.server_port)]
            environment = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
            flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        else:
            command = [sys.executable, "-B", str(APP_HOME / "localization/tools/editor_server.py"),
                       "--port", str(server.server_port)]
            environment = os.environ.copy()
            flags = 0
        subprocess.Popen(command, cwd=APP_HOME, env=environment, creationflags=flags)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
