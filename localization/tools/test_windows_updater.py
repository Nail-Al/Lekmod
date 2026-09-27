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
import time
import uuid
from urllib.request import Request, urlopen
import zipfile

from lekmod_localization.editor_update import UPDATE_FILES, write_windows_updater


def command(script: Path, stage: Path, root: Path, old_pid: int) -> list[str]:
    """Use one argument list for a live upgrade and a failed-stage retry."""
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-OldPid", str(old_pid), "-Stage", str(stage),
            "-EditorRoot", str(root), "-NoBrowser"]


def main() -> int:
    """Check replacement, backup, log, failure, and preservation of private work."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path,
                        help="The freshly built Windows editor ZIP")
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("run this smoke test on a Windows runner")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory) / "Editor With Spaces"
        stage = root / "localization/workspace/editor-updates/editor-v0.7"
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
            if name.endswith(".exe") or name.endswith("index.html"):
                shutil.copy2(incoming, previous)
            elif name.endswith("version.json"):
                previous.write_text(json.dumps({"version": "0.5", "release_tag": "editor-v0.5",
                                                "compatible_releases": ["v35.3"]}), encoding="utf-8")
            else:
                previous.write_text("old " + name, encoding="utf-8")
        script = write_windows_updater(stage, root)
        ticket = uuid.uuid4().hex
        old_log = (root / "old-editor.log").open("wb")
        try:
            old = subprocess.Popen([str(root / "LekmodLocalizationEditor.exe"),
                                    "--no-browser", "--update-ticket", ticket],
                                   cwd=root, stdout=old_log, stderr=subprocess.STDOUT)
        finally:
            old_log.close()
        ready = root / "localization/workspace/editor-updates" / f"ready-{ticket}.json"
        for _ in range(120):
            if ready.is_file():
                break
            if old.poll() is not None:
                raise RuntimeError("old packaged editor exited before startup")
            time.sleep(.25)
        else:
            raise RuntimeError("old packaged editor never opened its local server")
        signal = json.loads(ready.read_text(encoding="utf-8"))
        base = f"http://127.0.0.1:{signal['port']}"
        with urlopen(base + "/", timeout=10) as response:
            html = response.read().decode("utf-8")
        token = re.search(r'<meta name="editor-token" content="([^"]+)">', html).group(1)
        updater = subprocess.Popen(command(script, stage, root, signal["pid"]), cwd=root,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # Let the installer wait on the real PyInstaller child while the old
        # bootloader is still holding the same EXE path.
        request = Request(base + "/api/stop", data=b"{}", headers={
            "Origin": base, "X-Editor-Token": token, "Content-Type": "application/json"})
        with urlopen(request, timeout=10):
            pass
        stdout, stderr = updater.communicate(timeout=150)
        old.wait(timeout=20)
        result = subprocess.CompletedProcess(updater.args, updater.returncode, stdout, stderr)
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
        assert metadata["editor_version"] == "0.7"
        assert metadata["preferences"]["column_widths"] == {"key": 420}
        assert metadata["preferences"]["project_path"] == "C:/Lekmod"
        launcher = re.search(r"launcher_pid: (\d+)", installer_log)
        assert launcher
        subprocess.run(["taskkill", "/PID", launcher.group(1), "/T", "/F"],
                       capture_output=True, text=True, timeout=30)

        stage.mkdir(parents=True)
        failed = subprocess.run(command(script, stage, root, 99999999), cwd=root,
                                capture_output=True, text=True, timeout=120)
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
