from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile

import audit_primary_localization as primary_audit


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = primary_audit.DEFAULT_SOURCE
DEFAULT_OUTPUT = REPO_ROOT / "build" / "localization" / "catalog.json"
SOURCE_LOCALE = "en_US"
TRANSLATABLE_FIELDS = ("Text", "Gender", "Plurality")
LEKMOD_SENTINELS = {
    "TXT_KEY_LEKMOD_VERSION",
    "TXT_KEY_LEKMOD_MENU_DISCORD",
}

BRACKET_TOKEN_RE = re.compile(r"\[[^\[\]]+\]")
BRACE_TOKEN_RE = re.compile(r"\{[^{}]+\}")
PRINTF_TOKEN_RE = re.compile(r"%(?:\d+\$)?[a-zA-Z%]")

CATEGORY_RULES = (
    ("civilizations", re.compile(
        r"^TXT_KEY_(?:CIVILIZATION|CIV_|LEADER|TRAIT|CITY_NAME)"
    )),
    ("units", re.compile(
        r"^TXT_KEY_(?:UNIT|UNITCLASS|UNITCOMBAT|CIV5_UNIT)"
    )),
    ("buildings", re.compile(
        r"^TXT_KEY_(?:BUILDING|BUILDINGCLASS|SPECIALIST)"
    )),
    ("promotions", re.compile(
        r"^TXT_KEY_(?:PROMOTION|DEFENSEMOD)"
    )),
    ("technologies", re.compile(
        r"^TXT_KEY_(?:TECH|TECHNOLOGY)"
    )),
    ("policies", re.compile(
        r"^TXT_KEY_(?:POLICY|POLICY_BRANCH|SOCIAL_POLICY)"
    )),
    ("religion", re.compile(
        r"^TXT_KEY_(?:BELIEF|RELIGION|PANTHEON)"
    )),
    ("resources", re.compile(
        r"^TXT_KEY_(?:RESOURCE|IMPROVEMENT|BUILD_)"
    )),
    ("diplomacy", re.compile(
        r"^TXT_KEY_(?:DIPLO|LEADER_MESSAGE|AI_DIPLO)"
    )),
    ("game_options", re.compile(
        r"^TXT_KEY_(?:GAME_OPTION|GAMEOPTION|VICTORY|ERA|HANDICAP|"
        r"GAME_SPEED|WORLD|MAP_)"
    )),
    ("ui", re.compile(
        r"^TXT_KEY_(?:UI|MENU|OPTIONS|NOTIFICATION|POPUP|LEKMOD_MENU|"
        r"LEKMOD_MP)"
    )),
    ("concepts", re.compile(r"^TXT_KEY_(?:CONCEPT|CIV5_)")),
)


class CatalogError(ValueError):
    pass


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def canonical_fields(fields: dict[str, object]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}

    for name in TRANSLATABLE_FIELDS:
        if name in fields:
            value = fields[name]
            result[name] = None if value is None else str(value)

    return result


def normalize_text(value: object) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split())


def catalog_fields(fields: dict[str, object]) -> dict[str, str | None]:
    result = canonical_fields(fields)

    if "Text" in result:
        result["Text"] = normalize_text(result["Text"])

    return result


def normalized_fields(fields: dict[str, object]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}

    for name in TRANSLATABLE_FIELDS:
        value = fields.get(name)
        if name == "Text":
            value = normalize_text(value)
        if name in {"Gender", "Plurality"} and value in {None, ""}:
            value = None
        result[name] = None if value is None else str(value)

    return result


def database_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def language_tables(connection: sqlite3.Connection) -> dict[str, str]:
    tables = {}

    for (table_name,) in connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name GLOB 'Language_*'"
    ):
        locale = table_name[len("Language_"):]
        tables[locale.casefold()] = table_name

    return tables


