"""Capture one official language at a time and propose a single team snapshot.

Use a clean Civ V cache. The English table must match the pinned team baseline
at every capture, so a changed game build cannot silently mix translations.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from lekmod_localization.common import CatalogError, REPO_ROOT
from lekmod_localization.sources import entries_fingerprint, load_vanilla_locales
from lekmod_localization.vanilla_reference import (
    read_reference, verify_snapshot_reference, write_reference,
)
from lekmod_localization.vanilla_snapshot import read_snapshot


WORKSPACE = REPO_ROOT / "localization/workspace"
REFERENCE = REPO_ROOT / "localization/reference/vanilla-fingerprints.json.gz"


def stable_json(data: dict) -> bytes:
    """Produce portable bytes with reproducible gzip timestamps."""
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return gzip.compress(encoded, mtime=0)


def save_new(path: Path, content: bytes) -> None:
    """Never replace an existing capture or a pinned team file by accident."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".capture-",
                                     delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.link(temporary, path)
    except FileExistsError as error:
        raise CatalogError(f"output already exists: {path}") from error
    finally:
        temporary.unlink(missing_ok=True)


def read_consistent_database(database: Path) -> tuple[dict, dict, str]:
    """Back up the game cache to freeze one coherent SQLite read transaction."""
    if not database.is_file():
        raise CatalogError(f"game cache database not found: {database}")
    with tempfile.TemporaryDirectory() as temporary:
        copy = Path(temporary) / "Localization-Merged.db"
        source = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        try:
            with closing(sqlite3.connect(copy)) as target:
                source.backup(target)
        finally:
            source.close()
        locales, fingerprints = load_vanilla_locales(copy)
        digest = hashlib.sha256(copy.read_bytes()).hexdigest()
    return locales, fingerprints, digest


def capture_locale(database: Path, locale: str, base: Path, destination: Path,
                   minimum_rows: int = 1000, reference: Path = REFERENCE) -> int:
    """Keep only the selected nonempty language and proof of the English baseline."""
    verify_snapshot_reference(base, reference)
    baseline, baseline_fingerprints = read_snapshot(base)
    canonical = next((name for name in baseline if name.casefold() == locale.casefold()), None)
    if canonical is None or canonical.casefold() == "en_us":
        raise CatalogError(f"unknown target locale: {locale}")
    locales, fingerprints, digest = read_consistent_database(database)
    english = next((name for name in locales if name.casefold() == "en_us"), None)
    if english is None or len(locales[english]) < minimum_rows or (
        fingerprints[english] != baseline_fingerprints["en_US"]
    ):
        raise CatalogError("English game text is missing or differs from the pinned team baseline; "
                           "use the same clean game build as the existing snapshot")
    selected = next((name for name in locales if name.casefold() == canonical.casefold()), None)
    if selected is None or len(locales[selected]) < minimum_rows:
        raise CatalogError(f"{canonical} has fewer than {minimum_rows} rows in this cache. "
                           "Switch Steam language, launch the unmodded game, then capture again")
    if baseline[canonical] and fingerprints[selected] != baseline_fingerprints[canonical]:
        raise CatalogError(f"{canonical} differs from the already pinned team translation")
    payload = {"schema_version": 1, "locale": canonical,
               "entries": locales[selected], "fingerprint": fingerprints[selected],
               "english_fingerprint": fingerprints[english],
               "database_sha256": digest}
    save_new(destination, stable_json(payload))
    return len(locales[selected])


