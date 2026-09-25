from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET

import audit_primary_localization as primary_audit


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = primary_audit.DEFAULT_SOURCE
DEFAULT_ART_ROOT = REPO_ROOT / "LEKMOD" / "Art"
DEFAULT_OUTPUT = REPO_ROOT / "build" / "localization" / "catalog.json"
DEFAULT_REVIEW_OUTPUT = REPO_ROOT / "build" / "localization" / "review"
SOURCE_LOCALE = "en_US"
TRANSLATABLE_FIELDS = ("Text", "Gender", "Plurality")
LEKMOD_SENTINELS = {
    "TXT_KEY_LEKMOD_VERSION",
    "TXT_KEY_LEKMOD_MENU_DISCORD",
}

BRACKET_TOKEN_RE = re.compile(r"\[[^\[\]]+\]")
BRACE_TOKEN_RE = re.compile(r"\{[^{}]+\}")
PRINTF_TOKEN_RE = re.compile(r"%(?:\d+\$)?[a-zA-Z%]")
KEY_RE = re.compile(r"^TXT_KEY_[A-Za-z0-9_]+$")
LANGUAGE_RE = re.compile(r"^Language_([A-Za-z0-9_]+)$")
PLACEHOLDER_RE = re.compile(r"\([A-Za-z_]+ text\)", re.IGNORECASE)
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

TABLE_CATEGORY_RULES = (
    ("great_people", re.compile(
        r"^Unit_UniqueNames(?:_|$)", re.IGNORECASE
    )),
    ("promotions", re.compile(
        r"^(?:Unit)?Promotions?(?:_|$)", re.IGNORECASE
    )),
    ("units", re.compile(
        r"^(?:Units?|UnitClasses|UnitCombatInfos)(?:_|$)", re.IGNORECASE
    )),
    ("wonders", re.compile(
        r"^(?:Projects)(?:_|$)", re.IGNORECASE
    )),
    ("buildings", re.compile(
        r"^(?:Buildings?|BuildingClasses|Specialists|Processes)(?:_|$)",
        re.IGNORECASE,
    )),
    ("city_states", re.compile(
        r"^(?:MinorCivilizations?)(?:_|$)", re.IGNORECASE
    )),
    ("civilizations", re.compile(
        r"^(?:Civilizations?|Leaders?|Traits?|CityNames|"
        r"SpyNames)(?:_|$)",
        re.IGNORECASE,
    )),
    ("great_works", re.compile(
        r"^(?:GreatWorks?|GreatWorkClasses)(?:_|$)", re.IGNORECASE
    )),
    ("technologies", re.compile(
        r"^(?:Technologies|Technology)(?:_|$)", re.IGNORECASE
    )),
    ("policies", re.compile(
        r"^(?:Policies|PolicyBranchTypes)(?:_|$)", re.IGNORECASE
    )),
    ("religion", re.compile(
        r"^(?:Beliefs|Religions|Religious)(?:_|$)", re.IGNORECASE
    )),
    ("improvements", re.compile(
        r"^(?:Improvements|Builds)(?:_|$)", re.IGNORECASE
    )),
    ("resources", re.compile(
        r"^(?:Resources)(?:_|$)", re.IGNORECASE
    )),
    ("terrain", re.compile(
        r"^(?:Features|Terrains)(?:_|$)", re.IGNORECASE
    )),
    ("world_congress", re.compile(
        r"^(?:League|Resolutions?)(?:_|$)", re.IGNORECASE
    )),
    ("diplomacy", re.compile(
        r"^(?:Diplomacy|Responses)(?:_|$)", re.IGNORECASE
    )),
    ("game_options", re.compile(
        r"^(?:GameOptions|Victories|Eras|HandicapInfos|GameSpeeds|Worlds|"
        r"Maps)(?:_|$)",
        re.IGNORECASE,
    )),
    ("concepts", re.compile(r"^(?:Concepts)(?:_|$)", re.IGNORECASE)),
    ("ui", re.compile(
        r"^(?:Notifications|Popup|Interface|Controls|Colors)(?:_|$)",
        re.IGNORECASE,
    )),
)

