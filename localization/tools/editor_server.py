"""Serve a local translation editor; only localhost can write project files."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
import webbrowser
from xml.sax.saxutils import escape

import build_shipped_localization
import manage
import sync_primary_english
from lekmod_localization.common import (
    CatalogError, DEFAULT_EDITOR_OUTPUT, REPO_ROOT, WORKSPACE,
    PLACEHOLDER_RE, character_count, token_counts,
)
from lekmod_localization.shipped import read_approvals
from lekmod_localization.workspace import EDITOR_FIELDNAMES


PAGE = REPO_ROOT / "localization" / "editor" / "index.html"
TRANSLATIONS = REPO_ROOT / "localization" / "translations"
APPROVAL_FIELDS = ("key", "source_fingerprint", "text", "gender", "plurality", "translator_note")
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
        self.snapshot = snapshot
        manage.migrate_workspace()
        if not self.snapshot.is_file():
            raise CatalogError(f"vanilla snapshot is missing: {self.snapshot}")
        manage.prepare(manage.read_config(), self.snapshot)

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
        manifest = self.manifest()
        return {
            "locales": {locale: [name.removesuffix(".csv") for name in details["files"]]
                        for locale, details in manifest["locales"].items()},
            "config": manage.read_config(),
        }

    def rows(self, locale: str, category: str, query: str, offset: int) -> dict:
        """Search a category and return one page with approval state."""
        rows = csv_rows(self.path(locale, category))
        approved = read_approvals(TRANSLATIONS).get(locale, {})
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
            saved = approved.get(row["key"])
            if saved:
                item["translation_status"] = (
                    "approved" if saved["source_fingerprint"] == row["source_fingerprint"]
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
             "characters": character_count(primary_text(row["text"]))}
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
        result = subprocess.run(
            ["git", "blame", "--line-porcelain", "-L", f"{line},{line}",
             "--", str(source.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT, text=True, capture_output=True, check=True,
        )
        timestamp = re.search(r"^author-time (\d+)$", result.stdout, re.MULTILINE)
        commit = result.stdout.split(" ", 1)[0]
        return {
            "committed_at": (
                datetime.fromtimestamp(int(timestamp.group(1)), timezone.utc).isoformat()
                if timestamp and commit.strip("0") else None
            ),
            "commit": commit if commit.strip("0") else None,
        }

    def save_translation(self, data: dict) -> dict:
        """Approve one CSV row, update the tracked language CSV and game XML."""
        locale = str(data.get("locale", ""))
        category = str(data.get("category", ""))
        path = self.path(locale, category)
        rows = csv_rows(path)
        key = data.get("key")
        row = next((candidate for candidate in rows if candidate["key"] == key), None)
        if row is None or row["source_fingerprint"] != data.get("source_fingerprint"):
            raise CatalogError("the English source changed; reload this row")
        for name in ("translation", "translation_gender", "translation_plurality", "translator_note"):
            if not isinstance(data.get(name), str) or len(data[name]) > 10000:
                raise CatalogError(f"invalid {name}")
        translation = data["translation"]
        if translation and (
            PLACEHOLDER_RE.search(translation)
            or token_counts(translation) != json.loads(row["required_format_tokens"])
        ):
            raise CatalogError("translation must preserve all formatting tokens")
        approved_path = TRANSLATIONS / f"{locale}.csv"
        old_approved = approved_path.read_bytes() if approved_path.exists() else None
        old_editor = path.read_bytes()
        existing = read_approvals(TRANSLATIONS).get(locale, {})
        notes = {}
        if approved_path.is_file():
            with approved_path.open(encoding="utf-8-sig", newline="") as handle:
                notes = {entry["key"]: entry["translator_note"] for entry in csv.DictReader(handle)}
        if translation:
            existing[key] = {
                "source_fingerprint": row["source_fingerprint"], "text": translation,
                **{field: data["translation_" + field] for field in ("gender", "plurality")
                   if data["translation_" + field]},
            }
            notes[key] = data["translator_note"]
        else:
            existing.pop(key, None)
            notes.pop(key, None)
        approved_rows = [
            {"key": selected, "source_fingerprint": value["source_fingerprint"],
             "text": value["text"], "gender": value.get("gender", ""),
             "plurality": value.get("plurality", ""),
             "translator_note": notes.get(selected, "")}
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
                "translation_status": "approved" if translation else "missing",
            })
            atomic_bytes(path, encoded_csv(rows, list(EDITOR_FIELDNAMES)))
            if apply_to_game:
                sync_primary_english.atomic_text(build_shipped_localization.DEFAULT_SOURCE, candidate)
        except Exception:
            if old_approved is None:
                approved_path.unlink(missing_ok=True)
            else:
                atomic_bytes(approved_path, old_approved)
            atomic_bytes(path, old_editor)
            raise
        return {"applied_to_game": apply_to_game}

    def save_primary(self, data: dict) -> dict:
        """Change one English text, then refresh generated XML and draft statuses."""
        source = sync_primary_english.DEFAULT_ENGLISH
        game = build_shipped_localization.DEFAULT_SOURCE
        before = source.read_text(encoding="utf-8")
        index = data.get("index")
        if not isinstance(index, int):
            raise CatalogError("invalid primary row")
        rows = primary_operations(before)
        if index < 0 or index >= len(rows):
            raise CatalogError("unknown primary row")
        row = rows[index]
        new_text = data.get("text")
        if not isinstance(new_text, str) or len(new_text) > 10000 or (
            row["key"] != data.get("key")
            or primary_text(row["text"]) != data.get("old_text")
        ):
            raise CatalogError("primary row changed; reload before saving")
        replacement = before[:row["text_start"]] + escape(new_text) + before[row["text_end"]:]
        sync_primary_english.validate_source(replacement)
        old_game = game.read_bytes()
        atomic_bytes(source, replacement.encode("utf-8"))
        try:
            manage.prepare(manage.read_config(), self.snapshot)
        except Exception:
            atomic_bytes(source, before.encode("utf-8"))
            atomic_bytes(game, old_game)
            raise
        config = manage.read_config()["build"]
        return {"key": row["key"], "updated": True,
                "applied_to_game": config["english"] and config["shipped"]}


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
                    self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
                    self.send_header("Content-Length", str(len(html)))
                    self.end_headers()
                    self.wfile.write(html)
                elif url.path == "/api/meta":
                    self.respond(200, editor.metadata())
                elif url.path == "/api/rows":
                    self.respond(200, editor.rows(one("locale"), one("category"), one("q"), int(one("offset", "0"))))
                elif url.path == "/api/primary":
                    self.respond(200, editor.primary(one("q"), int(one("offset", "0"))))
                elif url.path == "/api/history":
                    self.respond(200, editor.history(
                        key=one("key") if "key" in args else None,
                        index=int(one("index")) if "index" in args else None,
                    ))
                else:
                    self.respond(404, {"error": "not found"})
            except (CatalogError, OSError, ValueError, subprocess.CalledProcessError) as error:
                self.respond(400, {"error": str(error)})

        def do_POST(self) -> None:
            if self.headers.get("Origin") != f"http://127.0.0.1:{port}" or (
                self.headers.get("X-Editor-Token") != token
            ):
                self.respond(403, {"error": "editor request rejected"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 50000:
                    raise CatalogError("invalid request size")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise CatalogError("invalid request")
                if self.path == "/api/translate":
                    result = editor.save_translation(data)
                elif self.path == "/api/primary":
                    result = editor.save_primary(data)
                else:
                    self.respond(404, {"error": "not found"})
                    return
                self.respond(200, result)
            except (CatalogError, OSError, ValueError, subprocess.CalledProcessError) as error:
                self.respond(400, {"error": str(error)})

        def log_message(self, format: str, *args: object) -> None:
            """Log one local request without exposing the private token."""
            print(format % args, file=sys.stderr)

    return Handler


def main() -> int:
    """Start the local-only editor and print its browser address."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("choose a port from 1024 to 65535")
    try:
        editor = Editor()
        token = secrets.token_urlsafe(32)
        server = HTTPServer(("127.0.0.1", args.port), make_handler(editor, token, args.port))
    except (CatalogError, OSError) as error:
        parser.exit(1, f"Editor failed: {error}\n")
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Open {url} in this Windows VM; stop with Ctrl+C.", flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
