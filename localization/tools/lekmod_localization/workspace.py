"""Create JSON reports and editable CSV workspaces atomically."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from .common import (
    CatalogError,
    REPO_ROOT,
    character_count,
    normalize_metadata,
    normalize_text,
)


EDITOR_FIELDNAMES = (
    "key",
    "category",
    "subcategory",
    "classification",
    "english_change",
    "vanilla_en_US_status",
    "vanilla_en_US",
    "vanilla_en_US_characters",
    "vanilla_en_US_gender",
    "vanilla_en_US_plurality",
    "vanilla_target_status",
    "vanilla_target",
    "vanilla_target_characters",
    "vanilla_target_gender",
    "vanilla_target_plurality",
    "lekmod_en_US",
    "lekmod_en_US_characters",
    "lekmod_en_US_gender",
    "lekmod_en_US_plurality",
    "lekmod_sources",
    "lekmod_target_status",
    "lekmod_target",
    "lekmod_target_characters",
    "lekmod_target_gender",
    "lekmod_target_plurality",
    "target_change",
    "required_format_tokens",
    "source_fingerprint",
    "translation_source_fingerprint",
    "translation",
    "translation_characters",
    "translation_gender",
    "translation_plurality",
    "translation_status",
    "translator_note",
)
EDITOR_TRANSLATION_FIELDS = (
    "translation",
    "translation_gender",
    "translation_plurality",
)
EDITOR_EDITABLE_FIELDS = (
    *EDITOR_TRANSLATION_FIELDS,
    "translator_note",
)


def editor_source_fingerprint(
    key: str,
    entry: dict[str, object],
) -> str:
    """Pin a CSV translation to the exact English source fields."""
    english = entry["lekmod_en_US"]
    identity = {
        "key": key,
        "classification": entry["classification"],
        "text": english.get("text"),
        "gender": english.get("gender"),
        "plurality": english.get("plurality"),
        "format_tokens": english.get("format_tokens", {}),
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def target_change(entry: dict[str, object]) -> str:
    """Describe whether existing target text differs from vanilla."""
    target = entry["lekmod_target"]
    status = target["status"]
    if status != "present":
        return str(status)

    if normalize_text(target.get("text")) == normalize_text(
        entry["lekmod_en_US"].get("text")
    ):
        return "same_as_english"

    official = entry["official_game"]
    if official["status"] != "present":
        return "new_in_lekmod"
    if normalize_text(target.get("text")) == normalize_text(
        official.get("text")
    ):
        return "same_as_vanilla"
    return "modified_from_vanilla"


def translation_status(
    translation: str,
    gender: str,
    plurality: str,
    target: dict[str, object],
) -> str:
    """Label an editor translation as missing, current, or draft."""
    if not translation:
        return "missing"
    if target["status"] != "present":
        return "draft"

    matches_current = (
        normalize_text(translation)
        == normalize_text(target.get("text"))
        and normalize_metadata(gender)
        == normalize_metadata(target.get("gender"))
        and normalize_metadata(plurality)
        == normalize_metadata(target.get("plurality"))
    )
    return "current" if matches_current else "draft"


def csv_value(value: object) -> str:
    """Serialize absent values as empty CSV cells."""
    return "" if value is None else str(value)


def build_editor_rows(
    reviews: dict[
        str, tuple[dict[str, dict], dict[str, object]]
    ],
) -> dict[str, dict[str, list[dict[str, object]]]]:
    """Convert per-locale review entries into translator CSV rows."""
    workspace: dict[
        str, dict[str, list[dict[str, object]]]
    ] = {}

    for locale, (documents, _manifest) in sorted(reviews.items()):
        locale_documents = {}

        for category, document in sorted(documents.items()):
            rows = []

            for subcategory, entries in sorted(
                document["subcategories"].items()
            ):
                for key, entry in sorted(entries.items()):
                    vanilla_english = entry["official_game_en_US"]
                    vanilla_target = entry["official_game"]
                    english = entry["lekmod_en_US"]
                    target = entry["lekmod_target"]
                    target_is_present = target["status"] == "present"
                    translation = (
                        csv_value(target.get("text"))
                        if target_is_present
                        else ""
                    )
                    translation_gender = (
                        csv_value(target.get("gender"))
                        if target_is_present
                        else ""
                    )
                    translation_plurality = (
                        csv_value(target.get("plurality"))
                        if target_is_present
                        else ""
                    )
                    classification = str(entry["classification"])
                    english_change = (
                        "new_in_lekmod"
                        if classification == "lekmod_new"
                        else "modified_from_vanilla"
                    )

                    rows.append({
                        "key": key,
                        "category": category,
                        "subcategory": subcategory,
                        "classification": classification,
                        "english_change": english_change,
                        "vanilla_en_US_status": (
                            vanilla_english["status"]
                        ),
                        "vanilla_en_US": csv_value(
                            vanilla_english.get("text")
                        ),
                        "vanilla_en_US_characters": csv_value(
                            vanilla_english.get("characters")
                        ),
                        "vanilla_en_US_gender": csv_value(
                            vanilla_english.get("gender")
                        ),
                        "vanilla_en_US_plurality": csv_value(
                            vanilla_english.get("plurality")
                        ),
                        "vanilla_target_status": (
                            vanilla_target["status"]
                        ),
                        "vanilla_target": csv_value(
                            vanilla_target.get("text")
                        ),
                        "vanilla_target_characters": csv_value(
                            vanilla_target.get("characters")
                        ),
                        "vanilla_target_gender": csv_value(
                            vanilla_target.get("gender")
                        ),
                        "vanilla_target_plurality": csv_value(
                            vanilla_target.get("plurality")
                        ),
                        "lekmod_en_US": csv_value(
                            english.get("text")
                        ),
                        "lekmod_en_US_characters": csv_value(
                            english.get("characters")
                        ),
                        "lekmod_en_US_gender": csv_value(
                            english.get("gender")
                        ),
                        "lekmod_en_US_plurality": csv_value(
                            english.get("plurality")
                        ),
                        "lekmod_sources": json.dumps(
                            english.get("sources", []),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "lekmod_target_status": target["status"],
                        "lekmod_target": csv_value(
                            target.get("text")
                        ),
                        "lekmod_target_characters": csv_value(
                            target.get("characters")
                        ),
                        "lekmod_target_gender": csv_value(
                            target.get("gender")
                        ),
                        "lekmod_target_plurality": csv_value(
                            target.get("plurality")
                        ),
                        "target_change": target_change(entry),
                        "required_format_tokens": json.dumps(
                            english.get("format_tokens", {}),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        "source_fingerprint": (
                            editor_source_fingerprint(key, entry)
                        ),
                        "translation_source_fingerprint": (
                            editor_source_fingerprint(key, entry)
                        ),
                        "translation": translation,
                        "translation_characters": (
                            character_count(translation)
                            if translation
                            else ""
                        ),
                        "translation_gender": translation_gender,
                        "translation_plurality": (
                            translation_plurality
                        ),
                        "translation_status": translation_status(
                            translation,
                            translation_gender,
                            translation_plurality,
                            target,
                        ),
                        "translator_note": "",
                    })

            locale_documents[category] = rows

        workspace[locale] = locale_documents

    return workspace


def load_editor_edits(
    destination: Path,
) -> dict[tuple[str, str], dict[str, str]]:
    """Read editable CSV fields while checking the prior manifest."""
    if not destination.exists():
        return {}
    if not destination.is_dir():
        raise CatalogError(
            f"editor workspace is not a directory: {destination}"
        )

    manifest_path = destination / "manifest.json"
    csv_paths = sorted(destination.rglob("*.csv"))
    if not manifest_path.is_file():
        if csv_paths or any(destination.iterdir()):
            raise CatalogError(
                "existing editor workspace has no manifest.json"
            )
        return {}

    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as error:
        raise CatalogError(
            f"cannot read editor manifest: {error}"
        ) from error

    if manifest.get("schema_version") != 1:
        raise CatalogError("unsupported editor workspace schema")
    old_fieldnames = [name for name in EDITOR_FIELDNAMES if name != "translation_source_fingerprint"]
    if manifest.get("fieldnames") not in (list(EDITOR_FIELDNAMES), old_fieldnames):
        raise CatalogError("editor workspace columns have changed")

    expected_files = {
        (locale, filename): count
        for locale, details in manifest.get("locales", {}).items()
        for filename, count in details.get("files", {}).items()
    }
    actual_files = {}
    edits: dict[tuple[str, str], dict[str, str]] = {}

    for path in csv_paths:
        relative = path.relative_to(destination)
        if len(relative.parts) != 2:
            raise CatalogError(
                f"editor CSV must be inside one locale directory: {path}"
            )
        locale, filename = relative.parts

        try:
            with path.open(
                mode="r",
                encoding="utf-8-sig",
                newline="",
            ) as handle:
                reader = csv.DictReader(handle)
                fieldnames = reader.fieldnames or []
                if fieldnames not in (list(EDITOR_FIELDNAMES), old_fieldnames):
                    raise CatalogError(
                        f"unexpected columns in editor CSV: {path}"
                    )

                count = 0
                for row_number, row in enumerate(reader, start=2):
                    key = row.get("key", "")
                    if not key:
                        raise CatalogError(
                            f"empty key in {path} at row {row_number}"
                        )
                    identity = (locale, key)
                    if identity in edits:
                        raise CatalogError(
                            f"duplicate editor key for {locale}: {key}"
                        )
                    edits[identity] = {
                        "source_fingerprint": row.get(
                            "source_fingerprint", ""
                        ),
                        "translation_source_fingerprint": row.get(
                            "translation_source_fingerprint"
                        ) or row.get("source_fingerprint", ""),
                        "lekmod_target_status": row.get(
                            "lekmod_target_status", ""
                        ),
                        "lekmod_target": row.get(
                            "lekmod_target", ""
                        ),
                        "lekmod_target_gender": row.get(
                            "lekmod_target_gender", ""
                        ),
                        "lekmod_target_plurality": row.get(
                            "lekmod_target_plurality", ""
                        ),
                        **{
                            field: row.get(field, "")
                            for field in EDITOR_EDITABLE_FIELDS
                        },
                    }
                    count += 1
        except UnicodeError as error:
            raise CatalogError(
                f"editor CSV is not valid UTF-8: {path}: {error}"
            ) from error

        actual_files[(locale, filename)] = count

    if actual_files != expected_files:
        raise CatalogError(
            "editor workspace files or rows differ from its manifest; "
            "restore the missing rows before refreshing"
        )
    return edits


def merge_editor_edits(
    workspace: dict[str, dict[str, list[dict[str, object]]]],
    edits: dict[tuple[str, str], dict[str, str]],
) -> None:
    """Preserve draft cells only when their English fingerprint matches."""
    rows_by_identity = {
        (locale, str(row["key"])): row
        for locale, documents in workspace.items()
        for rows in documents.values()
        for row in rows
    }

    for identity, edit in edits.items():
        if identity not in rows_by_identity:
            locale, key = identity
            raise CatalogError(
                "existing editor translation is no longer in the "
                f"generated workspace: {locale}/{key}"
            )

        row = rows_by_identity[identity]
        old_target_was_present = (
            edit["lekmod_target_status"] == "present"
        )
        if old_target_was_present:
            translation_was_default = (
                normalize_text(edit["translation"])
                == normalize_text(edit["lekmod_target"])
                and normalize_metadata(edit["translation_gender"])
                == normalize_metadata(edit["lekmod_target_gender"])
                and normalize_metadata(edit["translation_plurality"])
                == normalize_metadata(
                    edit["lekmod_target_plurality"]
                )
            )
        else:
            translation_was_default = not any(
                edit[field]
                for field in (
                    "translation",
                    "translation_gender",
                    "translation_plurality",
                )
            )

        if edit["translator_note"] or not translation_was_default or (
            edit["translation_source_fingerprint"] != edit["source_fingerprint"]
        ):
            for field in EDITOR_TRANSLATION_FIELDS:
                row[field] = edit[field]
            row["translation_source_fingerprint"] = edit["translation_source_fingerprint"]
        row["translator_note"] = edit["translator_note"]

        translation = str(row["translation"])
        row["translation_characters"] = (
            character_count(translation) if translation else ""
        )
        row["translation_status"] = translation_status(
            translation,
            str(row["translation_gender"]),
            str(row["translation_plurality"]),
            {
                "status": row["lekmod_target_status"],
                "text": row["lekmod_target"],
                "gender": row["lekmod_target_gender"],
                "plurality": row["lekmod_target_plurality"],
            },
        )
        if row["translation_source_fingerprint"] != row["source_fingerprint"] and (
            translation or row["translator_note"]
        ):
            row["translation_status"] = "stale"


def write_json_atomic(path: Path, value: object) -> None:
    """Replace generated JSON without leaving a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def validate_output_path(
    destination: Path,
    source: Path,
    vanilla_db: Path,
) -> None:
    """Keep generated output away from inputs and shipped files."""
    resolved = destination.resolve()
    if resolved in {source.resolve(), vanilla_db.resolve()}:
        raise CatalogError(
            "generated output cannot overwrite an input file"
        )
    if (
        source.resolve().is_relative_to(resolved)
        or vanilla_db.resolve().is_relative_to(resolved)
    ):
        raise CatalogError(
            "generated output cannot replace a directory containing "
            "an input file"
        )
    if resolved.is_relative_to(REPO_ROOT / "LEKMOD"):
        raise CatalogError(
            "generated output must be outside shipped LEKMOD files"
        )


