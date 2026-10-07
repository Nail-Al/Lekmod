"""Exercise the Windows EXE's source gate and a full translation save/undo."""

from __future__ import annotations

import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile


REPOSITORY = Path(__file__).resolve().parents[2]


def get(url: str) -> bytes:
    """Fetch one response from the local editor with a short timeout."""
    with urlopen(url, timeout=8) as response:
        return response.read()


def post(base: str, token: str, path: str, payload: dict) -> dict:
    """Use the same origin and one-time token as the browser UI."""
    request = Request(base + path, data=json.dumps(payload).encode(), headers={
        "Origin": base, "X-Editor-Token": token,
        "Content-Type": "application/json",
    })
    with urlopen(request, timeout=90) as response:
        return json.load(response)


def source_fixture(destination: Path) -> None:
    """Copy enough real repository inputs to test the packaged editor."""
    files = [
        "LEKMOD/VERSION", "LEKMOD/Override/CIV5Units_Mongol.xml",
        "localization/en_US/primary.xml", "localization/config.json",
        "localization/reference/vanilla-fingerprints.json.gz",
        "localization/reference/english-edit-dates.json.gz",
        "localization/tools/manage.py",
    ]
    files.extend(str(path.relative_to(REPOSITORY)) for path in
                 (REPOSITORY / "LEKMOD/Art").rglob("*")
                 if path.is_file() and path.suffix.lower() in (".xml", ".sql"))
    files.extend(str(path.relative_to(REPOSITORY)) for path in
                 (REPOSITORY / "localization/translations").glob("*.csv"))
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY / name, target)
    (destination / "LEKMOD/Lua/tmp").mkdir(parents=True, exist_ok=True)
    (destination / "localization/tools/tests").mkdir(parents=True, exist_ok=True)


def start_editor(root: Path, port: int, log: Path) -> tuple[subprocess.Popen, str, str]:
    """Wait for the frozen executable to prepare its local HTTP interface."""
    output = log.open("wb")
    process = subprocess.Popen([str(root / "LekmodLocalizationEditor.exe"),
                                "--no-browser", "--port", str(port)], cwd=root,
                               stdout=output, stderr=subprocess.STDOUT)
    output.close()
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError(f"editor exited early: {process.returncode}; " +
                               log.read_text(encoding="utf-8", errors="replace")[-3000:])
        try:
            html = get(base + "/").decode("utf-8")
            token = re.search(r'<meta name="editor-token" content="([^"]+)">', html)
            if not token:
                raise RuntimeError("editor page has no request token")
            metadata = json.loads(get(base + '/api/meta'))
            if metadata.get('initializing'):
                time.sleep(.25)
                continue
            return process, base, token.group(1)
        except (URLError, TimeoutError):
            time.sleep(1)
    raise RuntimeError("editor did not start in two minutes; " +
                       log.read_text(encoding="utf-8", errors="replace")[-3000:])


def stop_editor(process: subprocess.Popen, base: str, token: str) -> None:
    """Shut down the one process that owns this test's port."""
    if process.poll() is None:
        try:
            post(base, token, "/api/stop", {})
        except OSError:
            pass
    try:
        process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True)
        else:
            process.terminate()
        process.wait(timeout=10)


def stop_restarted_editor(base: str, token: str) -> None:
    """Stop the detached replacement, allowing a reset during Windows shutdown."""
    for _ in range(60):
        try:
            post(base, token, "/api/stop", {})
        except OSError:
            # Windows can reset the last HTTP request as the EXE exits.
            pass
        time.sleep(.25)
        try:
            get(base + "/api/meta")
        except OSError:
            return
    raise RuntimeError("replacement editor did not close its localhost port")


