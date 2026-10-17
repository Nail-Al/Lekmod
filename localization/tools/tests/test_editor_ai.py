"""AI candidates use real revision-checked drafts and never apply themselves."""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editor_cli
import test_editor_drafts as fixtures
from lekmod_localization.common import CatalogError
from lekmod_localization.llm_translation import LLMConnection


class EditorAITests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.EditorDraftTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.fixture, self.editor = fixture, fixture.editor
        self.editor.llm = LLMConnection(fixture.root / 'private-ai')
        self.editor.llm.configure({'enabled': True, 'api_key': 'fixture-secret', 'model': 'fixture-model', 'civilopedia': False})
        self.row_patch = patch.object(self.editor, 'rows', side_effect=self.rows)
        self.row_patch.start(); self.addCleanup(self.row_patch.stop)
        self.request = {'key': 'TXT_KEY_ONE', 'locale': 'RU_RU', 'revision': 0, 'source_fingerprint': 'a'*64}
        self.generated = {'translation': 'Культура [ICON_CULTURE]', 'provider': 'openai', 'model': 'fixture-model',
                          'warnings': [], 'sources': [{'name': 'Current English', 'kind': 'project'}]}
        self.generate = patch.object(self.editor.llm, 'generate', return_value=self.generated).start()
        self.addCleanup(patch.stopall)

    def rows(self, locale, category, query, offset, limit):
        slot = f'T:{locale}:TXT_KEY_ONE'; record = self.editor.drafts.get(slot)
        payload = record['payload'] if record and record['payload'] else None
        edit = payload['edit'] if payload else fixtures.edit('')
        return {'rows': [{'key': 'TXT_KEY_ONE', 'locale': locale, 'classification': 'lekmod_new',
            'category': 'buildings', 'lekmod_en_US': 'One [ICON_CULTURE]',
            'required_format_tokens': '{"[ICON_CULTURE]":1}', 'source_fingerprint': 'a'*64,
            'draft_slot': slot, 'draft_revision': record['revision'] if record else 0,
            'draft_base': {'approval': None}, 'translation': edit['text'],
            'translation_gender': edit['gender'], 'translation_plurality': edit['plurality'],
            'translator_note': edit['note']}], 'total': 1}

    def test_generation_is_read_only_then_use_is_undoable_local_draft(self):
        before = self.fixture.source.read_bytes()
        candidate = self.editor.ai_generate(self.request)
        self.assertEqual(self.editor.drafts.entries(), [])
        result = self.editor.ai_use({'candidate_id': candidate['candidate_id']})
        self.assertEqual(result['edit']['text'], self.generated['translation'])
        self.assertEqual(self.fixture.source.read_bytes(), before)
        self.fixture.prepare.assert_not_called()
        self.assertNotIn('formatting_approval', result['entry']['payload'])
        self.editor.drafts.replay(undo=True)
        self.assertEqual(self.editor.drafts.entries(), [])
        with self.assertRaisesRegex(CatalogError, 'expired'):
            self.editor.ai_use({'candidate_id': candidate['candidate_id']})

    def test_manual_edit_before_or_during_generation_is_never_replaced(self):
        candidate = self.editor.ai_generate(self.request)
        self.fixture.save(text='Ручной [ICON_CULTURE]')
        with self.assertRaises(CatalogError):
            self.editor.ai_use({'candidate_id': candidate['candidate_id']})
        self.assertEqual(self.editor.drafts.entries()[0]['payload']['edit']['text'], 'Ручной [ICON_CULTURE]')
        self.generate.reset_mock()
        with self.assertRaisesRegex(CatalogError, 'empty'):
            self.editor.ai_generate(self.request)
        self.generate.assert_not_called()

    def test_changed_source_rejects_late_candidate(self):
        candidate = self.editor.ai_generate(self.request)
        self.fixture.candidates.return_value['TXT_KEY_ONE']['source_fingerprint'] = 'c'*64
        with self.assertRaisesRegex(CatalogError, 'English changed'):
            self.editor.ai_use({'candidate_id': candidate['candidate_id']})
        self.assertEqual(self.editor.drafts.entries(), [])

    def test_note_only_change_and_other_locale_are_checked_independently(self):
        candidate = self.editor.ai_generate(self.request)
        self.fixture.save(locale='DE_DE', text='Kultur [ICON_CULTURE]')
        self.editor.ai_use({'candidate_id': candidate['candidate_id']})
        self.assertEqual(len(self.editor.drafts.entries()), 2)
        self.editor.drafts.replay(undo=True)
        request = {**self.request, 'revision': self.editor.drafts.get('T:RU_RU:TXT_KEY_ONE')['revision']}
        candidate = self.editor.ai_generate(request)
        self.fixture.save(text='', revision=request['revision'], edit={**fixtures.edit(''), 'note': 'Human note'})
        with self.assertRaisesRegex(CatalogError, 'changed'):
            self.editor.ai_use({'candidate_id': candidate['candidate_id']})

    def test_parallel_request_and_stale_view_rejected_before_provider_call(self):
        self.editor.ai_generate(self.request)
        self.generate.reset_mock()
        self.editor.ai_lock.acquire()
        try:
            with self.assertRaisesRegex(CatalogError, 'already running'):
                self.editor.ai_generate(self.request)
        finally:
            self.editor.ai_lock.release()
        with self.assertRaisesRegex(CatalogError, 'changed'):
            self.editor.ai_generate({**self.request, 'revision': 5})
        self.generate.assert_not_called()

    def test_cli_context_preview_and_translation_share_browser_service(self):
        def command(*args):
            return editor_cli.execute(self.editor, editor_cli.parser().parse_args(args))
        options = ('--locale', 'RU_RU', '--key', 'TXT_KEY_ONE')
        context = command('ai-context', *options)
        self.assertEqual(context['language']['locale'], 'RU_RU')
        self.generate.assert_not_called()
        preview = command('ai-translate', *options, '--preview')
        self.assertEqual(preview['translation'], self.generated['translation'])
        self.assertEqual(self.editor.drafts.entries(), [])
        result = command('ai-translate', *options)
        self.assertEqual(result['entry']['payload']['edit']['text'], preview['translation'])
        self.assertNotIn('fixture-secret', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