def validate_generated_outputs(
    catalog_output: Path,
    review_output: Path,
    editor_output: Path,
) -> None:
    """Reject overlapping catalog, review, and editor destinations."""
    outputs = {
        "catalog": catalog_output.resolve(),
        "review": review_output.resolve(),
        "editor": editor_output.resolve(),
    }
    items = list(outputs.items())

    for index, (left_name, left_path) in enumerate(items):
        for right_name, right_path in items[index + 1:]:
            if (
                left_path == right_path
                or left_path.is_relative_to(right_path)
                or right_path.is_relative_to(left_path)
            ):
                raise CatalogError(
                    f"{left_name} and {right_name} outputs overlap"
                )


def write_catalog(
    path: Path,
    catalog: dict,
    source: Path,
    vanilla_db: Path,
) -> None:
    """Write the classified catalog after validating its destination."""
    destination = path.resolve()
    if destination.suffix.lower() != ".json":
        raise CatalogError("catalog path must end in .json")
    validate_output_path(destination, source, vanilla_db)
    write_json_atomic(destination, catalog)


def write_review_workspace(
    destination: Path,
    reviews: dict[
        str, tuple[dict[str, dict], dict[str, object]]
    ],
    source: Path,
    vanilla_db: Path,
    source_conflicts: dict | None = None,
) -> None:
    """Atomically replace per-locale JSON and conflict review files."""
    destination = destination.resolve()
    validate_output_path(destination, source, vanilla_db)
    if destination == REPO_ROOT.resolve():
        raise CatalogError(
            "review workspace cannot replace the repository root"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    ))
    backup: Path | None = None

    try:
        if source_conflicts is None:
            source_conflicts = {
                "schema_version": 1,
                "status": "developer_review_required",
                "summary": {
                    "entries": 0,
                    "categories": {},
                    "subcategories": {},
                },
                "categories": {},
            }
        workspace_manifest = {
            "schema_version": 3,
            "locales": sorted(reviews, key=str.casefold),
            "source_conflicts": {
                "file": "source-conflicts.json",
                "entries": source_conflicts["summary"]["entries"],
            },
        }
        write_json_atomic(
            temporary / "manifest.json",
            workspace_manifest,
        )
        write_json_atomic(
            temporary / "source-conflicts.json",
            source_conflicts,
        )

        for locale, (documents, manifest) in sorted(
            reviews.items()
        ):
            locale_root = temporary / locale
            write_json_atomic(
                locale_root / "manifest.json",
                manifest,
            )
            for category, document in sorted(documents.items()):
                write_json_atomic(
                    locale_root / f"{category}.json",
                    document,
                )

        if destination.exists():
            backup = Path(tempfile.mkdtemp(
                prefix=f".{destination.name}.backup.",
                dir=destination.parent,
            ))
            backup.rmdir()
            destination.replace(backup)

        try:
            temporary.replace(destination)
        except OSError:
            if (
                backup is not None
                and backup.exists()
                and not destination.exists()
            ):
                try:
                    backup.replace(destination)
                    backup = None
                except OSError:
                    pass
            raise

        if backup is not None:
            shutil.rmtree(backup)
            backup = None
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
def write_editor_csv(
    path: Path,
    rows: list[dict[str, object]],
) -> None:
    """Serialize one locale/category group with stable CSV columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(
        mode="w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=EDITOR_FIELDNAMES,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_editor_workspace(
    destination: Path,
    reviews: dict[
        str, tuple[dict[str, dict], dict[str, object]]
    ],
    source: Path,
    vanilla_db: Path,
) -> None:
    """Merge translator edits and atomically refresh all CSV files."""
    destination = destination.resolve()
    validate_output_path(destination, source, vanilla_db)
    if destination == REPO_ROOT.resolve():
        raise CatalogError(
            "editor workspace cannot replace the repository root"
        )

    workspace = build_editor_rows(reviews)
    edits = load_editor_edits(destination)
    merge_editor_edits(workspace, edits)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    ))
    backup: Path | None = None

    try:
        manifest = {
            "schema_version": 1,
            "format": "utf-8-sig CSV",
            "fieldnames": list(EDITOR_FIELDNAMES),
            "editable_fields": list(EDITOR_EDITABLE_FIELDS),
            "locales": {
                locale: {
                    "entries": sum(
                        len(rows) for rows in documents.values()
                    ),
                    "files": {
                        f"{category}.csv": len(rows)
                        for category, rows in sorted(
                            documents.items()
                        )
                    },
                }
                for locale, documents in sorted(workspace.items())
            },
        }
        write_json_atomic(temporary / "manifest.json", manifest)

        for locale, documents in sorted(workspace.items()):
            for category, rows in sorted(documents.items()):
                write_editor_csv(
                    temporary / locale / f"{category}.csv",
                    rows,
                )

        if destination.exists():
            backup = Path(tempfile.mkdtemp(
                prefix=f".{destination.name}.backup.",
                dir=destination.parent,
            ))
            backup.rmdir()
            destination.replace(backup)

        try:
            temporary.replace(destination)
        except OSError:
            if (
                backup is not None
                and backup.exists()
                and not destination.exists()
            ):
                try:
                    backup.replace(destination)
                    backup = None
                except OSError:
                    pass
            raise

        if backup is not None:
            shutil.rmtree(backup)
            backup = None
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
