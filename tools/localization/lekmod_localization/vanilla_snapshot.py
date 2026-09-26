"""Freeze a verified local vanilla database for repeatable review builds."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .common import CatalogError, SOURCE_LOCALE
from .sources import LEKMOD_SENTINELS, entries_fingerprint, load_vanilla_locales


SCHEMA_VERSION = 1


def snapshot_bytes(database_path: Path) -> bytes:
    """Serialize all official language tables in a stable, portable form."""
    locales, fingerprints = load_vanilla_locales(database_path)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_database": database_path.name,
        "source_database_sha256": hashlib.sha256(database_path.read_bytes()).hexdigest(),
        "fingerprints": dict(sorted(fingerprints.items())),
        "locales": {
            locale: entries
            for locale, entries in sorted(locales.items())
        },
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return gzip.compress(encoded, mtime=0)


def write_snapshot(database_path: Path, output: Path) -> None:
    """Create once, refusing to overwrite a frozen reference."""
    if output.exists():
        raise CatalogError(f"snapshot already exists: {output}")
    if output.resolve() == database_path.resolve():
        raise CatalogError("snapshot cannot overwrite the game database")
    payload = snapshot_bytes(database_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output.parent, prefix=f".{output.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        try:
            os.link(temporary, output)
        except FileExistsError as error:
            raise CatalogError(f"snapshot appeared while writing: {output}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_snapshot(
    path: Path,
) -> tuple[dict[str, dict[str, dict[str, str | None]]], dict[str, str]]:
    """Recheck content hashes and locale shapes before using a frozen copy."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError, EOFError) as error:
        raise CatalogError(f"cannot read vanilla snapshot: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise CatalogError("unsupported vanilla snapshot schema")
    locales = payload.get("locales")
    fingerprints = payload.get("fingerprints")
    if not isinstance(locales, dict) or not isinstance(fingerprints, dict):
        raise CatalogError("vanilla snapshot has no locale index")
    if set(locales) != set(fingerprints) or not any(
        locale.casefold() == SOURCE_LOCALE.casefold() for locale in locales
    ):
        raise CatalogError("vanilla snapshot locales or English source are missing")
    if len({locale.casefold() for locale in locales}) != len(locales):
        raise CatalogError("vanilla snapshot has duplicate locale names")
    for locale, entries in locales.items():
        if not isinstance(entries, dict) or not all(
            isinstance(key, str) and isinstance(fields, dict)
            for key, fields in entries.items()
        ):
            raise CatalogError(f"invalid vanilla snapshot entries: {locale}")
        if LEKMOD_SENTINELS & set(entries):
            raise CatalogError(f"vanilla snapshot contains Lekmod keys: {locale}")
        if not isinstance(fingerprints[locale], str) or (
            entries_fingerprint(entries) != fingerprints[locale]
        ):
            raise CatalogError(f"vanilla snapshot fingerprint mismatch: {locale}")
    return locales, fingerprints


def verify_snapshot(database_path: Path, snapshot_path: Path) -> None:
    """Detect differences even when two SQLite files have different layouts."""
    current, fingerprints = load_vanilla_locales(database_path)
    frozen, frozen_fingerprints = read_snapshot(snapshot_path)
    if current != frozen or fingerprints != frozen_fingerprints:
        raise CatalogError("game database differs from the frozen vanilla snapshot")
