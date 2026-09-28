"""Upgrade the previous released GUI EXE through its actual browser API."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import zipfile

from test_windows_updater import start, stop, token_at, wait_for


def main() -> int:
    """Check GitHub discovery, download, same-tab restart, and preserved state."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-archive", type=Path, required=True)
    parser.add_argument("--new-archive", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("run the online upgrade test on Windows")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory) / "Translator Folder With Spaces"
        with zipfile.ZipFile(args.old_archive) as archive:
            archive.extractall(root)
        with zipfile.ZipFile(args.new_archive) as archive:
            version = json.loads(archive.read("localization/editor/version.json"))["version"]
            expected_exe = archive.read("LekmodLocalizationEditor.exe")
        preferences = {"project_path": "C:/Lekmod source", "mode": "translator",
                       "column_widths": {"key": 515}, "snapshot_url": "https://example.invalid"}
        workspace = root / "localization/workspace"
        workspace.mkdir(parents=True)
        (workspace / "editor-settings.json").write_text(json.dumps(preferences), encoding="utf-8")
        (workspace / "vanilla-snapshot.json.gz").write_bytes(b"private snapshot fixture")
        old, base, _ = start(root)
        previous = json.loads((root / "localization/editor/version.json").read_text())["version"]
        wait_for(base, previous)
        for _ in range(40):
            with urlopen(base + "/api/editor-latest", timeout=15) as response:
                latest = json.load(response)
            if latest["latest"] == version and latest["available"]:
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"GitHub did not publish editor v{version} in time: {latest}")
        request = Request(base + "/api/editor-update", data=b"{}", headers={
            "Origin": base, "X-Editor-Token": token_at(base),
            "Content-Type": "application/json"})
        with urlopen(request, timeout=15) as response:
            assert response.status == 202
        for _ in range(720):
            try:
                with urlopen(base + "/api/meta", timeout=2) as response:
                    metadata = json.load(response)
                if metadata["editor_version"] == version:
                    break
                notice = metadata.get("update_notice")
                if notice and notice["result"] == "failure":
                    raise RuntimeError("The old editor reopened after a failed update: " +
                                       (workspace / "editor-updates/update.log").read_text()[-2000:])
                with urlopen(base + "/api/editor-update-status", timeout=2) as response:
                    status = json.load(response)
                if status["state"] == "error":
                    raise RuntimeError("The update button failed: " + status["error"])
            except (URLError, TimeoutError, ConnectionError):
                pass  # Expected while the old app releases its localhost port.
            time.sleep(.25)
        else:
            raise RuntimeError("The updated EXE did not reopen the original browser address")
        assert metadata["preferences"]["project_path"] == "C:/Lekmod source"
        assert metadata["preferences"]["column_widths"] == {"key": 515}
        assert (root / "LekmodLocalizationEditor.exe").read_bytes() == expected_exe
        assert (workspace / "vanilla-snapshot.json.gz").read_bytes() == b"private snapshot fixture"
        # The new server may answer /api/meta before the helper records success.
        log = workspace / "editor-updates/update.log"
        for _ in range(40):
            details = log.read_text(encoding="utf-8") if log.exists() else ""
            if f"success  Installed v{version}" in details:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("The helper reopened the editor without confirming the update: "
                               + details[-2000:])
        stop(base, token_at(base))
        old.wait(timeout=20)
    print(f"Editor v{previous} downloaded v{version} from GitHub and reopened the same tab.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
