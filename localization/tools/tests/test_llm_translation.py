"""Provider contracts and private, explicitly requested AI translation."""

import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.common import CatalogError
from lekmod_localization import llm_translation as ai


def reply(text='Культура [ICON_CULTURE]', provider='openai'):
    content = json.dumps({'translation': text}, ensure_ascii=False)
    if provider == 'openai':
        return {'status': 'completed', 'output': [{'type': 'message',
                'content': [{'type': 'output_text', 'text': content}]}]}
    return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': content}]}


class TranslationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.connection = ai.LLMConnection(self.root)
        self.connection.configure({'enabled': True, 'api_key': 'private-test-key',
                                   'model': 'fixture-model', 'civilopedia': False})
        self.context = ai.build_context({'key': 'TXT_KEY_CULTURE_HELP',
            'lekmod_en_US': 'Culture [ICON_CULTURE]', 'required_format_tokens': '{"[ICON_CULTURE]":1}'}, 'RU_RU')

    def test_settings_hide_key_and_preserve_it_only_for_same_provider(self):
        self.assertNotIn('private-test-key', json.dumps(self.connection.status()))
        status = self.connection.configure({'enabled': True, 'model': 'other-model', 'api_key': ''})
        self.assertTrue(status['configured'])
        with self.assertRaisesRegex(CatalogError, 'API key'):
            self.connection.configure({'provider': 'anthropic', 'model': 'fixture-model'})
        self.assertEqual(self.connection.status()['provider'], 'openai')
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.connection.path.stat().st_mode), 0o600)
        else:
            self.assertNotIn(b'private-test-key', self.connection.path.read_bytes())
            self.assertTrue(ai.LLMConnection(self.root).status()['configured'])
        self.connection.configure({'disconnect': True})
        self.assertFalse(self.connection.status()['has_key'])
        self.assertFalse(self.connection.path.exists())

    def test_both_provider_payloads_use_native_schema_and_parse_raw_rest(self):
        for provider in ('openai', 'anthropic'):
            with self.subTest(provider=provider):
                self.connection.configure({'enabled': True, 'provider': provider,
                    'api_key': 'private-test-key', 'model': 'fixture-model', 'max_output_tokens': 4096})
                with patch.object(ai, 'request_json', return_value=reply(provider=provider)) as request:
                    result = self.connection.generate(self.context)
                self.assertEqual(result['translation'], 'Культура [ICON_CULTURE]')
                url, = request.call_args.args
                body = request.call_args.kwargs['body']; headers = request.call_args.kwargs['headers']
                self.assertNotIn('private-test-key', json.dumps(body))
                if provider == 'openai':
                    self.assertEqual(url, 'https://api.openai.com/v1/responses')
                    self.assertFalse(body['store']); self.assertEqual(body['max_output_tokens'], 4096)
                    self.assertTrue(body['text']['format']['strict'])
                    self.assertEqual(headers['Authorization'], 'Bearer private-test-key')
                else:
                    self.assertEqual(url, 'https://api.anthropic.com/v1/messages')
                    self.assertEqual(body['max_tokens'], 4096)
                    self.assertEqual(body['output_config']['format']['schema'], ai.OUTPUT_SCHEMA)
                    self.assertEqual(headers['anthropic-version'], '2023-06-01')

    def test_truncated_refused_and_malformed_responses_are_not_translations(self):
        for payload in ({'status': 'incomplete'}, {'status': 'completed', 'output': None},
                        {'status': 'completed', 'output': [{'type': 'message', 'content': None}]},
                        reply(''), {'status': 'completed', 'output': []}):
            with self.subTest(payload=payload), patch.object(ai, 'request_json', return_value=payload):
                with self.assertRaises(CatalogError):
                    self.connection.generate(self.context)
                self.assertFalse(self.connection.request_lock.locked())
        self.connection.configure({'enabled': True, 'provider': 'anthropic', 'api_key': 'other', 'model': 'fixture'})
        for payload in ({'stop_reason': 'max_tokens'}, {'stop_reason': 'end_turn', 'content': None}):
            with patch.object(ai, 'request_json', return_value=payload), self.assertRaises(CatalogError):
                self.connection.generate(self.context)

    def test_token_mismatch_is_a_review_warning_without_approval(self):
        with patch.object(ai, 'request_json', return_value=reply('Культура')):
            result = self.connection.generate(self.context)
        self.assertTrue(any('Formatting' in item for item in result['warnings']))
        self.assertNotIn('formatting_approval', result)

    def test_disconnect_during_request_discards_late_result(self):
        def disconnect(*args, **kwargs):
            self.connection.configure({'disconnect': True}); return reply()
        with patch.object(ai, 'request_json', side_effect=disconnect), self.assertRaisesRegex(CatalogError, 'changed'):
            self.connection.generate(self.context)

    def test_one_request_at_a_time_and_no_silent_retry(self):
        self.connection.request_lock.acquire(); self.addCleanup(self.connection.request_lock.release)
        with patch.object(ai, 'request_json') as request, self.assertRaisesRegex(CatalogError, 'already running'):
            self.connection.generate(self.context)
        request.assert_not_called()

    def test_model_list_is_read_only_and_uses_only_selected_provider_key(self):
        before = self.connection.path.read_bytes()
        with patch.object(ai, 'request_json', return_value={'data': [{'id': 'model-2'}, {'id': 'model-1'}]}) as request:
            result = self.connection.models({})
        self.assertEqual([item['id'] for item in result['models']], ['model-1', 'model-2'])
        self.assertEqual(request.call_args.args[0], 'https://api.openai.com/v1/models')
        self.assertEqual(self.connection.path.read_bytes(), before)

    def test_context_uses_relevant_official_and_reviewed_terms(self):
        vanilla = {'en_US': {'TXT_KEY_TECH_REFRIGERATION': {'Text': 'Refrigeration'},
                            'TXT_KEY_UNRELATED': {'Text': 'Unrelated'}},
                   'RU_RU': {'TXT_KEY_TECH_REFRIGERATION': {'Text': 'Криогеника'},
                             'TXT_KEY_UNRELATED': {'Text': 'Не отправлять'}}}
        row = {'key': 'TXT_KEY_TECH_REFRIGERATION_HELP', 'lekmod_en_US': 'Requires Refrigeration [ICON_RESEARCH].',
               'required_format_tokens': {'[ICON_RESEARCH]': 1}}
        related = [{'key': 'TXT_KEY_TECH_REFRIGERATION', 'lekmod_en_US': 'Refrigeration',
                    'translation': 'Непроверенный черновик', 'translation_status': 'draft'}]
        context = ai.build_context(row, 'RU_RU', vanilla=vanilla, related=related)
        self.assertEqual(context['terms'][0]['translation'], 'Криогеника')
        self.assertNotIn('Не отправлять', json.dumps(context, ensure_ascii=False))
        self.assertEqual(context['related'][0]['translation'], '')
        self.assertEqual(context['required_tokens'], {'[ICON_RESEARCH]': 1})

    def test_civilopedia_uses_actual_excerpts_and_successful_cache(self):
        wiki = ai.Civilopedia(self.root)
        payload = {'query': {'pages': [{'title': 'Криогеника (Civ5)', 'extract': 'Официальное название технологии.',
            'fullurl': 'https://civilopedia.fandom.com/ru/wiki/Криогеника_(Civ5)'},
            {'title': 'Refrigeration (Civ6)', 'extract': 'Wrong game', 'fullurl': 'https://civilopedia.fandom.com/wiki/Civ6'}]}}
        def context():
            return {'wiki_query': 'Криогеника', 'language': {'locale': 'RU_RU'}, 'sources': []}
        with patch.object(ai, 'request_json', return_value=payload) as request:
            first = context(); wiki.augment(first)
            second = context(); wiki.augment(second)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(first['civilopedia'], second['civilopedia'])
        self.assertEqual(len(first['civilopedia']), 1)
        self.assertTrue(first['sources'][0]['url'].endswith('_(Civ5)'))

    def test_unavailable_wiki_and_corrupt_cache_preserve_local_references(self):
        wiki = ai.Civilopedia(self.root)
        wiki.path.write_text('{"broken": 42}')
        context = {'wiki_query': 'Culture', 'language': {'locale': 'RU_RU'},
                   'sources': [{'name': 'Verified vanilla', 'kind': 'vanilla'}]}
        with patch.object(ai, 'request_json', side_effect=CatalogError('Blocked')):
            wiki.augment(context)
        self.assertNotIn('civilopedia', context)
        self.assertEqual(context['sources'][0]['kind'], 'vanilla')
        self.assertEqual(context['sources'][1]['kind'], 'wiki-unavailable')

    def test_transport_errors_never_include_raw_response_or_key(self):
        for error in (HTTPError('https://api.openai.com/v1/responses', 429, 'private-test-key', {},
                                io.BytesIO(b'private-test-key full prompt')),
                      URLError('private-test-key')):
            with patch.object(ai, 'build_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(CatalogError) as caught:
                    ai.request_json('https://api.openai.com/v1/responses')
            self.assertNotIn('private-test-key', str(caught.exception))
        with self.assertRaises(CatalogError):
            ai.request_json('http://api.openai.com/v1/responses')
        self.assertIsNone(ai.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))


if __name__ == '__main__':
    unittest.main()