def main() -> int:
    """Verify clean-game gating, then a connected project's save and undo."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(root)
        assert not (root / "LEKMOD").exists(), "portable editor shipped an incomplete mod"
        assert not (root / "localization/workspace/vanilla-snapshot.json.gz").exists()
        with socket.socket() as address:
            address.bind(("127.0.0.1", 0))
            port = address.getsockname()[1]
        process, base, token = start_editor(root, port, root / "first-launch.log")
        new_token = None
        try:
            meta = json.loads(get(base + "/api/meta"))
            health = json.loads(get(base + "/api/health"))
            assert health["editor_version"] == meta["editor_version"]
            assert health["server_instance"] == meta["server_instance"]
            assert not meta["ready"] and not meta["locales"]
            initial_html = get(base + "/")
            assert b'id="snapshot-encrypt"' in initial_html
            assert initial_html.index(b'id="identifier"') < initial_html.index(b'id="translation"')
            assert b"<svg" in get(base + "/favicon.svg")
            try:
                get(base + "/api/rows?locale=RU_RU&category=buildings")
            except HTTPError as error:
                assert error.code == 400
            else:
                raise RuntimeError("editor exposed rows without a full project")
            try:
                post(base, token, "/api/snapshot-encrypt", {"password": "a-long-team-password"})
            except HTTPError as error:
                assert error.code == 400
            else:
                raise RuntimeError("editor encrypted a snapshot without a connected project")
            project = root / "full-project"
            source_fixture(project)
            connected = post(base, token, "/api/connect", {"project_path": str(project),
                                                           "game_path": ""})
            assert connected["restart"], "first project connection must restart the editor"
            for _ in range(120):
                try:
                    live = json.loads(get(base + "/api/meta"))
                    if not live.get('initializing') and live["server_instance"] != meta["server_instance"] and live["ready"]:
                        break
                except OSError:
                    # Closing the old Windows server can reset a request mid-flight.
                    # A new instance must still answer before this check succeeds.
                    pass
                time.sleep(.5)
            else:
                raise RuntimeError("connected editor did not reopen the original browser URL")
            try:
                post(base, token, "/api/preferences", {"mode": "developer"})
            except HTTPError as error:
                assert error.code == 403
            else:
                raise RuntimeError("old browser token remained valid after project restart")
            html = get(base + "/").decode("utf-8")
            new_token = re.search(r'<meta name="editor-token" content="([^"]+)">', html).group(1)
            post(base, new_token, "/api/preferences", {"mode": "developer"})
            location = json.loads(get(base + "/api/primary-create-info"))
            assert location["operation"] == "Row" and location["line"] > 0
        finally:
            try:
                if new_token:
                    stop_restarted_editor(base, new_token)
            finally:
                stop_editor(process, base, token)
        for _ in range(60):
            try:
                get(base + "/api/meta")
            except (URLError, TimeoutError):
                break
            time.sleep(.25)

        project = root / "full-project"
        source_fixture(project)
        game = project / "LEKMOD/Override/CIV5Units_Mongol.xml"
        before = hashlib.sha256(game.read_bytes()).digest()
        # Existing v0.22/v0.23 projects must migrate through the frozen generator,
        # without touching canonical English, translations or local drafts.
        head, marker, fallback = game.read_bytes().partition(b'<!-- BEGIN GENERATED FALLBACK -->')
        rejected_xml = (head + marker + fallback).replace(b'<Text>&#160;</Text>', b'<Text></Text>')
        rejected_xml = rejected_xml.replace('<Text>\u00a0</Text>'.encode('utf-8'), b'<Text></Text>')
        game.write_bytes(rejected_xml)
        settings = root / "localization/workspace/editor-settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"project_path": str(project), "onboarded": True}),
                            encoding="utf-8")
        process, base, token = start_editor(root, port, root / "connected-launch.log")
        try:
            assert b"function renderTable" in get(base + "/app.js")
            meta = json.loads(get(base + "/api/meta"))
            expected_version = json.loads((root / "localization/editor/version.json").read_text(
                encoding="utf-8"))["version"]
            assert meta["ready"] and meta["editor_version"] == expected_version
            assert not meta["vanilla_counts"]
            post(base, token, "/api/preferences", {"mode": "developer"})
            checks = post(base, token, "/api/check", {})["summary"]
            assert "Skipped outside a Git checkout" in checks, checks
            with zipfile.ZipFile(io.BytesIO(get(base + "/api/export-english"))) as handoff:
                assert "localization/en_US/primary.xml" in handoff.namelist()
                assert not any("snapshot" in name for name in handoff.namelist())
            primary = json.loads(get(base + "/api/primary?offset=0"))["rows"]
            assert primary[0]["source_file"] == "localization/en_US/primary.xml"
            assert primary[0]["source_line"] > 0
            dates = json.loads(gzip.decompress((project /
                "localization/reference/english-edit-dates.json.gz").read_bytes()))
            from lekmod_localization.english_dates import source_hash
            assert dates["source_sha256"] == source_hash(
                project / "localization/en_US/primary.xml")
            assert primary and primary[0]["english_edited_at"]
            post(base, token, "/api/preferences", {"mode": "translator"})
            for category in meta["locales"]["RU_RU"]:
                rows = json.loads(get(base + "/api/rows?" + urlencode({
                    "locale": "RU_RU", "category": category, "offset": 0})))
                row = next((row for row in rows["rows"]
                            if row["required_format_tokens"] == "{}" and
                            row["vanilla_en_US_status"] == "unavailable"), None)
                if row:
                    break
            if row is None:
                raise RuntimeError("no eligible row in connected project")
            csv_path = project / 'localization/translations/RU_RU.csv'
            original_csv = csv_path.read_bytes()
            data = {'mode': 'translator', 'locale': 'RU_RU', 'key': row['key'],
                    'slot': row['draft_slot'], 'revision': row['draft_revision'], 'base': row['draft_base'],
                    'source_fingerprint': row['source_fingerprint'], 'group': 'smoke-typing',
                    'edit': {'text': 'Portable local draft', 'gender': '', 'plurality': '',
                             'note': 'Local contributor work', 'identifier': ''}}
            started = time.monotonic()
            for number in range(20):
                data['edit']['text'] = f'Portable local draft {number}'
                result = post(base, token, '/api/draft', data)
                assert not result.get('conflict'), result
                data['revision'] = result['entry']['revision']
            duration = time.monotonic() - started
            assert duration < 10, f'20 local saves took {duration:.1f}s; must not rebuild XML'
            assert hashlib.sha256(game.read_bytes()).digest() == before
            assert csv_path.read_bytes() == original_csv
            assert post(base, token, '/api/draft-undo', {})['draft_redo_available']
            assert not json.loads(get(base + '/api/drafts'))['entries']
            post(base, token, '/api/draft-redo', {})
            with zipfile.ZipFile(io.BytesIO(get(base + '/api/export?locales=RU_RU'))) as package:
                assert b'Portable local draft 19' in package.read('translations/RU_RU.csv')
                assert 'translations/DE_DE.csv' not in package.namelist()

            def apply_pending(*, installed=False, target=None):
                post(base, token, '/api/apply-project', {'target': target} if target else {'game': installed})
                for _ in range(600):
                    state = json.loads(get(base + '/api/apply-status'))
                    if state['state'] != 'running': break
                    time.sleep(.1)
                assert state['state'] == 'complete', state
                return state

            assert apply_pending()['applied_count'] == 1
            assert hashlib.sha256(game.read_bytes()).digest() != before
            assert b'Portable local draft 19' in csv_path.read_bytes()
            # Test the actual frozen generator's English-first fingerprints,
            # rather than substituting a hand-written fingerprint in a unit test.
            post(base, token, '/api/preferences', {'mode': 'developer'})
            english = next(item for item in json.loads(get(base + '/api/primary?' + urlencode(
                {'q': row['key'], 'limit': 'all'})))['rows'] if item['key'] == row['key'])
            post(base, token, '/api/draft', {'mode': 'developer', 'locale': '', 'key': english['key'],
                'index': english['index'], 'slot': english['draft_slot'], 'revision': english['draft_revision'],
                'base': english['draft_base'], 'edit': {'text': english['text'] + ' [ICON_CULTURE]',
                    'identifier': english['key'], 'gender': '', 'plurality': '', 'note': ''}})
            post(base, token, '/api/preferences', {'mode': 'translator'})
            fresh = next(item for item in json.loads(get(base + '/api/rows?' + urlencode({
                'locale': 'RU_RU', 'category': category, 'q': row['key']})))['rows'] if item['key'] == row['key'])
            assert '[ICON_CULTURE]' in fresh['lekmod_en_US']
            post(base, token, '/api/draft', {**data, 'revision': fresh['draft_revision'],
                'base': fresh['draft_base'], 'source_fingerprint': fresh['source_fingerprint'],
                'edit': {'text': 'Updated translation [ICON_CULTURE]', 'gender': '', 'plurality': '',
                         'note': '', 'identifier': ''}})
            assert apply_pending()['applied_count'] == 2
            assert b'Updated translation [ICON_CULTURE]' in csv_path.read_bytes()
            # Apply the reported menu translations to an installed DLC that
            # still contains the rejected pre-fix XML. Validate the actual
            # frozen EXE's file output, not just the Python renderer in tests.
            from lekmod_localization.runtime_xml import validate_runtime_xml, LOCALES
            sys.path.insert(0, str(REPOSITORY / 'localization/tools/tests'))
            from test_runtime_xml import KEYS, RUSSIAN
            for key, text in zip(KEYS[2:], RUSSIAN):
                menu_row = next(item for item in json.loads(get(base + '/api/rows?' + urlencode({
                    'locale': 'RU_RU', 'category': 'all', 'q': key})))['rows'] if item['key'] == key)
                post(base, token, '/api/draft', {'mode': 'translator', 'locale': 'RU_RU', 'key': key,
                    'slot': menu_row['draft_slot'], 'revision': menu_row['draft_revision'],
                    'base': menu_row['draft_base'], 'source_fingerprint': menu_row['source_fingerprint'],
                    'edit': {'text': text, 'gender': '', 'plurality': '', 'note': '', 'identifier': ''}})
            client = root / 'civilization-v'
            client.mkdir()
            (client / 'CivilizationV.exe').touch()
            installed = client / 'Assets/DLC/LEKMOD_v35.3'
            target = installed / 'Override/CIV5Units_Mongol.xml'
            target.parent.mkdir(parents=True)
            target.write_bytes(rejected_xml)
            shutil.copy2(project / 'LEKMOD/VERSION', installed / 'VERSION')
            connection = post(base, token, '/api/connect', {'project_path': str(project), 'game_path': str(client)})
            assert not connection['restart']
            applied = apply_pending(installed=True)
            assert applied['applied_count'] == 5
            assert set(applied['game_result']['validated_locales']) == set(LOCALES)
            assert Path(applied['game_result']['backup']).read_bytes() == rejected_xml
            menu = validate_runtime_xml(target.read_text(encoding='utf-8'), keys=KEYS)['texts']
            assert [menu['RU_RU'][key] for key in KEYS[2:]] == list(RUSSIAN)
            for locale in LOCALES:
                assert menu[locale][KEYS[0]] == 'DISCORD' and menu[locale][KEYS[1]] == 'GITHUB'
            assert '[COLOR_POSITIVE_TEXT]' in menu['RU_RU'][KEYS[-1]]
            assert not apply_pending(installed=True)['game_result']['changed']
            # Exercise independent destinations and deletion with the real frozen EXE.
            checking = KEYS[2]
            def menu_entry():
                return next(item for item in json.loads(get(base + '/api/rows?' + urlencode({
                    'locale': 'RU_RU', 'category': 'all', 'q': checking})))['rows'] if item['key'] == checking)
            def change_menu(text):
                item = menu_entry()
                return post(base, token, '/api/draft', {'mode': 'translator', 'locale': 'RU_RU',
                    'key': checking, 'slot': item['draft_slot'], 'revision': item['draft_revision'],
                    'base': item['draft_base'], 'source_fingerprint': item['source_fingerprint'],
                    'edit': {'text': text, 'gender': '', 'plurality': '', 'note': '', 'identifier': ''}})
            # A benign renamed command interpreter proves the frozen process check.
            if os.name == 'nt':
                fake_folder = root / 'process-check'
                fake_folder.mkdir()
                fake_game = fake_folder / 'CivilizationV.exe'
                shutil.copy2(Path(os.environ['SystemRoot']) / 'System32/cmd.exe', fake_game)
                fake_process = subprocess.Popen([str(fake_game), '/d', '/q', '/k'],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    for _ in range(30):
                        if json.loads(get(base + '/api/game-process'))['processes']:
                            break
                        time.sleep(.1)
                    assert json.loads(get(base + '/api/game-process'))['processes']
                    before_blocked = target.read_bytes(), game.read_bytes(), csv_path.read_bytes()
                    for destination in ('game', 'all'):
                        try:
                            post(base, token, '/api/apply-project', {'target': destination})
                            raise AssertionError('Apply accepted an open game')
                        except HTTPError as error:
                            detail = json.loads(error.read())
                            assert detail['error_code'] == 'game_running', detail
                    assert before_blocked == (target.read_bytes(), game.read_bytes(), csv_path.read_bytes())
                finally:
                    fake_process.communicate(b'exit\n', timeout=10)
                assert not json.loads(get(base + '/api/game-process'))['processes']
            post(base, token, '/api/draft-checkpoint', {})
            original_project = {path: path.read_bytes() for path in (game, csv_path, project / 'localization/en_US/primary.xml')}
            change_menu(RUSSIAN[0] + ' Test')
            apply_pending(target='game')
            assert {path: path.read_bytes() for path in original_project} == original_project
            item = menu_entry()
            assert item['synced_to'] == {'project': False, 'game': True}, item
            assert item['has_local_draft'] and item['translation_status'] == 'draft'
            installed_before = target.read_bytes()
            apply_pending(target='project')
            assert target.read_bytes() == installed_before, 'Project-only Apply modified installed game'
            assert menu_entry()['synced_to'] == {'project': True, 'game': True}
            change_menu('')
            apply_pending(target='game')
            item = menu_entry()
            assert item['translation'] == '' and item['synced_to'] == {'project': False, 'game': True}, item
            apply_pending(target='project')
            item = menu_entry()
            assert item['translation'] == '' and item['translation_status'] == 'missing', item
            assert item['synced_to'] == {'project': True, 'game': True}
            post(base, token, '/api/draft-restore', {})
            assert menu_entry()['translation'] == RUSSIAN[0], 'Save was lost across Apply/deletion'
            apply_pending(target='all')
            # Every non-English locale can delete a translation without reviving CSV content.
            english_checking = menu_entry()['lekmod_en_US']
            for locale in LOCALES:
                if locale.casefold() == 'en_us' or locale == 'RU_RU':
                    continue
                item = next(item for item in json.loads(get(base + '/api/rows?' + urlencode({
                    'locale': locale, 'category': 'all', 'q': checking})))['rows'] if item['key'] == checking)
                post(base, token, '/api/draft', {'mode': 'translator', 'locale': locale, 'key': checking,
                    'slot': item['draft_slot'], 'revision': item['draft_revision'], 'base': item['draft_base'],
                    'source_fingerprint': item['source_fingerprint'],
                    'edit': {'text': english_checking + ' Test', 'gender': '', 'plurality': '', 'note': '', 'identifier': ''}})
            apply_pending(target='all')
            for locale in LOCALES:
                if locale.casefold() == 'en_us' or locale == 'RU_RU':
                    continue
                item = next(item for item in json.loads(get(base + '/api/rows?' + urlencode({
                    'locale': locale, 'category': 'all', 'q': checking})))['rows'] if item['key'] == checking)
                post(base, token, '/api/draft', {'mode': 'translator', 'locale': locale, 'key': checking,
                    'slot': item['draft_slot'], 'revision': item['draft_revision'], 'base': item['draft_base'],
                    'source_fingerprint': item['source_fingerprint'],
                    'edit': {'text': '', 'gender': '', 'plurality': '', 'note': '', 'identifier': ''}})
            apply_pending(target='all')
            for locale in LOCALES:
                if locale.casefold() != 'en_us' and locale != 'RU_RU':
                    item = next(item for item in json.loads(get(base + '/api/rows?' + urlencode({
                        'locale': locale, 'category': 'all', 'q': checking})))['rows'] if item['key'] == checking)
                    assert item['translation'] == '' and item['translation_status'] == 'missing', item
            # Apply 292 real HTTP drafts: the reported three translations,
            # extra icons, and one genuinely incomplete menu translation.
            # Only the bad row stays local; the actual installed XML is checked.
            samples = {
                'TXT_KEY_UA_AKKAD_PEDIA': '[COLOR_XP_BLUE] (https://en.wikipedia.org/wiki/Akkadian_Empire)[ENDCOLOR]Аккадская империя была первой известной империей.',
                'TXT_KEY_UNIT_LITE_AKKAD_ONAGER_WAGON_HELP': 'Уникальный юнит Аккада. Не тратит [ICON_MOVES] Очки передвижения на разграбление клеток.',
                'TXT_KEY_BUILDING_COFFEE_HOUSE_HELP': 'Заменяет Мельницу. Требует меньше [ICON_PRODUCTION] Производства. Увеличивает на 20% скорость возникновения [ICON_GREAT_PEOPLE] Великих людей.',
            }
            all_rows = json.loads(get(base + '/api/rows?' + urlencode({
                'locale': 'RU_RU', 'category': 'all', 'limit': 'all'})))['rows']
            by_key = {item['key']: item for item in all_rows}
            eligible = [item for item in all_rows if item['required_format_tokens'] == '{}' and
                        item['classification'] != 'source_conflict' and item['source_fingerprint'] and
                        item['key'] not in {*samples, *KEYS} and not item.get('has_local_draft')][:288]
            assert len(eligible) == 288, 'fixture needs 288 independent source rows'
            samples.update({item['key']: 'Пакетный перевод ' + item['key'] + ' [ICON_PRODUCTION]' for item in eligible})
            before_save = post(base, token, '/api/draft-checkpoint', {})['save_id']
            for key, text in samples.items():
                item = by_key[key]
                post(base, token, '/api/draft', {'mode': 'translator', 'locale': 'RU_RU', 'key': key,
                    'slot': item['draft_slot'], 'revision': item['draft_revision'], 'base': item['draft_base'],
                    'source_fingerprint': item['source_fingerprint'],
                    'edit': {'text': text, 'gender': '', 'plurality': '', 'note': '', 'identifier': ''}})
            change_menu('Incomplete without version or color')
            assert json.loads(get(base + '/api/meta'))['draft_count'] == 292
            latest_save = post(base, token, '/api/draft-checkpoint', {})
            partial = apply_pending(target='all')
            assert partial['applied_count'] == 291 and partial['apply_issue_count'] == 1, partial
            assert partial['draft_count'] == 1, partial
            rejected = json.loads(get(base + '/api/apply-issues'))['issues']
            assert len(rejected) == 1 and rejected[0]['key'] == checking, rejected
            assert '{1_Version}' in rejected[0]['reason'], rejected
            runtime = validate_runtime_xml(target.read_text(encoding='utf-8'), keys=tuple(samples))['texts']['RU_RU']
            for key, text in samples.items():
                assert runtime[key] == text, key
            assert menu_entry()['synced_to'] == {'project': False, 'game': False}
            # A rejected row keeps its last Game-only value, even when Project
            # has a different baseline. Retry can copy valid Project changes
            # while the only remaining local draft is rejected.
            last_game_menu = RUSSIAN[0] + ' Last game-only version'
            change_menu(last_game_menu)
            apply_pending(target='game')
            change_menu('Incomplete without version or color again')
            coffee = 'TXT_KEY_BUILDING_COFFEE_HOUSE_HELP'
            coffee_row = next(item for item in json.loads(get(base + '/api/rows?' + urlencode({
                'locale': 'RU_RU', 'category': 'all', 'q': coffee})))['rows'] if item['key'] == coffee)
            coffee_text = samples[coffee] + ' Updated in project'
            post(base, token, '/api/draft', {'mode': 'translator', 'locale': 'RU_RU', 'key': coffee,
                'slot': coffee_row['draft_slot'], 'revision': coffee_row['draft_revision'], 'base': coffee_row['draft_base'],
                'source_fingerprint': coffee_row['source_fingerprint'],
                'edit': {'text': coffee_text, 'gender': '', 'plurality': '', 'note': '', 'identifier': ''}})
            project_partial = apply_pending(target='project')
            assert project_partial['applied_count'] == 1 and project_partial['draft_count'] == 1, project_partial
            retry_game = apply_pending(target='all')
            assert retry_game['applied_count'] == 0 and retry_game['apply_issue_count'] == 1, retry_game
            retained = validate_runtime_xml(target.read_text(encoding='utf-8'), keys=(checking, coffee))['texts']['RU_RU']
            assert retained[checking] == last_game_menu and retained[coffee] == coffee_text, retained
            change_menu(RUSSIAN[0] + ' Corrected')
            assert json.loads(get(base + '/api/apply-issues'))['issues'][0]['needs_recheck']
            corrected = apply_pending(target='all')
            assert corrected['applied_count'] == 1 and corrected['apply_issue_count'] == 0, corrected
            source_csv_before = csv_path.read_bytes()
            installed_before = target.read_bytes()
            loaded = post(base, token, '/api/draft-load', {'save_id': before_save})
            assert loaded['history_count'] == 0
            assert loaded['checkpoint_saved_at'] == latest_save['checkpoint_saved_at']
            assert csv_path.read_bytes() == source_csv_before and target.read_bytes() == installed_before
            saves = json.loads(get(base + '/api/draft-saves'))['saves']
            assert saves[0]['id'] == latest_save['save_id']
            assert any(saved['id'] == before_save for saved in saves)
            apply_pending(target='all')
            assert menu_entry()['translation'] == RUSSIAN[0]
            print('Frozen 292-draft partial Apply: 291 valid rows installed, one missing token retained, Troubleshoot fixed/retried, historical Save loaded locally.')
            # A newly created entity can go to game before project, and can be undone after Apply.
            post(base, token, '/api/preferences', {'mode': 'developer'})
            new_key = 'TXT_KEY_LLE_PORTABLE_CREATED'
            source_path = project / 'localization/en_US/primary.xml'
            source_before = source_path.read_bytes()
            post(base, token, '/api/draft', {'mode': 'developer', 'locale': '', 'key': new_key,
                'index': -1, 'slot': 'N:' + new_key, 'revision': 0, 'base': {}, 'create': True,
                'edit': {'identifier': new_key, 'text': 'Portable new entity', 'gender': '', 'plurality': '', 'note': ''}})
            apply_pending(target='game')
            assert source_path.read_bytes() == source_before
            created = json.loads(get(base + '/api/primary?' + urlencode({'q': new_key})))['rows'][0]
            assert created['entity_status'] == 'draft' and created['synced_to'] == {'project': False, 'game': True}, created
            installed_before = target.read_bytes()
            apply_pending(target='project')
            assert target.read_bytes() == installed_before
            created = json.loads(get(base + '/api/primary?' + urlencode({'q': new_key})))['rows'][0]
            assert created['draft_slot'] == 'N:' + new_key
            assert created['synced_to'] == {'project': True, 'game': True}, created
            post(base, token, '/api/draft-undo', {})
            apply_pending(target='all')
            assert new_key.encode() not in source_path.read_bytes()
            post(base, token, '/api/draft-redo', {})
            apply_pending(target='all')
            assert new_key.encode() in source_path.read_bytes()
            print('Frozen Save/restore, independent Project/Game Apply, deletion in all nine target locales and new entity Undo/Redo passed.')
            diagnostics = json.loads(get(base + '/api/game-diagnostics'))
            assert diagnostics['xml']['same_file']
            assert set(diagnostics['xml']['installed']['counts']) == set(LOCALES)
            assert 'schema_version' in diagnostics and 'databases' in diagnostics
            logs = json.loads(get(base + "/api/logs"))["events"]
            assert any(event["action"] == "draft-apply" for event in logs)
            print(f'Portable editor: 20 local saves in {duration:.2f}s; project unchanged before Apply.')
            print('Frozen batch Apply, English-first translation, local undo/redo and draft export passed.')
            print('Frozen migration and installed-game Apply: five Russian menu texts, ten locales, links/colors, backup and read-only diagnostics passed.')
        except Exception:
            print((root / "connected-launch.log").read_text(
                encoding="utf-8", errors="replace")[-8000:])
            raise
        finally:
            stop_editor(process, base, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
