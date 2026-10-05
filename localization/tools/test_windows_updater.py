"""Exercise the actual GUI EXE helper, its localhost handoff, and rollback."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import uuid
from urllib.error import URLError
from urllib.request import Request, urlopen
import zipfile

from lekmod_localization.editor_update import UPDATE_FILES, installer_command


def wait_for(url: str, version: str, timeout: int = 75) -> dict:
    """Wait for the editor to serve its real version on the same browser URL."""
    for _ in range(timeout * 4):
        try:
            with urlopen(url + "/api/meta", timeout=2) as response:
                metadata = json.load(response)
            if metadata["editor_version"] == version and not metadata.get('initializing'):
                return metadata
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(.25)
    raise RuntimeError(f"Editor v{version} did not answer at {url}")


def token_at(base: str) -> str:
    """Get the browser's CSRF token for the currently running process."""
    with urlopen(base + "/", timeout=10) as response:
        html = response.read().decode("utf-8")
    return re.search(r'<meta name="editor-token" content="([^"]+)">', html).group(1)


def stop(base: str, token: str) -> None:
    """Ask the editor to close before the helper replaces its EXE."""
    request = Request(base + "/api/stop", data=b"{}", headers={
        "Origin": base, "X-Editor-Token": token, "Content-Type": "application/json"})
    with urlopen(request, timeout=10):
        pass


