"""Check language XML against Civ V's required SQLite localization columns."""

from __future__ import annotations

from contextlib import closing
import sqlite3
import xml.etree.ElementTree as ET
from xml.parsers import expat

from .common import CatalogError, quote_identifier


# These are the shipped BNW language tables, not the spelling of a Steam UI label.
LOCALES = ('en_US', 'DE_DE', 'ES_ES', 'FR_FR', 'IT_IT', 'JA_JP', 'KO_KR',
           'PL_PL', 'RU_RU', 'ZH_HANT_HK')
# The DLC schema exposes Language_* views over these LocalizedText values.
DLC_LANGUAGES = dict(zip(LOCALES, ('en_US', 'de_DE', 'es_ES', 'fr_FR', 'it_IT',
                                 'ja_JP', 'ko_KR', 'pl_PL', 'ru_RU', 'zh_Hant_HK')))
FIELDS = {name.casefold(): name for name in ('Tag', 'Text', 'Gender', 'Plurality')}
BLANK_TEXT = '\u00a0'


def runtime_text(value: str) -> str:
    """Represent an intentionally blank value with a visible-width blank glyph.

    A non-breaking space is nonempty UTF-8 text, including after ASCII XML
    whitespace condensation. Both paired and self-closing empty elements can
    otherwise reach the game's SQL binder as NULL. Keep canonical sources and
    translator drafts unchanged; this substitution belongs only in game output.
    """
    return value if value.strip() else BLANK_TEXT


def materialize_blank_text(document: str) -> str:
    """Patch blank language Text children without reformatting adjacent XML."""
    raw = document.encode('utf-8')
    parser = expat.ParserCreate()
    stack = []
    field = None
    replacements = []

    def start(name, attributes):
        nonlocal field
        stack.append(name)
        if field is not None and len(stack) > field[4]:
            field[5].append(True)
        if (field is None and name == 'Text' and any(node.startswith('Language_') for node in stack[:-1])
                and stack[-2] in ('Row', 'Replace', 'Set') and not attributes):
            at = parser.CurrentByteIndex
            end = raw.find(b'>', at) + 1
            field = (at, end, raw[at:end].rstrip().endswith(b'/>'), [], len(stack), [])

    def characters(value):
        if field is not None:
            field[3].append(value)

    def end(name):
        nonlocal field
        if field is not None and name == 'Text' and len(stack) == field[4]:
            at, opening_end, self_closing, parts, _, children = field
            if not children and not ''.join(parts).strip():
                finish = opening_end if self_closing else raw.find(b'>', parser.CurrentByteIndex) + 1
                replacements.append((at, finish))
            field = None
        stack.pop()

    parser.StartElementHandler = start
    parser.CharacterDataHandler = characters
    parser.EndElementHandler = end
    parser.Parse(raw, True)
    for at, finish in reversed(replacements):
        raw = raw[:at] + b'<Text>&#160;</Text>' + raw[finish:]
    return raw.decode('utf-8')


