"""Read-only checks for Civilization V processes before installed-game writes."""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path
import re
import subprocess

from .common import CatalogError


class GameRunningError(CatalogError):
    code = 'game_running'

    def __init__(self, processes: list[str]):
        self.processes = processes
        super().__init__('Civilization V is running. Close the game, then choose Retry to apply your changes.')


def is_game_process(name: str) -> bool:
    name = name.replace('\\', '/').rsplit('/', 1)[-1]
    return bool(re.fullmatch(r'CivilizationV(?:_[A-Za-z0-9_]+)?(?:\.exe)?|Civ5(?:XP)?', name, re.IGNORECASE))


def running_game_processes() -> list[str]:
    """Inspect image names, never command lines or user files."""
    if os.name == 'nt':
        try:
            result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True,
                text=True, encoding='utf-8', errors='replace', timeout=5,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CatalogError('Could not check game processes. Retry after closing Civilization V.') from error
        if result.returncode:
            raise CatalogError('Windows could not check game processes. Retry after closing Civilization V.')
        names = [row[0] for row in csv.reader(io.StringIO(result.stdout)) if row]
    elif Path('/proc').is_dir():
        names = []
        for process in Path('/proc').iterdir():
            if not process.name.isdigit():
                continue
            try:
                names.append((process / 'comm').read_text().strip())
            except (OSError, UnicodeError):
                continue
    else:
        try:
            result = subprocess.run(['ps', '-A', '-o', 'comm='], capture_output=True, text=True,
                                    timeout=5, check=True)
            names = result.stdout.splitlines()
        except (OSError, subprocess.SubprocessError) as error:
            raise CatalogError('Could not check game processes. Retry after closing Civilization V.') from error
    return sorted({name for name in names if is_game_process(name)}, key=str.casefold)


def require_game_closed() -> None:
    processes = running_game_processes()
    if processes:
        raise GameRunningError(processes)
