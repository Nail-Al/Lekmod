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


def _published_version(version: str) -> dict:
    """Find the official archive for the installed version, even if it is old."""
    if not re.fullmatch(r"\d+\.\d+", version):
        raise ValueError("invalid installed editor version")
    metadata = json.loads(_download(
        f"https://api.github.com/repos/{REPOSITORY}/releases/tags/editor-v{version}",
        2 * 1024 * 1024))
    asset = next((item for item in metadata.get("assets", [])
                  if item.get("name") == ARCHIVE), None)
    if metadata.get("draft") or not asset:
        raise ValueError(f"no published files are available for editor v{version}")
    return {"latest": version, "download_url": asset["browser_download_url"],
            "digest": asset.get("digest", "")}


def _verified_files(release: dict, progress: Progress | None = None) -> dict[str, bytes]:
    """Read only signed-off package members after validating the release SHA-256."""
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
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        if len(names) != len(archive.infolist()) or names != set(UPDATE_FILES):
            raise ValueError("release archive has missing, duplicate or unexpected editor files")
        if sum(info.file_size for info in archive.infolist()) > 180 * 1024 * 1024:
            raise ValueError("release archive exceeds its uncompressed size limit")
        for name in UPDATE_FILES:
            info = archive.getinfo(name)
            if info.file_size > 90 * 1024 * 1024 or not name.isascii():
                raise ValueError("unsafe editor file in release")
        return {name: archive.read(name) for name in UPDATE_FILES}


def verify_installation(home: Path = APP_HOME,
                        progress: Progress | None = None) -> dict:
    """Compare only application files with this version's official release.

    Workspaces, project files, translations and vanilla references are never
    read or compared against the release, much less replaced by a repair.
    """
    current = editor_manifest(home)["version"]
    latest = latest_release(home)
    published = latest if latest["latest"] == current else _published_version(current)
    expected = _verified_files(published, progress)
    damaged = [name for name in UPDATE_FILES
               if (home / name).is_symlink() or not (home / name).is_file()
               or hashlib.sha256((home / name).read_bytes()).digest() !=
               hashlib.sha256(expected[name]).digest()]
    return {"current": current, "latest": latest["latest"],
            "available": latest["available"], "can_auto_update": latest["can_auto_update"],
            "damaged_files": damaged, "verified": not damaged}


