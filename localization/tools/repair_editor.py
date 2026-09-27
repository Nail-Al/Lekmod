"""Repair a legacy Windows editor in place without downloading a ZIP manually.

This is a one-time bootstrap for executables whose built-in updater cannot be
changed retroactively. Close the old editor before running it from a fresh
localization project checkout. Subsequent releases update in the editor UI.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

from lekmod_localization.connections import editor_manifest
from lekmod_localization.editor_update import latest_release, stage_release, write_windows_updater


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
        current = editor_manifest(root)["version"]
        release = latest_release(root)
        if not release["available"]:
            print(f"Editor v{current} is already current; no repair needed.")
            return 0
        print(f"Downloading and verifying editor v{release['latest']} for {root}...", flush=True)
        release["can_auto_update"] = True
        stage = stage_release(release, root)
        script = write_windows_updater(stage, root)
        result = subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-OldPid", "99999999", "-Stage", str(stage),
            "-EditorRoot", str(root),
        ], cwd=root, env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
            capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or
                               "installation failed; see localization/workspace/editor-updates/update.log")
        print(f"Editor v{release['latest']} installed and started. Settings and projects remain in {root}.")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, "Editor repair failed: " + str(error) + "\nClose the old editor and "
                    "retry. Your workspace and previous EXE are preserved.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
