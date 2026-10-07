"""A partial Apply must retain each rejected row at its destination."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.runtime_xml import preserve_rejected_rows, validate_runtime_xml


def row(key, text):
    return f'<Replace Tag="{key}"><Text>{text}</Text></Replace>'


def document(english, russian, german=''):
    return '<GameData><Units><Row Type="UNCHANGED"><Description>Gameplay</Description></Row></Units>' + \
        '<Language_en_US>' + english + '</Language_en_US><Language_RU_RU>' + russian + \
        '</Language_RU_RU><Language_DE_DE>' + german + '</Language_DE_DE></GameData>'


class PartialGameTests(unittest.TestCase):
    def test_rejected_translation_keeps_last_game_only_version_and_other_rows_apply(self):
        candidate = document(row('TXT_KEY_ONE', 'English'),
            row('TXT_KEY_ONE', 'Project version') + row('TXT_KEY_TWO', 'Valid new translation'),
            row('TXT_KEY_ONE', 'New German'))
        installed = document(row('TXT_KEY_ONE', 'English'), row('TXT_KEY_ONE', 'Last game-only version'),
            row('TXT_KEY_ONE', 'Old German'))
        merged = preserve_rejected_rows(candidate, installed,
            [{'mode': 'translator', 'locale': 'RU_RU', 'key': 'TXT_KEY_ONE'}])
        texts = validate_runtime_xml(merged, keys=('TXT_KEY_ONE', 'TXT_KEY_TWO'))['texts']
        self.assertEqual(texts['RU_RU']['TXT_KEY_ONE'], 'Last game-only version')
        self.assertEqual(texts['RU_RU']['TXT_KEY_TWO'], 'Valid new translation')
        self.assertEqual(texts['DE_DE']['TXT_KEY_ONE'], 'New German')
        self.assertIn('<Units><Row Type="UNCHANGED"><Description>Gameplay</Description></Row></Units>', merged)
        self.assertEqual(preserve_rejected_rows(candidate, merged,
            [{'mode': 'translator', 'locale': 'RU_RU', 'key': 'TXT_KEY_ONE'}]), merged)

    def test_rejected_english_keeps_all_languages_and_identifier_operations(self):
        candidate = document(row('TXT_KEY_OLD', 'Project English') + row('TXT_KEY_NEW', 'Project rename'),
            row('TXT_KEY_OLD', 'Project Russian'), row('TXT_KEY_NEW', 'Project German'))
        installed = document(row('TXT_KEY_OLD', 'Game English'), row('TXT_KEY_OLD', 'Game Russian'),
            row('TXT_KEY_NEW', 'Game German'))
        merged = preserve_rejected_rows(candidate, installed,
            [{'mode': 'developer', 'key': 'TXT_KEY_OLD', 'identifier': 'TXT_KEY_NEW'}])
        texts = validate_runtime_xml(merged, keys=('TXT_KEY_OLD', 'TXT_KEY_NEW'))['texts']
        self.assertEqual(texts['en_US']['TXT_KEY_OLD'], 'Game English')
        self.assertNotIn('TXT_KEY_NEW', texts['en_US'])
        self.assertEqual(texts['RU_RU']['TXT_KEY_OLD'], 'Game Russian')
        self.assertEqual(texts['DE_DE']['TXT_KEY_NEW'], 'Game German')

    def test_complete_update_delete_and_blank_operations_are_preserved_without_reformatting(self):
        operations = '<Replace Tag="TXT_KEY_ONE"><Text>Old\r\nmultiline &amp; text</Text></Replace>' + \
            '<Update><Where Tag="TXT_KEY_ONE"/><Set><Gender>feminine</Gender><Text>&#160;</Text></Set></Update>' + \
            '<Delete Tag="TXT_KEY_REMOVED"/>'
        candidate = document(row('TXT_KEY_EN', 'English'),
            row('TXT_KEY_ONE', 'Project Russian') + row('TXT_KEY_REMOVED', 'Project override'))
        installed = document(row('TXT_KEY_EN', 'English'), operations)
        issues = [{'mode': 'translator', 'locale': 'RU_RU', 'key': key}
                  for key in ('TXT_KEY_ONE', 'TXT_KEY_REMOVED')]
        merged = preserve_rejected_rows(candidate, installed, issues)
        self.assertIn(operations.split('<Update>')[0], merged)
        texts = validate_runtime_xml(merged, keys=('TXT_KEY_ONE', 'TXT_KEY_REMOVED'))['texts']['RU_RU']
        self.assertEqual(texts['TXT_KEY_ONE'], '\u00a0')
        self.assertNotIn('TXT_KEY_REMOVED', texts)

    def test_absent_rejected_override_stays_absent_and_equal_snapshot_is_untouched(self):
        candidate = document(row('TXT_KEY_EN', 'English'), row('TXT_KEY_ONE', 'Project Russian'))
        installed = document(row('TXT_KEY_EN', 'English'), '')
        issues = [{'mode': 'translator', 'locale': 'RU_RU', 'key': 'TXT_KEY_ONE'}]
        merged = preserve_rejected_rows(candidate, installed, issues)
        self.assertNotIn('Project Russian', merged)
        self.assertEqual(preserve_rejected_rows(installed, installed, issues), installed)

    def test_old_intentionally_blank_value_stays_blank_without_binding_as_null(self):
        candidate = document(row('TXT_KEY_EN', 'English'), row('TXT_KEY_ONE', 'Project Russian'))
        installed = document(row('TXT_KEY_EN', 'English'), '<Replace Tag="TXT_KEY_ONE"><Text></Text></Replace>')
        merged = preserve_rejected_rows(candidate, installed,
            [{'mode': 'translator', 'locale': 'RU_RU', 'key': 'TXT_KEY_ONE'}])
        self.assertEqual(validate_runtime_xml(merged, keys=('TXT_KEY_ONE',))['texts']['RU_RU']['TXT_KEY_ONE'], '\u00a0')


if __name__ == '__main__':
    unittest.main()
