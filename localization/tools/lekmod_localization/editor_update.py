"""Download a verified release and let its GUI executable replace the old one."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Callable

from .connections import APP_HOME, editor_manifest


REPOSITORY = "Nail-Al/Lekmod"
RELEASES = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=30"
ARCHIVE = "LekmodLocalizationEditor-Windows.zip"
UPDATE_FILES = (
    "LekmodLocalizationEditor.exe", "README-START.txt", "localization/README.md",
    "localization/editor/index.html", "localization/editor/app.js",
    "localization/editor/version.json", "LekmodInstaller/github_setup/versions.json",
)
VERSION = re.compile(r"editor-v(\d+)\.(\d+)$")
Progress = Callable[[int, int | None], None]


def _download(url: str, limit: int, progress: Progress | None = None) -> bytes:
    """Read a bounded HTTPS release, reporting actual transferred bytes."""
    request = urllib.request.Request(url, headers={"User-Agent": "Lekmod-Localization-Editor"})
    with urllib.request.urlopen(request, timeout=45) as response:
        if not response.url.startswith("https://"):
            raise ValueError("editor release was redirected away from HTTPS")
        length = response.headers.get("Content-Length")
        total = int(length) if length and length.isdigit() else None
        if total is not None and total > limit:
            raise ValueError("editor release exceeds its size limit")
        data = io.BytesIO()
        while block := response.read(256 * 1024):
            data.write(block)
            if data.tell() > limit:
                raise ValueError("editor release exceeds its size limit")
            if progress:
                progress(data.tell(), total)
    return data.getvalue()


def latest_release(home: Path = APP_HOME) -> dict:
    """Discover the newest published editor release, excluding mod tags."""
    releases = json.loads(_download(RELEASES, 2 * 1024 * 1024))
    current = editor_manifest(home)["version"]
    candidates = []
    for release in releases:
        match = VERSION.fullmatch(str(release.get("tag_name", "")))
        if not match or release.get("draft") or release.get("prerelease"):
            continue
        asset = next((a for a in release.get("assets", []) if a.get("name") == ARCHIVE), None)
        if asset:
            candidates.append((tuple(map(int, match.groups())), release, asset))
    if not candidates:
        raise ValueError("no published Windows editor release was found")
    version, release, asset = max(candidates, key=lambda item: item[0])
    return {
        "current": current, "latest": ".".join(map(str, version)),
        "available": version > tuple(map(int, current.split("."))),
        "release_url": release["html_url"], "download_url": asset["browser_download_url"],
        "digest": asset.get("digest", ""),
        "can_auto_update": bool(getattr(sys, "frozen", False) and sys.platform == "win32"),
    }


def stage_release(release: dict, home: Path = APP_HOME,
                  progress: Progress | None = None) -> Path:
    """Verify GitHub's digest and allowlisted ZIP contents before staging."""
    if not release["available"] or not release["can_auto_update"]:
        raise ValueError("automatic update is available only for an older Windows EXE")
    tag = "editor-v" + release["latest"]
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{ARCHIVE}"
    if release["download_url"] != expected_url:
        raise ValueError("unexpected editor release download URL")
    digest = release["digest"]
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("editor release has no SHA-256 digest; ask a maintainer to republish it")
    data = _download(expected_url, 100 * 1024 * 1024, progress)
    if hashlib.sha256(data).hexdigest() != digest.split(":", 1)[1]:
        raise ValueError("editor download failed its SHA-256 check")
    folder = home / "localization/workspace/editor-updates"
    folder.mkdir(parents=True, exist_ok=True)
    stage = folder / tag
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        if len(names) != len(archive.infolist()):
            raise ValueError("release archive has duplicate file names")
        if names != set(UPDATE_FILES):
            raise ValueError("release archive has missing or unexpected editor files")
        for name in UPDATE_FILES:
            info = archive.getinfo(name)
            if info.file_size > 90 * 1024 * 1024 or not name.isascii():
                raise ValueError("unsafe editor file in release")
        if stage.is_symlink():
            raise ValueError("pending editor update path is a link; choose a fresh folder")
        if stage.exists():
            existing = list(stage.rglob("*")) if stage.is_dir() else []
            valid = stage.is_dir() and not any(p.is_symlink() for p in existing) and {
                p.relative_to(stage).as_posix() for p in existing if p.is_file()} == names
            if valid:
                valid = all((stage / name).read_bytes() == archive.read(name)
                            for name in UPDATE_FILES)
            if valid:
                if editor_manifest(stage)["version"] != release["latest"]:
                    raise ValueError("downloaded editor version differs from release tag")
                return stage
            stage.rename(folder / (tag + ".failed-" + uuid.uuid4().hex))
        with tempfile.TemporaryDirectory(dir=folder, prefix=".stage-") as temporary:
            temp = Path(temporary)
            for name in UPDATE_FILES:
                target = temp / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
            if editor_manifest(temp)["version"] != release["latest"]:
                raise ValueError("downloaded editor version differs from release tag")
            temp.rename(stage)
    return stage


