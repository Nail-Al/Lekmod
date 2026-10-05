"""Slow project preparation must not look like a broken editor update."""

import json
from http.server import HTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editor_server
from lekmod_localization.editor_update import _record


class StartupTests(unittest.TestCase):
    def test_failed_project_initialization_keeps_settings_and_logs_available(self):
        """A broken local comparison record can be repaired without a dead browser page."""
        servers = []
        def server(*args, **kwargs):
            result = HTTPServer(*args, **kwargs)
            servers.append(result)
            return result
        with tempfile.TemporaryDirectory() as directory, \
             patch('editor_server.APP_HOME', Path(directory)), \
             patch('editor_server.HTTPServer', side_effect=server), \
             patch('editor_server.validate_project', return_value={'version': 'v35.4'}), \
             patch('editor_server.manage.migrate_workspace'), \
             patch('editor_server.manage.prepare'), \
             patch('editor_server.read_dates', return_value={}), \
             patch('editor_server.english_base'), \
             patch('editor_server.change_map', side_effect=ValueError('Broken comparison record')), \
             patch('editor_server.detect_game', return_value=''), \
             patch('builtins.print'), \
             patch.object(sys, 'argv', ['editor_server.py', '--no-browser']):
            worker = threading.Thread(target=editor_server.main)
            worker.start()
            try:
                deadline = time.monotonic() + 5
                while not servers and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(servers)
                base = f'http://127.0.0.1:{servers[0].server_port}'
                while time.monotonic() < deadline:
                    with urlopen(base + '/api/meta', timeout=2) as response: value = json.load(response)
                    if not value.get('initializing'): break
                    time.sleep(.01)
                self.assertFalse(value['ready'])
                self.assertIn('Broken comparison record', value['connection_error'])
                self.assertIn('preferences', value)
                with urlopen(base + '/api/logs', timeout=2) as response: events = json.load(response)['events']
                self.assertIn('Broken comparison record', events[-1]['result'])
            finally:
                if servers: servers[0].shutdown()
                worker.join(3)
                self.assertFalse(worker.is_alive())

    def test_http_health_and_loading_ui_open_while_project_is_still_blocked(self):
        """The updater can confirm a real server without waiting for the catalog."""
        started, finish = threading.Event(), threading.Event()
        servers = []

        def server(*args, **kwargs):
            result = HTTPServer(*args, **kwargs)
            servers.append(result)
            return result

        def slow_prepare(self):
            started.set()
            finish.wait(5)

        with tempfile.TemporaryDirectory() as directory, \
             patch('editor_server.APP_HOME', Path(directory)), \
             patch('editor_server.Editor.__init__', slow_prepare), \
             patch('editor_server.HTTPServer', side_effect=server), \
             patch('builtins.print'), \
             patch.object(sys, 'argv', ['editor_server.py', '--no-browser']):
            worker = threading.Thread(target=editor_server.main)
            worker.start()
            try:
                self.assertTrue(started.wait(3))
                base = f'http://127.0.0.1:{servers[0].server_port}'
                with urlopen(base + '/api/health', timeout=2) as response:
                    self.assertIn('editor_version', json.load(response))
                with urlopen(base + '/api/meta', timeout=2) as response:
                    self.assertTrue(json.load(response)['initializing'])
                with urlopen(base + '/', timeout=2) as response:
                    self.assertIn(b'loading-spinner', response.read())
                self.assertFalse(finish.is_set())
            finally:
                finish.set()
                servers[0].shutdown()
                worker.join(3)
                self.assertFalse(worker.is_alive())

    def test_helper_log_written_after_startup_is_not_erased_by_ui_event(self):
        """A late rollback cause remains visible after a user opens Logs."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editor = object.__new__(editor_server.Editor)
            editor.log_path = root / 'localization/workspace/editor-actions.jsonl'
            editor.log_lock = threading.Lock()
            editor.events = []
            _record(root, 'failure', 'Updated editor did not open')
            editor.record_event('logs-open', 'ui')
            events = editor.read_events()
            self.assertEqual([event['action'] for event in events], ['editor-update-install', 'logs-open'])
            self.assertEqual(events[0]['result'], 'failure')
            self.assertIn('did not open', events[0]['detail'])


if __name__ == '__main__': unittest.main()