def start(root: Path) -> tuple[subprocess.Popen, str, str]:
    """Launch a windowless EXE and use its readiness file to find its port."""
    ticket = uuid.uuid4().hex
    process = subprocess.Popen([str(root / "LekmodLocalizationEditor.exe"),
                                "--no-browser", "--update-ticket", ticket], cwd=root,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    ready = root / "localization/workspace/editor-updates" / f"ready-{ticket}.json"
    for _ in range(120):
        if ready.is_file():
            signal = json.loads(ready.read_text(encoding="utf-8"))
            return process, f"http://127.0.0.1:{signal['port']}", signal["pid"]
        if process.poll() is not None:
            raise RuntimeError("packaged editor exited before opening its server")
        time.sleep(.25)
    raise RuntimeError("packaged editor did not open within 30 seconds")


@contextmanager
def locked_file(path: Path):
    """Hold a real Windows handle allowing reads but denying file replacement."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    # FILE_SHARE_READ | FILE_SHARE_WRITE deliberately omits FILE_SHARE_DELETE.
    handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


def test_locked_updates(archive_path: Path) -> None:
    """Reproduce the EXE bottleneck and partial-install rollback on every release."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        for locked in ('LekmodLocalizationEditor.exe', 'localization/editor/index.html'):
            root = Path(directory) / Path(locked).name
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(root)
                manifest = json.loads(archive.read('localization/editor/version.json'))
                current = manifest['version']
                stage = root / 'localization/workspace/editor-updates' / ('editor-v' + current)
                archive.extractall(stage)
            major, minor = map(int, current.split('.'))
            previous = f'{major}.{minor - 1}'
            manifest.update(version=previous, release_tag='editor-v' + previous)
            (root / 'localization/editor/version.json').write_text(json.dumps(manifest), encoding='utf-8')
            private = {
                'localization/workspace/editor-settings.json': json.dumps({
                    'locale': 'DE_DE', 'column_widths': {'translation': 740},
                    'translator_filters': {'status': 'missing', 'date_field': 'translation_updated_at'},
                    'developer_filters': {'kind': 'Replace', 'date_field': 'english_edited_at'}}).encode(),
                'localization/workspace/vanilla-snapshot.json.gz': b'private reference',
                'localization/translations/DE_DE.csv': 'saved translation'.encode(),
                'localization/en_US/primary.xml': b'saved English source',
            }
            for name, content in private.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            before = {name: (root / name).read_bytes() for name in UPDATE_FILES}
            old, base, pid = start(root)
            original = wait_for(base, previous)
            port = int(base.rsplit(':', 1)[1])
            with locked_file(root / locked):
                started = time.monotonic()
                helper = subprocess.Popen(installer_command(stage, root, pid, port, no_browser=True),
                    cwd=stage, env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'},
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                stop(base, token_at(base))
                # The original browser address must expose live progress while locked.
                progress = None
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    try:
                        with urlopen(base + '/api/editor-update-status', timeout=1) as response:
                            progress = json.load(response)
                        if progress.get('phase') == 'installing':
                            break
                    except (URLError, TimeoutError, ConnectionError):
                        pass
                    time.sleep(.1)
                assert progress and progress.get('phase') == 'installing', 'No live installation progress'
                assert helper.wait(timeout=90) != 0, 'Locked file unexpectedly replaced'
                assert time.monotonic() - started < 90, 'Update lock recovery exceeded its time limit'
                restored = wait_for(base, previous)
                assert restored['server_instance'] != original['server_instance']
            old.wait(timeout=30)
            for name, content in {**before, **private}.items():
                assert (root / name).read_bytes() == content, 'Failure changed ' + name
            events = [json.loads(line) for line in (stage.parent.parent / 'editor-actions.jsonl').read_text().splitlines()]
            failure = next(e for e in reversed(events) if e['result'] == 'failure')
            assert failure['recovery'] == ('unchanged' if locked.endswith('.exe') else 'restored')
            log = (stage.parent / 'update.log').read_text(encoding='utf-8')
            assert 'Windows denied replacing ' + Path(locked).name in log
            assert 'restoring ' + Path(locked).name + ' failed' not in log
            assert 'Previous editor reopened' in log
            stop(base, token_at(base))
            print('Windows file-lock regression passed:', locked, f'({time.monotonic() - started:.1f}s)')


def main() -> int:
    """Verify no console, same-tab restart, preserved work, and failed-start rollback."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument('--locks-only', action='store_true',
                        help='Test real Windows locks, bounded recovery, and private file preservation')
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("run this executable test on a Windows runner")
    if args.locks_only:
        test_locked_updates(args.archive)
        return 0
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory) / "Editor With Spaces"
        with zipfile.ZipFile(args.archive) as archive:
            assert set(archive.namelist()) == set(UPDATE_FILES)
            new_version = json.loads(archive.read("localization/editor/version.json"))["version"]
            major, minor = map(int, new_version.split("."))
            old_version, broken_version = f"{major}.{minor - 1}", f"{major}.{minor + 1}"
            stage = root / "localization/workspace/editor-updates" / ("editor-v" + new_version)
            archive.extractall(root)
            archive.extractall(stage)
        binary = (root / "LekmodLocalizationEditor.exe").read_bytes()
        pe = struct.unpack_from("<I", binary, 0x3c)[0]
        subsystem = struct.unpack_from("<H", binary, pe + 24 + 68)[0]
        assert subsystem == 2, "editor EXE is a Windows GUI application, not a console application"
        version = root / "localization/editor/version.json"
        version.write_text(json.dumps({"version": old_version, "release_tag": "editor-v" + old_version,
                                       "compatible_releases": ["v35.3"]}), encoding="utf-8")
        settings = root / "localization/workspace/editor-settings.json"
        preferences = {"project_path": "C:/Lekmod", "snapshot_url": "https://example.invalid",
                       "column_widths": {"key": 420}, "mode": "developer"}
        settings.write_text(json.dumps(preferences), encoding="utf-8")
        snapshot = settings.parent / "vanilla-snapshot.json.gz"
        snapshot.write_bytes(b"private snapshot fixture")
        project = settings.parent / "projects/v35.3/localization/translations/RU_RU.csv"
        project.parent.mkdir(parents=True)
        project.write_text("approved row", encoding="utf-8")
        translation = root / "localization/translations/RU_RU.csv"
        translation.parent.mkdir(parents=True)
        translation.write_text("translator's saved text", encoding="utf-8")
        primary = root / "localization/en_US/primary.xml"
        primary.parent.mkdir(parents=True)
        primary.write_text("developer's English text", encoding="utf-8")

        old, base, pid = start(root)
        wait_for(base, old_version)
        duplicate, duplicate_base, duplicate_pid = start(root)
        assert duplicate_base == base and duplicate_pid == pid, 'Double launch started another editor server'
        assert duplicate.wait(timeout=15) == 0, 'Duplicate launch did not reuse the running editor'
        helper = subprocess.Popen(installer_command(stage, root, pid, int(base.rsplit(":", 1)[1]),
                                                    no_browser=True), cwd=stage,
                                  env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        stop(base, token_at(base))
        assert helper.wait(timeout=210) == 0, (root /
            "localization/workspace/editor-updates/update.log").read_text(encoding="utf-8")
        old.wait(timeout=30)
        meta = wait_for(base, new_version)
        assert meta["preferences"]["column_widths"] == {"key": 420}
        assert meta["preferences"]["project_path"] == "C:/Lekmod"
        with zipfile.ZipFile(args.archive) as archive:
            assert (root / "LekmodLocalizationEditor.exe").read_bytes() == archive.read(
                "LekmodLocalizationEditor.exe")
        assert snapshot.read_bytes() == b"private snapshot fixture"
        assert project.read_text(encoding="utf-8") == "approved row"
        assert translation.read_text(encoding="utf-8") == "translator's saved text"
        assert primary.read_text(encoding="utf-8") == "developer's English text"
        backups = list((root / "localization/workspace/editor-updates").glob("previous-editor-*"))
        assert len(backups) == 1
        assert json.loads((backups[0] / "localization/editor/version.json").read_text())["version"] == old_version

        # Repair the same version after damage, preserving editor drafts and
        # project files that may share the portable installation folder.
        (root / "README-START.txt").write_text("damaged", encoding="utf-8")
        log_path = stage.parent / "update.log"
        current_pid = int(re.findall(r"Installed v" + re.escape(new_version) +
                                     r";[^\n]*pid: (\d+)", log_path.read_text())[-1])
        repaired = subprocess.Popen(installer_command(stage, root, current_pid,
                                   int(base.rsplit(":", 1)[1]), no_browser=True, repair=True),
                                   cwd=stage, env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        stop(base, token_at(base))
        assert repaired.wait(timeout=210) == 0, log_path.read_text(encoding="utf-8")
        assert wait_for(base, new_version)["server_instance"] != meta["server_instance"]
        assert (root / "README-START.txt").read_bytes() == (stage / "README-START.txt").read_bytes()
        assert translation.read_text(encoding="utf-8") == "translator's saved text"
        assert primary.read_text(encoding="utf-8") == "developer's English text"
        assert project.read_text(encoding="utf-8") == "approved row"
        assert snapshot.read_bytes() == b"private snapshot fixture"
        assert settings.read_text(encoding="utf-8") == json.dumps(preferences)

        # A downloaded copy whose page cannot load must fail after file replacement.
        # The helper must restore the original EXE and reopen the original URL.
        broken = stage.parent / ("editor-v" + broken_version)
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(broken)
        (broken / "localization/editor/version.json").write_text(json.dumps({
            "version": broken_version, "release_tag": "editor-v" + broken_version,
            "compatible_releases": ["v35.3"]}), encoding="utf-8")
        (broken / "localization/editor/index.html").write_text("broken page", encoding="utf-8")
        previous = (root / "LekmodLocalizationEditor.exe").read_bytes()
        success_log = (stage.parent / "update.log").read_text(encoding="utf-8")
        pid = int(re.findall(r"Installed v" + re.escape(new_version) +
                             r";[^\n]*pid: (\d+)", success_log)[-1])
        failed = subprocess.Popen(installer_command(broken, root, pid,
                                  int(base.rsplit(":", 1)[1]), no_browser=True), cwd=broken,
                                  env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        stop(base, token_at(base))
        assert failed.wait(timeout=210) != 0, "invalid UI unexpectedly passed readiness"
        wait_for(base, new_version)
        assert (root / "LekmodLocalizationEditor.exe").read_bytes() == previous
        assert (root / "localization/editor/index.html").read_bytes() == (stage /
            "localization/editor/index.html").read_bytes()
        assert settings.read_text(encoding="utf-8") == json.dumps(preferences)
        assert snapshot.read_bytes() == b"private snapshot fixture"
        assert project.read_text(encoding="utf-8") == "approved row"
        assert translation.read_text(encoding="utf-8") == "translator's saved text"
        assert primary.read_text(encoding="utf-8") == "developer's English text"
        log = (stage.parent / "update.log").read_text(encoding="utf-8")
        assert "success" in log and "failure" in log and "Previous editor reopened" in log
        assert "incomplete page or script" in log
        stop(base, token_at(base))
    print("Windowless updater restarted the same browser URL and restored a failed release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
