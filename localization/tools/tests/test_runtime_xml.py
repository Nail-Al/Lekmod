"""Prevent a rejected language row from removing unrelated menu text in game."""

from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.common import CatalogError, REPO_ROOT, token_counts, normalize_text
from lekmod_localization.runtime_xml import BLANK_TEXT, LOCALES, materialize_blank_text, validate_runtime_xml
from lekmod_localization.shipped import install_candidate, render_blocks
from lekmod_localization.shipped import approved_entries
from lekmod_localization.catalog import build_catalog
from lekmod_localization.workspace import editor_source_fingerprint
import audit_primary_localization
import sync_primary_english


KEYS = ('TXT_KEY_LEKMOD_MENU_DISCORD', 'TXT_KEY_LEKMOD_MENU_GITHUB',
        *('TXT_KEY_LEKMOD_MENU_VERSION_' + state for state in
          ('CHECKING', 'OUTDATED', 'UNKNOWN', 'UNREACHABLE', 'UPTODATE')))
RUSSIAN = (
    'Lekmod запущен {1_Version} — [COLOR_YELLOW]Проверка обновлений...[ENDCOLOR]',
    'Lekmod запущен {1_Version} — [COLOR_WARNING_TEXT]Доступна новая версия({2_Latest})[ENDCOLOR] '
    '[COLOR_CYAN](НАЖМИТЕ, ЧТОБЫ СКАЧАТЬ)[ENDCOLOR]',
    'Lekmod запущен {1_Version}',
    'Lekmod запущен {1_Version} - [COLOR_WARNING_TEXT]Не удалось подключиться к серверу обновлений[ENDCOLOR]',
    'Lekmod запущен {1_Version} - [COLOR_POSITIVE_TEXT]Установлена последняя версия![ENDCOLOR]',
)