KEY_CATEGORY_RULES = (
    ("city_states", re.compile(
        r"^TXT_KEY_(?:CITYSTATE|CITY_STATE|MINOR_CIV|BUY_CITY_STATE|"
        r"MINOR_CITY|POP_CSTATE|BASE_INFLUENCE|INFLUENCE_.*CSTATE|"
        r"LEKMOD_CITY_STATE)"
    )),
    ("great_works", re.compile(r"^TXT_KEY_GREAT_WORK_")),
    ("great_people", re.compile(
        r"^TXT_KEY_(?:GREAT_(?:PERSON|ADMIRAL|GENERAL)_|DALAILAMA|"
        r"CHOOSEGP|SWEDISH_HUMANITARIAN|MISC_GREAT_PERSON)"
    )),
    ("wonders", re.compile(
        r"^TXT_KEY_(?:WONDER|GREAT_LIBRARY|HERMITAGE|LOUVRE|"
        r"GREAT_ZIMBABWE|OXFORD_UNIVERSITY|ISRAEL_NATIONAL_COLLEGE|"
        r"THEMING_BONUS)"
    )),
    ("civilopedia", re.compile(
        r"^TXT_KEY_(?:CIVLOPEDIA|CIVILOPEDIA|CIV5PEDIA|PEDIA_)"
    )),
    ("civilizations", re.compile(
        r"^TXT_KEY_(?:CIVILIZATION|CIV_|LEADER|TRAIT|CITY_NAME|SPY_NAME|"
        r"CIV5_DOM|CIV5_[A-Z0-9]+_(?:DOM|DAWN_OF_MAN)|CITY_(?!STATE_)|"
        r"GAUL_CITY|PHOENICIAN_CITY|MC_|DAWN_OF_MAN|UA_|LITE_AKKAD|"
        r"UAE(?:_|$)|AKSUM(?:_|$)|US_WALES|PMMM_LEADER|WELSH_LANGUAGE|"
        r"DOM_|GENERIC_LITE_AKKAD|MANNERHEIM_|NZ_MEET|ZABONAH_|"
        r"THP_BOLIVIA|UKRAINE_)"
    )),
    ("units", re.compile(
        r"^TXT_KEY_(?:UNIT|UNITCLASS|UNITCOMBAT|CIV5_UNIT|MISSION|RETRAIN)"
    )),
    ("buildings", re.compile(
        r"^TXT_KEY_(?:BUILDING|BUILDINGCLASS|SPECIALIST|PROJECT|"
        r"BURMA_BUILDING|CIV5_BUILDINGS)"
    )),
    ("promotions", re.compile(
        r"^TXT_KEY_(?:PROMOTION|ANTINAVAL_PROMO|RAIDER)"
    )),
    ("technologies", re.compile(r"^TXT_KEY_(?:TECH|TECHNOLOGY)")),
    ("policies", re.compile(
        r"^TXT_KEY_(?:POLICY|POLICY_BRANCH|SOCIAL_POLICY)"
    )),
    ("religion", re.compile(
        r"^TXT_KEY_(?:BELIEF|RELIGION|PANTHEON)"
    )),
    ("improvements", re.compile(
        r"^TXT_KEY_(?:IMPROVEMENT|BUILD_|MC_MAORI_PA)"
    )),
    ("resources", re.compile(
        r"^TXT_KEY_(?:RESOURCE|CIV5_RESOURCE)"
    )),
    ("diplomacy", re.compile(
        r"^TXT_KEY_(?:DIPLO|LEADER_MESSAGE|AI_DIPLO|ABLTY_D_PACT|"
        r"ALLOWS_DEFENSIVE_PACTS|DO_PACT|"
        r"MISC_PLAYERS_SIGN_DEFENSIVE_PACT)"
    )),
    ("game_options", re.compile(
        r"^TXT_KEY_(?:GAME_OPTION|GAMEOPTION|VICTORY|ERA|HANDICAP|"
        r"GAME_SPEED|GAMESPEED|WORLD|MAP_)"
    )),
    ("multiplayer", re.compile(
        r"^TXT_KEY_(?:MP_|WARN_MP|MESSAGE_MP|MISC_TURN_TIMER|"
        r"MULTIPLAYER|LEKMOD_MP|RESET_TURN_TIMER|CC_VOTE|"
        r"POSITIVE_VOTE|NEGATIVE_VOTE)"
    )),
    ("world_congress", re.compile(
        r"^TXT_KEY_(?:LEAGUE|RESOLUTION|LEKMOD_VOTE)"
    )),
    ("terrain", re.compile(
        r"^TXT_KEY_(?:FEATURE|BUGANDA_LAKE)"
    )),
    ("scenarios", re.compile(
        r"^TXT_KEY_(?:MEDIEVAL_SCENARIO|STEAMPUNK_SCENARIO)"
    )),
    ("gameplay", re.compile(
        r"^TXT_KEY_(?:COMBATMOD|ATTACKMOD|DEFENSEMOD|PRODMOD|GOLDMOD|"
        r"YIELD|PRODUCTION|GOODY|LEKMOD_(?:CHOOSE_)?GOODY|NO_ACTION|"
        r"CAN_PARADROP|JFD_GOLD|FAITH_FROM)"
    )),
    ("ui", re.compile(
        r"^TXT_KEY_(?:UI|MENU|OPTIONS|NOTIFICATION|POPUP|LEKMOD_MENU|"
        r"LEKMOD_MP|LEKMOD_|REPLAY|TP_|ACTIONTYPE|CONTROL|OPSCREEN|"
        r"CHOOSE_|"
        r"CO_|CITYVIEW|EUPANEL|COMBATPANEL|HOTKEY|RIGHT_BUTTON|"
        r"TOGGLE|TOP_PANEL|AD_SETUP|GAME_SELECTION|EO_|LARGE_ESPIONAGE|"
        r"OPEN_VICTORY|RANDOM_|INTERFACEMODE|TREASURY|HP$)"
    )),
    ("concepts", re.compile(
        r"^TXT_KEY_(?:CONCEPT|CIV5_|CULTURE_|GOLDEN_AGE_POINTS|"
        r"HAPPINESS_|TOURISM_)"
    )),
)

KEY_CATEGORY_REFINEMENTS = {
    "wonders": {"buildings"},
    "great_people": {"units"},
    "city_states": {"civilizations"},
    "improvements": {"resources"},
}

