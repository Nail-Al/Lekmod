"""Guard fast local saves, one batch rebuild, and work retained across failures."""

import csv
from contextlib import ExitStack
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editor_server import Editor, APPROVAL_FIELDS, encoded_csv
from lekmod_localization.common import CatalogError
from lekmod_localization.drafts import DraftStore


def edit(text, identifier=''):
    """Use the same five-field form for both editor modes."""
    return dict(text=text, gender='', plurality='', note='', identifier=identifier)


class DraftStoreTests(unittest.TestCase):
    def test_restart_undo_revision_and_discard_are_durable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'drafts.sqlite3'
            store = DraftStore(path)
            initial = {'edit': edit('one')}
            first = store.put('T:RU:TXT_KEY_ONE', initial, 0, 'row-session')
            second = store.put('T:RU:TXT_KEY_ONE', {'edit': edit('two')}, first['entry']['revision'], 'row-session')
            store = DraftStore(path)
            self.assertEqual(store.entries()[0]['payload']['edit']['text'], 'two')
            with self.assertRaises(CatalogError):
                store.put('T:RU:TXT_KEY_ONE', initial, first['entry']['revision'])
            store.replay(undo=True)
            self.assertEqual(store.entries(), [])
            store.replay(undo=False)
            current = store.get('T:RU:TXT_KEY_ONE')
            self.assertGreater(current['revision'], second['entry']['revision'])
            store.put(current['slot'], None, current['revision'])
            self.assertFalse(store.entries())
            store.replay(undo=True)
            self.assertEqual(store.entries()[0]['payload']['edit']['text'], 'two')

    def test_apply_clears_only_its_revision_and_rebases_later_typing(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DraftStore(Path(folder) / 'drafts.sqlite3')
            payload = {'mode': 'translator', 'base': {'approval': None}, 'edit': edit('first')}
            row = store.put('T:RU:TXT_KEY_ONE', payload, 0)['entry']
            store.put(row['slot'], {**payload, 'edit': edit('later')}, row['revision'])
            base = {'approval': {'text': 'first'}}
            store.finish([row], {row['slot']: base})
            current = store.entries()[0]['payload']
            self.assertEqual(current['edit']['text'], 'later')
            self.assertEqual(current['base'], base)
            store.finish(store.entries(), {row['slot']: {'approval': {'text': 'later'}}})
            self.assertFalse(store.entries())

    def test_upgrade_copy_keeps_both_projects_and_destination_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new = (DraftStore(Path(folder) / (name + '.sqlite3')) for name in ('old', 'new'))
            old.put('T:RU:TXT_KEY_ONE', {'edit': edit('old')}, 0)
            old.put('T:RU:TXT_KEY_TWO', {'edit': edit('second')}, 0)
            new.put('T:RU:TXT_KEY_ONE', {'edit': edit('destination')}, 0)
            self.assertEqual(new.carry_from(old), 1)
            self.assertEqual(old.status()['draft_count'], 2)
            self.assertEqual(new.get('T:RU:TXT_KEY_ONE')['payload']['edit']['text'], 'destination')


class EditorDraftTests(unittest.TestCase):
    def setUp(self):
        """Isolate every source file and record rebuilds independently of draft storage."""
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / 'localization/en_US/primary.xml'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('<GameData>\n\t<Language_en_US>\n'
            '\t\t<Row Tag="TXT_KEY_ONE">\n\t\t\t<Text>One [ICON_CULTURE]</Text>\n\t\t</Row>\n'
            '\t\t<Row Tag="TXT_KEY_TWO">\n\t\t\t<Text>Two</Text>\n\t\t</Row>\n'
            '\t</Language_en_US>\n</GameData>', encoding='utf-8')
        self.game = self.root / 'LEKMOD/Override/CIV5Units_Mongol.xml'
        self.game.parent.mkdir(parents=True)
        self.game.write_bytes(b'original game')
        self.translations = self.root / 'localization/translations'
        self.translations.mkdir()
        for locale in ('RU_RU', 'DE_DE'):
            (self.translations / (locale + '.csv')).write_bytes(encoded_csv([], APPROVAL_FIELDS))
        self.editor = object.__new__(Editor)
        self.editor.ready = True
        self.editor.snapshot = None
        self.editor.save_state = {'state': 'idle'}
        self.editor.apply_state = {'state': 'idle'}
        self.editor.row_cache = {}
        self.editor.drafts = DraftStore(self.root / 'localization/workspace/editor-drafts.sqlite3')
        self.editor.record_event = lambda *args: None
        self.editor.mark_english_edit = lambda *args: None
        self.editor.mark_english_edits = lambda *args: None
        self.editor.manifest = lambda: {'locales': {'RU_RU': {}, 'DE_DE': {}}}
        self.mode = 'translator'
        stack = ExitStack(); self.addCleanup(stack.close)
        for name, value in (('REPO_ROOT', self.root), ('WORKSPACE', self.root / 'localization/workspace'),
                            ('TRANSLATIONS', self.translations),
                            ('sync_primary_english.DEFAULT_ENGLISH', self.source),
                            ('build_shipped_localization.DEFAULT_SOURCE', self.game)):
            stack.enter_context(patch('editor_server.' + name, value))
        stack.enter_context(patch('editor_server.settings', side_effect=lambda: {'mode': self.mode}))
        stack.enter_context(patch('editor_server.read_reference', return_value={'english': {}}))
        stack.enter_context(patch('editor_server.sync_primary_english.validate_source'))
        stack.enter_context(patch('editor_server.manage.read_config', return_value={'build': {'shipped': True}}))
        self.prepare = stack.enter_context(patch('editor_server.manage.prepare'))
        stack.enter_context(patch('editor_server.LOG.exception'))
        self.candidates = stack.enter_context(patch('editor_server.candidate_sources', return_value={
            'TXT_KEY_ONE': {'classification': 'lekmod_new', 'source_fingerprint': 'a' * 64,
                'required_format_tokens': '{"[ICON_CULTURE]":1}'},
            'TXT_KEY_TWO': {'classification': 'lekmod_new', 'source_fingerprint': 'a' * 64,
                'required_format_tokens': '{}'}}))

    def save(self, key='TXT_KEY_ONE', text='Translated [ICON_CULTURE]', locale='RU_RU', revision=0, **extra):
        """Save an actual browser payload, not a direct store implementation call."""
        return self.editor.save_draft({'mode': self.mode, 'locale': locale, 'key': key, 'revision': revision,
            'source_fingerprint': 'a' * 64, 'base': {'approval': None}, 'edit': edit(text), **extra})

    def finish(self):
        """Wait briefly for our controlled batch; a minute-long save would fail this gate."""
        end = time.monotonic() + 5
        while self.editor.save_state['state'] == 'running' and time.monotonic() < end:
            time.sleep(.005)
        self.assertNotEqual(self.editor.save_state['state'], 'running')
        return self.editor.apply_state

    def test_fast_save_bottleneck_never_invokes_rebuild_or_changes_project_files(self):
        """Permanent regression: 100 local writes must perform zero catalog/XML builds."""
        files = [self.source, self.game, self.translations / 'RU_RU.csv']
        originals = {path: path.read_bytes() for path in files}
        revision = 0
        started = time.monotonic()
        for number in range(100):
            result = self.save(text=f'incomplete draft {number}', revision=revision, group='typing')
            revision = result['entry']['revision']
        self.assertLess(time.monotonic() - started, 5, 'Local Save regressed to a rebuild-like delay')
        self.prepare.assert_not_called(); self.candidates.assert_not_called()
        self.assertEqual({path: path.read_bytes() for path in files}, originals)
        restored = DraftStore(self.editor.drafts.path)
        self.assertEqual(restored.entries()[0]['payload']['edit']['text'], 'incomplete draft 99')

    def test_batch_applies_all_languages_once_and_preserves_other_ide_rows(self):
        self.save(); self.save('TXT_KEY_TWO', 'Second', locale='DE_DE')
        other = {'key': 'TXT_KEY_IDE', 'source_fingerprint': 'b' * 64, 'text': 'Another contributor',
                 'gender': '', 'plurality': '', 'translator_note': 'Retain', 'updated_at': ''}
        (self.translations / 'RU_RU.csv').write_bytes(encoded_csv([other], APPROVAL_FIELDS))
        self.editor.start_apply()
        result = self.finish()
        self.assertEqual(result['state'], 'complete', result)
        self.assertEqual(result['applied_count'], 2)
        self.prepare.assert_called_once(); self.candidates.assert_called_once()
        saved = (self.translations / 'RU_RU.csv').read_text()
        self.assertIn('Another contributor', saved); self.assertIn('Translated', saved)
        self.assertEqual(self.editor.drafts.status()['draft_count'], 0)

    def test_invalid_tokens_save_but_apply_keeps_draft_and_files(self):
        self.save(text='Still working')
        before = (self.translations / 'RU_RU.csv').read_bytes()
        self.editor.start_apply()
        result = self.finish()
        self.assertEqual(result['state'], 'error')
        self.assertIn('formatting token', result['error'])
        self.prepare.assert_not_called()
        self.assertEqual((self.translations / 'RU_RU.csv').read_bytes(), before)
        self.assertEqual(self.editor.drafts.status()['draft_count'], 1)

    def test_same_key_ide_conflict_is_reported_without_overwriting(self):
        self.save()
        row = dict(key='TXT_KEY_ONE', source_fingerprint='a'*64, text='IDE edit',
                   gender='', plurality='', translator_note='', updated_at='')
        path = self.translations / 'RU_RU.csv'
        path.write_bytes(encoded_csv([row], APPROVAL_FIELDS)); before = path.read_bytes()
        self.editor.start_apply(); result = self.finish()
        self.assertIn('changed in an IDE', result['error'])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.editor.drafts.status()['draft_count'], 1)

    def test_rebuild_failure_restores_files_and_retains_drafts(self):
        self.save()
        before = (self.translations / 'RU_RU.csv').read_bytes()
        def fail(*args):
            self.game.write_bytes(b'partial build')
            raise CatalogError('broken build')
        self.prepare.side_effect = fail
        self.editor.start_apply(); result = self.finish()
        self.assertEqual(result['state'], 'error')
        self.assertEqual(self.game.read_bytes(), b'original game')
        self.assertEqual((self.translations / 'RU_RU.csv').read_bytes(), before)
        self.assertEqual(self.editor.drafts.status()['draft_count'], 1)

    def test_interrupted_apply_can_finish_without_replacing_other_work(self):
        """Our own already-written row is idempotent, rather than a false IDE conflict."""
        entry = self.save()['entry']
        path = self.translations / 'RU_RU.csv'
        saved = {'key': 'TXT_KEY_ONE', 'source_fingerprint': 'a' * 64,
                 'text': 'Translated [ICON_CULTURE]', 'gender': '', 'plurality': '',
                 'translator_note': '', 'updated_at': entry['updated_at']}
        path.write_bytes(encoded_csv([saved], APPROVAL_FIELDS)); before = path.read_bytes()
        self.editor.start_apply(); self.assertEqual(self.finish()['state'], 'complete')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.editor.drafts.status()['draft_count'], 0)

    def test_ide_write_during_rebuild_is_retained_and_drafts_stay_pending(self):
        """The rollback may reverse our files but must not undo a concurrent IDE edit."""
        self.save()
        path = self.translations / 'RU_RU.csv'
        foreign = b'IDE changed this file during the rebuild'
        self.prepare.side_effect = lambda *args: path.write_bytes(foreign)
        self.editor.start_apply(); result = self.finish()
        self.assertEqual(result['state'], 'error')
        self.assertEqual(path.read_bytes(), foreign)
        self.assertEqual(self.editor.drafts.status()['draft_count'], 1)

    def test_editing_during_apply_survives_and_can_be_applied_next(self):
        first = self.save()
        entered, release = threading.Event(), threading.Event()
        def pause(*args):
            entered.set(); self.assertTrue(release.wait(3))
        self.prepare.side_effect = pause
        self.editor.start_apply(); self.assertTrue(entered.wait(2))
        second = self.save(text='Later [ICON_CULTURE]', revision=first['entry']['revision'])
        release.set(); self.assertEqual(self.finish()['state'], 'complete')
        pending = self.editor.drafts.entries()[0]
        self.assertEqual(pending['payload']['edit']['text'], 'Later [ICON_CULTURE]')
        self.assertEqual(pending['payload']['base']['approval']['text'], 'Translated [ICON_CULTURE]')
        self.assertGreater(pending['revision'], second['entry']['revision'])
        self.prepare.side_effect = None
        self.editor.start_apply(); self.assertEqual(self.finish()['state'], 'complete')
        self.assertIn('Later [ICON_CULTURE]', (self.translations / 'RU_RU.csv').read_text())

    def test_developer_text_and_new_keys_are_local_until_single_apply(self):
        self.mode = 'developer'
        before = self.source.read_bytes()
        self.save(text='Updated [ICON_CULTURE]', locale='', index=0,
            base={'key': 'TXT_KEY_ONE', 'kind': 'Row', 'text': 'One [ICON_CULTURE]'},
            edit=edit('Updated [ICON_CULTURE]', 'TXT_KEY_ONE'))
        self.save('TXT_KEY_CREATED', 'New', locale='', index=-1, create=True,
                  base={}, edit=edit('New', 'TXT_KEY_CREATED'))
        self.assertEqual(self.source.read_bytes(), before)
        self.editor.start_apply(); self.assertEqual(self.finish()['state'], 'complete')
        self.assertIn('<Text>Updated [ICON_CULTURE]</Text>', self.source.read_text())
        self.assertIn('Tag="TXT_KEY_CREATED"', self.source.read_text())
        self.prepare.assert_called_once()

    def test_source_fingerprint_rejects_translation_written_before_english_edit(self):
        self.save()
        self.candidates.return_value['TXT_KEY_ONE']['source_fingerprint'] = 'c'*64
        self.editor.start_apply(); result = self.finish()
        self.assertIn('English changed', result['error'])
        self.assertEqual(self.editor.drafts.status()['draft_count'], 1)


if __name__ == '__main__':
    unittest.main()
