"""CLI operations use the same real draft/Apply service as the browser."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editor_cli
import test_editor_drafts as fixtures
from lekmod_localization.common import CatalogError


class EditorCLITests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.EditorDraftTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.fixture, self.editor = fixture, fixture.editor
        self.editor.english_dates = {}

    def command(self, *words):
        return editor_cli.execute(self.editor, editor_cli.parser().parse_args(words))

    def test_cli_save_load_restore_and_history_operate_on_browser_drafts(self):
        self.fixture.save(text='Первый [ICON_CULTURE]')
        first = self.command('save')['save_id']
        revision = self.editor.drafts.entries()[0]['revision']
        self.fixture.save(text='Второй [ICON_CULTURE]', revision=revision)
        self.assertEqual(self.command('drafts')['entries'][0]['payload']['edit']['text'], 'Второй [ICON_CULTURE]')
        self.command('undo')
        self.assertEqual(self.command('drafts')['entries'][0]['payload']['edit']['text'], 'Первый [ICON_CULTURE]')
        self.command('redo'); self.command('load', str(first))
        self.assertEqual(self.command('saves')['saves'][0]['id'], first)
        self.assertEqual(self.command('drafts')['entries'][0]['payload']['edit']['text'], 'Первый [ICON_CULTURE]')

    def test_cli_accept_and_apply_preserve_exact_translation_and_generator_approval(self):
        self.fixture.save(text='Без повторной иконки')
        result = self.command('apply')
        self.assertEqual(result['applied_count'], 0)
        issue = self.command('issues')['issues'][0]
        self.command('accept-formatting', '--slot', issue['slot'], '--revision', str(issue['current_revision']),
                     '--source-fingerprint', issue['source_fingerprint'])
        result = self.command('apply')
        self.assertEqual(result['applied_count'], 1)
        self.assertIn('formatting_approval', (self.fixture.translations / 'RU_RU.csv').read_text(encoding='utf-8-sig'))

    def test_cli_requires_explicit_game_folder_and_preserves_ui_preferences(self):
        self.editor.preference_overrides = {'mode': 'developer', 'game_path': 'explicit game'}
        self.assertEqual(self.editor.preferences()['game_path'], 'explicit game')
        self.assertEqual(self.fixture.mode, 'translator')
        with self.assertRaisesRegex(CatalogError, '--game-folder'):
            self.command('apply', '--target', 'game')

    def test_ide_csv_can_be_accepted_before_prepare_with_backup_and_stale_guard(self):
        target = self.fixture.translations / 'RU_RU.csv'
        target.write_text('key,source_fingerprint,text,gender,plurality,translator_note\n'
                          'TXT_KEY_ONE,' + 'a'*64 + ',Без иконки,,,\n', encoding='utf-8')
        before = target.read_bytes()
        with patch('editor_cli.REPO_ROOT', self.fixture.root), patch('editor_cli.WORKSPACE', self.fixture.root / 'workspace'), \
                patch('editor_cli.candidate_sources', self.fixture.candidates):
            result = editor_cli.accept_project_formatting('RU_RU', 'TXT_KEY_ONE')
            self.assertTrue(result['accepted'])
            self.assertEqual(Path(result['backup']).read_bytes(), before)
            self.assertIn('formatting_approval', target.read_text(encoding='utf-8-sig'))
            self.fixture.candidates.return_value['TXT_KEY_ONE']['source_fingerprint'] = 'c'*64
            with self.assertRaisesRegex(CatalogError, 'missing/stale'):
                editor_cli.accept_project_formatting('RU_RU', 'TXT_KEY_ONE')

    def test_cli_form_sends_utf8_and_current_revision_through_real_save_service(self):
        text = self.fixture.root / 'translation.txt'; text.write_text('Перевод [ICON_CULTURE]', encoding='utf-8')
        row = {'key': 'TXT_KEY_ONE', 'draft_slot': 'T:RU_RU:TXT_KEY_ONE', 'draft_revision': 0,
               'draft_base': {'approval': None}, 'source_fingerprint': 'a'*64, 'applied_edit': fixtures.edit('')}
        with patch.object(self.editor, 'rows', return_value={'rows': [row]}):
            result = self.command('draft', '--locale', 'RU_RU', '--key', row['key'], '--text-file', str(text))
        self.assertEqual(result['entry']['payload']['edit']['text'], text.read_text(encoding='utf-8'))
        self.fixture.prepare.assert_not_called()

    def test_cli_english_creation_edit_rename_and_removal_share_developer_rules(self):
        key = 'TXT_KEY_CLI_CREATED'
        with patch.object(self.editor, 'game_texts', return_value=None):
            self.command('draft', '--key', key, '--create', '--text', 'New English')
            self.assertEqual(self.command('apply')['applied_count'], 1)
            self.assertIn(key, self.fixture.source.read_text())
            self.command('draft', '--key', key, '--text', 'Edited English', '--identifier', key + '_RENAMED')
            # Subsequent edits identify the same pending row by its original key.
            self.command('draft', '--key', key, '--text', 'Updated renamed English')
            self.assertEqual(self.command('apply')['applied_count'], 1)
            self.assertIn('Updated renamed English', self.fixture.source.read_text())
            self.command('draft', '--key', key + '_RENAMED', '--remove')
            self.assertEqual(self.command('apply')['applied_count'], 1)
            self.assertNotIn(key, self.fixture.source.read_text())

    def test_cli_json_stdout_and_runtime_errors_are_separate(self):
        output = io.StringIO()
        with patch('editor_server.Editor', return_value=self.editor), redirect_stdout(output):
            self.assertEqual(editor_cli.main(['status']), 0)
        self.assertEqual(json.loads(output.getvalue())['draft_count'], 0)


if __name__ == '__main__':
    unittest.main()
