"""Assemble a translator download with only the files the editor reads."""

from __future__ import annotations

import argparse
from pathlib import Path
import zipfile

from lekmod_localization.common import CatalogError, REPO_ROOT


START = """Lekmod Localization Editor for Windows

1. Extract the complete ZIP into a new folder. Keep the LEKMOD and
   localization folders next to LekmodLocalizationEditor.exe.
2. Double-click LekmodLocalizationEditor.exe. Leave its window open while
   the browser editor is running. No Python, Git, VS Code, or game install
   is needed to start translating new and changed Lekmod text.
3. On the first run, Settings opens. Use the included source or select a full
   compatible Lekmod project folder. Select your language, translate a row,
   and click Save and apply. Exports can prepare a ZIP for a developer.
4. For in-game testing, connect to the Civilization V installation in
   Settings, select its installed Lekmod DLC, and click Apply to installed
   game after saving. This checks versions and backs up the existing XML.
   Install the matching complete Lekmod release separately first.
5. To compare original vanilla sentences, ask the team for the matching
   vanilla-snapshot.json.gz and import it in Settings. The app checks it
   against the pinned shared fingerprints.

The standalone package is a translation workspace, not an installable
Civilization V mod. Close both the browser tab and console when finished.
"""


def package(executable: Path, output: Path, root: Path = REPO_ROOT) -> int:
    """Create a portable ZIP from an explicit source allowlist."""
    if not executable.is_file():
        raise CatalogError(f"Windows editor executable is missing: {executable}")
    required = [
        Path("LEKMOD/VERSION"),
        Path("LEKMOD/Override/CIV5Units_Mongol.xml"),
        Path("localization/en_US/primary.xml"),
        Path("localization/config.json"),
        Path("localization/README.md"),
        Path("localization/reference/vanilla-fingerprints.json.gz"),
        Path("localization/editor/index.html"),
        Path("localization/editor/app.js"),
        Path("LekmodInstaller/github_setup/versions.json"),
    ]
    required.extend(sorted(Path("LEKMOD/Art") / path.relative_to(root / "LEKMOD/Art")
                           for path in (root / "LEKMOD/Art").rglob("*")
                           if path.is_file() and path.suffix.casefold() in {".xml", ".sql"}))
    required.extend(sorted(Path("localization/translations") / path.name
                           for path in (root / "localization/translations").glob("*.csv")))
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