def installer_command(stage: Path, home: Path, old_pid: int, port: int = 0, *,
                      no_browser: bool = False) -> list[str]:
    """Start the downloaded GUI EXE as updater, independent of the old EXE."""
    command = [str(stage / "LekmodLocalizationEditor.exe"), "--install-update",
               "--editor-root", str(home), "--stage", str(stage),
               "--old-pid", str(old_pid), "--port", str(port)]
    if no_browser:
        command.append("--no-browser")
    return command


def launch_update(stage: Path, home: Path = APP_HOME, old_pid: int | None = None,
                  port: int = 0) -> None:
    """Run the staged helper without PowerShell, a terminal, or inherited handles."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise ValueError("automatic installation requires the packaged Windows editor")
    subprocess.Popen(installer_command(stage, home, old_pid or os.getpid(), port,
                                       no_browser=bool(port)), cwd=stage,
                     env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True,
                     creationflags=subprocess.CREATE_NO_WINDOW)


def _pid_alive(pid: int) -> bool:
    """Check the process, including a PyInstaller child with a different PID."""
    if not pid:
        return False
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0x102
    finally:
        kernel.CloseHandle(handle)


def _wait_old_process(pid: int, seconds: int = 90) -> None:
    """Wait on a process handle so PID reuse cannot block installation."""
    if not pid:
        return
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return  # The old editor has already closed.
    try:
        if kernel.WaitForSingleObject(handle, seconds * 1000) != 0:
            raise TimeoutError("The old editor is still running; close it and retry.")
    finally:
        kernel.CloseHandle(handle)


def _replace(source: Path, target: Path) -> None:
    """Copy in the destination directory, then atomically swap with lock retries."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".updating-" + uuid.uuid4().hex)
    try:
        shutil.copy2(source, temporary)
        for attempt in range(40):
            try:
                os.replace(temporary, target)
                return
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(.5)
    finally:
        temporary.unlink(missing_ok=True)


def _record(home: Path, state: str, detail: str) -> None:
    """Keep the helper's diagnostics on disk, since it has no console."""
    workspace = home / "localization/workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with (workspace / "editor-updates/update.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp}  {state}  {detail}\n")
    with (workspace / "editor-actions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": timestamp, "action": "editor-update-install",
                                 "result": state}) + "\n")


def _wait_ready(home: Path, ticket: str, expected: str, process: subprocess.Popen) -> dict:
    """Confirm the new editor responds over HTTP before declaring success."""
    ready = home / "localization/workspace/editor-updates" / ("ready-" + ticket + ".json")
    for _ in range(300):
        if ready.is_file():
            try:
                signal = json.loads(ready.read_text(encoding="utf-8"))
                if signal.get("ticket") == ticket and _pid_alive(signal.get("pid", 0)):
                    url = f"http://127.0.0.1:{int(signal['port'])}"
                    with urllib.request.urlopen(url + "/api/meta", timeout=1) as response:
                        version = json.load(response).get("editor_version")
                    with urllib.request.urlopen(url + "/", timeout=1) as response:
                        page = response.read()
                    with urllib.request.urlopen(url + "/app.js", timeout=1) as response:
                        script = response.read()
                    if (version == expected and b"Lekmod Localization Editor" in page
                            and b"function renderTable" in script):
                        ready.unlink(missing_ok=True)
                        return signal
            except (OSError, ValueError, KeyError, urllib.error.URLError):
                pass
        if process.poll() is not None:
            break
        time.sleep(.2)
    raise RuntimeError("The updated editor did not open within 60 seconds.")