def stage_release(release: dict, home: Path = APP_HOME,
                  progress: Progress | None = None, *, repair: bool = False) -> Path:
    """Verify GitHub's digest and allowlisted ZIP contents before staging."""
    if (not release["available"] and not repair) or not release["can_auto_update"]:
        raise ValueError("automatic installation requires an older or damaged Windows EXE")
    tag = "editor-v" + release["latest"]
    expected = _verified_files(release, progress)
    folder = home / "localization/workspace/editor-updates"
    folder.mkdir(parents=True, exist_ok=True)
    stage = folder / tag
    if stage.is_symlink():
        raise ValueError("pending editor update path is a link; choose a fresh folder")
    if stage.exists():
        existing = list(stage.rglob("*")) if stage.is_dir() else []
        valid = stage.is_dir() and not any(p.is_symlink() for p in existing) and {
            p.relative_to(stage).as_posix() for p in existing if p.is_file()} == set(expected)
        if valid:
            valid = all((stage / name).read_bytes() == expected[name]
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
            target.write_bytes(expected[name])
        if editor_manifest(temp)["version"] != release["latest"]:
            raise ValueError("downloaded editor version differs from release tag")
        temp.rename(stage)
    return stage


def installer_command(stage: Path, home: Path, old_pid: int, port: int = 0, *,
                      no_browser: bool = False, handoff_ticket: str = "",
                      repair: bool = False) -> list[str]:
    """Start the downloaded GUI EXE as updater, independent of the old EXE."""
    command = [str(stage / "LekmodLocalizationEditor.exe"), "--install-update",
               "--editor-root", str(home), "--stage", str(stage),
               "--old-pid", str(old_pid), "--port", str(port)]
    if no_browser:
        command.append("--no-browser")
    if handoff_ticket:
        command.extend(("--handoff-ticket", handoff_ticket))
    if repair:
        command.append("--repair")
    return command


def launch_update(stage: Path, home: Path = APP_HOME, old_pid: int | None = None,
                  port: int = 0, *, repair: bool = False) -> None:
    """Run the staged helper without PowerShell, a terminal, or inherited handles."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise ValueError("automatic installation requires the packaged Windows editor")
    ticket = uuid.uuid4().hex
    ready = home / "localization/workspace/editor-updates" / ("helper-ready-" + ticket)
    process = subprocess.Popen(installer_command(stage, home, old_pid or os.getpid(), port,
                                                 no_browser=bool(port), handoff_ticket=ticket,
                                                 repair=repair),
                               cwd=stage, env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, close_fds=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(150):
        if ready.is_file():
            ready.unlink(missing_ok=True)
            return
        if process.poll() is not None:
            raise RuntimeError("The downloaded editor update helper exited early. "
                               "The current editor remains open; see editor-startup.log.")
        time.sleep(.2)
    raise RuntimeError("The downloaded editor did not start its update helper. "
                       "The current editor remains open; retry the download.")


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
    (workspace / "editor-updates").mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    safe_detail = re.sub(r"(?i)\b(password|secret|token)\s*[:=]\s*\S+",
                         r"\1=[redacted]", detail)
    safe_detail = re.sub(r"https?://\S+", "[link]", safe_detail)
    with (workspace / "editor-updates/update.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp}  {state}  {safe_detail}\n")
    event = {"at": timestamp, "action": "editor-update-install", "result": state}
    if state == "failure":
        # The GUI has no terminal. Keep the cause in its Logs after rollback.
        event["detail"] = re.sub(
            r"(?i)\b[A-Z]:[\\/]\S+|(?<!\w)/(?:[^\s/]+/)+[^\s]*",
            "[path]", safe_detail)[:360]
    with (workspace / "editor-actions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _wait_ready(home: Path, ticket: str, expected: str, process: subprocess.Popen) -> dict:
    """Wait through a slow first launch, then check the lightweight server and UI."""
    ready = home / "localization/workspace/editor-updates" / ("ready-" + ticket + ".json")
    started = time.monotonic()
    timeout_seconds = 150
    last_report = 0
    last_error = "the new editor has not opened its local server"
    while time.monotonic() - started < timeout_seconds:
        if ready.is_file():
            try:
                signal = json.loads(ready.read_text(encoding="utf-8"))
                if signal.get("ticket") == ticket and _pid_alive(signal.get("pid", 0)):
                    url = f"http://127.0.0.1:{int(signal['port'])}"
                    with urllib.request.urlopen(url + "/api/health", timeout=8) as response:
                        version = json.load(response).get("editor_version")
                    if version != expected:
                        raise RuntimeError(f"The new editor reported v{version}, expected v{expected}.")
                    with urllib.request.urlopen(url + "/", timeout=8) as response:
                        page = response.read()
                    with urllib.request.urlopen(url + "/app.js", timeout=8) as response:
                        script = response.read()
                    if b"Lekmod Localization Editor" not in page or b"function renderTable" not in script:
                        raise RuntimeError("The updated editor served an incomplete page or script.")
                    ready.unlink(missing_ok=True)
                    return signal
                last_error = "the new editor's startup marker has an invalid ticket or PID"
            except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
                last_error = f"the new editor's local server is still starting: {error}"
        status = process.poll()
        if status is not None:
            raise RuntimeError(f"The updated editor exited with status {status}; "
                               f"{last_error}. See editor-startup.log.")
        elapsed = int(time.monotonic() - started)
        if elapsed >= last_report + 30:
            last_report = elapsed
            _record(home, "waiting", f"Editor v{expected} is still opening after {elapsed}s; "
                    + last_error)
        time.sleep(.25)
    raise RuntimeError(f"The updated editor did not open within {timeout_seconds} seconds; "
                       f"{last_error}. See editor-startup.log.")


def install_update(home: Path, stage: Path, old_pid: int, *, port: int = 0,
                   no_browser: bool = False, repair: bool = False) -> None:
    """Backup, install, validate startup, and restore the old editor on failure."""
    home, stage = home.resolve(), stage.resolve()
    updates = home / "localization/workspace/editor-updates"
    backup = updates / ("previous-editor-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                        + "-" + uuid.uuid4().hex[:6])
    process: subprocess.Popen | None = None
    existed: dict[str, bool] = {}
    started = False
    try:
        _wait_old_process(old_pid)
        if stage.parent != updates or stage == home or not home.is_dir():
            raise ValueError("invalid editor update location")
        expected = editor_manifest(stage)["version"]
        try:
            installed = editor_manifest(home)["version"]
        except (OSError, ValueError):
            if not repair:
                raise
            installed = "0.0"  # A damaged manifest can be restored by explicit repair.
        if (tuple(map(int, expected.split("."))) < tuple(map(int, installed.split(".")))
                or (expected == installed and not repair)):
            raise ValueError("the staged editor is not newer than this installation")
        for name in UPDATE_FILES:
            if not (stage / name).is_file() or (stage / name).is_symlink():
                raise ValueError(f"Staged editor file is missing or unsafe: {name}")
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
        if any((stage / name).read_bytes() != (home / name).read_bytes()
               for name in UPDATE_FILES):
            raise RuntimeError("Installed editor files differ from the verified release")
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
        if (rollback_ok and not _pid_alive(old_pid) and
                (home / "LekmodLocalizationEditor.exe").is_file()):
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
                _record(home, "recovered", "Previous editor reopened after failed update")
            except OSError as restart_error:
                failure += f"; could not reopen previous editor: {restart_error}"
        _record(home, "failure", failure)
        raise RuntimeError(failure) from error


def installer_main(argv: list[str]) -> int:
    """Run only in the downloaded EXE, before importing the browser server."""
    parser = argparse.ArgumentParser(description="Finish a staged editor update")
    parser.add_argument("--editor-root", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--old-pid", type=int, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--handoff-ticket", default="")
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        parser.error("the update helper must be the packaged Windows EXE")
    if args.handoff_ticket:
        if not re.fullmatch(r"[0-9a-f]{32}", args.handoff_ticket):
            parser.error("invalid update ticket")
        marker = args.editor_root / "localization/workspace/editor-updates" / (
            "helper-ready-" + args.handoff_ticket)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("ready", encoding="utf-8")
    try:
        install_update(args.editor_root, args.stage, args.old_pid, port=args.port,
                       no_browser=args.no_browser, repair=args.repair)
    except Exception as error:
        # The GUI EXE has no stdout. The helper already wrote update.log.
        try:
            _record(args.editor_root, "failure", f"Update helper exited: {error}")
        except OSError:
            pass
        return 1
    return 0
