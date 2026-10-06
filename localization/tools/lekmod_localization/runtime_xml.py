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


def _self_closing_fields(document: str) -> set[tuple[int, tuple[str, ...]]]:
    """Retain the empty-element distinction that ElementTree discards.

    Preserve the paired empty-field convention already used by upstream
    Lekmod. We conservatively reject self-closing Text fields; this is an
    output policy, not a claim that our parser reproduces Civ V's parser.
    """
    raw = document.encode('utf-8')
    parser = expat.ParserCreate()
    stack = []
    ordinal = -1
    empty_fields = set()

    def start(name, attributes):
        nonlocal ordinal
        stack.append(name)
        if len(stack) >= 3 and stack[1].startswith('Language_'):
            if len(stack) == 3:
                ordinal += 1
            elif len(stack) >= 4 and name.casefold() in FIELDS:
                at = parser.CurrentByteIndex
                end = raw.find(b'>', at)
                if raw[at:end].rstrip().endswith(b'/'):
                    empty_fields.add((ordinal, tuple(stack[2:])))

    parser.StartElementHandler = start
    parser.EndElementHandler = lambda name: stack.pop()
    parser.Parse(raw, True)
    return empty_fields


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
        empty_fields = _self_closing_fields(document)
    except (ET.ParseError, expat.ExpatError) as error:
        raise CatalogError(f'invalid game localization XML: {error}') from error
    if root.tag != 'GameData':
        raise CatalogError('game localization XML must have a GameData root')
    tables = {('Language_' + locale).casefold(): 'Language_' + locale for locale in LOCALES}
    loaded = set()
    ordinal = -1
    with closing(sqlite3.connect(':memory:')) as database:
        database.execute('CREATE TABLE LocalizedText(Language TEXT, Tag TEXT, Text TEXT, '
                         'Gender TEXT, Plurality TEXT, PRIMARY KEY(Language, Tag))')
        for table in tables.values():
            database.execute(f'CREATE TABLE {quote_identifier(table)} ('
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

        def fields(element, path):
            values = {}
            for child in element:
                if child.tag.casefold() == 'text' and (ordinal, (*path, child.tag)) in empty_fields:
                    raise CatalogError('Use paired tags for intentional empty text: '
                                       '<Text></Text>, not <Text /> (compatibility policy)')
            for name, value in [*element.attrib.items(), *(
                    (child.tag, child.text or '') for child in element)]:
                canonical = FIELDS.get(name.casefold())
                if canonical is None or canonical in values:
                    raise CatalogError(f'unknown or duplicate localization field: {name}')
                values[canonical] = value
            if any(list(child) or child.attrib for child in element):
                raise CatalogError('localization fields must contain plain escaped text')
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
                        ordinal += 1
                        values = fields(operation, (operation.tag,)) if operation.tag != 'Update' else {}
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
                                selected = fields(where_node, ('Update', 'Where'))
                                changed = fields(set_node, ('Update', 'Set'))
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
