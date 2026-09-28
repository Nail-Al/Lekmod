"""Repair a Windows editor in place without downloading a ZIP manually.

Use this when the executable's UI is damaged or the built-in updater cannot be
changed retroactively. Close the old editor before running it from a current
localization project checkout. Subsequent releases update in the editor UI.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

from lekmod_localization.connections import editor_manifest
from lekmod_localization.editor_update import (
    latest_release, stage_release, installer_command, verify_installation,
)


def main() -> int:
    """Use the same verified download, backup, rollback, and startup handshake as the UI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor-root", required=True, type=Path,
                        help="Folder containing the old LekmodLocalizationEditor.exe")
    args = parser.parse_args()
    root = args.editor_root.expanduser().resolve()
    if sys.platform != "win32":
        parser.error("this recovery command runs only on Windows")
    if not (root / "LekmodLocalizationEditor.exe").is_file():
        parser.error("select the folder containing LekmodLocalizationEditor.exe")
    try:
        try:
            current = editor_manifest(root)["version"]
        except (OSError, ValueError):
            current = None
        release = latest_release(root) if current else latest_release()
        integrity = verify_installation(root) if current else None
        repair = not integrity or bool(integrity["damaged_files"])
        if not release["available"] and not repair:
            print(f"Editor v{current} is current and its files are verified.")
            return 0
        print(f"Downloading and verifying editor v{release['latest']} for {root}...", flush=True)
        release["can_auto_update"] = True
        stage = stage_release(release, root, repair=repair)
        result = subprocess.run(installer_command(stage, root, 0, repair=repair), cwd=stage,
            env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=240,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            log = root / "localization/workspace/editor-updates/update.log"
            detail = log.read_text(encoding="utf-8")[-2000:] if log.is_file() else "no update log"
            raise RuntimeError("installation failed; " + detail)
        print(f"Editor v{release['latest']} installed and started. Settings and projects remain in {root}.")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, "Editor repair failed: " + str(error) + "\nClose the old editor and "
                    "retry. Your workspace and previous EXE are preserved.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
