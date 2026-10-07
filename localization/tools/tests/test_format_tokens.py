"""Protect real game substitutions while allowing translator prose and icons."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.common import REPO_ROOT, normalize_text, token_counts, token_difference, tokens_match
from lekmod_localization.workspace import editor_source_fingerprint


class FormattingTests(unittest.TestCase):
    def test_real_markup_including_link_and_color_variants_remains_required(self):
        text = '[COLOR_XP_BLUE][ENDCOLOR][COLOR:205:127:50:255][/COLOR][LINK=UNIT_TEST][\\LINK][NEWLINE][TAB]{1_Name}%s'
        counts = token_counts(text)
        self.assertEqual(len(counts), 10)
        for token in counts:
            with self.subTest(token=token):
                self.assertFalse(tokens_match(text.replace(token, '', 1), counts))
        self.assertFalse(tokens_match(text.replace('{1_Name}', '{2_Name}'), counts))
        self.assertFalse(tokens_match(text + '[NEWLINE]', counts))

    def test_footnotes_and_bracketed_prose_are_not_game_tokens(self):
        text = '[COLOR_XP_BLUE]Wikipedia[ENDCOLOR] Empire[2][3–4][a][citation needed][Note 1][it is][…]'
        required = token_counts(text)
        self.assertEqual(required, {'[COLOR_XP_BLUE]': 1, '[ENDCOLOR]': 1})
        self.assertTrue(tokens_match('[COLOR_XP_BLUE]Википедия[ENDCOLOR] Империя', required))
        self.assertTrue(tokens_match('Империя [это было] [COLOR_XP_BLUE]Википедия[ENDCOLOR]', required))
        self.assertTrue(tokens_match('[COLOR_XP_BLUE]Википедия[ENDCOLOR]', {**required, '[2]': 1}))

    def test_all_three_reported_translations_pass_with_unchanged_english_hashes(self):
        translations = {
            'TXT_KEY_UA_AKKAD_PEDIA': '[COLOR_XP_BLUE] (https://en.wikipedia.org/wiki/Akkadian_Empire)[ENDCOLOR]Аккадская империя была первой известной империей.',
            'TXT_KEY_UNIT_LITE_AKKAD_ONAGER_WAGON_HELP': 'Уникальный юнит Аккада. Не тратит [ICON_MOVES] Очки передвижения на разграбление клеток.',
            'TXT_KEY_BUILDING_COFFEE_HOUSE_HELP': 'Заменяет Мельницу. Требует меньше [ICON_PRODUCTION] Производства. Увеличивает на 20% скорость возникновения [ICON_GREAT_PEOPLE] Великих людей.',
        }
        source = ET.parse(REPO_ROOT / 'localization/en_US/primary.xml').getroot()
        quoted_english = {
            'TXT_KEY_UNIT_LITE_AKKAD_ONAGER_WAGON_HELP': 'A unit only available to Akkad. Unlike the Chariot Archer it replaces, rough terrain does not end turn. Ignores enemy Zone of Control and has no movement cost for pillaging tiles.',
            'TXT_KEY_BUILDING_COFFEE_HOUSE_HELP': 'Replaces the Windmill. Much cheaper to construct. +20% [ICON_GREAT_PEOPLE] Great People generation in this City. Available earlier.',
        }
        for key, translation in translations.items():
            with self.subTest(key=key):
                node = next((row for row in source.iter('Row') if row.get('Tag') == key), None)
                text = normalize_text(node.findtext('Text') if node is not None else quoted_english[key])
                entry = {'classification': 'lekmod_new', 'lekmod_en_US': {
                    'text': text, 'gender': None, 'plurality': None,
                    'format_tokens': token_counts(text)}}
                legacy = dict(Counter(re.findall(r'\[[^\[\]]+\]|\{[^{}]+\}|%(?:\d+\$)?[a-zA-Z%]', text)))
                identity = {'key': key, 'classification': entry['classification'], 'text': text,
                            'gender': entry['lekmod_en_US']['gender'], 'plurality': entry['lekmod_en_US']['plurality'],
                            'format_tokens': legacy}
                old_hash = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                                    separators=(',', ':')).encode('utf-8')).hexdigest()
                self.assertEqual(editor_source_fingerprint(key, entry), old_hash, 'v0.25 drafts must stay current')
                self.assertTrue(tokens_match(translation, entry['lekmod_en_US']['format_tokens']))
                entry['lekmod_en_US']['text'] += ' Changed English.'
                self.assertNotEqual(editor_source_fingerprint(key, entry), old_hash)

    def test_original_icons_and_dynamic_values_are_still_required(self):
        self.assertTrue(tokens_match('[ICON_PRODUCTION] {1_Num} [ICON_GREAT_PEOPLE]',
                                     {'{1_Num}': 1, '[ICON_GREAT_PEOPLE]': 1}))
        self.assertEqual(token_difference('[ICON_PRODUCTION]', {'{1_Num}': 1, '[ICON_GREAT_PEOPLE]': 1}),
                         ({'{1_Num}': 1, '[ICON_GREAT_PEOPLE]': 1}, {}))
        self.assertFalse(tokens_match('{2_Num}', {'{1_Num}': 1}))
        self.assertFalse(tokens_match('%d', {'%s': 1}))


if __name__ == '__main__':
    unittest.main()