def install_update(home: Path, stage: Path, old_pid: int, *, port: int = 0,
                   no_browser: bool = False) -> None:
    """Backup, install, validate startup, and restore the old editor on failure."""
    home, stage = home.resolve(), stage.resolve()
    updates = home / "localization/workspace/editor-updates"
    if stage.parent != updates or stage == home or not home.is_dir():
        raise ValueError("invalid editor update location")
    expected = editor_manifest(stage)["version"]
    if tuple(map(int, expected.split("."))) <= tuple(map(int, editor_manifest(home)["version"].split("."))):
        raise ValueError("the staged editor is not newer than this installation")
    for name in UPDATE_FILES:
        if not (stage / name).is_file() or (stage / name).is_symlink():
            raise ValueError(f"Staged editor file is missing or unsafe: {name}")
    backup = updates / ("previous-editor-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                        + "-" + uuid.uuid4().hex[:6])
    process: subprocess.Popen | None = None
    existed: dict[str, bool] = {}
    started = False
    try:
        _wait_old_process(old_pid)
        backup.mkdir(parents=True)
        for name in UPDATE_FILES:
            target = home / name
            existed[name] = target.is_file()
            if existed[name]:
                destination = backup / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, destination)
        started = True
        for name in UPDATE_FILES:
            _replace(stage / name, home / name)
        ticket = uuid.uuid4().hex
        command = [str(home / "LekmodLocalizationEditor.exe"), "--update-ticket", ticket]
        if port:
            command.extend(("--port", str(port)))
        if no_browser:
            command.append("--no-browser")
        _record(home, "starting", f"Starting v{expected}; backup: {backup}")
        process = subprocess.Popen(command, cwd=home,
                                   env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, close_fds=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        signal = _wait_ready(home, ticket, expected, process)
        _record(home, "success", f"Installed v{expected}; backup: {backup}; pid: {signal['pid']}")
    except Exception as error:
        failure = str(error)
        if process is not None:
            try:
                subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as stop_error:
                failure += f"; could not stop the failed editor: {stop_error}"
        rollback_ok = True
        if started:
            for name in UPDATE_FILES:
                try:
                    target = home / name
                    if existed.get(name):
                        _replace(backup / name, target)
                    else:
                        target.unlink(missing_ok=True)
                except OSError as rollback_error:
                    rollback_ok = False
                    failure += f"; restoring {name} failed: {rollback_error}"
        _record(home, "failure", failure)
        if rollback_ok and started and (home / "LekmodLocalizationEditor.exe").is_file():
            try:
                command = [str(home / "LekmodLocalizationEditor.exe")]
                if port:
                    command.extend(("--port", str(port)))
                if no_browser:
                    command.append("--no-browser")
                subprocess.Popen(command, cwd=home,
                                 env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
                _record(home, "failure", "Previous editor reopened after failed update")
            except OSError as restart_error:
                _record(home, "failure", f"Could not reopen previous editor: {restart_error}")
        raise RuntimeError(failure) from error


def installer_main(argv: list[str]) -> int:
    """Run only in the downloaded EXE, before importing the browser server."""
    parser = argparse.ArgumentParser(description="Finish a staged editor update")
    parser.add_argument("--editor-root", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--old-pid", type=int, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        parser.error("the update helper must be the packaged Windows EXE")
    try:
        install_update(args.editor_root, args.stage, args.old_pid, port=args.port,
                       no_browser=args.no_browser)
    except Exception as error:
        # The GUI EXE has no stdout. The helper already wrote update.log.
        try:
            _record(args.editor_root, "failure", f"Update helper exited: {error}")
        except OSError:
            pass
        return 1
    return 0
