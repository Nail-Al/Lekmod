"""Run the generated PowerShell updater on Windows with a disposable editor."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from lekmod_localization.editor_update import UPDATE_FILES, write_windows_updater


def run(script: Path, stage: Path, root: Path) -> subprocess.CompletedProcess:
    """Run an update after its hypothetical old process has already exited."""
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), "-OldPid", "99999999", "-Stage", str(stage),
         "-EditorRoot", str(root)],
        capture_output=True, text=True, timeout=120,
    )


def main() -> int:
    """Check replacement, backup, log, failure, and preservation of private work."""
    if os.name != "nt":
        raise RuntimeError("run this smoke test on a Windows runner")
    harmless_exe = shutil.which("whoami.exe")
    if not harmless_exe:
        raise RuntimeError("Windows whoami.exe is missing")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "Editor With Spaces"
        stage = root / "localization/workspace/editor-updates/editor-v0.4"
        private = root / "localization/workspace/editor-settings.json"
        private.parent.mkdir(parents=True)
        private.write_text("personal settings", encoding="utf-8")
        for name in UPDATE_FILES:
            previous, incoming = root / name, stage / name
            previous.parent.mkdir(parents=True, exist_ok=True)
            incoming.parent.mkdir(parents=True, exist_ok=True)
            if name.endswith(".exe"):
                shutil.copy2(harmless_exe, previous)
                shutil.copy2(harmless_exe, incoming)
            else:
                previous.write_text("old " + name, encoding="utf-8")
                incoming.write_text("new " + name, encoding="utf-8")
        script = write_windows_updater(stage, root)
        result = run(script, stage, root)
        if result.returncode:
            raise RuntimeError("Updater failed: " + result.stdout + result.stderr +
                               (root / "localization/workspace/editor-updates/update.log").read_text(
                                   encoding="utf-8-sig"))
        assert not stage.exists(), "successful update left a pending stage"
        assert (root / "localization/editor/app.js").read_text() == "new localization/editor/app.js"
        backups = list((root / "localization/workspace/editor-updates").glob("previous-editor-*"))
        assert len(backups) == 1
        assert (backups[0] / "localization/editor/app.js").read_text() == "old localization/editor/app.js"
        assert private.read_text() == "personal settings"
        log = (root / "localization/workspace/editor-actions.jsonl").read_text(encoding="utf-8")
        assert json.loads(log.splitlines()[-1])["result"] == "success"

        stage.mkdir(parents=True)
        failed = run(script, stage, root)
        assert failed.returncode != 0, "missing staged files must stop installation"
        assert (root / "localization/editor/app.js").read_text() == "new localization/editor/app.js"
        assert private.read_text() == "personal settings"
        assert json.loads((root / "localization/workspace/editor-actions.jsonl").read_text(
            encoding="utf-8").splitlines()[-1])["result"] == "failure"
        assert "Staged editor file is missing" in (root /
            "localization/workspace/editor-updates/update.log").read_text(encoding="utf-8-sig")
    print("Windows updater replaced editor files, preserved data, and recorded failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
