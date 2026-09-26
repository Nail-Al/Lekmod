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
3. Select a language, translate a row, and click Save and apply. Use
   Download translation ZIP to send your completed language to a developer.
4. To compare original vanilla sentences, ask the team for the matching
   vanilla-snapshot.json.gz and copy it to localization/workspace/ before
   starting the editor. The app checks it against the shared fingerprint.

The standalone package is a translation workspace, not an installable
Civilization V mod. Download test game XML exports only the changed Override
file; use a separate full Lekmod test installation for in-game testing.
"""


def package(executable: Path, output: Path, root: Path = REPO_ROOT) -> int:
    """Create a portable ZIP from an explicit source allowlist."""
    if not executable.is_file():
        raise CatalogError(f"Windows editor executable is missing: {executable}")
    required = [
        Path("LEKMOD/Override/CIV5Units_Mongol.xml"),
        Path("localization/en_US/primary.xml"),
        Path("localization/config.json"),
        Path("localization/README.md"),
        Path("localization/reference/vanilla-fingerprints.json.gz"),
        Path("localization/editor/index.html"),
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