def read_capture(path: Path) -> dict:
    """Reject malformed or modified captures before combining any languages."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, UnicodeError, ValueError, EOFError) as error:
        raise CatalogError(f"cannot read capture {path}: {error}") from error
    if (not isinstance(payload, dict) or set(payload) != {
        "schema_version", "locale", "entries", "fingerprint",
        "english_fingerprint", "database_sha256"} or payload["schema_version"] != 1 or
        not isinstance(payload["locale"], str) or
        not isinstance(payload["entries"], dict) or not all(
            isinstance(key, str) and isinstance(fields, dict)
            for key, fields in payload["entries"].items()) or
        not isinstance(payload["fingerprint"], str) or
        entries_fingerprint(payload["entries"]) != payload["fingerprint"]):
        raise CatalogError(f"invalid locale capture: {path}")
    return payload


def merge_captures(base: Path, captures: list[Path], output: Path, reference_output: Path,
                   minimum_rows: int = 1000, reference: Path = REFERENCE) -> dict[str, int]:
    """Fill only empty languages while preserving every existing team's text."""
    if not captures:
        raise CatalogError("no language captures were supplied")
    verify_snapshot_reference(base, reference)
    original, baseline_hashes = read_snapshot(base)
    english_hash = baseline_hashes["en_US"]
    combined = dict(original)
    hashes = dict(baseline_hashes)
    provenance = {}
    seen = set()
    for path in captures:
        item = read_capture(path)
        locale = item["locale"]
        if locale not in combined or locale == "en_US" or locale in seen:
            raise CatalogError(f"unknown or duplicate captured language: {locale}")
        seen.add(locale)
        if item["english_fingerprint"] != english_hash:
            raise CatalogError(f"English baseline differs in {locale}; do not combine builds")
        if len(item["entries"]) < minimum_rows:
            raise CatalogError(f"{locale} has too few rows for a complete reference")
        if combined[locale] and hashes[locale] != item["fingerprint"]:
            raise CatalogError(f"{locale} conflicts with the current pinned reference")
        combined[locale] = item["entries"]
        hashes[locale] = item["fingerprint"]
        provenance[locale] = item["database_sha256"]
    missing = [name for name, entries in combined.items() if not entries]
    if missing:
        raise CatalogError("still missing languages: " + ", ".join(sorted(missing)) +
                           "; collect these before proposing a complete team baseline")
    payload = {"schema_version": 1, "source_database": "reviewed per-language captures",
               "capture_database_sha256": provenance,
               "fingerprints": dict(sorted(hashes.items())),
               "locales": dict(sorted(combined.items()))}
    if output.exists() or reference_output.exists():
        raise CatalogError("proposal output already exists; choose new output paths")
    save_new(output, stable_json(payload))
    try:
        read_snapshot(output)
        write_reference(output, reference_output)
    except Exception:
        output.unlink(missing_ok=True)
        reference_output.unlink(missing_ok=True)
        raise
    return {locale: len(entries) for locale, entries in combined.items()}


def main() -> int:
    """Inspect a game cache, capture one selected language, or merge all captures."""
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    inspect = actions.add_parser("inspect", help="Show populated language tables in one cache")
    inspect.add_argument("--vanilla-db", required=True, type=Path)
    capture = actions.add_parser("capture", help="Save one language without replacing the team baseline")
    capture.add_argument("--vanilla-db", required=True, type=Path)
    capture.add_argument("--locale", required=True)
    capture.add_argument("--base", type=Path, default=WORKSPACE / "vanilla-snapshot.json.gz")
    capture.add_argument("--capture-dir", type=Path, default=WORKSPACE / "vanilla-captures")
    merge = actions.add_parser("merge", help="Propose one complete multilingual snapshot")
    merge.add_argument("--base", type=Path, default=WORKSPACE / "vanilla-snapshot.json.gz")
    merge.add_argument("--capture-dir", type=Path, default=WORKSPACE / "vanilla-captures")
    merge.add_argument("--output", type=Path, default=WORKSPACE / "vanilla-snapshot-proposed.json.gz")
    merge.add_argument("--reference-output", type=Path,
                       default=WORKSPACE / "vanilla-fingerprints-proposed.json.gz")
    args = parser.parse_args()
    try:
        if args.action == "inspect":
            locales, fingerprints, _ = read_consistent_database(args.vanilla_db)
            pinned = read_reference(REFERENCE)["locales"]
            for name in sorted(pinned):
                current = next((value for key, value in locales.items()
                                if key.casefold() == name.casefold()), {})
                fingerprint = next((value for key, value in fingerprints.items()
                                    if key.casefold() == name.casefold()), None)
                match = fingerprint == pinned[name] if current else False
                print(f"{name}: {len(current)} rows" + (" · matches baseline" if match else ""))
        elif args.action == "capture":
            destination = args.capture_dir / (args.locale.upper() + ".json.gz")
            count = capture_locale(args.vanilla_db, args.locale, args.base, destination)
            print(f"Captured {args.locale.upper()}: {count} rows at {destination}")
        else:
            paths = sorted(args.capture_dir.glob("*.json.gz"))
            counts = merge_captures(args.base, paths, args.output, args.reference_output)
            print("Proposed reference: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
            print(f"Full snapshot: {args.output}\nText-free index: {args.reference_output}")
    except (CatalogError, OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f"Vanilla collection failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