FIELD_SUBCATEGORIES = {
    "description": "names",
    "shortdescription": "names",
    "name": "names",
    "adjective": "adjectives",
    "help": "help",
    "strategy": "strategy",
    "civilopedia": "civilopedia",
    "quote": "quotes",
    "title": "headings",
    "heading": "headings",
    "caption": "headings",
    "response": "dialogue",
    "uniquenames": "names",
}


class CatalogError(ValueError):
    pass


def local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


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


def normalize_metadata(value: object) -> str | None:
    if value is None:
        return None

    normalized = " ".join(str(value).split())
    return normalized or None


def catalog_fields(fields: dict[str, object]) -> dict[str, str | None]:
    result = canonical_fields(fields)

    if "Text" in result:
        result["Text"] = normalize_text(result["Text"])

    for name in ("Gender", "Plurality"):
        if name in result:
            result[name] = normalize_metadata(result[name])

    return result


def normalized_fields(fields: dict[str, object]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}

    for name in TRANSLATABLE_FIELDS:
        value = fields.get(name)
        if name == "Text":
            value = normalize_text(value)
        elif name in {"Gender", "Plurality"}:
            value = normalize_metadata(value)
        result[name] = None if value is None else str(value)

    return result


def character_count(text: str | None) -> int | None:
    normalized = normalize_text(text)
    return None if normalized is None else len(normalized)


def token_counts(text: str | None) -> dict[str, int]:
    if not text:
        return {}

    tokens = (
        BRACKET_TOKEN_RE.findall(text)
        + BRACE_TOKEN_RE.findall(text)
        + PRINTF_TOKEN_RE.findall(text)
    )
    return dict(sorted(Counter(tokens).items()))


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


def load_language_table(
    connection: sqlite3.Connection,
    table_name: str,
) -> dict[str, dict[str, str | None]]:
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
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def exact_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if KEY_RE.fullmatch(normalized) else None


def reference_records(
    root: ET.Element,
    path: str,
) -> dict[str, list[dict[str, str]]]:
    references: dict[str, list[dict[str, str]]] = defaultdict(list)

    def visit(element: ET.Element, ancestors: list[str]) -> None:
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
    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.index = 0

    def done(self) -> bool:
        return self.index >= len(self.tokens)

    def peek(self) -> tuple[str, str] | None:
        return None if self.done() else self.tokens[self.index]

    def accept_symbol(self, symbol: str) -> bool:
        if self.peek() == ("symbol", symbol):
            self.index += 1
            return True
        return False

    def expect_symbol(self, symbol: str) -> None:
        if not self.accept_symbol(symbol):
            raise CatalogError(
                f"expected SQL symbol {symbol!r} at token {self.index}"
            )

    def accept_keyword(self, keyword: str) -> bool:
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
        if not self.accept_keyword(keyword):
            raise CatalogError(
                f"expected SQL keyword {keyword} at token {self.index}"
            )

    def identifier(self) -> str:
        token = self.peek()
        if token is None or token[0] != "identifier":
            raise CatalogError(
                f"expected SQL identifier at token {self.index}"
            )
        self.index += 1
        return token[1]

    def value(self) -> str | None:
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
    variants: dict[
        str, dict[str, list[dict[str, object]]]
    ] = defaultdict(lambda: defaultdict(list))
    references: dict[str, list[dict[str, str]]] = defaultdict(list)
    scanned_paths = xml_paths(source, art_root)
    skipped_empty_selectors = 0

    for path in scanned_paths:
        relative = source_name(path, root)
        try:
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
    return next(
        (
            entries
            for name, entries in localizations.items()
            if name.casefold() == locale.casefold()
        ),
        {},
    )


