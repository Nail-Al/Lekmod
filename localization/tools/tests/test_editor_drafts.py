"""Guard fast local saves, one batch rebuild, and work retained across failures."""

import csv
from contextlib import ExitStack, closing
import io
import json
from pathlib import Path
import sqlite3
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
    def test_fifty_saves_keep_deduplicated_content_and_prune_old_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DraftStore(Path(folder) / 'drafts.sqlite3')
            stable = 'Большой неизменный перевод. ' * 5000
            store.put('T:RU:STABLE', {'edit': edit(stable)}, 0)
            revision = 0
            ids = []
            for index in range(55):
                revision = store.put('T:DE:CHANGED', {'edit': edit(str(index))}, revision)['entry']['revision']
                ids.append(store.save_checkpoint()['save_id'])
            reopened = DraftStore(store.path)
            saves = reopened.saved_versions()
            self.assertEqual(len(saves), 50)
            self.assertEqual(saves[0]['id'], ids[-1])
            self.assertEqual(saves[-1]['id'], ids[5])
            self.assertEqual(saves[-1]['row_count'], 2)
            with self.assertRaises(CatalogError):
                reopened.load_saved_version(ids[0])
            with reopened.connection() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM saved_content').fetchone()[0], 51)
            self.assertLess(store.path.stat().st_size, 2 * 1024 * 1024, '50 Saves duplicated a large unchanged row')
            latest_time = reopened.status()['checkpoint_saved_at']
            reopened.load_saved_version(ids[5])
            self.assertEqual(reopened.get('T:DE:CHANGED')['payload']['edit']['text'], '5')
            self.assertEqual(reopened.get('T:RU:STABLE')['payload']['edit']['text'], stable)
            self.assertEqual(reopened.status()['history_count'], 0)
            self.assertEqual(reopened.status()['checkpoint_saved_at'], latest_time)
            self.assertEqual(reopened.status()['saved_version_count'], 50)
            reopened.restore_checkpoint()
            self.assertEqual(reopened.get('T:DE:CHANGED')['payload']['edit']['text'], '54')

    def test_v025_save_is_migrated_once_and_not_replaced_by_unsaved_work(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DraftStore(Path(folder) / 'drafts.sqlite3')
            first = store.put('T:RU:ONE', {'edit': edit('Explicit old Save')}, 0)
            store.save_checkpoint()
            old_time = store.status()['checkpoint_saved_at']
            store.put('T:RU:ONE', {'edit': edit('Later autosave')}, first['entry']['revision'])
            store.put('T:DE:TWO', {'edit': edit('Another language')}, 0)
            with store.connection() as db:
                for table in ('saved_rows', 'saved_content', 'saved_checkpoints'):
                    db.execute('DROP TABLE ' + table)
                db.execute("DELETE FROM migrations WHERE name='saved-checkpoints'")
            migrated = DraftStore(store.path)
            self.assertEqual(migrated.get('T:RU:ONE')['payload']['edit']['text'], 'Later autosave')
            self.assertEqual(migrated.saved_versions(), [{'id': 1, 'saved_at': old_time, 'row_count': 1}])
            self.assertEqual(DraftStore(store.path).status()['saved_version_count'], 1)
            migrated.load_saved_version(1)
            self.assertEqual(migrated.get('T:RU:ONE')['payload']['edit']['text'], 'Explicit old Save')
            self.assertIsNone(migrated.get('T:DE:TWO')['payload'])

    def test_empty_save_can_be_loaded_after_editing(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DraftStore(Path(folder) / 'drafts.sqlite3')
            saved = store.save_checkpoint()
            store.put('ONE', {'edit': edit('Later work')}, 0)
            store.load_saved_version(saved['save_id'])
            self.assertEqual(store.entries(), [])
            self.assertEqual(store.status()['saved_version_count'], 1)

    def test_v024_cleared_draft_history_survives_version_migration(self):
        """Old NULL draft rows still have useful Undo/Redo actions."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'drafts.sqlite3'
            payload = {'mode': 'translator', 'locale': 'RU_RU', 'key': 'TXT_KEY_ONE',
                       'index': -1, 'create': False, 'base': {'approval': None},
                       'source_fingerprint': 'a' * 64, 'edit': edit('Legacy edit')}
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript('''
                    CREATE TABLE state (id INTEGER PRIMARY KEY, revision INTEGER, cursor INTEGER);
                    INSERT INTO state VALUES (1, 2, 2);
                    CREATE TABLE drafts (slot TEXT PRIMARY KEY, revision INTEGER, payload TEXT, updated_at TEXT);
                    CREATE TABLE history (id INTEGER PRIMARY KEY, slot TEXT, before TEXT, after TEXT, edit_group TEXT);
                ''')
                slot = 'T:RU_RU:TXT_KEY_ONE'
                db.execute('INSERT INTO drafts VALUES (?,2,NULL,?)', (slot, '2026-10-06'))
                db.execute('INSERT INTO history VALUES (1,?,NULL,?,?)', (slot, json.dumps(payload), 'first'))
                db.execute('INSERT INTO history VALUES (2,?,?,NULL,?)', (slot, json.dumps(payload), 'clear'))
            store = DraftStore(path)
            self.assertEqual(store.entries(), [])
            store.replay(undo=True)
            self.assertEqual(store.entries()[0]['payload']['edit']['text'], 'Legacy edit')
            store.replay(undo=True)
            self.assertEqual(store.entries(), [])
            store.replay(undo=False)
            self.assertEqual(store.entries()[0]['payload']['edit']['text'], 'Legacy edit')
            store.replay(undo=False)
            self.assertEqual(store.entries(), [])
            self.assertFalse(DraftStore(path).status()['draft_redo_available'])

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
        # Windows CI includes antivirus/filesystem sync jitter. The budget still
        # gives each durable autosave less than half of the 350 ms typing debounce.
        budget = 15 if sys.platform == 'win32' else 5
        self.assertLess(time.monotonic() - started, budget, 'Local Save regressed to a rebuild-like delay')
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
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['applied_count'], 0)
        self.assertEqual(result['apply_issue_count'], 1)
        self.assertIn('formatting token', self.editor.drafts.apply_issues()[0]['reason'])
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
        self.assertIn('changed in an IDE', self.editor.drafts.apply_issues()[0]['reason'])
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
        self.assertIn('English changed', self.editor.drafts.apply_issues()[0]['reason'])
        self.assertEqual(self.editor.drafts.status()['draft_count'], 1)

    def test_all_292_drafts_allow_extra_icons_and_keep_save_after_apply(self):
        self.candidates.return_value = {}
        for index in range(292):
            key = f'TXT_KEY_BATCH_{index}'
            self.candidates.return_value[key] = {'classification': 'lekmod_new', 'source_fingerprint': 'a' * 64,
                                                'required_format_tokens': '{}'}
            self.save(key, f'Перевод {index} [ICON_PRODUCTION]')
        saved = self.editor.drafts.save_checkpoint()['save_id']
        self.editor.start_apply()
        self.assertEqual(self.finish()['state'], 'complete')
        self.assertEqual(self.editor.apply_state['applied_count'], 292)
        self.assertEqual(self.editor.drafts.status()['draft_count'], 0)
        self.prepare.assert_called_once()
        with (self.translations / 'RU_RU.csv').open(encoding='utf-8-sig', newline='') as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 292)
        self.editor.drafts.load_saved_version(saved)
        self.assertEqual(self.editor.drafts.status()['draft_count'], 0)

    def test_format_error_names_missing_token_and_retains_draft(self):
        self.save(text='Незавершённый перевод [ICON_PRODUCTION]')
        self.editor.start_apply()
        result = self.finish()
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['applied_count'], 0)
        self.assertIn('Missing: [ICON_CULTURE] × 1', self.editor.drafts.apply_issues()[0]['reason'])
        self.assertEqual(self.editor.drafts.status()['draft_count'], 1)
        self.prepare.assert_not_called()

    def test_partial_apply_retains_bad_row_and_reports_it_across_restart(self):
        bad = self.save(text='Needs an icon')
        self.save('TXT_KEY_TWO', 'Correct [ICON_MOVES]', locale='DE_DE')
        before_ru = (self.translations / 'RU_RU.csv').read_bytes()
        self.editor.start_apply()
        result = self.finish()
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['applied_count'], 1)
        self.assertEqual(result['apply_issue_count'], 1)
        self.assertEqual(result['draft_count'], 1)
        self.assertEqual((self.translations / 'RU_RU.csv').read_bytes(), before_ru)
        self.assertIn('Correct [ICON_MOVES]', (self.translations / 'DE_DE.csv').read_text())
        self.prepare.assert_called_once()
        reopened = DraftStore(self.editor.drafts.path)
        self.assertEqual(reopened.apply_issues()[0]['slot'], bad['entry']['slot'])
        self.assertFalse(reopened.apply_issues()[0]['needs_recheck'])
        self.save(text='Fixed [ICON_CULTURE]', revision=reopened.get(bad['entry']['slot'])['revision'])
        self.assertTrue(reopened.apply_issues()[0]['needs_recheck'])
        self.assertEqual(reopened.apply_issues()[0]['draft_text'], 'Fixed [ICON_CULTURE]')
        self.editor.start_apply()
        self.assertEqual(self.finish()['apply_issue_count'], 0)
        self.assertEqual(reopened.entries(), [])

    def test_partial_apply_collects_every_bad_row_without_writing_empty_batch(self):
        self.save(text='Missing [ICON_PRODUCTION]')
        self.save(locale='DE_DE', source_fingerprint='c' * 64)
        self.save('TXT_KEY_TWO', '(ru text)')
        originals = {p: p.read_bytes() for p in self.translations.glob('*.csv')}
        self.editor.start_apply()
        result = self.finish()
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['applied_count'], 0)
        self.assertEqual(result['apply_issue_count'], 3)
        self.assertEqual(result['draft_count'], 3)
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)
        self.prepare.assert_not_called()

    def test_translation_depending_on_rejected_english_waits_while_other_row_applies(self):
        self.mode = 'developer'
        self.save(text='New English [ICON_CULTURE]', index=0,
            base={'key': 'TXT_KEY_ONE', 'kind': 'Row', 'text': 'Old English from before IDE change'},
            edit=edit('New English [ICON_CULTURE]', 'TXT_KEY_ONE'))
        self.mode = 'translator'
        self.save(text='Dependent [ICON_CULTURE]', source_fingerprint='c' * 64)
        self.save('TXT_KEY_TWO', 'Independent [ICON_MOVES]', locale='DE_DE')
        before = self.source.read_bytes()
        self.editor.start_apply()
        result = self.finish()
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['applied_count'], 1)
        self.assertEqual(result['apply_issue_count'], 2)
        self.assertEqual(self.source.read_bytes(), before)
        issues = self.editor.drafts.apply_issues()
        dependent = next(issue for issue in issues if issue['mode'] == 'translator')
        self.assertIn('depends on an English draft', dependent['reason'])

    def test_game_only_partial_apply_builds_only_valid_rows_without_source_writes(self):
        self.save(text='Missing icon')
        self.save('TXT_KEY_TWO', 'Game [ICON_MOVES]')
        originals = {p: p.read_bytes() for p in (self.source, self.game, self.translations / 'RU_RU.csv')}
        with patch('editor_server.require_game_closed'), patch('editor_server.inspect_game', return_value={'state': 'installed'}), \
                patch.object(self.editor, 'apply_game_version', return_value={'updated': True}) as copy:
            self.editor.start_apply(target='game')
            result = self.finish()
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['applied_count'], 1)
        self.assertEqual(result['apply_issue_count'], 1)
        self.assertEqual(result['draft_count'], 2)
        plan = copy.call_args.args[0]
        self.assertEqual([entry['payload']['key'] for entry in plan['entries']], ['TXT_KEY_TWO'])
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)


    def test_save_survives_history_pruning_branching_restart_and_overwrite(self):
        first = self.save(text='Saved [ICON_CULTURE]')
        self.editor.drafts.save_checkpoint()
        revision = first['entry']['revision']
        for index in range(120):
            revision = self.save(text=f'Edit {index} [ICON_CULTURE]', revision=revision,
                                 group=f'action-{index}')['entry']['revision']
        self.assertEqual(self.editor.drafts.status()['history_count'], 100)
        for _ in range(10):
            self.editor.drafts.replay(undo=True)
        current = self.editor.drafts.get(first['entry']['slot'])
        self.save(text='New branch [ICON_CULTURE]', revision=current['revision'], group='action-109')
        self.assertFalse(self.editor.drafts.status()['draft_redo_available'])
        restored = DraftStore(self.editor.drafts.path)
        restored.restore_checkpoint()
        self.assertEqual(restored.entries()[0]['payload']['edit']['text'], 'Saved [ICON_CULTURE]')
        self.assertFalse(restored.status()['checkpoint_dirty'])
        self.assertEqual(restored.status()['history_count'], 0)
        current = restored.entries()[0]
        self.save(text='Second Save [ICON_CULTURE]', revision=current['revision'])
        restored.save_checkpoint()
        restored.replay(undo=True)
        restored.restore_checkpoint()
        self.assertEqual(restored.entries()[0]['payload']['edit']['text'], 'Second Save [ICON_CULTURE]')

    def test_undo_and_save_restore_after_project_apply_are_local_versions(self):
        original = self.save(text='First [ICON_CULTURE]')
        self.editor.drafts.save_checkpoint()
        self.editor.start_apply()
        self.assertEqual(self.finish()['state'], 'complete')
        self.assertEqual(self.editor.drafts.status()['draft_count'], 0)
        current = self.editor.drafts.get(original['entry']['slot'])
        self.save(text='Second [ICON_CULTURE]', revision=current['revision'],
                  base=self.editor.drafts.baselines()[original['entry']['slot']]['base'])
        self.editor.start_apply()
        self.assertEqual(self.finish()['state'], 'complete')
        self.editor.drafts.replay(undo=True)
        pending = self.editor.drafts.entries()[0]['payload']
        self.assertEqual(pending['edit']['text'], 'First [ICON_CULTURE]')
        self.assertEqual(pending['base']['approval']['text'], 'Second [ICON_CULTURE]')
        self.assertIn('Second [ICON_CULTURE]', (self.translations / 'RU_RU.csv').read_text())
        self.editor.drafts.replay(undo=True)
        self.editor.drafts.restore_checkpoint()
        self.assertEqual(self.editor.drafts.entries()[0]['payload']['edit']['text'], 'First [ICON_CULTURE]')

    def test_old_csv_translation_never_returns_after_local_clear_or_replacement(self):
        from lekmod_localization.workspace import EDITOR_FIELDNAMES
        path = self.root / 'editor-menus.csv'
        row = dict.fromkeys(EDITOR_FIELDNAMES, '')
        row.update(key='TXT_KEY_ONE', classification='lekmod_new',
            source_fingerprint='a'*64, translation_source_fingerprint='a'*64,
            lekmod_en_US='One [ICON_CULTURE]', required_format_tokens='{"[ICON_CULTURE]":1}',
            translation='Ghost [ICON_CULTURE]', translation_status='draft')
        path.write_bytes(encoded_csv([row], EDITOR_FIELDNAMES))
        self.editor.manifest = lambda: {'locales': {'RU_RU': {'files': {'menus.csv': {}}}}}
        self.editor.path = lambda *args: path
        self.editor.english_dates = {}
        self.editor.version_changes = {}
        self.editor.version_info = {'upgrade_versions': []}
        with patch.object(self.editor, 'game_texts', return_value=None):
            self.assertEqual(self.editor.rows('RU_RU', 'menus', '', 0)['rows'][0]['translation'], '')
            self.editor.migrate_legacy_drafts()
            legacy = self.editor.rows('RU_RU', 'menus', '', 0)['rows'][0]
            self.assertTrue(legacy['has_local_draft'])
            self.assertEqual(legacy['translation'], 'Ghost [ICON_CULTURE]')
            result = self.save(text='', revision=legacy['draft_revision'])
            self.assertEqual(result['draft_count'], 0)
            self.editor.start_apply()
            self.assertEqual(self.finish()['state'], 'complete')
            self.editor.migrate_legacy_drafts()
            current = self.editor.rows('RU_RU', 'menus', '', 0)['rows'][0]
            self.assertEqual(current['translation'], '')
            self.assertEqual(current['translation_status'], 'missing')
            self.assertFalse(current.get('has_local_draft'))
            result = self.save(text='Replacement [ICON_CULTURE]', revision=current['draft_revision'])
            self.editor.start_apply()
            self.assertEqual(self.finish()['state'], 'complete')
            current = self.editor.rows('RU_RU', 'menus', '', 0)['rows'][0]
            self.assertEqual(current['translation'], 'Replacement [ICON_CULTURE]')
            self.assertEqual(current['translation_status'], 'applied')

    def test_running_game_blocks_merge_before_source_changes(self):
        from lekmod_localization.game_process import GameRunningError
        self.editor.handoff_data = b'reviewed package'
        self.editor.handoff_preview = {'target_sha256': 'a' * 64}
        self.editor.handoff_id = 'reviewed'
        with patch('editor_server.require_game_closed', side_effect=GameRunningError(['CivilizationV_DX11.exe'])), \
             patch('editor_server.review_merge') as merge:
            with self.assertRaises(GameRunningError):
                self.editor.apply_handoff({'handoff_id': 'reviewed', 'choices': {}, 'destination': 'project_game'})
            merge.assert_not_called()
        self.assertEqual(self.editor.handoff_data, b'reviewed package')

    def test_deleting_an_approved_translation_is_not_a_discarded_empty_draft(self):
        approved = dict(key='TXT_KEY_ONE', source_fingerprint='a'*64,
            text='Old [ICON_CULTURE]', gender='', plurality='', translator_note='', updated_at='')
        path = self.translations / 'RU_RU.csv'
        path.write_bytes(encoded_csv([approved], APPROVAL_FIELDS))
        self.save(text='', base={'approval': approved, 'source_fingerprint': 'a'*64})
        self.assertEqual(self.editor.drafts.status()['draft_count'], 1)
        self.editor.start_apply()
        self.assertEqual(self.finish()['state'], 'complete')
        self.assertNotIn('TXT_KEY_ONE', path.read_text())
        self.assertEqual(self.editor.drafts.status()['draft_count'], 0)
        self.editor.drafts.replay(undo=True)
        self.assertEqual(self.editor.drafts.entries()[0]['payload']['edit']['text'], 'Old [ICON_CULTURE]')

    def test_game_only_build_does_not_modify_project_or_clear_history(self):
        # Exercise Windows line endings on every CI platform, not just Windows.
        self.source.write_bytes(self.source.read_text().replace('\n', '\r\n').encode('utf-8'))
        self.save()
        originals = {path: path.read_bytes() for path in [self.source, self.game, self.translations / 'RU_RU.csv']}
        with patch('editor_server.settings', return_value={'game_path': str(self.root / 'game')}), \
             patch('editor_server.inspect_game', return_value={'state': 'installed', 'mods': [{'name': 'LEKMOD_v35.4'}]}), \
             patch('editor_server.sync_primary_english.synchronize'), \
             patch('editor_server.build_shipped_localization.build_candidate') as build, \
             patch('editor_server.apply_game', return_value={'changed': True}) as copy:
            def candidate(snapshot, **paths):
                self.assertIn('Translated [ICON_CULTURE]', (paths['approvals'] / 'RU_RU.csv').read_text())
                self.assertEqual(paths['english_source'].read_text(), self.source.read_text())
                return '<GameData>local candidate</GameData>', {}
            build.side_effect = candidate
            self.editor.start_apply(target='game')
            result = self.finish()
            self.assertEqual(result['state'], 'complete', result)
            self.assertEqual(result['target'], 'game')
            self.assertEqual(copy.call_args.kwargs['content'], b'<GameData>local candidate</GameData>')
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        self.assertEqual(self.editor.drafts.status()['draft_count'], 1)
        self.assertTrue(self.editor.drafts.status()['draft_undo_available'])
        self.prepare.assert_not_called()
        self.assertFalse(list((self.root / 'localization/workspace').glob('game-version-*')))

    def test_created_entity_can_be_undone_after_project_apply(self):
        self.mode = 'developer'
        self.save('TXT_KEY_CREATED', 'New', locale='', index=-1, create=True,
            base={}, edit=edit('New', 'TXT_KEY_CREATED'), slot='N:TXT_KEY_CREATED')
        self.editor.start_apply()
        self.assertEqual(self.finish()['state'], 'complete')
        self.assertIn('TXT_KEY_CREATED', self.source.read_text())
        self.editor.drafts.replay(undo=True)
        pending = self.editor.drafts.entries()[0]['payload']
        self.assertTrue(pending['delete'])
        self.editor.start_apply()
        self.assertEqual(self.finish()['state'], 'complete')
        self.assertNotIn('TXT_KEY_CREATED', self.source.read_text())
        self.editor.drafts.replay(undo=False)
        self.assertTrue(self.editor.drafts.entries()[0]['payload']['create'])

    def test_sync_chips_compare_current_content_instead_of_last_apply_target(self):
        item = {'locale': 'RU_RU', 'key': 'TXT_KEY_ONE', 'lekmod_en_US': 'One [ICON_CULTURE]',
            'required_format_tokens': '{"[ICON_CULTURE]":1}', 'source_fingerprint': 'a'*64,
            'translation': 'New [ICON_CULTURE]', 'translation_gender': '', 'translation_plurality': '',
            'has_local_draft': True}
        installed = {'ru_ru': {'TXT_KEY_ONE': {'Text': 'New [ICON_CULTURE]', 'Gender': '', 'Plurality': ''}}}
        self.editor.row_sync(item, installed, developer=False)
        self.assertEqual(item['synced_to'], {'project': False, 'game': True})
        item['translation'] = ''
        self.editor.row_sync(item, installed, developer=False)
        self.assertFalse(item['synced_to']['game'])
        installed['ru_ru']['TXT_KEY_ONE']['Text'] = item['lekmod_en_US']
        self.editor.row_sync(item, installed, developer=False)
        self.assertTrue(item['synced_to']['game'])
        self.editor.row_sync(item, None, developer=False)
        self.assertIsNone(item['synced_to']['game'])


    def test_same_text_can_be_reapproved_against_changed_english(self):
        approved = dict(key='TXT_KEY_ONE', source_fingerprint='a'*64,
            text='Same [ICON_CULTURE]', gender='', plurality='', translator_note='', updated_at='')
        path = self.translations / 'RU_RU.csv'
        path.write_bytes(encoded_csv([approved], APPROVAL_FIELDS))
        self.candidates.return_value['TXT_KEY_ONE']['source_fingerprint'] = 'c'*64
        result = self.save(text=approved['text'], source_fingerprint='c'*64,
                           base={'approval': approved, 'source_fingerprint': 'c'*64})
        self.assertEqual(result['draft_count'], 1)
        self.editor.start_apply()
        self.assertEqual(self.finish()['state'], 'complete')
        with path.open(encoding='utf-8-sig', newline='') as handle:
            self.assertEqual(next(csv.DictReader(handle))['source_fingerprint'], 'c'*64)
        self.assertEqual(self.editor.drafts.status()['draft_count'], 0)


    def test_open_game_blocks_all_before_changing_project_or_starting_job(self):
        from lekmod_localization.game_process import GameRunningError
        self.save()
        before = self.source.read_bytes(), (self.translations / 'RU_RU.csv').read_bytes()
        with patch('editor_server.require_game_closed', side_effect=GameRunningError(['CivilizationV.exe'])):
            for target in ('game', 'all'):
                with self.assertRaises(GameRunningError):
                    self.editor.start_apply(target=target)
        self.assertEqual(before, (self.source.read_bytes(), (self.translations / 'RU_RU.csv').read_bytes()))
        self.assertEqual(self.editor.save_state['state'], 'idle')
        self.prepare.assert_not_called()



if __name__ == '__main__':
    unittest.main()
