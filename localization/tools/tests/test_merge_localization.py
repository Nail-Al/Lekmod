"""English and translated handoffs share one ordered, reversible review."""

import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from merge_localization import (PRIMARY, GAME, REFERENCE, candidate_sources,
                                digest, english_base, english_groups, review_merge)
from merge_translation_handoff import csv_records, encoded_records
from lekmod_localization.common import CatalogError
from lekmod_localization.shipped import approved_entries
from lekmod_localization.catalog import build_catalog
from lekmod_localization.sources import load_repository_localizations
from lekmod_localization.workspace import editor_source_fingerprint
from lekmod_localization.vanilla_reference import read_reference
import audit_primary_localization
from sync_primary_english import BEGIN, END


KEY = 'TXT_KEY_BUILDING_ONE_HELP'
OTHER = 'TXT_KEY_BUILDING_TWO_HELP'


def document(one='Mod one', two='Mod two'):
    """Use real tagged operations and the canonical XML table layout."""
    rows = ''.join(f'\t\t<Row Tag="{key}">\n\t\t\t<Text>{text}</Text>\n\t\t</Row>\n'
                   for key, text in ((KEY, one), (OTHER, two)))
    return '<GameData>\n\t<Language_en_US>\n' + rows + '\t</Language_en_US>\n</GameData>\n'


