"""Load official and repository localization sources without executing them."""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import xml.etree.ElementTree as ET

import audit_primary_localization as primary_audit

from .common import (
    CatalogError,
    KEY_RE,
    LANGUAGE_RE,
    REPO_ROOT,
    SOURCE_LOCALE,
    TRANSLATABLE_FIELDS,
    catalog_fields,
    local_name,
    normalized_fields,
    quote_identifier,
)


LEKMOD_SENTINELS = {
    "TXT_KEY_LEKMOD_VERSION",
    "TXT_KEY_LEKMOD_MENU_DISCORD",
}
SQL_LANGUAGE_RE = re.compile(
    r"^Language_([A-Za-z0-9_]+)$",
    re.IGNORECASE,
)
SQL_TOKEN_RE = re.compile(
    r"(?P<space>\s+)"
    r"|(?P<line_comment>--[^\r\n]*)"
    r"|(?P<block_comment>/\*.*?\*/)"
    r"|(?P<string>'(?:''|[^'])*')"
    r'|(?P<quoted_identifier>"(?:""|[^"])*")'
    r"|(?P<identifier>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<symbol>[(),;=])"
    r"|(?P<other>.)",
    re.DOTALL,
)


def database_uri(path: Path) -> str:
    """Make a read-only SQLite URI for a local game database."""
    return f"{path.resolve().as_uri()}?mode=ro"


def language_tables(connection: sqlite3.Connection) -> dict[str, str]:
    """Find all official Language tables or views by locale."""
    tables = {}

    for (table_name,) in connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name GLOB 'Language_*'"
    ):
        locale = table_name[len("Language_"):]
        tables[locale.casefold()] = table_name

    return tables


