"""Upgrade the previous released GUI EXE through its actual browser API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import zipfile

from test_windows_updater import start, stop, token_at, wait_for
from test_portable_editor import source_fixture, REPOSITORY, get
from merge_localization import candidate_sources
from merge_translation_handoff import encoded_records
from lekmod_localization.drafts import DraftStore


def main() -> int:
    """Check GitHub discovery, download, same-tab restart, and preserved state."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-archive", type=Path, required=True)
    parser.add_argument("--new-archive", type=Path, required=True)
    parser.add_argument("--test-repair", action="store_true")
    parser.add_argument('--connected-project', action='store_true')
    parser.add_argument('--test-reference-migration', action='store_true')
    parser.add_argument('--test-extra-instance', action='store_true')
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("run the online upgrade test on Windows")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory) / "Translator Folder With Spaces"
        with zipfile.ZipFile(args.old_archive) as archive:
            archive.extractall(root)
        with zipfile.ZipFile(args.new_archive) as archive:
            version = json.loads(archive.read("localization/editor/version.json"))["version"]
            expected_exe = archive.read("LekmodLocalizationEditor.exe")
            expected_ui = {name: archive.read(name) for name in (
                "localization/editor/index.html", "localization/editor/app.js",
                "localization/editor/version.json")}
        preferences = {"project_path": "C:/Lekmod source", "mode": "translator",
                       "column_widths": {"key": 515}, "snapshot_url": "https://example.invalid",
                       'translator_column_order': ['translation', 'key', 'lekmod_en_US']}
        connected_files = {}
        previous_reference = None
        if args.connected_project:
            project = root / 'Connected project with spaces'
            source_fixture(project)
            primary = project / 'localization/en_US/primary.xml'
            sources = candidate_sources(project, primary.read_text(encoding='utf-8'))
            key = 'TXT_KEY_BUILDING_ALCAZABA_PEDIA'
            approved = project / 'localization/translations/RU_RU.csv'
            approved.write_bytes(encoded_records({key: {'key': key,
                'source_fingerprint': sources[key]['source_fingerprint'],
                'text': 'Saved contributor translation', 'gender': '', 'plurality': '',
                'translator_note': 'Keep this work', 'updated_at': '2026-10-05T00:00:00Z'}}))
            connected_files = {path: path.read_bytes() for path in (primary, approved)}
            preferences['project_path'] = str(project)
            if args.test_reference_migration:
                # Use the immutable reference from the user's prior editor release,
                # not a new fixture that accidentally skips the upgrade scenario.
                with urlopen('https://raw.githubusercontent.com/Nail-Al/Lekmod/editor-v0.17/'
                             'localization/reference/vanilla-fingerprints.json.gz', timeout=45) as response:
                    previous_reference = response.read(2 * 1024 * 1024)
                assert hashlib.sha256(previous_reference).hexdigest() == (
                    '5b10d7361ea52b8c6613d6679a54c9d1174c3a30abad86c1485cbdf908a0bd17')
                (project / 'localization/reference/vanilla-fingerprints.json.gz').write_bytes(previous_reference)
        workspace = root / "localization/workspace"
        workspace.mkdir(parents=True)
        draft_path = (project / 'localization/workspace/editor-drafts.sqlite3'
                      if args.connected_project else workspace / 'editor-drafts.sqlite3')
        DraftStore(draft_path).put('T:DE_DE:TXT_KEY_BUILDING_ALCAZABA_PEDIA', {
            'mode': 'translator', 'locale': 'DE_DE', 'key': 'TXT_KEY_BUILDING_ALCAZABA_PEDIA',
            'index': -1, 'create': False, 'base': {'approval': None}, 'source_fingerprint': 'a' * 64,
            'edit': {'text': 'Unapplied local translation; keep across update and repair',
                     'gender': '', 'plurality': '', 'note': 'Local-only work', 'identifier': ''}}, 0)
        draft_snapshot = DraftStore(draft_path).entries()
        DraftStore(draft_path).save_checkpoint()
        checkpoint_time = DraftStore(draft_path).status()['checkpoint_saved_at']
        (workspace / "editor-settings.json").write_text(json.dumps(preferences), encoding="utf-8")
        (workspace / "vanilla-snapshot.json.gz").write_bytes(b"private snapshot fixture")
        translation = root / "localization/translations/RU_RU.csv"
        translation.parent.mkdir(parents=True)
        translation.write_text("saved translation from the old editor", encoding="utf-8")
        english = root / "localization/en_US/primary.xml"
        english.parent.mkdir(parents=True)
        english.write_text("saved English source", encoding="utf-8")
        old, base, _ = start(root)
        previous = json.loads((root / "localization/editor/version.json").read_text())["version"]
        wait_for(base, previous)
        for _ in range(40):
            with urlopen(base + "/api/editor-latest", timeout=15) as response:
                latest = json.load(response)
            if latest["latest"] == version and latest["available"]:
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"GitHub did not publish editor v{version} in time: {latest}")
        extra = None
        other = None
        if args.test_extra_instance:
            # Old released editors allowed multiple EXE copies from one folder.
            # The downloaded helper must close the idle duplicate automatically,
            # keeping the session that requested the update until handoff is ready.
            extra, extra_base, _ = start(root)
            assert extra_base != base, 'Fixture needs the released multi-instance editor'
            wait_for(extra_base, previous)
            # An editor copied into another installation belongs to that project,
            # even when its EXE filename/version is identical.
            other_root = Path(directory) / 'Independent editor installation'
            with zipfile.ZipFile(args.old_archive) as archive:
                archive.extractall(other_root)
            other, other_base, _ = start(other_root)
            other_instance = wait_for(other_base, previous)['server_instance']
        request = Request(base + "/api/editor-update", data=b"{}", headers={
            "Origin": base, "X-Editor-Token": token_at(base),
            "Content-Type": "application/json"})
        with urlopen(request, timeout=15) as response:
            assert response.status == 202
        for _ in range(720):
            try:
                with urlopen(base + "/api/meta", timeout=2) as response:
                    metadata = json.load(response)
                if metadata["editor_version"] == version and not metadata.get('initializing'):
                    break
                notice = metadata.get("update_notice")
                if notice and notice["result"] == "failure":
                    raise RuntimeError("The old editor reopened after a failed update: " +
                                       (workspace / "editor-updates/update.log").read_text()[-2000:])
                with urlopen(base + "/api/editor-update-status", timeout=2) as response:
                    status = json.load(response)
                if status["state"] == "error":
                    raise RuntimeError("The update button failed: " + status["error"])
            except (URLError, TimeoutError, ConnectionError):
                pass  # Expected while the old app releases its localhost port.
            time.sleep(.25)
        else:
            raise RuntimeError("The updated EXE did not reopen the original browser address")
        if extra is not None:
            assert extra.wait(timeout=15) == 0, 'Legacy duplicate was not closed cleanly'
            assert wait_for(other_base, previous)['server_instance'] == other_instance
            stop(other_base, token_at(other_base))
            assert other.wait(timeout=15) == 0
            print('Legacy duplicate closed automatically; the requesting session updated on its original URL.')
            print('Independent editor installation remained running and unchanged.')
        assert metadata["preferences"]["project_path"] == preferences['project_path']
        if args.connected_project:
            assert metadata['ready'], metadata.get('connection_error')
            for path, content in connected_files.items():
                assert path.read_bytes() == content, f'updater modified {path}'
            if previous_reference is not None:
                reference = project / 'localization/reference/vanilla-fingerprints.json.gz'
                assert reference.read_bytes() == (REPOSITORY /
                    'localization/reference/vanilla-fingerprints.json.gz').read_bytes()
                backup = project / 'localization/workspace/reference-backups' / (
                    hashlib.sha256(previous_reference).hexdigest() + '.json.gz')
                assert backup.read_bytes() == previous_reference
                assert any(event['action'] == 'vanilla-reference-migration'
                           for event in json.loads(get(base + '/api/logs'))['events'])
        assert metadata["preferences"]["column_widths"] == {"key": 515}
        assert metadata['preferences']['translator_column_order'] == ['translation', 'key', 'lekmod_en_US']
        assert DraftStore(draft_path).entries() == draft_snapshot, 'update/migration changed unapplied local drafts'
        assert DraftStore(draft_path).status()['checkpoint_saved_at'] == checkpoint_time, 'update lost explicit Save'
        if args.connected_project:
            assert metadata['draft_count'] == 1
        assert (root / "LekmodLocalizationEditor.exe").read_bytes() == expected_exe
        for name, expected in expected_ui.items():
            assert (root / name).read_bytes() == expected, f"editor kept an old {name}"
        assert (workspace / "vanilla-snapshot.json.gz").read_bytes() == b"private snapshot fixture"
        assert translation.read_text(encoding="utf-8") == "saved translation from the old editor"
        assert english.read_text(encoding="utf-8") == "saved English source"
        # The new server may answer /api/meta before the helper records success.
        log = workspace / "editor-updates/update.log"
        for _ in range(40):
            details = log.read_text(encoding="utf-8") if log.exists() else ""
            if f"success  Installed v{version}" in details:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("The helper reopened the editor without confirming the update: "
                               + details[-2000:])
        if args.test_repair:
            old_instance = metadata["server_instance"]
            readme = root / "README-START.txt"
            readme.write_text("damaged", encoding="utf-8")
            request = Request(base + "/api/editor-verify", data=b"{}", headers={
                "Origin": base, "X-Editor-Token": token_at(base),
                "Content-Type": "application/json"})
            with urlopen(request, timeout=15) as response:
                assert response.status == 202
            for _ in range(900):
                with urlopen(base + "/api/editor-verify-status", timeout=15) as response:
                    integrity = json.load(response)
                if integrity["state"] == "complete":
                    break
                if integrity["state"] == "error":
                    raise RuntimeError("Editor file verification failed: " + integrity["error"])
                time.sleep(.2)
            else:
                raise RuntimeError("Editor file verification did not finish")
            assert integrity["damaged_files"] == ["README-START.txt"]
            assert not integrity["available"] and integrity["current"] == version
            request = Request(base + "/api/editor-update", data=b'{"repair":true}', headers={
                "Origin": base, "X-Editor-Token": token_at(base),
                "Content-Type": "application/json"})
            with urlopen(request, timeout=15) as response:
                assert response.status == 202
            for _ in range(720):
                try:
                    with urlopen(base + "/api/health", timeout=3) as response:
                        health = json.load(response)
                    if health["server_instance"] != old_instance:
                        break
                except (URLError, TimeoutError, ConnectionError):
                    pass
                time.sleep(.25)
            else:
                raise RuntimeError("Same-version repair did not reopen the editor")
            # Health is deliberately available before a large connected project
            # finishes preparing. Verify preserved inputs after that work too.
            repaired = wait_for(base, version, timeout=150)
            if args.connected_project:
                assert repaired['ready'], repaired.get('connection_error')
            with zipfile.ZipFile(args.new_archive) as archive:
                assert readme.read_bytes() == archive.read("README-START.txt")
            assert translation.read_text(encoding="utf-8") == "saved translation from the old editor"
            assert english.read_text(encoding="utf-8") == "saved English source"
            assert (workspace / "vanilla-snapshot.json.gz").read_bytes() == b"private snapshot fixture"
            assert json.loads((workspace / "editor-settings.json").read_text()) == preferences
            for path, content in connected_files.items():
                assert path.read_bytes() == content, f'repair modified {path}'
            for _ in range(40):
                if details.count(f"success  Installed v{version}") >= 2:
                    break
                time.sleep(.25)
                details = log.read_text(encoding="utf-8")
            else:
                raise RuntimeError("Repair restarted without confirming all editor files: " + details[-2000:])
        stop(base, token_at(base))
        old.wait(timeout=20)
    print(f"Editor v{previous} downloaded v{version} from GitHub and reopened the same tab.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