class OrderedMergeTests(unittest.TestCase):
    def test_reviewed_reference_extension_keeps_english_and_translation_imports(self):
        """Earlier packages retain English-first ordering after the baseline extension."""
        incoming = document(one="Updated mod one")
        sources = candidate_sources(self.project, incoming)
        package = self.package(english=incoming,
            translations={KEY: self.row(KEY, sources, "Updated translation")})
        stream = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(package)) as source, zipfile.ZipFile(stream, "w") as target:
            for name in source.namelist():
                content = source.read(name)
                if name == "manifest.json":
                    manifest = json.loads(content)
                    manifest["vanilla_reference_sha256"] = "a" * 64
                    content = json.dumps(manifest).encode()
                target.writestr(name, content)
        content = stream.getvalue()
        with self.assertRaisesRegex(CatalogError, "vanilla reference differs"):
            review_merge([content], self.project)
        with patch("lekmod_localization.vanilla_reference.COMPATIBLE_REFERENCE_EXTENSIONS",
                   {("a" * 64, digest(self.reference.read_bytes()))}):
            preview = review_merge([content], self.project)
        self.assertEqual([row["locale"] for row in preview["items"]], ["en_US", "RU_RU"])
        self.assertEqual(preview["items"][1]["status_label"], "English first")

    def test_inline_operations_do_not_consume_the_next_multiline_row(self):
        """Upstream XML mixes one-line rows with multiline operations."""
        mixed = document().replace(
            '\t\t<Row Tag="' + KEY + '">\n\t\t\t<Text>Mod one</Text>\n\t\t</Row>',
            '\t\t<Row Tag="' + KEY + '"><Text>Mod one</Text></Row>')
        groups = english_groups(mixed)
        self.assertEqual(set(groups), {KEY, OTHER})
        self.assertEqual(groups[KEY]['text'], 'Mod one')
    def setUp(self):
        """Build a complete small source index using the production catalog."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.primary = self.project / PRIMARY
        self.primary.parent.mkdir(parents=True)
        self.primary.write_text(document(), encoding='utf-8')
        self.reference = self.project / REFERENCE
        self.reference.parent.mkdir(parents=True)
        self.reference.write_bytes(gzip.compress(json.dumps({
            'schema_version': 1, 'locales': {'en_US': 'a' * 64, 'RU_RU': 'b' * 64, 'DE_DE': 'c' * 64},
            'english': {KEY: {'Text': digest(b'Vanilla one'), 'Gender': None, 'Plurality': None}}
        }).encode(), mtime=0))
        (self.project / 'LEKMOD/Art').mkdir(parents=True)
        game = self.project / GAME
        game.parent.mkdir(parents=True)
        table = document().split('<GameData>\n', 1)[1].split('</GameData>')[0]
        game.write_text('<GameData>\n' + BEGIN + table + END + '</GameData>\n', encoding='utf-8')
        self.base = english_base(self.project)
        self.old_sources = candidate_sources(self.project, document())
        for locale in ('RU_RU', 'DE_DE'):
            path = self.project / f'localization/translations/{locale}.csv'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded_records({KEY: self.row(KEY, self.old_sources, locale + ' old')}))
            workspace = self.project / 'localization/workspace/editor' / locale
            workspace.mkdir(parents=True)
            import csv
            with (workspace / 'buildings.csv').open('w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.DictWriter(handle, ('key', 'source_fingerprint', 'lekmod_en_US', 'required_format_tokens'))
                writer.writeheader()
                writer.writerows({'key': key, **value} for key, value in self.old_sources.items())
        manifest = {'primary_sha256': digest(self.primary.read_bytes()),
                    'source_english_sha256': digest(table.encode()),
                    'locales': {locale: {'files': {'buildings.csv': 2}} for locale in ('RU_RU', 'DE_DE')}}
        (self.project / 'localization/workspace/editor/manifest.json').write_text(json.dumps(manifest))

    @staticmethod
    def row(key, sources, text):
        """Pin a translation to the exact English generation it was written for."""
        return {'key': key, 'source_fingerprint': sources[key]['source_fingerprint'], 'text': text,
                'gender': '', 'plurality': '', 'translator_note': '', 'updated_at': '2026-10-05T00:00:00Z'}

    def package(self, english=None, translations=None):
        """Produce the same checksummed manifests as the editor's two exports."""
        manifest = {'vanilla_reference_sha256': digest(self.reference.read_bytes())}
        entries = {'README.txt': b'handoff'}
        if english is not None:
            entries[PRIMARY] = english.encode()
            manifest.update(english_sha256=digest(entries[PRIMARY]), english_base=self.base)
        if translations is not None:
            payload = encoded_records(translations)
            entries['translations/RU_RU.csv'] = payload
            manifest['locales'] = {'RU_RU': digest(payload)}
        entries['manifest.json'] = json.dumps(manifest).encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as package:
            for name, data in entries.items(): package.writestr(name, data)
        return stream.getvalue()

    def test_english_first_then_translation_resets_only_other_language(self):
        """An EN+RU import applies RU to new English while DE falls back."""
        changed = document(one='Updated [ICON_CULTURE]')
        sources = candidate_sources(self.project, changed)
        en = self.package(english=changed)
        ru = self.package(translations={KEY: self.row(KEY, sources, 'RU new [ICON_CULTURE]')})
        before = self.primary.read_bytes()
        preview = review_merge([ru, en], self.project)
        self.assertEqual([item['locale'] for item in preview['items']], ['en_US', 'RU_RU'])
        self.assertEqual(preview['items'][0]['resets'], ['DE_DE', 'RU_RU'])
        self.assertEqual(preview['items'][1]['status_label'], 'English first')
        self.assertEqual(self.primary.read_bytes(), before)
        chosen = {'en_US:' + KEY: 'incoming', 'RU_RU:' + KEY: 'incoming'}
        result = review_merge([ru, en], self.project, apply=True, choices=chosen,
                              expected=preview['target_sha256'])
        self.assertTrue(result['applied'])
        self.assertEqual(Path(result['backups'][PRIMARY]).read_bytes(), before)
        self.assertEqual(csv_records((self.project / 'localization/translations/RU_RU.csv').read_bytes(), 'ru')[KEY]['text'],
                         'RU new [ICON_CULTURE]')
        self.assertNotEqual(csv_records((self.project / 'localization/translations/DE_DE.csv').read_bytes(), 'de')[KEY]['source_fingerprint'],
                            sources[KEY]['source_fingerprint'])

    def test_team_edits_unchanged_by_sender_are_not_incoming_changes(self):
        """A full-file source export cannot replace another developer's independent edit."""
        self.primary.write_text(document(two='New team text'), encoding='utf-8')
        archive = self.package(english=document(one='Incoming edit'))
        result = review_merge([archive], self.project, apply=True)
        self.assertTrue(result['applied'])
        self.assertIn('New team text', self.primary.read_text())
        self.assertEqual([item['key'] for item in result['items']], [KEY])

    def test_declining_english_makes_dependent_translation_unmergeable(self):
        """The new translation cannot be accepted while its EN change is skipped."""
        changed = document(one='Updated')
        sources = candidate_sources(self.project, changed)
        archives = [self.package(english=changed), self.package(translations={KEY: self.row(KEY, sources, 'New RU')})]
        report = review_merge(archives, self.project, choices={'en_US:' + KEY: 'keep'})
        self.assertEqual(report['items'][1]['status'], 'stale')
        before = self.primary.read_bytes()
        with self.assertRaisesRegex(CatalogError, 'invalid merge choice'):
            review_merge(archives, self.project, apply=True,
                         choices={'en_US:' + KEY: 'keep', 'RU_RU:' + KEY: 'incoming'})
        self.assertEqual(self.primary.read_bytes(), before)

    def test_any_team_csv_changed_since_preview_blocks_entire_transaction(self):
        """A teammate's other-language edit invalidates the reviewed reset list."""
        archive = self.package(english=document(one='Updated'))
        preview = review_merge([archive], self.project)
        de = self.project / 'localization/translations/DE_DE.csv'
        de.write_bytes(encoded_records({KEY: self.row(KEY, self.old_sources, 'Another developer')}))
        before = self.primary.read_bytes()
        with self.assertRaisesRegex(CatalogError, 'Project changed since preview'):
            review_merge([archive], self.project, apply=True, expected=preview['target_sha256'])
        self.assertEqual(self.primary.read_bytes(), before)

    def test_returning_to_vanilla_preserves_old_csv_without_applying_it(self):
        """A reverted mechanic uses official text without deleting prior translated work."""
        changed = document(one='Vanilla one')
        review_merge([self.package(english=changed)], self.project, apply=True)
        ref = read_reference(self.reference)
        report = audit_primary_localization.parse_source(self.primary, 'en_US')
        localizations, references, summary = load_repository_localizations(self.primary, self.project / 'LEKMOD/Art', self.project)
        catalog = build_catalog(report, {key: {} for key in ref['english']}, sorted(ref['locales']), 'test',
                                ref['locales']['en_US'], localizations, references, summary, vanilla_hashes=ref['english'])
        approval = {KEY: {'source_fingerprint': self.old_sources[KEY]['source_fingerprint'], 'text': 'old RU'}}
        entries, count = approved_entries(catalog, ['RU_RU', 'DE_DE'], {'RU_RU': approval})
        self.assertNotIn(KEY, entries['RU_RU'])
        self.assertEqual(count['RU_RU'], 0)
        self.assertIn(KEY, csv_records((self.project / 'localization/translations/RU_RU.csv').read_bytes(), 'ru'))


if __name__ == '__main__': unittest.main()
