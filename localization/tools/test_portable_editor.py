"""Launch the real Windows EXE and verify a full save/undo in a temporary copy."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile


def get(url: str) -> bytes:
    """Fetch a response from the local-only test editor."""
    with urlopen(url, timeout=3) as response:
        return response.read()


def main() -> int:
    """Exercise the packaged interpreter, hash-only catalog, save and undo."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(root)
        game = root / "LEKMOD/Override/CIV5Units_Mongol.xml"
        before = hashlib.sha256(game.read_bytes()).digest()
        with socket.socket() as address:
            address.bind(("127.0.0.1", 0))
            port = address.getsockname()[1]
        log = root / "editor-launch.log"
        with log.open("wb") as output:
            process = subprocess.Popen(
                [str(root / "LekmodLocalizationEditor.exe"), "--no-browser",
                 "--port", str(port)], cwd=root, stdout=output,
                stderr=subprocess.STDOUT,
            )
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError(f"editor exited early: {process.returncode}")
                try:
                    html = get(base + "/").decode("utf-8")
                    break
                except (URLError, TimeoutError):
                    time.sleep(1)
            else:
                raise RuntimeError("editor did not start in two minutes")
            token = re.search(r'<meta name="editor-token" content="([^"]+)">', html)
            if not token:
                raise RuntimeError("editor page has no request token")
            assert b"function renderTable" in get(base + "/app.js")
            meta = json.loads(get(base + "/api/meta"))
            assert not meta["vanilla_counts"], "test package unexpectedly contains vanilla text"
            locale = "RU_RU"
            for category in meta["locales"][locale]:
                rows = json.loads(get(base + "/api/rows?" + urlencode({
                    "locale": locale, "category": category, "offset": 0})))
                row = next((row for row in rows["rows"]
                            if row["required_format_tokens"] == "{}" and
                            row["vanilla_en_US_status"] == "unavailable"), None)
                if row:
                    break
            if row is None:
                raise RuntimeError("no eligible row in the portable editor")
            payload = json.dumps({
                "locale": locale, "category": category, "key": row["key"],
                "source_fingerprint": row["source_fingerprint"],
                "translation": "Portable editor smoke test", "translation_gender": "",
                "translation_plurality": "", "translator_note": "",
            }).encode("utf-8")
            request = Request(base + "/api/translate", data=payload, headers={
                "Origin": base, "X-Editor-Token": token.group(1),
                "Content-Type": "application/json",
            })
            with urlopen(request, timeout=60) as response:
                result = json.load(response)
            assert result["applied_to_game"]
            assert hashlib.sha256(game.read_bytes()).digest() != before
            undo = Request(base + "/api/undo", data=b"{}", headers={
                "Origin": base, "X-Editor-Token": token.group(1),
                "Content-Type": "application/json",
            })
            try:
                with urlopen(undo, timeout=60) as response:
                    assert json.load(response)["redo_available"]
            except HTTPError as error:
                raise RuntimeError(f"undo failed: {error.read().decode('utf-8')}") from error
            assert hashlib.sha256(game.read_bytes()).digest() == before
            print("Portable editor launch, classification, save, and undo passed.")
        except Exception:
            print(log.read_text(encoding="utf-8", errors="replace")[-8000:])
            raise
        finally:
            if process.poll() is None:
                try:
                    stop = Request(base + "/api/stop", data=b"{}", headers={
                        "Origin": base, "X-Editor-Token": token.group(1),
                        "Content-Type": "application/json",
                    })
                    with urlopen(stop, timeout=5):
                        pass
                except (NameError, AttributeError, URLError, TimeoutError):
                    pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                                   capture_output=True)
                else:
                    process.terminate()
                process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