def coalesce_variants(
    variants: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}

    for variant in variants:
        fields = catalog_fields(dict(variant.get("fields", {})))
        identity = json.dumps(
            normalized_fields(fields),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if identity not in grouped:
            grouped[identity] = {
                "fields": fields,
                "sources": [],
            }
        grouped[identity]["sources"].extend(
            variant.get("sources", [])
        )

    result = []
    for identity in sorted(grouped):
        variant = grouped[identity]
        variant["sources"] = sorted(set(variant["sources"]))
        result.append(variant)
    return result


def text_variant(
    fields: dict[str, object],
    sources: list[str] | None = None,
) -> dict[str, object]:
    normalized = catalog_fields(fields)
    text = normalized.get("Text")
    return {
        "text": text,
        "characters": character_count(text),
        "gender": normalized.get("Gender"),
        "plurality": normalized.get("Plurality"),
        "fields_present": sorted(normalized),
        "format_tokens": token_counts(text),
        "sources": sorted(set(sources or [])),
    }


def source_text_record(
    variants: list[dict[str, object]],
) -> dict[str, object]:
    compact = coalesce_variants(variants)
    if not compact:
        return {
            "status": "missing",
            "text": None,
            "characters": None,
            "gender": None,
            "plurality": None,
            "fields_present": [],
            "format_tokens": {},
            "sources": [],
        }
    if len(compact) == 1:
        variant = compact[0]
        return {
            "status": "present",
            **text_variant(
                variant["fields"],
                variant["sources"],
            ),
        }
    return {
        "status": "source_conflict",
        "text": None,
        "characters": None,
        "gender": None,
        "plurality": None,
        "fields_present": [],
        "format_tokens": {},
        "sources": sorted({
            source
            for variant in compact
            for source in variant["sources"]
        }),
        "variants": [
            text_variant(
                variant["fields"],
                variant["sources"],
            )
            for variant in compact
        ],
    }


def official_game_record(
    key: str,
    locale: str,
    vanilla_english: dict[str, dict[str, object]],
    vanilla_target: dict[str, dict[str, object]],
) -> dict[str, object]:
    if key not in vanilla_english:
        return {
            "locale": locale,
            "status": "not_in_vanilla",
            "text": None,
            "characters": None,
            "gender": None,
            "plurality": None,
            "fields_present": [],
            "format_tokens": {},
        }
    if key not in vanilla_target:
        return {
            "locale": locale,
            "status": "missing_in_vanilla_locale",
            "text": None,
            "characters": None,
            "gender": None,
            "plurality": None,
            "fields_present": [],
            "format_tokens": {},
        }
    return {
        "locale": locale,
        "status": "present",
        **{
            name: value
            for name, value in text_variant(
                vanilla_target[key]
            ).items()
            if name not in {"sources", "fields_present"}
        },
    }


def target_text_record(
    locale: str,
    variants: list[dict[str, object]],
    english: dict[str, object],
) -> dict[str, object]:
    record = source_text_record(variants)
    record["locale"] = locale
    if record["status"] == "present":
        text = record.get("text")
        record["status"] = (
            "placeholder"
            if isinstance(text, str) and PLACEHOLDER_RE.search(text)
            else "present"
        )
        record["same_as_english"] = (
            normalize_text(text) is not None
            and normalize_text(text)
            == normalize_text(english.get("text"))
        )
    return record


def explicit_metadata_changed(
    source_fields: dict[str, object],
    vanilla_fields: dict[str, object],
) -> bool:
    for name in ("Gender", "Plurality"):
        if name not in source_fields:
            continue

        source_value = normalize_metadata(source_fields.get(name))
        vanilla_value = normalize_metadata(vanilla_fields.get(name))

        if source_value != vanilla_value:
            return True

    return False


def category_from_table(table: str) -> str | None:
    for category, pattern in TABLE_CATEGORY_RULES:
        if pattern.match(table):
            return category
    return None


def category_from_key(key: str) -> str | None:
    for category, pattern in KEY_CATEGORY_RULES:
        if pattern.match(key):
            return category
    return None


def subcategory_from_key(
    key: str,
    category: str,
    include_default: bool = True,
) -> str | None:
    if category == "civilizations":
        if (
            "_SPY_NAME_" in key
            or key.startswith("TXT_KEY_SPY_NAME_")
        ):
            return "spy_names"
        if (
            "DAWN_OF_MAN" in key
            or key.startswith("TXT_KEY_DOM_")
            or "_DOM" in key
        ):
            return "dawn_of_man"
        if key.startswith(("TXT_KEY_TRAIT_", "TXT_KEY_UA_")):
            if any(token in key for token in (
                "_PEDIA",
                "_TEXT",
                "_HEADING",
            )):
                return "civilopedia"
            if "_SHORT" in key:
                return "trait_names"
            return "trait_descriptions"
        if key.startswith("TXT_KEY_LEADER_"):
            if any(token in key for token in (
                "_PEDIA",
                "_TEXT",
                "_HEADING",
                "_LIVED",
                "_SUBTITLE",
                "_TITLES",
                "_FACTOID",
                "_STATBOX",
                "_FACT_",
            )):
                return "civilopedia"
            if any(token in key for token in (
                "FIRSTGREETING",
                "FIRST_GREETING",
                "GREETING",
                "DEFEATED",
                "DECLARE_WAR",
                "ATTACKED",
                "RESPONSE_",
                "DIPLO_",
            )) or re.search(r"_[0-9]+$", key):
                return "dialogue"
            return "leader_names"
        if (
            "_CITY_NAME_" in key
            or key.startswith("TXT_KEY_CITY_NAME_")
            or key.startswith("TXT_KEY_GAUL_CITY_")
            or key.startswith("TXT_KEY_PHOENICIAN_CITY_")
            or re.match(r"^TXT_KEY_CITY_(?!STATE_)", key)
        ):
            return "city_names"
        if "_ADJECTIVE" in key or "_ADJ" in key:
            return "adjectives"
        if any(token in key for token in (
            "_PEDIA",
            "_TEXT",
            "_HEADING",
        )):
            return "civilopedia"
        return "names" if include_default else None

    if category == "city_states":
        if "DIPLO_" in key:
            return "dialogue"
        if "_PEDIA" in key or "_TEXT" in key:
            return "civilopedia"
        if "_HELP" in key or key.startswith("TXT_KEY_BUY_"):
            return "help"
        if "_ADJ" in key or "_ADJECTIVE" in key:
            return "adjectives"
        if "PERSONALITY" in key:
            return "personalities"
        if key.startswith((
            "TXT_KEY_POP_CSTATE_",
            "TXT_KEY_BASE_INFLUENCE_",
            "TXT_KEY_INFLUENCE_",
            "TXT_KEY_LEKMOD_CITY_STATE_",
        )):
            return "interface"
        return "names" if include_default else None

    if category == "civilopedia":
        if "PROMOTION" in key:
            return "promotions"
        if "LEADER" in key:
            return "leaders"
        if "CIVILIZATION" in key or "DAWN_OF_MAN" in key:
            return "civilizations"
        if "UNIT" in key:
            return "units"
        if "BUILDING" in key or "IMPROVEMENT" in key:
            return "buildings_and_improvements"
        if "SCENARIO" in key:
            return "scenarios"
        if "CATEGORY" in key:
            return "headings"
        if "FACTOID" in key:
            return "factoids"
        if "STATBOX" in key:
            return "statboxes"
        if "_HEADING" in key:
            return "headings"
        if "_TITLE" in key:
            return "titles"
        if "_TEXT" in key or key.endswith("_PEDIA"):
            return "articles"
        return "articles" if include_default else None

    if category == "religion":
        if key.startswith("TXT_KEY_BELIEF_"):
            if "_SHORT" in key:
                return "belief_names"
            return "belief_descriptions"
        if key.startswith("TXT_KEY_RELIGION_"):
            return "religion_names"

    if category == "ui":
        prefixes = (
            ("TXT_KEY_NOTIFICATION_", "notifications"),
            ("TXT_KEY_POPUP_", "popups"),
            ("TXT_KEY_REPLAY_", "replay"),
            ("TXT_KEY_CONTROL_", "controls_and_hotkeys"),
            ("TXT_KEY_HOTKEY_", "controls_and_hotkeys"),
            ("TXT_KEY_OPSCREEN_", "controls_and_hotkeys"),
            ("TXT_KEY_ACTIONTYPE_", "controls_and_hotkeys"),
            ("TXT_KEY_INTERFACEMODE_", "controls_and_hotkeys"),
            ("TXT_KEY_RIGHT_BUTTON_", "controls_and_hotkeys"),
            ("TXT_KEY_TOGGLE_", "controls_and_hotkeys"),
            ("TXT_KEY_TP_", "top_panel"),
            ("TXT_KEY_TOP_PANEL_", "top_panel"),
            ("TXT_KEY_TREASURY_", "top_panel"),
            ("TXT_KEY_CITYVIEW_", "city_view"),
            ("TXT_KEY_COMBATPANEL_", "combat_panel"),
            ("TXT_KEY_EUPANEL_", "combat_panel"),
            ("TXT_KEY_HP", "combat_panel"),
            ("TXT_KEY_CHOOSE_INTERNATIONAL_", "trade_routes"),
            ("TXT_KEY_LEKMOD_MENU_", "menus"),
            ("TXT_KEY_LEKMOD_VERSION", "menus"),
            ("TXT_KEY_AD_SETUP_", "game_setup"),
            ("TXT_KEY_GAME_SELECTION_", "game_setup"),
            ("TXT_KEY_RANDOM_", "game_setup"),
            ("TXT_KEY_CO_", "diplomacy_overview"),
            ("TXT_KEY_EO_", "diplomacy_overview"),
            ("TXT_KEY_LEKMOD_RESPOND_", "diplomacy_overview"),
            ("TXT_KEY_LARGE_ESPIONAGE_", "espionage"),
            ("TXT_KEY_OPEN_VICTORY_", "victory"),
            ("TXT_KEY_UI_CHECK_", "compatibility"),
        )
        for prefix, subcategory in prefixes:
            if key.startswith(prefix):
                return subcategory

    if category == "gameplay":
        prefixes = (
            ("TXT_KEY_COMBATMOD_", "combat_modifiers"),
            ("TXT_KEY_ATTACKMOD_", "combat_modifiers"),
            ("TXT_KEY_DEFENSEMOD_", "combat_modifiers"),
            ("TXT_KEY_YIELD_", "yield_breakdowns"),
            ("TXT_KEY_FAITH_FROM_", "yield_breakdowns"),
            ("TXT_KEY_JFD_GOLD_", "yield_breakdowns"),
            ("TXT_KEY_PRODMOD_", "production_modifiers"),
            ("TXT_KEY_PRODUCTION_", "production_modifiers"),
            ("TXT_KEY_GOLDMOD_", "gold_modifiers"),
            ("TXT_KEY_GOODY_", "goody_huts"),
            ("TXT_KEY_LEKMOD_GOODY_", "goody_huts"),
            ("TXT_KEY_LEKMOD_CHOOSE_GOODY_", "goody_huts"),
            ("TXT_KEY_CAN_PARADROP", "unit_actions"),
            ("TXT_KEY_NO_ACTION_", "unit_actions"),
        )
        for prefix, subcategory in prefixes:
            if key.startswith(prefix):
                return subcategory

    if category == "diplomacy":
        if key.startswith("TXT_KEY_DIPLOSTACK_"):
            return "interface"
        if "_ITEM_" in key:
            return "trade_items"
        if key.endswith("_TT"):
            return "tooltips"
        if any(token in key for token in (
            "PACT",
            "EMBASSY",
            "OPENBORDERS",
            "AGREEMENT",
        )):
            return "agreements"
        if "REQUEST" in key:
            return "requests"

    if category == "concepts" and "_SUMMARY" in key:
        return "summaries"

    if category == "multiplayer":
        if any(token in key for token in (
            "PROPOSAL",
            "PROPOSE",
            "VOTE_CHART",
            "CC_VOTE",
        )):
            return "proposals"
        if "TURN_TIMER" in key:
            return "turn_timer"
        if key.startswith("TXT_KEY_MULTIPLAYER_"):
            return "lobby"
        if key.startswith("TXT_KEY_LEKMOD_MP_"):
            return "status"

    if category == "world_congress":
        if key.startswith("TXT_KEY_RESOLUTION_"):
            return "resolutions"
        return "interface"

    if category == "great_people":
        if "POINTS_GAINED" in key:
            return "progress"
        return "names" if include_default else None

    if category == "great_works":
        return "names" if include_default else None

    if category == "scenarios":
        if "MEDIEVAL_SCENARIO" in key:
            return "medieval"
        if "STEAMPUNK_SCENARIO" in key:
            return "steampunk"

    if category == "terrain":
        if "_HELP" in key:
            return "help"
        if "_TEXT" in key or "_PEDIA" in key:
            return "civilopedia"
        return "features" if include_default else None

    suffixes = (
        ("_STRATEGY", "strategy"),
        ("_HELP", "help"),
        ("_PEDIA", "civilopedia"),
        ("_QUOTE", "quotes"),
        ("_ADJ", "adjectives"),
        ("_SHORT_DESC", "names"),
        ("_DESC", "names"),
        ("_NAME", "names"),
        ("_TITLE", "headings"),
        ("_HEADING", "headings"),
        ("_BODY", "civilopedia"),
        ("_TEXT", "civilopedia"),
    )
    for suffix, subcategory in suffixes:
        if key.endswith(suffix) or f"{suffix}_" in key:
            return subcategory

    if not include_default:
        return None
    if category in {
        "buildings",
        "game_options",
        "improvements",
        "policies",
        "promotions",
        "resources",
        "technologies",
        "units",
        "wonders",
    }:
        return "names"
    return "general"


def classify_context(
    key: str,
    references: list[dict[str, str]],
) -> tuple[str, str, str]:
    context_categories = {
        category
        for reference in references
        if (
            category := category_from_table(reference["table"])
        ) is not None
    }
    key_category = category_from_key(key)

    if len(context_categories) == 1:
        context_category = next(iter(context_categories))
        if (
            key_category in KEY_CATEGORY_REFINEMENTS
            and context_category
            in KEY_CATEGORY_REFINEMENTS[key_category]
        ):
            category = key_category
            category_source = "key_refinement"
        else:
            category = context_category
            category_source = "database_reference"
    elif key_category is not None:
        category = key_category
        category_source = "key_fallback"
    else:
        category = "unclassified"
        category_source = "unclassified"

    subcategories = {
        FIELD_SUBCATEGORIES[field]
        for reference in references
        if (
            field := reference["field"].casefold()
        ) in FIELD_SUBCATEGORIES
    }
    key_subcategory = subcategory_from_key(
        key,
        category,
        include_default=False,
    )

    if len(subcategories) == 1:
        database_subcategory = next(iter(subcategories))
        if (
            key_subcategory is not None
            and key_subcategory != database_subcategory
        ):
            subcategory = key_subcategory
            subcategory_source = "key_refinement"
        else:
            subcategory = database_subcategory
            subcategory_source = "database_reference"
    elif key_subcategory is not None:
        subcategory = key_subcategory
        subcategory_source = "key_fallback"
    else:
        subcategory = subcategory_from_key(
            key,
            category,
            include_default=True,
        )
        subcategory_source = "key_fallback"

    return (
        category,
        subcategory,
        f"{category_source}/{subcategory_source}",
    )


def source_variants_from_report(
    source_report: dict,
) -> dict[str, list[dict[str, object]]]:
    return {
        key: [{
            "fields": catalog_fields(fields),
            "sources": [source_report["source"]],
        }]
        for key, fields in source_report["entries"].items()
    }


def build_catalog(
    source_report: dict,
    vanilla_entries: dict[str, dict[str, object]],
    vanilla_locales: list[str],
    vanilla_database_name: str,
    vanilla_fingerprint: str,
    repository_localizations: (
        dict[str, dict[str, list[dict[str, object]]]] | None
    ) = None,
    references: dict[str, list[dict[str, str]]] | None = None,
    repository_summary: dict[str, object] | None = None,
) -> dict:
    if source_report["summary"]["errors"]:
        raise CatalogError(
            "primary localization source contains audit errors"
        )

    if repository_localizations is None:
        english_variants = source_variants_from_report(source_report)
    else:
        english_variants = locale_entries(
            repository_localizations,
            SOURCE_LOCALE,
        )

    references = references or {}
    classification_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    subcategory_counts: dict[
        str, Counter[str]
    ] = defaultdict(Counter)
    categories: dict[
        str, dict[str, dict[str, dict[str, object]]]
    ] = defaultdict(lambda: defaultdict(dict))
    metadata_review: dict[str, dict[str, object]] = {}

    for key in sorted(english_variants):
        english = source_text_record(english_variants[key])
        vanilla_fields = vanilla_entries.get(key)

        if english["status"] == "source_conflict":
            classification = "source_conflict"
        else:
            field_values = {
                "Text": english.get("text"),
                "Gender": english.get("gender"),
                "Plurality": english.get("plurality"),
            }
            source_fields = {
                name: field_values[name]
                for name in english.get("fields_present", [])
            }
            if vanilla_fields is None:
                classification = "lekmod_new"
            elif "Text" in source_fields and (
                source_fields.get("Text")
                != vanilla_fields.get("Text")
            ):
                classification = "vanilla_modified"
            elif explicit_metadata_changed(
                source_fields,
                vanilla_fields,
            ):
                classification = "vanilla_metadata_only"
            else:
                classification = "vanilla_unchanged"

        classification_counts[classification] += 1
        context = references.get(key, [])
        category, subcategory, context_source = classify_context(
            key,
            context,
        )

        if classification in {
            "vanilla_unchanged",
            "vanilla_metadata_only",
        }:
            if classification == "vanilla_metadata_only":
                metadata_review[key] = {
                    "category": category,
                    "subcategory": subcategory,
                    "lekmod_en_US": english,
                }
            continue

        category_counts[category] += 1
        subcategory_counts[category][subcategory] += 1
        categories[category][subcategory][key] = {
            "classification": classification,
            "context_source": context_source,
            "contexts": context,
            "lekmod_en_US": english,
        }

    ordered_categories = {
        category: {
            subcategory: dict(sorted(entries.items()))
            for subcategory, entries in sorted(
                categories[category].items()
            )
        }
        for category in sorted(categories)
    }
    target_locales = [
        locale
        for locale in vanilla_locales
        if locale.casefold() != SOURCE_LOCALE.casefold()
    ]
    ready = (
        classification_counts["lekmod_new"]
        + classification_counts["vanilla_modified"]
    )
    source_review = classification_counts["source_conflict"]

    return {
        "schema_version": 2,
        "source": {
            "file": source_report["source"],
            "sha256": source_report["source_sha256"],
            "locale": SOURCE_LOCALE,
            "primary_entries": source_report["summary"]["entries"],
            "combined_english_selectors": len(english_variants),
            "repository": repository_summary or {
                "xml_files": 1,
                "locales": [SOURCE_LOCALE],
            },
        },
        "vanilla": {
            "database": vanilla_database_name,
            "locale": SOURCE_LOCALE,
            "entries": len(vanilla_entries),
            "content_sha256": vanilla_fingerprint,
        },
        "locales": [SOURCE_LOCALE, *target_locales],
        "summary": {
            "classifications": dict(
                sorted(classification_counts.items())
            ),
            "requires_translation": ready,
            "requires_source_review": source_review,
            "workspace_entries": ready + source_review,
            "categories": dict(sorted(category_counts.items())),
            "subcategories": {
                category: dict(sorted(counts.items()))
                for category, counts in sorted(
                    subcategory_counts.items()
                )
            },
        },
        "source_categories": ordered_categories,
        "review": {
            "metadata_only": metadata_review,
        },
    }


def build_locale_review(
    catalog: dict,
    locale: str,
    vanilla_english: dict[str, dict[str, object]],
    vanilla_target: dict[str, dict[str, object]],
    repository_localizations: dict[
        str, dict[str, list[dict[str, object]]]
    ],
) -> tuple[dict[str, dict], dict[str, object]]:
    target_entries = locale_entries(
        repository_localizations,
        locale,
    )
    documents = {}
    status_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()

    for category, subcategories in (
        catalog["source_categories"].items()
    ):
        output_subcategories = {}
        category_statuses: Counter[str] = Counter()

        for subcategory, entries in subcategories.items():
            output_entries = {}
            for key, source_entry in entries.items():
                if (
                    source_entry["classification"]
                    == "source_conflict"
                ):
                    continue

                english = source_entry["lekmod_en_US"]
                target = target_text_record(
                    locale,
                    target_entries.get(key, []),
                    english,
                )
                official = official_game_record(
                    key,
                    locale,
                    vanilla_english,
                    vanilla_target,
                )
                status_counts[target["status"]] += 1
                category_statuses[target["status"]] += 1
                classification_counts[
                    source_entry["classification"]
                ] += 1
                output_entries[key] = {
                    "classification": (
                        source_entry["classification"]
                    ),
                    "official_game": official,
                    "lekmod_en_US": english,
                    "lekmod_target": target,
                    "context_source": (
                        source_entry["context_source"]
                    ),
                    "contexts": source_entry["contexts"],
                }

            if output_entries:
                output_subcategories[subcategory] = output_entries

        if not output_subcategories:
            continue

        documents[category] = {
            "schema_version": 1,
            "locale": locale,
            "category": category,
            "summary": {
                "entries": sum(
                    len(entries)
                    for entries in output_subcategories.values()
                ),
                "target_status": dict(
                    sorted(category_statuses.items())
                ),
            },
            "subcategories": output_subcategories,
        }

    manifest = {
        "schema_version": 1,
        "locale": locale,
        "generated_from": "catalog.json",
        "summary": {
            "entries": sum(status_counts.values()),
            "target_status": dict(sorted(status_counts.items())),
            "classifications": dict(
                sorted(classification_counts.items())
            ),
            "categories": {
                category: document["summary"]["entries"]
                for category, document in sorted(documents.items())
            },
        },
        "files": [
            f"{category}.json"
            for category in sorted(documents)
        ],
    }
    return documents, manifest


def build_source_conflict_review(catalog: dict) -> dict:
    categories: dict[
        str, dict[str, dict[str, dict[str, object]]]
    ] = {}
    category_counts: Counter[str] = Counter()
    subcategory_counts: dict[
        str, Counter[str]
    ] = defaultdict(Counter)

    for category, subcategories in (
        catalog["source_categories"].items()
    ):
        output_subcategories = {}

        for subcategory, entries in subcategories.items():
            output_entries = {}

            for key, entry in entries.items():
                if entry["classification"] != "source_conflict":
                    continue

                output_entries[key] = {
                    "context_source": entry["context_source"],
                    "contexts": entry["contexts"],
                    "lekmod_en_US": entry["lekmod_en_US"],
                }
                category_counts[category] += 1
                subcategory_counts[category][subcategory] += 1

            if output_entries:
                output_subcategories[subcategory] = output_entries

        if output_subcategories:
            categories[category] = output_subcategories

    return {
        "schema_version": 1,
        "status": "developer_review_required",
        "summary": {
            "entries": sum(category_counts.values()),
            "categories": dict(sorted(category_counts.items())),
            "subcategories": {
                category: dict(sorted(counts.items()))
                for category, counts in sorted(
                    subcategory_counts.items()
                )
            },
        },
        "categories": categories,
    }


def write_json_atomic(path: Path, value: object) -> None:
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


def write_catalog(
    path: Path,
    catalog: dict,
    source: Path,
    vanilla_db: Path,
) -> None:
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
            "schema_version": 2,
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


def canonical_locale(
    locale: str,
    available: list[str],
) -> str:
    match = next(
        (
            name
            for name in available
            if name.casefold() == locale.casefold()
        ),
        None,
    )
    if match is None:
        raise CatalogError(
            f"Language_{locale} was not found; available locales: "
            + ", ".join(sorted(available, key=str.casefold))
        )
    return match


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a categorized, per-locale Lekmod translation review "
            "workspace from repository sources and a clean Civilization V "
            "localization database."
        )
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
    )
    parser.add_argument(
        "--art-root",
        type=Path,
        default=DEFAULT_ART_ROOT,
    )
    parser.add_argument(
        "--vanilla-db",
        type=Path,
        required=True,
        help="Path to a clean Civilization V Localization-Merged.db.",
    )
    parser.add_argument(
        "--locale",
        action="append",
        metavar="LOCALE",
        help=(
            "Target locale to generate. Repeat for multiple locales; "
            "the default is every non-English locale in the clean database."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        default=DEFAULT_REVIEW_OUTPUT,
    )
    args = parser.parse_args()

    source = args.source.resolve()
    art_root = args.art_root.resolve()
    vanilla_db = args.vanilla_db.resolve()
    source_report = primary_audit.parse_source(
        source,
        SOURCE_LOCALE,
    )

    try:
        all_vanilla, fingerprints = load_vanilla_locales(
            vanilla_db
        )
        available = sorted(all_vanilla, key=str.casefold)
        english_locale = canonical_locale(
            SOURCE_LOCALE,
            available,
        )
        requested = (
            [
                canonical_locale(locale, available)
                for locale in args.locale
            ]
            if args.locale
            else [
                locale
                for locale in available
                if locale.casefold()
                != SOURCE_LOCALE.casefold()
            ]
        )
        target_locales = list(dict.fromkeys(requested))
        if any(
            locale.casefold() == SOURCE_LOCALE.casefold()
            for locale in target_locales
        ):
            raise CatalogError(
                "--locale must select a non-English target locale"
            )

        localizations, references, repository_summary = (
            load_repository_localizations(
                source,
                art_root,
            )
        )
        catalog = build_catalog(
            source_report,
            all_vanilla[english_locale],
            [english_locale, *target_locales],
            vanilla_db.name,
            fingerprints[english_locale],
            localizations,
            references,
            repository_summary,
        )
        source_conflicts = build_source_conflict_review(catalog)
        reviews = {
            locale: build_locale_review(
                catalog,
                locale,
                all_vanilla[english_locale],
                all_vanilla[locale],
                localizations,
            )
            for locale in target_locales
        }
        write_catalog(
            args.output,
            catalog,
            source,
            vanilla_db,
        )
        write_review_workspace(
            args.review_output,
            reviews,
            source,
            vanilla_db,
            source_conflicts,
        )
    except (CatalogError, OSError, sqlite3.Error) as error:
        parser.exit(
            1,
            f"Cannot build localization workspace: {error}\n",
        )

    classifications = catalog["summary"]["classifications"]
    print(
        "Primary English entries: "
        f"{catalog['source']['primary_entries']}"
    )
    print(
        "Combined English selectors: "
        f"{catalog['source']['combined_english_selectors']}"
    )
    print(
        "Vanilla English entries: "
        f"{catalog['vanilla']['entries']}"
    )
    print(
        "Vanilla unchanged: "
        f"{classifications.get('vanilla_unchanged', 0)}"
    )
    print(
        "Vanilla metadata-only changes: "
        f"{classifications.get('vanilla_metadata_only', 0)}"
    )
    print(
        "Vanilla modified: "
        f"{classifications.get('vanilla_modified', 0)}"
    )
    print(
        "Lekmod new: "
        f"{classifications.get('lekmod_new', 0)}"
    )
    print(
        "Source conflicts: "
        f"{classifications.get('source_conflict', 0)}"
    )
    print(
        "Entries ready for translation: "
        f"{catalog['summary']['requires_translation']}"
    )
    print(f"Target locales generated: {len(reviews)}")
    print(f"Catalog: {args.output}")
    print(f"Review workspace: {args.review_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
