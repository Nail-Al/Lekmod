"""Run the generated PowerShell updater on Windows with a disposable editor."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.request import urlopen
import zipfile

from lekmod_localization.editor_update import UPDATE_FILES, write_windows_updater


def run(script: Path, stage: Path, root: Path) -> subprocess.CompletedProcess:
    """Run an update after its hypothetical old process has already exited."""
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), "-OldPid", "99999999", "-Stage", str(stage),
         "-EditorRoot", str(root), "-NoBrowser"],
        capture_output=True, text=True, timeout=120,
    )


def main() -> int:
    """Check replacement, backup, log, failure, and preservation of private work."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path,
                        help="The freshly built Windows editor ZIP")
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("run this smoke test on a Windows runner")
    harmless_exe = shutil.which("whoami.exe")
    if not harmless_exe:
        raise RuntimeError("Windows whoami.exe is missing")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "Editor With Spaces"
        stage = root / "localization/workspace/editor-updates/editor-v0.6"
        private = root / "localization/workspace/editor-settings.json"
        private.parent.mkdir(parents=True)
        preferences = json.dumps({"project_path": "C:/Lekmod", "snapshot_url":
                                  "https://www.dropbox.com/example?dl=1",
                                  "column_widths": {"key": 420}, "mode": "developer"})
        private.write_text(preferences, encoding="utf-8")
        snapshot = private.parent / "vanilla-snapshot.json.gz"
        snapshot.write_bytes(b"private snapshot fixture")
        project = private.parent / "projects/v35.3/localization/translations/RU_RU.csv"
        project.parent.mkdir(parents=True)
        project.write_text("approved row", encoding="utf-8")
        with zipfile.ZipFile(args.archive) as archive:
            assert set(archive.namelist()) == set(UPDATE_FILES)
            archive.extractall(stage)
        for name in UPDATE_FILES:
            previous, incoming = root / name, stage / name
            previous.parent.mkdir(parents=True, exist_ok=True)
            if name.endswith(".exe"):
                shutil.copy2(harmless_exe, previous)
            else:
                previous.write_text("old " + name, encoding="utf-8")
        script = write_windows_updater(stage, root)
        result = run(script, stage, root)
        if result.returncode:
            raise RuntimeError("Updater failed: " + result.stdout + result.stderr +
                               (root / "localization/workspace/editor-updates/update.log").read_text(
                                   encoding="utf-8-sig"))
        assert not stage.exists(), "successful update left a pending stage"
        with zipfile.ZipFile(args.archive) as archive:
            assert (root / "localization/editor/app.js").read_bytes() == archive.read(
                "localization/editor/app.js")
            assert (root / "LekmodLocalizationEditor.exe").read_bytes() == archive.read(
                "LekmodLocalizationEditor.exe")
        backups = list((root / "localization/workspace/editor-updates").glob("previous-editor-*"))
        assert len(backups) == 1
        assert (backups[0] / "localization/editor/app.js").read_text() == "old localization/editor/app.js"
        assert private.read_text() == preferences
        assert snapshot.read_bytes() == b"private snapshot fixture"
        assert project.read_text() == "approved row"
        log = (root / "localization/workspace/editor-actions.jsonl").read_text(encoding="utf-8")
        assert json.loads(log.splitlines()[-1])["result"] == "success"
        installer_log = (root / "localization/workspace/editor-updates/update.log").read_text(
            encoding="utf-8-sig")
        match = re.search(r"pid: (\d+)", installer_log)
        assert match, "real editor did not acknowledge its startup"
        port = re.search(r"port: (\d+)", installer_log)
        assert port, "updated editor did not report its local server"
        with urlopen(f"http://127.0.0.1:{port.group(1)}/api/meta", timeout=15) as response:
            metadata = json.load(response)
        assert metadata["editor_version"] == "0.6"
        assert metadata["preferences"]["column_widths"] == {"key": 420}
        assert metadata["preferences"]["project_path"] == "C:/Lekmod"
        launcher = re.search(r"launcher_pid: (\d+)", installer_log)
        assert launcher
        subprocess.run(["taskkill", "/PID", launcher.group(1), "/T", "/F"],
                       capture_output=True, text=True, timeout=30)

        stage.mkdir(parents=True)
        failed = run(script, stage, root)
        assert failed.returncode != 0, "missing staged files must stop installation"
        with zipfile.ZipFile(args.archive) as archive:
            assert (root / "localization/editor/app.js").read_bytes() == archive.read(
                "localization/editor/app.js")
        assert private.read_text() == preferences
        assert snapshot.read_bytes() == b"private snapshot fixture"
        assert project.read_text() == "approved row"
        assert json.loads((root / "localization/workspace/editor-actions.jsonl").read_text(
            encoding="utf-8").splitlines()[-1])["result"] == "failure"
        assert "Staged editor file is missing" in (root /
            "localization/workspace/editor-updates/update.log").read_text(encoding="utf-8-sig")
        recovery_log = (root / "localization/workspace/editor-updates/update.log").read_text(
            encoding="utf-8-sig")
        restored = re.search(r"Previous editor reopened after failed update; pid: (\d+)",
                             recovery_log)
        assert restored, "failed update did not reopen the previous editor"
        subprocess.run(["taskkill", "/PID", restored.group(1), "/T", "/F"],
                       capture_output=True, text=True, timeout=30)
    print("Windows updater relaunched the actual EXE, preserved data, and recorded failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