def validate_runtime_xml(document: str, *, keys: tuple[str, ...] = ()) -> dict:
    """Load language operations in one disposable SQLite transaction.

    Check both the core Tag/Text constraints and DLC view/trigger routing
    without modifying the game cache. Non-language tables are outside this check.
    This is a database compatibility gate, not a replacement for a game test.
    """
    if '<!DOCTYPE' in document.upper():
        raise CatalogError('DOCTYPE is not allowed in game localization XML')
    try:
        root = ET.fromstring(document)
    except (ET.ParseError, expat.ExpatError) as error:
        raise CatalogError(f'invalid game localization XML: {error}') from error
    if root.tag != 'GameData':
        raise CatalogError('game localization XML must have a GameData root')
    tables = {('Language_' + locale).casefold(): 'Language_' + locale for locale in LOCALES}
    loaded = set()
    with closing(sqlite3.connect(':memory:')) as database:
        database.execute('CREATE TABLE LocalizedText(Language TEXT, Tag TEXT, Text TEXT, '
                         'Gender TEXT, Plurality TEXT, PRIMARY KEY(Language, Tag))')
        for table in tables.values():
            database.execute(f'CREATE TABLE {quote_identifier(table)} ('
                             'ID INTEGER PRIMARY KEY AUTOINCREMENT, '
                             'Tag TEXT UNIQUE NOT NULL, Text TEXT NOT NULL, '
                             'Gender TEXT, Plurality TEXT)')
            locale = DLC_LANGUAGES[table[9:]]
            view = quote_identifier('DLC_' + table)
            database.execute(f'CREATE VIEW {view} AS SELECT Tag,Text,Gender,Plurality '
                             f"FROM LocalizedText WHERE Language='{locale}'")
            database.execute(f'CREATE TRIGGER {quote_identifier(table + "_insert")} '
                             f'INSTEAD OF INSERT ON {view} BEGIN '
                             'INSERT INTO LocalizedText(Language,Tag,Text,Gender,Plurality) '
                             f"VALUES('{locale}',NEW.Tag,NEW.Text,NEW.Gender,NEW.Plurality); END")
            database.execute(f'CREATE TRIGGER {quote_identifier(table + "_update")} '
                             f'INSTEAD OF UPDATE ON {view} BEGIN UPDATE LocalizedText SET '
                             'Text=NEW.Text,Gender=NEW.Gender,Plurality=NEW.Plurality '
                             f"WHERE Language='{locale}' AND Tag=NEW.Tag; END")
            database.execute(f'CREATE TRIGGER {quote_identifier(table + "_delete")} '
                             f'INSTEAD OF DELETE ON {view} BEGIN DELETE FROM LocalizedText '
                             f"WHERE Language='{locale}' AND Tag=OLD.Tag; END")

        def fields(element):
            values = {}
            if any(list(child) or child.attrib for child in element):
                raise CatalogError('localization fields must contain plain escaped text')
            for name, value in [*element.attrib.items(), *(
                    (child.tag, child.text) for child in element)]:
                canonical = FIELDS.get(name.casefold())
                if canonical is None or canonical in values:
                    raise CatalogError(f'unknown or duplicate localization field: {name}')
                if canonical == 'Text' and not value:
                    raise CatalogError('Empty game Text can bind as NULL and reject the entire file; '
                                       'render intentional blanks as a non-breaking space (&#160;)')
                values[canonical] = value
            return values

        try:
            with database:
                for block in root:
                    if not block.tag.startswith('Language_'):
                        continue
                    table = tables.get(block.tag.casefold())
                    if table is None:
                        raise CatalogError(f'unsupported Civ V language table: {block.tag}')
                    loaded.add(table)
                    quoted = quote_identifier(table)
                    dlc_view = quote_identifier('DLC_' + table)

                    def execute(sql, parameters):
                        database.execute(sql.format(table=quoted), parameters)
                        database.execute(sql.format(table=dlc_view), parameters)

                    for operation in block:
                        values = fields(operation) if operation.tag != 'Update' else {}
                        key = values.get('Tag', '(no Tag)')
                        try:
                            if operation.tag in ('Row', 'Replace'):
                                names = ','.join(quote_identifier(name) for name in values)
                                parameters = ','.join('?' for name in values)
                                policy = ' OR REPLACE' if operation.tag == 'Replace' else ''
                                execute(f'INSERT{policy} INTO {{table}} ({names}) '
                                        f'VALUES ({parameters})', tuple(values.values()))
                            elif operation.tag == 'Delete':
                                if not values:
                                    raise CatalogError('localization Delete needs a selector')
                                where = ' AND '.join(quote_identifier(name) + ' IS ?' for name in values)
                                execute(f'DELETE FROM {{table}} WHERE {where}', tuple(values.values()))
                            elif operation.tag == 'Update':
                                where_node, set_node = operation.find('Where'), operation.find('Set')
                                if where_node is None or set_node is None or len(operation) != 2:
                                    raise CatalogError('localization Update needs one Where and one Set')
                                selected = fields(where_node)
                                changed = fields(set_node)
                                key = selected.get('Tag', '(selector)')
                                if not selected or not changed:
                                    raise CatalogError('localization Update needs a selector and values')
                                where = ' AND '.join(quote_identifier(name) + ' IS ?' for name in selected)
                                assignments = ','.join(quote_identifier(name) + '=?' for name in changed)
                                execute(f'UPDATE {{table}} SET {assignments} WHERE {where}',
                                        (*changed.values(), *selected.values()))
                            else:
                                raise CatalogError(f'unsupported localization operation: {operation.tag}')
                        except sqlite3.Error as error:
                            raise CatalogError(f'Localization compatibility check rejected '
                                               f'{block.tag} {key}: {error}') from error
        except CatalogError:
            # The SDK XMLSerializer uses transactions by default. This gate
            # models that ordering; actual game loading still needs verification.
            raise
        counts = {table[9:]: database.execute(f'SELECT COUNT(*) FROM {quote_identifier(table)}').fetchone()[0]
                  for table in sorted(loaded)}
        for table in loaded:
            core = database.execute(f'SELECT Tag,Text,Gender,Plurality FROM {quote_identifier(table)} '
                                    'ORDER BY Tag').fetchall()
            dlc = database.execute(f'SELECT Tag,Text,Gender,Plurality FROM {quote_identifier("DLC_" + table)} '
                                   'ORDER BY Tag').fetchall()
            if core != dlc:
                raise CatalogError(f'core and DLC localization results differ for {table}')
        texts = {locale: {key: row[0] for key in keys
                          if (row := database.execute(f'SELECT Text FROM {quote_identifier("Language_" + locale)} '
                                                      'WHERE Tag=?', (key,)).fetchone()) is not None}
                 for locale in counts}
    return {'counts': counts, 'texts': texts, 'checked_schemas': ['core', 'dlc']}