def load_vanilla_database(
    path: Path,
    locale: str = SOURCE_LOCALE,
) -> tuple[dict[str, dict[str, str | None]], list[str], str]:
    if not path.is_file():
        raise CatalogError(f"vanilla localization database not found: {path}")

    try:
        connection = sqlite3.connect(database_uri(path), uri=True)
    except sqlite3.Error as error:
        raise CatalogError(f"cannot open vanilla localization database: {error}") from error

    with closing(connection):
        connection.execute("PRAGMA query_only = ON")
        tables = language_tables(connection)
        table_name = tables.get(locale.casefold())

        if table_name is None:
            raise CatalogError(f"Language_{locale} was not found in {path}")

        columns = {
            row[1].casefold(): row[1]
            for row in connection.execute(
                f"PRAGMA table_info({quote_identifier(table_name)})"
            )
        }

        tag_column = columns.get("tag")
        text_column = columns.get("text")

        if tag_column is None or text_column is None:
            raise CatalogError(
                f"{table_name} must contain Tag and Text columns"
            )

        selected_columns = []

        for canonical in ("Tag", *TRANSLATABLE_FIELDS):
            actual = columns.get(canonical.casefold())
            if actual is None:
                selected_columns.append(f"NULL AS {quote_identifier(canonical)}")
            else:
                selected_columns.append(
                    f"{quote_identifier(actual)} AS {quote_identifier(canonical)}"
                )

        query = (
            f"SELECT {', '.join(selected_columns)} "
            f"FROM {quote_identifier(table_name)} ORDER BY {quote_identifier(tag_column)}"
        )
        entries: dict[str, dict[str, str | None]] = {}

        for tag, text, gender, plurality in connection.execute(query):
            if not isinstance(tag, str) or not tag:
                raise CatalogError(f"{table_name} contains an empty Tag")
            if tag in entries:
                raise CatalogError(f"{table_name} contains duplicate Tag {tag}")

            entries[tag] = catalog_fields({
                "Text": text,
                "Gender": gender,
                "Plurality": plurality,
            })

        locales = sorted(
            (name[len("Language_"):] for name in tables.values()),
            key=str.casefold,
        )

    contaminated = sorted(LEKMOD_SENTINELS & entries.keys())
    if contaminated:
        raise CatalogError(
            "vanilla database contains Lekmod sentinel keys: "
            + ", ".join(contaminated)
        )

    fingerprint = entries_fingerprint(entries)
    return entries, locales, fingerprint