class RuntimeXMLTests(unittest.TestCase):
    def test_dlc_trigger_operations_match_core_with_repeated_keys(self):
        """Replace, Update and Delete preserve order through the actual DLC routing."""
        document = '''<GameData><Language_RU_RU>
            <Row Tag="TXT_KEY_ONE"><Text>One</Text><Gender>feminine</Gender></Row>
            <Replace Tag="TXT_KEY_ONE"><Text>Два &amp; &lt;три&gt;</Text></Replace>
            <Update><Where Tag="TXT_KEY_ONE"/><Set><Text>Четыре</Text></Set></Update>
            <Row Tag="TXT_KEY_TWO"><Text>Remove</Text></Row>
            <Delete Tag="TXT_KEY_TWO"/>
            </Language_RU_RU></GameData>'''
        result = validate_runtime_xml(document, keys=('TXT_KEY_ONE', 'TXT_KEY_TWO'))
        self.assertEqual(result['checked_schemas'], ['core', 'dlc'])
        self.assertEqual(result['counts'], {'RU_RU': 1})
        self.assertEqual(result['texts'], {'RU_RU': {'TXT_KEY_ONE': 'Четыре'}})

    def test_bazaar_help_reference_override_translation_and_english_change(self):
        """Trace the real Bazaar key; source edits invalidate its old translation."""
        key = 'TXT_KEY_BUILDING_BAZAAR_HELP'
        gameplay = ET.parse(REPO_ROOT / 'LEKMOD/Override/CIV5Units.xml').getroot()
        self.assertEqual(gameplay.find("./Buildings/Row[Type='BUILDING_BAZAAR']/Help").text, key)
        source = REPO_ROOT / 'localization/en_US/primary.xml'
        node = ET.parse(source).getroot().find(f"./Language_en_US/Replace[@Tag='{key}']")
        self.assertIsNotNone(node)
        english = normalize_text(node.findtext('Text'))
        self.assertIn('Provides +1 [ICON_GOLD] Gold to every', english)
        self.assertIn('[COLOR_POSITIVE_TEXT]Luxury Resource[ENDCOLOR]', english)
        vanilla = 'Provides 1 extra copy of each improved luxury resource near this City.'
        report = audit_primary_localization.parse_source(source, 'en_US')
        report['entries'] = {name: report['entries'][name] for name in (key, *KEYS[:2])}
        catalog = build_catalog(report, {key: {'Text': vanilla}}, ['en_US', 'RU_RU'],
                                'test vanilla reference', 'fixture')
        entry = next(rows[key] for categories in catalog['source_categories'].values()
                     for rows in categories.values() if key in rows)
        self.assertEqual(entry['classification'], 'vanilla_modified')
        locales = ['RU_RU', 'DE_DE']
        targets, _ = approved_entries(catalog, locales, {})
        base = ET.Element('GameData')
        language = ET.SubElement(base, 'Language_en_US')
        old = ET.SubElement(language, 'Row', {'Tag': key})
        ET.SubElement(old, 'Text').text = vanilla
        # The actual Lekmod Replace supersedes the already loaded vanilla row.
        language.append(node)
        for name in KEYS[:2]:
            row = ET.SubElement(language, 'Row', {'Tag': name})
            ET.SubElement(row, 'Text').text = report['entries'][name]['Text']
        ET.indent(base, space='  ')
        document = ET.tostring(base, encoding='unicode')
        candidate = install_candidate(document, render_blocks(targets), locales)
        texts = validate_runtime_xml(candidate, keys=(key, *KEYS[:2]))['texts']
        self.assertEqual(normalize_text(texts['en_US'][key]), english)
        self.assertEqual(texts['RU_RU'][key], english)
        translated = '[ICON_CIVILIZATION_ARABIA] [ICON_BUILDING_MARKET] [ICON_GOLD] '
        translated += '[COLOR_POSITIVE_TEXT]Ресурс[ENDCOLOR] [ICON_RES_OIL] '
        translated += '[ICON_GOLD] [ICON_GOLD] [ICON_GOLD]'
        self.assertEqual(token_counts(translated), token_counts(english))
        approvals = {'RU_RU': {key: {'source_fingerprint': editor_source_fingerprint(key, entry),
                                    'text': translated}}}
        targets, counts = approved_entries(catalog, locales, approvals)
        self.assertEqual(counts['RU_RU'], 1)
        applied = install_candidate(candidate, render_blocks(targets), locales)
        self.assertEqual(validate_runtime_xml(applied, keys=(key,))['texts']['RU_RU'][key], translated)
        entry['lekmod_en_US']['text'] = english.replace('Provides +1', 'Provides +2')
        node.find('Text').text = entry['lekmod_en_US']['text']
        targets, counts = approved_entries(catalog, locales, approvals)
        self.assertEqual(counts['RU_RU'], 0)
        self.assertEqual(targets['RU_RU'][key]['Text'], node.findtext('Text'))
        changed = install_candidate(ET.tostring(base, encoding='unicode'), render_blocks(targets), locales)
        result = validate_runtime_xml(changed, keys=(key, *KEYS[:2]))['texts']
        self.assertEqual(result['RU_RU'][key], node.findtext('Text'))
        self.assertEqual(result['RU_RU'][KEYS[0]], 'DISCORD')
        self.assertEqual(approvals['RU_RU'][key]['text'], translated, 'stale work is retained')

    def test_previous_generator_empty_text_fails_conservative_gate(self):
        """Retain upstream's empty-field format; this is not an engine test."""
        original = (REPO_ROOT / 'LEKMOD/Override/CIV5Units_Mongol.xml').read_text(encoding='utf-8')
        head, marker, fallback = original.partition('<!-- BEGIN GENERATED FALLBACK -->')
        self.assertTrue(marker)
        old = head + marker + fallback.replace('<Text>\u00a0</Text>', '<Text />')
        ET.fromstring(old)  # This is exactly the insufficient pre-v0.23 check.
        remaining = []

        class ObservedConnection(sqlite3.Connection):
            def close(self):
                remaining.append(self.execute('SELECT COUNT(*) FROM Language_en_US').fetchone()[0])
                super().close()

        connect = sqlite3.connect
        with patch('lekmod_localization.runtime_xml.sqlite3.connect',
                   side_effect=lambda *args: connect(*args, factory=ObservedConnection)):
            with self.assertRaisesRegex(CatalogError, 'NULL'):
                validate_runtime_xml(old)
        self.assertEqual(remaining, [0], 'the failed file must roll back earlier English rows')

    def test_original_paired_empty_text_and_all_shipped_languages_load(self):
        """The unchanged English block and every fallback preserve menu tokens."""
        source = (REPO_ROOT / 'localization/en_US/primary.xml').read_text(encoding='utf-8')
        sync_primary_english.validate_source(source)
        document = (REPO_ROOT / 'LEKMOD/Override/CIV5Units_Mongol.xml').read_text(encoding='utf-8')
        self.assertNotIn('<Text />', document)
        result = validate_runtime_xml(document, keys=KEYS)
        self.assertEqual(set(result['counts']), set(LOCALES))
        for locale, texts in result['texts'].items():
            self.assertEqual(set(texts), set(KEYS), locale)
            self.assertEqual(texts[KEYS[0]], 'DISCORD')
            self.assertEqual(texts[KEYS[1]], 'GITHUB')
            self.assertIn('[COLOR_POSITIVE_TEXT]Up to date![ENDCOLOR]', texts[KEYS[-1]])
            for key in KEYS:
                self.assertEqual(token_counts(texts[key]), token_counts(result['texts']['en_US'][key]))

    def test_five_russian_menu_translations_and_removal_do_not_break_other_keys(self):
        """Exercise the user's texts through rendering, the core tables and DLC views."""
        english = ET.parse(REPO_ROOT / 'localization/en_US/primary.xml').getroot()[0]
        texts = {row.get('Tag'): row.findtext('Text') for row in english if row.get('Tag') in KEYS}
        entries = {key: {'Text': text} for key, text in texts.items()}
        entries['TXT_KEY_EMPTY_HELP'] = {'Text': ''}
        targets = {locale: {key: fields.copy() for key, fields in entries.items()}
                   for locale in LOCALES if locale != 'en_US'}
        for key, text in zip(KEYS[2:], RUSSIAN):
            self.assertEqual(token_counts(text), token_counts(texts[key]))
            targets['RU_RU'][key] = {'Text': text}
        base = materialize_blank_text('<GameData>\n' + sync_primary_english.english_block(
            (REPO_ROOT / 'localization/en_US/primary.xml').read_text(encoding='utf-8')) + '</GameData>')
        candidate = install_candidate(base, render_blocks(targets), list(targets))
        result = validate_runtime_xml(candidate, keys=KEYS)
        self.assertEqual([result['texts']['RU_RU'][key] for key in KEYS[2:]], list(RUSSIAN))
        self.assertEqual(result['texts']['RU_RU'][KEYS[0]], 'DISCORD')
        self.assertEqual(result['texts']['RU_RU'][KEYS[1]], 'GITHUB')
        # DLC localization uses views/triggers over LocalizedText. Check the
        # same generated values with that routing as well as required columns.
        with closing(sqlite3.connect(':memory:')) as database:
            database.execute('CREATE TABLE LocalizedText(Language TEXT, Tag TEXT, Text TEXT, '
                             'Gender TEXT, Plurality TEXT, PRIMARY KEY(Language,Tag))')
            for locale in targets:
                table = 'Language_' + locale
                database.executescript(f'CREATE VIEW "{table}" AS SELECT Tag,Text FROM LocalizedText '
                    f"WHERE Language='{locale}'; CREATE TRIGGER \"{table}_insert\" INSTEAD OF INSERT "
                    f'ON "{table}" BEGIN INSERT INTO LocalizedText(Language,Tag,Text) '
                    f"VALUES('{locale}',NEW.Tag,NEW.Text); END;")
                for key, fields in targets[locale].items():
                    database.execute(f'INSERT OR REPLACE INTO "{table}"(Tag,Text) VALUES(?,?)',
                                     (key, fields['Text']))
            for key, text in zip(KEYS[2:], RUSSIAN):
                self.assertEqual(database.execute('SELECT Text FROM Language_RU_RU WHERE Tag=?',
                                                 (key,)).fetchone()[0], text)
        # Clearing the translations restores colored English and the links.
        targets['RU_RU'] = entries
        restored = install_candidate(candidate, render_blocks(targets), list(targets))
        restored_texts = validate_runtime_xml(restored, keys=KEYS)['texts']['RU_RU']
        self.assertEqual(restored_texts, texts)

    def test_empty_missing_unknown_and_duplicate_fields_fail_before_output(self):
        """Both forms of empty Text fail; a blank glyph preserves intentional blanks."""
        paired = '<GameData><Language_en_US><Row Tag="TXT_KEY_ONE"><Text></Text></Row></Language_en_US></GameData>'
        safe = materialize_blank_text(paired)
        self.assertEqual(validate_runtime_xml(safe, keys=('TXT_KEY_ONE',))['texts']['en_US']['TXT_KEY_ONE'], BLANK_TEXT)
        for broken in (paired, paired.replace('<Text></Text>', '<Text />'),
                       paired.replace('<Text></Text>', ''),
                       paired.replace('Language_en_US', 'Language_NO_SUCH_TABLE'),
                       paired.replace('<Text></Text>', '<Text>One</Text><Text>Two</Text>')):
            with self.subTest(broken=broken), self.assertRaises(CatalogError):
                validate_runtime_xml(broken)
        # Comments and self-closing Delete are not empty text fields.
        valid = safe.replace('</Language_en_US>', '<!-- <Text /> -->'
                               '<Delete Tag="TXT_KEY_ONE" /></Language_en_US>')
        self.assertEqual(validate_runtime_xml(valid)['counts']['en_US'], 0)
        updated = safe.replace('</Language_en_US>', '<Update><Where Tag="TXT_KEY_ONE" />'
                                 '<Set><Text>Changed</Text></Set></Update></Language_en_US>')
        self.assertEqual(validate_runtime_xml(updated, keys=('TXT_KEY_ONE',))['texts']['en_US']['TXT_KEY_ONE'], 'Changed')
        with self.assertRaisesRegex(CatalogError, 'NULL'):
            validate_runtime_xml(updated.replace('<Text>Changed</Text>', '<Text />'))

    def test_blank_output_survives_observed_mixed_russian_cache_schema(self):
        """The user's EN/RU views and inactive DE NOT NULL table share one load.

        This exercises conservative NULL binding, not the closed-source parser.
        A failure in DE rolls back the earlier EN and RU menu rows.
        """
        document = '''<GameData><Language_en_US>
          <Row Tag="TXT_KEY_LEKMOD_MENU_DISCORD"><Text>DISCORD</Text></Row>
          </Language_en_US><Language_RU_RU>
          <Row Tag="TXT_KEY_LEKMOD_MENU_VERSION_UPTODATE"><Text>Актуально</Text></Row>
          </Language_RU_RU><Language_DE_DE>
          <Replace Tag="TXT_KEY_CIVILOPEDIA_LEADERS_US_MICHAEL_COLLINS_TEXT_1"><Text></Text></Replace>
          </Language_DE_DE></GameData>'''
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('CREATE TABLE LocalizedText(Language TEXT,Tag TEXT,Text TEXT, '
                             'PRIMARY KEY(Language,Tag)); CREATE TABLE Language_DE_DE('
                             'ID INTEGER PRIMARY KEY,Tag TEXT UNIQUE NOT NULL,Text TEXT NOT NULL);')
            for locale in ('en_US', 'RU_RU'):
                db.executescript(f'CREATE VIEW Language_{locale} AS SELECT Tag,Text FROM LocalizedText '
                                 f"WHERE Language='{locale}'; CREATE TRIGGER insert_{locale} "
                                 f'INSTEAD OF INSERT ON Language_{locale} BEGIN '
                                 'INSERT INTO LocalizedText VALUES('
                                 f"'{locale}',NEW.Tag,NEW.Text); END;")

            def load(xml):
                with db:
                    for block in ET.fromstring(xml):
                        for operation in block:
                            db.execute(f'INSERT OR REPLACE INTO {block.tag}(Tag,Text) VALUES(?,?)',
                                       (operation.get('Tag'), operation.find('Text').text))

            for unsafe in (document, document.replace('<Text></Text>', '<Text />')):
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'Language_DE_DE.Text'):
                    load(unsafe)
                self.assertEqual(db.execute('SELECT count(*) FROM LocalizedText').fetchone()[0], 0)
            load(materialize_blank_text(document))
            self.assertEqual(db.execute('SELECT count(*) FROM LocalizedText').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT Text FROM Language_DE_DE').fetchone()[0], BLANK_TEXT)

    def test_blank_materialization_preserves_adjacent_xml_and_source_text(self):
        """Only empty language fields change; entities, comments and gameplay do not."""
        document = '''<GameData><!-- <Text></Text> -->
          <Units><Row><Text></Text></Row></Units><Language_en_US>
          <Row Tag="TXT_KEY_EMPTY"><Text><![CDATA[]]></Text></Row>
          <Replace Tag="TXT_KEY_SPACE"><Text>   </Text></Replace>
          <Row Tag="TXT_KEY_ESCAPE"><Text> A &amp; &lt;Б&gt; </Text></Row>
          </Language_en_US></GameData>'''
        safe = materialize_blank_text(document)
        self.assertIn('<!-- <Text></Text> -->', safe)
        self.assertIn('<Units><Row><Text></Text></Row></Units>', safe)
        self.assertIn('<Text> A &amp; &lt;Б&gt; </Text>', safe)
        self.assertEqual(safe.count('<Text>&#160;</Text>'), 2)
        self.assertEqual(materialize_blank_text(safe), safe)
