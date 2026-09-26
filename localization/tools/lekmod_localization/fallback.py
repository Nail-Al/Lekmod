"""Preview English fallback rows for Lekmod changes in other locales."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET

from .common import CatalogError, REPO_ROOT, SOURCE_LOCALE, token_counts


FALLBACK_CLASSES = {"lekmod_new", "vanilla_modified"}


def fallback_entries(catalog: dict) -> dict[str, dict[str, str]]:
    """Select only new/changed English texts, never unchanged vanilla text."""
    selected = {}
    for subcategories in catalog["source_categories"].values():
        for entries in subcategories.values():
            for key, entry in entries.items():
                if entry["classification"] not in FALLBACK_CLASSES:
                    continue
                english = entry["lekmod_en_US"]
                value = english.get("text")
                if english["status"] != "present" or not isinstance(value, str):
                    raise CatalogError(f"no usable English fallback text for {key}")
                if token_counts(value) != english["format_tokens"]:
                    raise CatalogError(f"English formatting tokens changed for {key}")
                selected[key] = {"Text": value}
                for field in ("Gender", "Plurality"):
                    metadata = english.get(field.lower())
                    if metadata is not None:
                        selected[key][field] = str(metadata)
    expected = catalog["summary"]["requires_translation"]
    if len(selected) != expected:
        raise CatalogError(
            f"English fallback count {len(selected)} differs from catalog {expected}"
        )
    return dict(sorted(selected.items()))


def render_locale(locale: str, entries: dict[str, dict[str, str]]) -> bytes:
    """Create a standalone review XML; runtime ordering is not assumed."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", locale):
        raise CatalogError(f"invalid locale name: {locale}")
    game = ET.Element("GameData")
    language = ET.SubElement(game, f"Language_{locale}")
    for key, fields in entries.items():
        row = ET.SubElement(language, "Replace", {"Tag": key})
        for name in ("Text", "Gender", "Plurality"):
            if name in fields:
                ET.SubElement(row, name).text = fields[name]
    ET.indent(game, space="  ")
    rendered = ET.tostring(game, encoding="utf-8", xml_declaration=True) + b"\n"
    try:
        ET.fromstring(rendered)
    except ET.ParseError as error:
        raise CatalogError(f"invalid fallback XML for {locale}: {error}") from error
    return rendered


def preview_files(catalog: dict, locales: list[str]) -> dict[str, bytes]:
    """Render each review locale and a checksum manifest in memory."""
    entries = fallback_entries(catalog)
    target_locales = sorted({
        locale for locale in locales
        if locale.casefold() != SOURCE_LOCALE.casefold()
    })
    if not target_locales:
        raise CatalogError("no non-English locales to preview")
    if len({locale.casefold() for locale in target_locales}) != len(target_locales):
        raise CatalogError("duplicate locale names with different casing")

    files = {
        f"{locale}.xml": render_locale(locale, entries)
        for locale in target_locales
    }
    manifest = {
        "schema_version": 1,
        "status": "review_only_not_installed",
        "source_sha256": catalog["source"]["sha256"],
        "vanilla_english_sha256": catalog["vanilla"]["content_sha256"],
        "entries_per_locale": len(entries),
        "empty_english_text_keys": [
            key for key, fields in entries.items() if fields["Text"] == ""
        ],
        "withheld_conflicts": catalog["summary"]["requires_source_review"],
        "locales": target_locales,
        "files_sha256": {
            name: hashlib.sha256(contents).hexdigest()
            for name, contents in sorted(files.items())
        },
    }
    files["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    return files


def write_preview(
    destination: Path,
    files: dict[str, bytes],
    snapshot: Path,
    root: Path = REPO_ROOT,
) -> None:
    """Replace only generated review output, never a game or input directory."""
    destination = destination.resolve()
    if not destination.is_relative_to((root / "localization" / "workspace").resolve()):
        raise CatalogError("fallback preview must stay under localization/workspace")
    if snapshot.resolve().is_relative_to(destination):
        raise CatalogError("fallback preview cannot contain the vanilla snapshot")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".fallback.", dir=destination.parent))
    backup: Path | None = None
    try:
        for name, contents in files.items():
            (temporary / name).write_bytes(contents)
        if destination.exists():
            manifest_path = destination / "manifest.json"
            if not manifest_path.is_file():
                raise CatalogError("existing fallback preview has no manifest")
            try:
                previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise CatalogError("invalid existing fallback manifest") from error
            expected = (
                previous.get("files_sha256", {})
                if isinstance(previous, dict) else None
            )
            actual = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in destination.iterdir()
                if path.name != "manifest.json" and path.is_file()
            }
            if not isinstance(expected, dict) or actual != expected or (
                len(list(destination.iterdir())) != len(expected) + 1
            ):
                raise CatalogError("existing fallback preview was manually changed")
            backup = Path(tempfile.mkdtemp(prefix=".fallback.backup.", dir=destination.parent))
            backup.rmdir()
            destination.replace(backup)
        try:
            temporary.replace(destination)
        except OSError:
            if backup is not None and backup.exists():
                backup.replace(destination)
                backup = None
            raise
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