def entries_fingerprint(entries: dict[str, dict[str, object]]) -> str:
    serialized = json.dumps(
        [
            [key, normalized_fields(entries[key])]
            for key in sorted(entries)
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def category_for_key(key: str) -> str:
    for category, pattern in CATEGORY_RULES:
        if pattern.match(key):
            return category
    return "misc"


def token_counts(text: str | None) -> dict[str, int]:
    if not text:
        return {}

    tokens = (
        BRACKET_TOKEN_RE.findall(text)
        + BRACE_TOKEN_RE.findall(text)
        + PRINTF_TOKEN_RE.findall(text)
    )
    return dict(sorted(Counter(tokens).items()))


def explicit_metadata_changed(
    source_fields: dict[str, object],
    vanilla_fields: dict[str, object],
) -> bool:
    for name in ("Gender", "Plurality"):
        if name not in source_fields:
            continue

        source_value = source_fields.get(name)
        vanilla_value = vanilla_fields.get(name)

        if source_value in {None, ""}:
            source_value = None
        if vanilla_value in {None, ""}:
            vanilla_value = None

        if source_value != vanilla_value:
            return True

    return False


def build_catalog(
    source_report: dict,
    vanilla_entries: dict[str, dict[str, object]],
    vanilla_locales: list[str],
    vanilla_database_name: str,
    vanilla_fingerprint: str,
) -> dict:
    if source_report["summary"]["errors"]:
        raise CatalogError("primary localization source contains audit errors")

    source_entries = source_report["entries"]
    classification_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    categories: dict[str, dict[str, dict[str, object]]] = {}
    metadata_review: dict[str, dict[str, object]] = {}

    for key in sorted(source_entries):
        source_fields = catalog_fields(source_entries[key])
        vanilla_fields = vanilla_entries.get(key)

        if vanilla_fields is None:
            classification = "lekmod_new"
        elif source_fields.get("Text") != vanilla_fields.get("Text"):
            classification = "vanilla_modified"
        elif explicit_metadata_changed(source_fields, vanilla_fields):
            classification = "vanilla_metadata_only"
        else:
            classification = "vanilla_unchanged"

        classification_counts[classification] += 1

        if classification in {"vanilla_unchanged", "vanilla_metadata_only"}:
            if classification == "vanilla_metadata_only":
                metadata_review[key] = {
                    "category": category_for_key(key),
                    "fields": source_fields,
                }
            continue

        category = category_for_key(key)
        category_counts[category] += 1
        categories.setdefault(category, {})[key] = {
            "classification": classification,
            "fields": source_fields,
            "tokens": token_counts(source_fields.get("Text")),
        }

    ordered_categories = {
        category: categories[category]
        for category in sorted(categories)
    }
    target_locales = [
        locale for locale in vanilla_locales
        if locale.casefold() != SOURCE_LOCALE.casefold()
    ]
    empty_categories = {
        category: {}
        for category in ordered_categories
    }

    translations = {
        locale: {
            "categories": {
                category: dict(entries)
                for category, entries in empty_categories.items()
            }
        }
        for locale in target_locales
    }

    required = sum(category_counts.values())

    return {
        "schema_version": 1,
        "source": {
            "file": source_report["source"],
            "sha256": source_report["source_sha256"],
            "locale": SOURCE_LOCALE,
            "entries": len(source_entries),
        },
        "vanilla": {
            "database": vanilla_database_name,
            "locale": SOURCE_LOCALE,
            "entries": len(vanilla_entries),
            "content_sha256": vanilla_fingerprint,
        },
        "locales": [SOURCE_LOCALE, *target_locales],
        "summary": {
            "classifications": dict(sorted(classification_counts.items())),
            "requires_translation": required,
            "categories": dict(sorted(category_counts.items())),
        },
        "source_categories": ordered_categories,
        "review": {
            "metadata_only": metadata_review,
        },
        "translations": translations,
    }


def write_catalog(path: Path, catalog: dict, source: Path, vanilla_db: Path) -> None:
    destination = path.resolve()

    if destination.suffix.lower() != ".json":
        raise CatalogError("catalog path must end in .json")
    if destination in {source.resolve(), vanilla_db.resolve()}:
        raise CatalogError("catalog cannot overwrite an input file")
    if destination.is_relative_to(REPO_ROOT / "LEKMOD"):
        raise CatalogError("catalog must be written outside the shipped LEKMOD files")

    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=destination.parent,
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(catalog, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    temporary.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Lekmod's primary English localization with a clean "
            "Civilization V localization database and build a translation catalog."
        )
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--vanilla-db",
        type=Path,
        required=True,
        help="Path to a clean Civilization V Localization-Merged.db.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source = args.source.resolve()
    vanilla_db = args.vanilla_db.resolve()
    source_report = primary_audit.parse_source(source, SOURCE_LOCALE)

    try:
        vanilla_entries, vanilla_locales, vanilla_fingerprint = (
            load_vanilla_database(vanilla_db)
        )
        catalog = build_catalog(
            source_report,
            vanilla_entries,
            vanilla_locales,
            vanilla_db.name,
            vanilla_fingerprint,
        )
        write_catalog(args.output, catalog, source, vanilla_db)
    except (CatalogError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Cannot build localization catalog: {error}\n")

    classifications = catalog["summary"]["classifications"]
    print(f"Lekmod source entries: {catalog['source']['entries']}")
    print(f"Vanilla English entries: {catalog['vanilla']['entries']}")
    print(f"Vanilla unchanged: {classifications.get('vanilla_unchanged', 0)}")
    print(
        "Vanilla metadata-only changes: "
        f"{classifications.get('vanilla_metadata_only', 0)}"
    )
    print(f"Vanilla modified: {classifications.get('vanilla_modified', 0)}")
    print(f"Lekmod new: {classifications.get('lekmod_new', 0)}")
    print(f"Entries requiring translation: {catalog['summary']['requires_translation']}")
    print(f"Target locales: {len(catalog['translations'])}")
    print(f"Catalog: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
