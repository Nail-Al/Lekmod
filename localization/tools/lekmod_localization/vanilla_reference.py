"""Pin vanilla language content without publishing the game's actual text."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import re
import tempfile

from .common import CatalogError, REPO_ROOT, normalize_metadata, normalize_text
from .vanilla_snapshot import read_snapshot


DEFAULT_REFERENCE = REPO_ROOT / "localization" / "reference" / "vanilla-fingerprints.json.gz"
HEX = re.compile(r"^[0-9a-f]{64}$")
# This reviewed extension keeps all English field hashes and the Russian
# digest; it fills only the eight formerly empty locales. Unknown baselines
# remain incompatible, including references with changed game text.
COMPATIBLE_REFERENCE_EXTENSIONS = {
    ("5b10d7361ea52b8c6613d6679a54c9d1174c3a30abad86c1485cbdf908a0bd17",
     "c886466d7d6c75eb4011e9575c64375e8acb17b7843bf4e0a359106fc298d2e2"),
}


def compatible_reference_digest(previous: object, current: bytes) -> bool:
    """Accept exact reference bytes or a specifically reviewed forward extension."""
    if not isinstance(previous, str):
        return False
    digest = hashlib.sha256(current).hexdigest()
    return previous == digest or (previous, digest) in COMPATIBLE_REFERENCE_EXTENSIONS


def adopt_reference_extension(project: Path, bundled: Path) -> bool:
    """Upgrade only the known old project index, keeping its exact private backup."""
    target = project / "localization/reference/vanilla-fingerprints.json.gz"
    if not bundled.is_file() or not target.is_file():
        return False
    previous, current = target.read_bytes(), bundled.read_bytes()
    digest = hashlib.sha256(previous).hexdigest()
    if previous == current or not compatible_reference_digest(digest, current):
        return False
    read_reference(bundled)
    backup = project / "localization/workspace/reference-backups" / (digest + ".json.gz")
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        if backup.read_bytes() != previous:
            raise CatalogError("the previous vanilla reference backup differs; stop and review it")
    else:
        with backup.open("xb") as handle:
            handle.write(previous)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".reference.",
                                     delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(current)
    try:
        if target.read_bytes() != previous:
            raise CatalogError("the vanilla reference changed during migration; retry after reviewing it")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def value_hash(value: object, *, metadata: bool = False) -> str | None:
    """Hash one normalized field; retain the difference between empty and absent."""
    normalized = normalize_metadata(value) if metadata else normalize_text(value)
    if normalized is None:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def reference_from_snapshot(snapshot: Path) -> dict:
    """Extract stable English field hashes and every locale's content digest."""
    locales, fingerprints = read_snapshot(snapshot)
    english = next(locale for locale in locales if locale.casefold() == "en_us")
    return {
        "schema_version": 1,
        "locales": dict(sorted(fingerprints.items())),
        "english": {
            key: {
                "Text": value_hash(fields.get("Text")),
                "Gender": value_hash(fields.get("Gender"), metadata=True),
                "Plurality": value_hash(fields.get("Plurality"), metadata=True),
            }
            for key, fields in sorted(locales[english].items())
        },
    }


def write_reference(snapshot: Path, destination: Path = DEFAULT_REFERENCE) -> None:
    """Create a reproducible, text-free index for review and CI."""
    content = json.dumps(reference_from_snapshot(snapshot), sort_keys=True, separators=(",", ":"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(gzip.compress(content.encode("utf-8"), mtime=0))


def read_reference(path: Path = DEFAULT_REFERENCE) -> dict:
    """Reject malformed or incomplete reference data before comparing text."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, ValueError, EOFError) as error:
        raise CatalogError(f"cannot read pinned vanilla reference: {error}") from error
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "locales", "english"} or payload["schema_version"] != 1:
        raise CatalogError("unsupported vanilla reference")
    locales, english = payload["locales"], payload["english"]
    if not isinstance(locales, dict) or "en_US" not in locales or not isinstance(english, dict):
        raise CatalogError("vanilla reference has no English index")
    if not all(isinstance(value, str) and HEX.fullmatch(value) for value in locales.values()):
        raise CatalogError("invalid vanilla locale fingerprints")
    if not all(isinstance(key, str) and isinstance(fields, dict)
               and set(fields) == {"Text", "Gender", "Plurality"}
               and all(value is None or isinstance(value, str) and HEX.fullmatch(value)
                       for value in fields.values())
               for key, fields in english.items()):
        raise CatalogError("invalid vanilla English fingerprints")
    return payload


def verify_snapshot_reference(snapshot: Path, reference_path: Path = DEFAULT_REFERENCE) -> None:
    """Refuse a different game build or mixed language tables on any machine."""
    _, fingerprints = read_snapshot(snapshot)
    reference = read_reference(reference_path)
    if fingerprints != reference["locales"]:
        raise CatalogError(
            "local vanilla snapshot differs from the pinned team reference; "
            "do not rebuild game XML from this installation"
        )