def load_language_table(
    connection: sqlite3.Connection,
    table_name: str,
) -> dict[str, dict[str, str | None]]:
    """Read supported columns from one official language table."""
    columns = {
        row[1].casefold(): row[1]
        for row in connection.execute(
            f"PRAGMA table_info({quote_identifier(table_name)})"
        )
    }
    tag_column = columns.get("tag")
    text_column = columns.get("text")

    if tag_column is None or text_column is None:
        raise CatalogError(f"{table_name} must contain Tag and Text columns")

    selected_columns = []

    for canonical in ("Tag", *TRANSLATABLE_FIELDS):
        actual = columns.get(canonical.casefold())
        if actual is None:
            selected_columns.append(
                f"NULL AS {quote_identifier(canonical)}"
            )
        else:
            selected_columns.append(
                f"{quote_identifier(actual)} AS "
                f"{quote_identifier(canonical)}"
            )

    query = (
        f"SELECT {', '.join(selected_columns)} "
        f"FROM {quote_identifier(table_name)} "
        f"ORDER BY {quote_identifier(tag_column)}"
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

    return entries


def load_vanilla_locales(
    path: Path,
    requested_locales: list[str] | None = None,
) -> tuple[
    dict[str, dict[str, dict[str, str | None]]],
    dict[str, str],
]:
    """Load all requested official locales and reject Lekmod sentinels."""
    if not path.is_file():
        raise CatalogError(
            f"vanilla localization database not found: {path}"
        )

    try:
        connection = sqlite3.connect(database_uri(path), uri=True)
    except sqlite3.Error as error:
        raise CatalogError(
            f"cannot open vanilla localization database: {error}"
        ) from error

    with closing(connection):
        connection.execute("PRAGMA query_only = ON")
        tables = language_tables(connection)

        if requested_locales is None:
            requested = sorted(tables, key=str.casefold)
        else:
            requested = []
            for locale in requested_locales:
                folded = locale.casefold()
                if folded not in tables:
                    available = ", ".join(sorted(
                        (
                            name[len("Language_"):]
                            for name in tables.values()
                        ),
                        key=str.casefold,
                    ))
                    raise CatalogError(
                        f"Language_{locale} was not found in {path}; "
                        f"available locales: {available}"
                    )
                if folded not in requested:
                    requested.append(folded)

        entries_by_locale = {}
        fingerprints = {}

        for folded in requested:
            table_name = tables[folded]
            locale = table_name[len("Language_"):]
            entries = load_language_table(connection, table_name)
            entries_by_locale[locale] = entries
            fingerprints[locale] = entries_fingerprint(entries)

    contaminated = {
        locale: sorted(LEKMOD_SENTINELS & set(entries))
        for locale, entries in entries_by_locale.items()
        if LEKMOD_SENTINELS & set(entries)
    }
    if contaminated:
        details = "; ".join(
            f"{locale}: {', '.join(keys)}"
            for locale, keys in sorted(contaminated.items())
        )
        raise CatalogError(
            "vanilla database contains Lekmod sentinel keys: " + details
        )

    return entries_by_locale, fingerprints


def load_vanilla_database(
    path: Path,
    locale: str = SOURCE_LOCALE,
) -> tuple[dict[str, dict[str, str | None]], list[str], str]:
    """Return the English vanilla entries and available locales."""
    entries_by_locale, fingerprints = load_vanilla_locales(path)
    canonical = next(
        (
            name
            for name in entries_by_locale
            if name.casefold() == locale.casefold()
        ),
        None,
    )
    if canonical is None:
        raise CatalogError(f"Language_{locale} was not found in {path}")

    locales = sorted(entries_by_locale, key=str.casefold)
    return entries_by_locale[canonical], locales, fingerprints[canonical]


def entries_fingerprint(entries: dict[str, dict[str, object]]) -> str:
    """Hash normalized entries independently of database layout."""
    serialized = json.dumps(
        [
            [key, normalized_fields(entries[key])]
            for key in sorted(entries)
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def xml_paths(source: Path, art_root: Path) -> list[Path]:
    """Select the primary Override XML and supported Art XML inputs."""
    paths = [source.resolve()]
    if not art_root.is_dir():
        raise CatalogError(f"Art directory not found: {art_root}")
    paths.extend(sorted(
        (
            path.resolve()
            for path in art_root.rglob("*")
            if path.is_file() and path.suffix.casefold() == ".xml"
        ),
        key=lambda path: path.as_posix().casefold(),
    ))
    return list(dict.fromkeys(paths))


def source_name(path: Path, root: Path = REPO_ROOT) -> str:
    """Return a stable repository-relative source path."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def exact_key(value: object) -> str | None:
    """Recognize a literal localization key without partial matches."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if KEY_RE.fullmatch(normalized) else None


def reference_records(
    root: ET.Element,
    path: str,
) -> dict[str, list[dict[str, str]]]:
    """Find game-data fields that refer to localization keys."""
    references: dict[str, list[dict[str, str]]] = defaultdict(list)

    def visit(element: ET.Element, ancestors: list[str]) -> None:
        """Walk game-data XML and record exact key references."""
        name = local_name(element.tag)
        if LANGUAGE_RE.fullmatch(name):
            return

        table = ancestors[1] if len(ancestors) > 1 else name

        for field, value in element.attrib.items():
            key = exact_key(value)
            if key:
                references[key].append({
                    "table": table,
                    "field": field,
                    "path": path,
                })

        if not list(element):
            key = exact_key("".join(element.itertext()))
            if key:
                references[key].append({
                    "table": table,
                    "field": name,
                    "path": path,
                })

        for child in element:
            visit(child, [*ancestors, name])

    visit(root, [])
    return references


def read_update(element: ET.Element) -> tuple[str, dict[str, str]]:
    """Parse a single-key XML localization Update operation."""
    children: dict[str, list[ET.Element]] = defaultdict(list)
    for child in element:
        children[local_name(child.tag)].append(child)
    if element.attrib or set(children) != {"Where", "Set"}:
        raise CatalogError("Update must contain one Where and one Set")
    if len(children["Where"]) != 1 or len(children["Set"]) != 1:
        raise CatalogError(
            "Update must contain exactly one Where and one Set"
        )

    where, _ = primary_audit.read_columns(children["Where"][0])
    fields, _ = primary_audit.read_columns(children["Set"][0])
    if set(where) != {"Tag"} or not where["Tag"]:
        raise CatalogError(
            "localization Update must select one nonempty Tag"
        )
    return where["Tag"], fields


def sql_tokens(text: str) -> list[tuple[str, str]]:
    """Tokenize restricted localization SQL without executing it."""
    tokens = []
    for match in SQL_TOKEN_RE.finditer(text):
        kind = match.lastgroup
        value = match.group()
        if kind in {"space", "line_comment", "block_comment"}:
            continue
        if kind == "other":
            raise CatalogError(
                f"unsupported SQL token {value!r}"
            )
        if kind == "string":
            value = value[1:-1].replace("''", "'")
        elif kind == "quoted_identifier":
            kind = "identifier"
            value = value[1:-1].replace('""', '"')
        tokens.append((str(kind), value))
    return tokens


class SqlCursor:
    """Small cursor for the deliberately restricted localization SQL grammar."""

    def __init__(self, tokens: list[tuple[str, str]]):
        """Initialize the restricted SQL token cursor."""
        self.tokens = tokens
        self.index = 0

    def done(self) -> bool:
        """Report whether the SQL token stream was fully consumed."""
        return self.index >= len(self.tokens)

    def peek(self) -> tuple[str, str] | None:
        """Inspect the next SQL token without advancing."""
        return None if self.done() else self.tokens[self.index]

    def accept_symbol(self, symbol: str) -> bool:
        """Consume an expected punctuation token when present."""
        if self.peek() == ("symbol", symbol):
            self.index += 1
            return True
        return False

    def expect_symbol(self, symbol: str) -> None:
        """Require and consume an expected punctuation token."""
        if not self.accept_symbol(symbol):
            raise CatalogError(
                f"expected SQL symbol {symbol!r} at token {self.index}"
            )

    def accept_keyword(self, keyword: str) -> bool:
        """Consume a case-insensitive SQL keyword when present."""
        token = self.peek()
        if (
            token is not None
            and token[0] == "identifier"
            and token[1].casefold() == keyword.casefold()
        ):
            self.index += 1
            return True
        return False

    def expect_keyword(self, keyword: str) -> None:
        """Require and consume a case-insensitive SQL keyword."""
        if not self.accept_keyword(keyword):
            raise CatalogError(
                f"expected SQL keyword {keyword} at token {self.index}"
            )

    def identifier(self) -> str:
        """Read the next SQL identifier token."""
        token = self.peek()
        if token is None or token[0] != "identifier":
            raise CatalogError(
                f"expected SQL identifier at token {self.index}"
            )
        self.index += 1
        return token[1]

    def value(self) -> str | None:
        """Read a literal SQL value from the restricted grammar."""
        token = self.peek()
        if token is None:
            raise CatalogError(
                f"expected SQL value at token {self.index}"
            )
        if token[0] == "string":
            self.index += 1
            return token[1]
        if (
            token[0] == "identifier"
            and token[1].casefold() == "null"
        ):
            self.index += 1
            return None
        raise CatalogError(
            f"only string and NULL SQL values are supported at "
            f"token {self.index}"
        )


def canonical_sql_fields(
    fields: dict[str, str | None],
) -> dict[str, str]:
    """Keep supported SQL text columns in canonical spelling."""
    canonical = {}
    for name, value in fields.items():
        match = next(
            (
                candidate
                for candidate in ("Tag", *TRANSLATABLE_FIELDS)
                if candidate.casefold() == name.casefold()
            ),
            name,
        )
        if value is not None:
            canonical[match] = value
    return canonical


def parse_language_sql(
    text: str,
) -> tuple[dict[str, dict[str, dict[str, str]]], int]:
    """Interpret only supported language-table SQL statements."""
    cursor = SqlCursor(sql_tokens(text))
    entries: dict[
        str, dict[str, dict[str, str]]
    ] = defaultdict(dict)
    skipped_empty = 0

    while not cursor.done():
        if cursor.accept_symbol(";"):
            continue

        if cursor.accept_keyword("INSERT"):
            if cursor.accept_keyword("OR"):
                cursor.expect_keyword("REPLACE")
            cursor.expect_keyword("INTO")
            table = cursor.identifier()
            language = SQL_LANGUAGE_RE.fullmatch(table)
            if language is None:
                raise CatalogError(
                    f"unsupported non-language INSERT table {table}"
                )
            locale = language.group(1)
            cursor.expect_symbol("(")
            columns = [cursor.identifier()]
            while cursor.accept_symbol(","):
                columns.append(cursor.identifier())
            cursor.expect_symbol(")")
            cursor.expect_keyword("VALUES")

            while True:
                cursor.expect_symbol("(")
                values = [cursor.value()]
                while cursor.accept_symbol(","):
                    values.append(cursor.value())
                cursor.expect_symbol(")")
                if len(values) != len(columns):
                    raise CatalogError(
                        "SQL localization tuple does not match its columns"
                    )
                fields = canonical_sql_fields(
                    dict(zip(columns, values))
                )
                key = fields.get("Tag")
                if not key:
                    skipped_empty += 1
                else:
                    entries[locale][key] = fields
                if not cursor.accept_symbol(","):
                    break
            cursor.expect_symbol(";")
            continue

        if cursor.accept_keyword("UPDATE"):
            table = cursor.identifier()
            language = SQL_LANGUAGE_RE.fullmatch(table)
            if language is None:
                raise CatalogError(
                    f"unsupported non-language UPDATE table {table}"
                )
            locale = language.group(1)
            cursor.expect_keyword("SET")
            assignments = {}

            while True:
                name = cursor.identifier()
                cursor.expect_symbol("=")
                assignments[name] = cursor.value()
                if not cursor.accept_symbol(","):
                    break

            cursor.expect_keyword("WHERE")
            selector = cursor.identifier()
            if selector.casefold() != "tag":
                raise CatalogError(
                    "SQL localization UPDATE must select Tag"
                )
            keys = []
            if cursor.accept_keyword("IN"):
                cursor.expect_symbol("(")
                keys.append(cursor.value())
                while cursor.accept_symbol(","):
                    keys.append(cursor.value())
                cursor.expect_symbol(")")
            else:
                cursor.expect_symbol("=")
                keys.append(cursor.value())
            cursor.expect_symbol(";")

            fields = canonical_sql_fields(assignments)
            for key in keys:
                if not key:
                    skipped_empty += 1
                    continue
                current = dict(entries[locale].get(key, {}))
                current.update(fields)
                entries[locale][key] = current
            continue

        token = cursor.peek()
        raise CatalogError(
            f"unsupported SQL statement at token {cursor.index}: "
            f"{token!r}"
        )

    return entries, skipped_empty


def load_repository_localizations(
    source: Path,
    art_root: Path,
    root: Path = REPO_ROOT,
) -> tuple[
    dict[str, dict[str, list[dict[str, object]]]],
    dict[str, list[dict[str, str]]],
    dict[str, object],
]:
    """Collect source variants and references from Override and Art."""
    variants: dict[
        str, dict[str, list[dict[str, object]]]
    ] = defaultdict(lambda: defaultdict(list))
    references: dict[str, list[dict[str, str]]] = defaultdict(list)
    scanned_paths = xml_paths(source, art_root)
    skipped_empty_selectors = 0

    for path in scanned_paths:
        relative = source_name(path, root)
        try:
            if path.resolve() == source.resolve():
                document = path.read_text(encoding="utf-8-sig")
                begin = "<!-- BEGIN GENERATED FALLBACK -->"
                end = "<!-- END GENERATED FALLBACK -->"
                if begin in document or end in document:
                    if document.count(begin) != 1 or document.count(end) != 1:
                        raise CatalogError("generated fallback markers are invalid")
                    start = document.index(begin)
                    finish = document.index(end)
                    if finish <= start:
                        raise CatalogError("generated fallback markers are out of order")
                    document = document[:start] + document[finish + len(end):]
                xml_root = ET.fromstring(document)
            else:
                xml_root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as error:
            raise CatalogError(
                f"cannot parse {relative}: {error}"
            ) from error

        for key, records in reference_records(xml_root, relative).items():
            references[key].extend(records)

        file_entries: dict[
            str, dict[str, dict[str, str]]
        ] = defaultdict(dict)

        for block in xml_root.iter():
            language = LANGUAGE_RE.fullmatch(local_name(block.tag))
            if not language:
                continue
            locale = language.group(1)

            for operation in block:
                operation_name = local_name(operation.tag)
                try:
                    if operation_name in {"Row", "Replace"}:
                        fields, _ = primary_audit.read_columns(operation)
                        key = fields.get("Tag")
                        if not key:
                            skipped_empty_selectors += 1
                            continue
                        file_entries[locale][key] = fields
                    elif operation_name == "Update":
                        key, fields = read_update(operation)
                        current = dict(
                            file_entries[locale].get(key, {})
                        )
                        current.update(fields)
                        file_entries[locale][key] = current
                    elif operation_name == "Delete":
                        where, _ = primary_audit.read_columns(operation)
                        if set(where) == {"Tag"} and not where["Tag"]:
                            skipped_empty_selectors += 1
                            continue
                        if set(where) != {"Tag"}:
                            raise CatalogError(
                                "localization Delete must select one "
                                "nonempty Tag"
                            )
                        file_entries[locale].pop(where["Tag"], None)
                    else:
                        raise CatalogError(
                            "unsupported localization operation "
                            f"{operation_name}"
                        )
                except (CatalogError, ValueError) as error:
                    raise CatalogError(
                        f"cannot read {relative} Language_{locale}: {error}"
                    ) from error

        for locale, entries in file_entries.items():
            for key, fields in entries.items():
                normalized = catalog_fields(fields)
                if not normalized:
                    continue
                variants[locale][key].append({
                    "fields": normalized,
                    "sources": [relative],
                })

    sql_language_files = 0
    for path in sorted(
        (
            candidate.resolve()
            for candidate in art_root.rglob("*")
            if candidate.is_file()
            and candidate.suffix.casefold() == ".sql"
        ),
        key=lambda candidate: candidate.as_posix().casefold(),
    ):
        relative = source_name(path, root)
        try:
            text = path.read_text(
                encoding="utf-8-sig",
                errors="strict",
            )
        except (OSError, UnicodeError) as error:
            raise CatalogError(
                f"cannot read {relative}: {error}"
            ) from error
        if not re.search(
            r"\bLanguage_[A-Za-z0-9_]+\b",
            text,
            re.IGNORECASE,
        ):
            continue

        try:
            sql_entries, sql_skipped = parse_language_sql(text)
        except CatalogError as error:
            raise CatalogError(
                f"cannot read {relative}: {error}"
            ) from error
        sql_language_files += 1
        skipped_empty_selectors += sql_skipped

        for locale, entries in sql_entries.items():
            for key, fields in entries.items():
                normalized = catalog_fields(fields)
                if not normalized:
                    continue
                variants[locale][key].append({
                    "fields": normalized,
                    "sources": [relative],
                })

    deduplicated_references = {
        key: [
            {
                "table": table,
                "field": field,
                "path": path,
            }
            for table, field, path in sorted({
                (
                    record["table"],
                    record["field"],
                    record["path"],
                )
                for record in records
            })
        ]
        for key, records in references.items()
    }
    summary = {
        "xml_files": len(scanned_paths),
        "sql_language_files": sql_language_files,
        "locales": sorted(variants, key=str.casefold),
        "write_selectors": {
            locale: len(entries)
            for locale, entries in sorted(variants.items())
        },
        "references": len(deduplicated_references),
        "skipped_empty_selectors": skipped_empty_selectors,
    }
    return variants, deduplicated_references, summary


def locale_entries(
    localizations: dict[
        str, dict[str, list[dict[str, object]]]
    ],
    locale: str,
) -> dict[str, list[dict[str, object]]]:
    """Find repository definitions for a locale regardless of case."""
    return next(
        (
            entries
            for name, entries in localizations.items()
            if name.casefold() == locale.casefold()
        ),
        {},
    )
