"""Keep the shipped primary XML in sync with its editable English source."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET

import audit_primary_localization as primary_audit
from lekmod_localization.common import CatalogError, DEFAULT_SOURCE, REPO_ROOT


DEFAULT_ENGLISH = REPO_ROOT / "localization" / "en_US" / "primary.xml"
BEGIN = "\t<!-- BEGIN GENERATED ENGLISH: localization/en_US/primary.xml -->\n"
END = "\t<!-- END GENERATED ENGLISH -->\n"
LANGUAGE_BLOCK = re.compile(
    r"(?m)^\t<Language_en_US>\s*$.*?^\t</Language_en_US>\s*\n",
    re.DOTALL,
)


def english_block(document: str) -> str:
    """Extract the sole primary language block without changing whitespace."""
    if "<!DOCTYPE" in document.upper():
        raise CatalogError("DOCTYPE is not allowed in localization XML")
    blocks = list(LANGUAGE_BLOCK.finditer(document))
    if len(blocks) != 1:
        raise CatalogError("expected exactly one primary Language_en_US block")
    return blocks[0].group()


def validate_source(document: str) -> str:
    """Reject stray game tables in the editable English-only document."""
    block = english_block(document)
    try:
        root = ET.fromstring(document)
    except ET.ParseError as error:
        raise CatalogError(f"invalid English source XML: {error}") from error
    if root.tag != "GameData" or [child.tag for child in root] != [
        "Language_en_US"
    ]:
        raise CatalogError("English source must contain only Language_en_US")
    return block


def audit_english(path: Path) -> None:
    """Reject invalid source operations before changing the game XML."""
    report = primary_audit.parse_source(path, "en_US")
    if report["summary"]["errors"]:
        issue = next(item for item in report["issues"] if item["severity"] == "error")
        raise CatalogError(f"invalid English operations: {issue['message']}")


def marked_block(document: str) -> tuple[int, int, str]:
    """Locate and validate the generated English section in game XML."""
    if document.count(BEGIN) != 1 or document.count(END) != 1:
        raise CatalogError("generated English markers are missing or duplicated")
    start = document.index(BEGIN) + len(BEGIN)
    end = document.index(END)
    if start >= end:
        raise CatalogError("generated English markers are out of order")
    block = document[start:end]
    if block != english_block(document):
        raise CatalogError("generated section contains text outside its language block")
    return start, end, block


def atomic_text(path: Path, content: str) -> None:
    """Replace a UTF-8 file atomically using a same-directory temporary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix=f".{path.name}.", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def bootstrap(source: Path, game_xml: Path) -> None:
    """One-time, lossless extraction from the existing shipped XML."""
    if source.exists():
        raise CatalogError(f"English source already exists: {source}")
    document = game_xml.read_text(encoding="utf-8")
    if BEGIN in document or END in document:
        raise CatalogError("game XML already contains generated markers")
    block = english_block(document)
    match = LANGUAGE_BLOCK.search(document)
    assert match is not None
    proposed = document[:match.start()] + BEGIN + block + END + document[match.end():]
    source_content = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<GameData>\n" + block + "</GameData>\n"
    )
    validate_source(source_content)
    atomic_text(source, source_content)
    audit_english(source)
    atomic_text(game_xml, proposed)


def synchronize(source: Path, game_xml: Path, *, write: bool) -> bool:
    """Check or update only the marked English block of the game XML."""
    replacement = validate_source(source.read_text(encoding="utf-8"))
    audit_english(source)
    document = game_xml.read_text(encoding="utf-8")
    start, end, current = marked_block(document)
    if current == replacement:
        return False
    if write:
        atomic_text(game_xml, document[:start] + replacement + document[end:])
        return True
    raise CatalogError(
        "shipped English is out of sync; run sync_primary_english.py --write"
    )


def main() -> int:
    """Check, update, or bootstrap the primary English game block."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_ENGLISH)
    parser.add_argument("--game-xml", type=Path, default=DEFAULT_SOURCE)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Update the generated game block")
    mode.add_argument("--bootstrap", action="store_true", help="Extract English once from unmarked game XML")
    args = parser.parse_args()
    try:
        if args.bootstrap:
            bootstrap(args.source, args.game_xml)
            print(f"English source extracted: {args.source}")
        else:
            changed = synchronize(args.source, args.game_xml, write=args.write)
            print("Shipped English updated." if changed else "Shipped English is in sync.")
    except (CatalogError, OSError, UnicodeError) as error:
        parser.exit(1, f"English source sync failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
