"""Upgrading a mod keeps previous work and all intermediate source changes."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.common import CatalogError
from lekmod_localization.version_history import between, carry_translations, change_map, synchronize
from merge_translation_handoff import csv_records, encoded_records


HISTORY = {'releases': {
    'v35.1': {'changes': {'TXT_KEY_ONE': 'new'}},
    'v35.2': {'changes': {'TXT_KEY_ONE': 'updated', 'TXT_KEY_TWO': 'new'}},
    'v35.3': {'changes': {'TXT_KEY_ONE': 'updated'}},
    'v35.4': {'changes': {'TXT_KEY_THREE': 'new'}},
}}


class VersionHistoryTests(unittest.TestCase):
    def test_upgrade_keeps_intermediate_changes_even_when_later_reverted(self):
        """A change followed by a revert still appears in the version filter."""
        with tempfile.TemporaryDirectory() as directory, \
             patch('lekmod_localization.version_history.read_history', return_value=HISTORY):
            root = Path(directory)
            synchronize(root, between(HISTORY, 'v35.1', 'v35.4'), since='v35.1')
            keys, info = change_map(root, 'v35.4')
            self.assertEqual(keys['TXT_KEY_ONE'], ['v35.2', 'v35.3'])
            self.assertEqual(info['upgrade_versions'], ['v35.2', 'v35.3', 'v35.4'])
            synchronize(root, ['v35.1'])
            keys, info = change_map(root, 'v35.4')
            self.assertEqual(keys['TXT_KEY_ONE'], ['v35.1', 'v35.2', 'v35.3'])
            self.assertEqual(info['since'], 'v35.1')

    @staticmethod
    def project(root, version, rows):
        """Give each release its own canonical source, approvals and editor index."""
        primary = root / 'localization/en_US/primary.xml'
        primary.parent.mkdir(parents=True)
        primary.write_text('<Text>LEKMOD ' + version + '</Text>')
        reference = root / 'localization/reference/vanilla-fingerprints.json.gz'
        reference.parent.mkdir(parents=True)
        reference.write_bytes(b'one shared baseline')
        target = root / 'localization/translations/RU_RU.csv'
        target.parent.mkdir(parents=True)
        target.write_bytes(encoded_records(rows))
        catalog = root / 'localization/workspace/editor'
        (catalog / 'RU_RU').mkdir(parents=True)
        (catalog / 'manifest.json').write_text(json.dumps({'locales': {'RU_RU': {'files': {'buildings.csv': 2}}}}))
        (catalog / 'RU_RU/buildings.csv').write_text('key\nTXT_KEY_ONE\nTXT_KEY_TWO\n')
        return target

    @staticmethod
    def row(key, text, fingerprint='a'):
        """Saved notes and stale fingerprints are user work too."""
        return {'key': key, 'source_fingerprint': fingerprint * 64, 'text': text,
                'gender': 'feminine', 'plurality': '2', 'translator_note': 'Review context',
                'updated_at': '2026-10-05T00:00:00Z'}

    def test_transfer_never_replaces_old_project_or_newer_destination_work(self):
        """An existing destination row wins; independent and removed work is retained."""
        with tempfile.TemporaryDirectory() as directory, \
             patch('lekmod_localization.version_history.read_history', return_value=HISTORY):
            old, new = Path(directory) / 'old', Path(directory) / 'new'
            old_file = self.project(old, 'v35.1', {
                key: self.row(key, 'Old ' + key) for key in ('TXT_KEY_ONE', 'TXT_KEY_TWO', 'TXT_KEY_REMOVED')})
            new_file = self.project(new, 'v35.4', {'TXT_KEY_ONE': self.row('TXT_KEY_ONE', 'New team text', 'b')})
            original = old_file.read_bytes()
            result = carry_translations(old, new)
            self.assertEqual(old_file.read_bytes(), original)
            rows = csv_records(new_file.read_bytes(), 'new')
            self.assertEqual(rows['TXT_KEY_ONE']['text'], 'New team text')
            self.assertEqual(rows['TXT_KEY_TWO']['translator_note'], 'Review context')
            self.assertEqual(rows['TXT_KEY_TWO']['source_fingerprint'], 'a' * 64)
            self.assertNotIn('TXT_KEY_REMOVED', rows)
            archive = new / 'localization/workspace/carried-translations/v35.1/RU_RU.csv'
            self.assertEqual(archive.read_bytes(), original)
            self.assertEqual((result['copied'], result['conflicts'], result['archived']), (1, 1, 1))
            _, info = change_map(new, 'v35.4')
            self.assertEqual(info['upgrade_versions'], ['v35.2', 'v35.3', 'v35.4'])

    def test_different_team_reference_cannot_transfer_any_rows(self):
        """A different dictionary stops a transfer before touching translations."""
        with tempfile.TemporaryDirectory() as directory:
            old, new = Path(directory) / 'old', Path(directory) / 'new'
            self.project(old, 'v35.1', {'TXT_KEY_ONE': self.row('TXT_KEY_ONE', 'Old')})
            target = self.project(new, 'v35.4', {})
            before = target.read_bytes()
            (new / 'localization/reference/vanilla-fingerprints.json.gz').write_bytes(b'different baseline')
            with self.assertRaisesRegex(CatalogError, 'different vanilla references'):
                carry_translations(old, new)
            self.assertEqual(target.read_bytes(), before)

    def test_failed_comparison_write_restores_destination_translations(self):
        """A failure after CSV writes must not leave a partially transferred project."""
        with tempfile.TemporaryDirectory() as directory, \
             patch('lekmod_localization.version_history.read_history', return_value=HISTORY), \
             patch('lekmod_localization.version_history.synchronize', side_effect=OSError('Disk full')):
            old, new = Path(directory) / 'old', Path(directory) / 'new'
            old_file = self.project(old, 'v35.1', {'TXT_KEY_ONE': self.row('TXT_KEY_ONE', 'Saved')})
            target = self.project(new, 'v35.4', {})
            old_data, target_data = old_file.read_bytes(), target.read_bytes()
            with self.assertRaisesRegex(OSError, 'Disk full'):
                carry_translations(old, new)
            self.assertEqual(old_file.read_bytes(), old_data)
            self.assertEqual(target.read_bytes(), target_data)


if __name__ == '__main__': unittest.main()
