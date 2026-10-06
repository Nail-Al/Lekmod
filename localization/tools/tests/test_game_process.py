"""Game process checks block writes and can be retried after the process exits."""

import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.game_process import (
    GameRunningError, is_game_process, require_game_closed, running_game_processes,
)
from lekmod_localization.common import CatalogError


class GameProcessTests(unittest.TestCase):
    def test_all_game_image_names_and_other_processes(self):
        for name in ('CivilizationV.exe', 'CivilizationV_DX11.exe', 'CivilizationV_Tablet.exe',
                     r'C:\Games\CivilizationV_DX11.exe', 'Civ5XP'):
            self.assertTrue(is_game_process(name), name)
        for name in ('LekmodLocalizationEditor.exe', 'LauncherPatcher.exe', 'notepad.exe', 'CivilizationVI.exe'):
            self.assertFalse(is_game_process(name), name)

    def test_windows_csv_check_and_retry_do_not_expose_command_lines(self):
        output = '"CivilizationV_DX11.exe","42","Console","1","100 K"\n"notepad.exe","43","Console","1","10 K"\n'
        with patch('lekmod_localization.game_process.os.name', 'nt'), \
             patch('lekmod_localization.game_process.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 0, stdout=output)) as run:
            self.assertEqual(running_game_processes(), ['CivilizationV_DX11.exe'])
            with self.assertRaises(GameRunningError) as error:
                require_game_closed()
            self.assertEqual(error.exception.code, 'game_running')
            self.assertEqual(error.exception.processes, ['CivilizationV_DX11.exe'])
            self.assertEqual(run.call_args.args[0], ['tasklist', '/FO', 'CSV', '/NH'])
            run.return_value.stdout = '"notepad.exe","43","Console","1","10 K"\n'
            require_game_closed()

    def test_failed_process_check_blocks_instead_of_assuming_game_closed(self):
        with patch('lekmod_localization.game_process.os.name', 'nt'), \
             patch('lekmod_localization.game_process.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('tasklist', 5)):
            with self.assertRaisesRegex(CatalogError, 'Could not check game'):
                require_game_closed()
