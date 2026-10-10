"""Assemble a translator download with only the files the editor reads."""

from __future__ import annotations

import argparse
from pathlib import Path
import zipfile

from lekmod_localization.common import CatalogError, REPO_ROOT


START = """Lekmod Localization Editor for Windows

1. Extract the complete ZIP into a new writable folder. Keep the
   localization folder next to LekmodLocalizationEditor.exe.
2. Double-click LekmodLocalizationEditor.exe. The browser editor opens
   without a command window. No Python, Git, VS Code, or game install is
   needed to open it.
3. In Settings, select a complete compatible Lekmod project folder or click
   Download. Progress and destination are shown. A plain Civilization V installation is not a
   project. Save connections, then select a language and translate.
   Text auto-saves locally. Save (Ctrl+S) keeps up to 50 saved versions;
   the clock beside Save opens the list to load one. Trash restores the
   latest Save across every language and both modes. Undo/redo uses
   Ctrl+Z/Ctrl+Y with up to 100 local steps and keeps Save separate.
   The Apply arrow menu chooses All, Project or Game; the last choice stays
   on the main button. Project and Game can be updated independently.
   Correct drafts apply even if other rows need review. Troubleshoot lists
   those rows and exact token/source errors. Rejected rows keep their last
   installed Game version. Intentional differences can be reviewed with
   Accept formatting; approval is bound to exact translation and English.
   Retry can still send valid Project changes to
   Game. Search runs with Enter or its button; the X clears it.
4. For in-game testing, connect to the Civilization V installation in
   Settings and click Find / verify game, then close Civilization V and
   choose Apply to installed game. One matching Lekmod DLC must be installed;
   the XML is backed up. If the game is running, close it and choose Retry.
   Game-only Apply keeps source files unchanged; Synced to compares each row
   with the project and game independently.
   Install the matching complete Lekmod release separately first.
5. To compare original vanilla sentences in all ten languages, expand
   Settings > Vanilla reference. Keep the prefilled encrypted link, enter
   the team's password and click Download, decrypt and verify. The app
   keeps the verified local copy for future launches, without saving the
   password.
6. For future editor versions, use Settings > Editor updates > Check latest
   version > Download and update. The wrench button checks app files against
   the published release; Fix version repairs missing or changed app files.
   The app shows download progress and reloads this tab after checking the
   new EXE. Local drafts, applied translations, settings, projects,
   column order/widths and the snapshot remain.
   A legacy EXE with a broken updater can be repaired once from a current
   project checkout with localization/tools/repair_editor.py --editor-root
   "<folder containing your old LekmodLocalizationEditor.exe>". Close the
   old editor first. The repair command downloads the release itself.

The standalone package is a translation workspace, not an installable
Civilization V mod. To stop the background editor, use Settings > Quit editor.

Illustrated user manual:
https://github.com/Nail-Al/Lekmod/blob/localization-infrastructure/localization/docs/LLE-manual.md

IDE and terminal commands:
https://github.com/Nail-Al/Lekmod/blob/localization-infrastructure/localization/docs/terminal-guide.md
"""


def package(executable: Path, output: Path, root: Path = REPO_ROOT) -> int:
    """Create a portable ZIP from an explicit source allowlist."""
    if not executable.is_file():
        raise CatalogError(f"Windows editor executable is missing: {executable}")
    required = [
        Path("localization/README.md"),
        Path("localization/editor/index.html"),
        Path("localization/editor/app.js"),
        Path("localization/editor/version.json"),
        Path("LekmodInstaller/github_setup/versions.json"),
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        archive.write(executable, "LekmodLocalizationEditor.exe")
        archive.writestr("README-START.txt", START)
        for relative in required:
            path = root / relative
            if not path.is_file():
                raise CatalogError(f"required editor input is missing: {path}")
            archive.write(path, relative.as_posix())
    return len(required)


def main() -> int:
    """Build a Windows Actions download after PyInstaller has created it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        count = package(args.exe, args.output)
    except (CatalogError, OSError) as error:
        parser.exit(1, f"Portable editor packaging failed: {error}\n")
    print(f"Portable editor: {args.output} ({count} project files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
