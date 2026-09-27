"""Exercise the Windows EXE's source gate and a full translation save/undo."""

from __future__ import annotations

import hashlib
import gzip
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile


REPOSITORY = Path(__file__).resolve().parents[2]


def get(url: str) -> bytes:
    """Fetch one response from the local editor with a short timeout."""
    with urlopen(url, timeout=8) as response:
        return response.read()


def post(base: str, token: str, path: str, payload: dict) -> dict:
    """Use the same origin and one-time token as the browser UI."""
    request = Request(base + path, data=json.dumps(payload).encode(), headers={
        "Origin": base, "X-Editor-Token": token,
        "Content-Type": "application/json",
    })
    with urlopen(request, timeout=90) as response:
        return json.load(response)


def source_fixture(destination: Path) -> None:
    """Copy enough real repository inputs to test the packaged editor."""
    files = [
        "LEKMOD/VERSION", "LEKMOD/Override/CIV5Units_Mongol.xml",
        "localization/en_US/primary.xml", "localization/config.json",
        "localization/reference/vanilla-fingerprints.json.gz",
        "localization/reference/english-edit-dates.json.gz",
        "localization/tools/manage.py",
    ]
    files.extend(str(path.relative_to(REPOSITORY)) for path in
                 (REPOSITORY / "LEKMOD/Art").rglob("*")
                 if path.is_file() and path.suffix.lower() in (".xml", ".sql"))
    files.extend(str(path.relative_to(REPOSITORY)) for path in
                 (REPOSITORY / "localization/translations").glob("*.csv"))
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY / name, target)
    (destination / "LEKMOD/Lua/tmp").mkdir(parents=True)
    (destination / "localization/tools/tests").mkdir(parents=True)


def start_editor(root: Path, port: int, log: Path) -> tuple[subprocess.Popen, str, str]:
    """Wait for the frozen executable to prepare its local HTTP interface."""
    output = log.open("wb")
    process = subprocess.Popen([str(root / "LekmodLocalizationEditor.exe"),
                                "--no-browser", "--port", str(port)], cwd=root,
                               stdout=output, stderr=subprocess.STDOUT)
    output.close()
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError(f"editor exited early: {process.returncode}; " +
                               log.read_text(encoding="utf-8", errors="replace")[-3000:])
        try:
            html = get(base + "/").decode("utf-8")
            token = re.search(r'<meta name="editor-token" content="([^"]+)">', html)
            if not token:
                raise RuntimeError("editor page has no request token")
            return process, base, token.group(1)
        except (URLError, TimeoutError):
            time.sleep(1)
    raise RuntimeError("editor did not start in two minutes; " +
                       log.read_text(encoding="utf-8", errors="replace")[-3000:])


def stop_editor(process: subprocess.Popen, base: str, token: str) -> None:
    """Shut down the one process that owns this test's port."""
    if process.poll() is None:
        try:
            post(base, token, "/api/stop", {})
        except (URLError, TimeoutError):
            pass
    try:
        process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True)
        else:
            process.terminate()
        process.wait(timeout=10)


def main() -> int:
    """Verify clean-game gating, then a connected project's save and undo."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(root)
        assert not (root / "LEKMOD").exists(), "portable editor shipped an incomplete mod"
        assert not (root / "localization/workspace/vanilla-snapshot.json.gz").exists()
        with socket.socket() as address:
            address.bind(("127.0.0.1", 0))
            port = address.getsockname()[1]
        process, base, token = start_editor(root, port, root / "first-launch.log")
        try:
            meta = json.loads(get(base + "/api/meta"))
            assert not meta["ready"] and not meta["locales"]
            try:
                get(base + "/api/rows?locale=RU_RU&category=buildings")
            except HTTPError as error:
                assert error.code == 400
            else:
                raise RuntimeError("editor exposed rows without a full project")
        finally:
            stop_editor(process, base, token)

        project = root / "full-project"
        source_fixture(project)
        game = project / "LEKMOD/Override/CIV5Units_Mongol.xml"
        before = hashlib.sha256(game.read_bytes()).digest()
        settings = root / "localization/workspace/editor-settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"project_path": str(project), "onboarded": True}),
                            encoding="utf-8")
        process, base, token = start_editor(root, port, root / "connected-launch.log")
        try:
            assert b"function renderTable" in get(base + "/app.js")
            meta = json.loads(get(base + "/api/meta"))
            expected_version = json.loads((root / "localization/editor/version.json").read_text(
                encoding="utf-8"))["version"]
            assert meta["ready"] and meta["editor_version"] == expected_version
            assert not meta["vanilla_counts"]
            post(base, token, "/api/preferences", {"mode": "developer"})
            primary = json.loads(get(base + "/api/primary?offset=0"))["rows"]
            assert primary[0]["source_file"] == "localization/en_US/primary.xml"
            assert primary[0]["source_line"] > 0
            dates = json.loads(gzip.decompress((project /
                "localization/reference/english-edit-dates.json.gz").read_bytes()))
            from lekmod_localization.english_dates import source_hash
            assert dates["source_sha256"] == source_hash(
                project / "localization/en_US/primary.xml")
            assert primary and primary[0]["english_edited_at"]
            post(base, token, "/api/preferences", {"mode": "translator"})
            for category in meta["locales"]["RU_RU"]:
                rows = json.loads(get(base + "/api/rows?" + urlencode({
                    "locale": "RU_RU", "category": category, "offset": 0})))
                row = next((row for row in rows["rows"]
                            if row["required_format_tokens"] == "{}" and
                            row["vanilla_en_US_status"] == "unavailable"), None)
                if row:
                    break
            if row is None:
                raise RuntimeError("no eligible row in connected project")
            result = post(base, token, "/api/translate", {
                "locale": "RU_RU", "category": category, "key": row["key"],
                "source_fingerprint": row["source_fingerprint"],
                "translation": "Portable editor smoke test", "translation_gender": "",
                "translation_plurality": "", "translator_note": "",
            })
            assert result["applied_to_game"]
            assert hashlib.sha256(game.read_bytes()).digest() != before
            assert post(base, token, "/api/undo", {})["redo_available"]
            assert hashlib.sha256(game.read_bytes()).digest() == before
            logs = json.loads(get(base + "/api/logs"))["events"]
            assert any(event["action"] == "translate" for event in logs)
            print("Portable editor source gate, mode switch, save, undo and log passed.")
        except Exception:
            print((root / "connected-launch.log").read_text(
                encoding="utf-8", errors="replace")[-8000:])
            raise
        finally:
            stop_editor(process, base, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
