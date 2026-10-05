"""Download a verified release and let its GUI executable replace the old one."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from contextlib import contextmanager

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
    verify_editor_processes(home, old_pid or os.getpid())
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
            try:
                ready.unlink(missing_ok=True)
            except PermissionError:
                pass  # A Windows scanner may hold the acknowledgement briefly.
            else:
                return
        if process.poll() is not None:
            raise RuntimeError("The downloaded editor update helper exited early. "
                               "The current editor remains open; see Logs or editor-updates/update.log.")
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


def editor_processes(home: Path) -> dict[int, int]:
    """Find this installation's EXE processes and their parents without a shell."""
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(kernel, name)
        function.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
        function.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    expected = os.path.normcase(str((home / "LekmodLocalizationEditor.exe").resolve()))
    result = {}
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.casefold() == "lekmodlocalizationeditor.exe":
                handle = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                if handle:
                    try:
                        buffer = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(len(buffer))
                        if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                            path = os.path.normcase(str(Path(buffer.value).resolve()))
                            if path == expected:
                                result[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                    finally:
                        kernel.CloseHandle(handle)
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return result


def verify_editor_processes(home: Path, old_pid: int) -> None:
    """Refuse extra copies before closing the user's current editor."""
    processes = editor_processes(home)
    allowed = set()
    pid = old_pid
    while pid in processes and pid not in allowed:
        allowed.add(pid)
        pid = processes[pid]  # PyInstaller also keeps its parent bootloader alive.
    extra = set(processes) - allowed
    if extra:
        raise RuntimeError("Another editor instance is using this installation (processes " +
                           ", ".join(map(str, sorted(extra))) + "). Save work in every editor, "
                           "then close the extra LekmodLocalizationEditor processes in Task Manager "
                           "and retry. Closing a browser tab alone does not stop an older editor.")


def _wait_executable_stopped(home: Path, seconds: float = 30) -> None:
    """Wait for the exited server's bootloader before replacing its Windows EXE."""
    deadline = time.monotonic() + seconds
    while remaining := editor_processes(home):
        if time.monotonic() >= deadline:
            raise RuntimeError("The editor executable is still running (processes " +
                               ", ".join(map(str, sorted(remaining))) +
                               "). Save work and close its remaining processes before retrying.")
        time.sleep(.2)


@contextmanager
def editor_instance(home: Path):
    """Keep one GUI server per installation; Windows releases the lock on exit."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield True
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    name = "Local\\LekmodLocalizationEditor-" + hashlib.sha256(
        os.path.normcase(str(home.resolve())).encode("utf-8")).hexdigest()
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        if status not in (0, 0x80, 0x102):
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = status in (0, 0x80)
        yield acquired
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
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
            except PermissionError as error:
                if attempt == 39:
                    raise PermissionError("Windows denied replacing " + target.name +
                        f" (error {getattr(error, 'winerror', error.errno)}). Check for another "
                        "running editor, a read-only file, or folder access restrictions. "
                        "Personal project files and translations are not replaced.") from error
                time.sleep(.5)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # Keep the original swap error if a scanner also holds the temporary file.


def _record(home: Path, state: str, detail: str, *, recovery: str = "") -> None:
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
            "[path]", safe_detail)[:1800]
        if recovery:
            event["recovery"] = recovery
    with (workspace / "editor-actions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


class UpdateProgress:
    """Keep the original localhost address responsive while the app is stopped."""
    def __init__(self, home: Path, port: int):
        self.home = home
        self.state = {"state": "installing", "phase": "waiting-processes",
                      "message": "Waiting for the previous editor to release its files…"}
        self.server = None
        self.thread = None
        if not port:
            return
        progress = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                """Serve only progress, health, and a reconnect screen; never project writes."""
                if self.path == '/':
                    data = ("<!doctype html><meta charset=utf-8><title>Editor update</title>"
                        "<style>body{font:16px system-ui;background:#f4f6f8;color:#243443;margin:0;"
                        "display:grid;place-items:center;min-height:100vh}main{background:white;"
                        "padding:2rem;border-radius:12px;box-shadow:0 8px 30px #0002;max-width:36rem}"
                        "#spinner{width:20px;height:20px;border:3px solid #dbe5ee;"
                        "border-top-color:#426e91;border-radius:50%;animation:spin 1s linear infinite}"
                        "@keyframes spin{to{transform:rotate(360deg)}}</style>"
                        "<main><h1>Updating Lekmod Localization Editor</h1><div id=spinner></div>"
                        "<p id=status>The editor is restarting…</p><p>Your project and translations "
                        "stay in place.</p></main><script>async function check(){"
                        "const c=new AbortController(),t=setTimeout(()=>c.abort(),2000);try{"
                        "const h=await(await fetch('/api/health',{signal:c.signal})).json();"
                        "if(!h.updating){location.reload();return;}"
                        "const s=await(await fetch('/api/editor-update-status',{signal:c.signal})).json();"
                        "document.getElementById('status').textContent=s.message||s.error||"
                        "'The editor is restarting…';}catch(e){}finally{clearTimeout(t);}"
                        "setTimeout(check,700);}check();</script>").encode('utf-8')
                    content_type = 'text/html; charset=utf-8'
                else:
                    if self.path == '/api/health':
                        value = {'editor_version': '', 'server_instance': 'update-helper', 'updating': True}
                    elif self.path == '/api/meta':
                        value = {'editor_version': '', 'server_instance': 'update-helper'}
                    elif self.path == '/api/editor-update-status':
                        value = progress.state.copy()
                    else:
                        self.send_error(404)
                        return
                    data = json.dumps(value).encode('utf-8')
                    content_type = 'application/json; charset=utf-8'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                """Progress requests contain no application data and need no request log."""
                pass

        self.server = HTTPServer(('127.0.0.1', port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def update(self, phase: str, message: str) -> None:
        """Expose a real installation stage and retain it in the local log."""
        self.state = {**self.state, 'phase': phase, 'message': message}
        _record(self.home, 'progress', phase + ': ' + message)

    def close(self) -> None:
        """Release the original port before starting the new or restored editor."""
        if self.server is not None:
            self.server.shutdown()
            self.thread.join(timeout=3)
            self.server.server_close()
            self.server = None


def _wait_ready(home: Path, ticket: str, expected: str, process: subprocess.Popen,
                *, expected_port: int = 0) -> dict:
    """Wait through a slow first launch, then check the lightweight server and UI."""
    ready = home / "localization/workspace/editor-updates" / ("ready-" + ticket + ".json")
    started = time.monotonic()
    timeout_seconds = 120
    last_report = 0
    last_error = "the new editor has not opened its local server"
    while time.monotonic() - started < timeout_seconds:
        if ready.is_file():
            try:
                signal = json.loads(ready.read_text(encoding="utf-8"))
                if signal.get("ticket") == ticket and _pid_alive(signal.get("pid", 0)):
                    if expected_port and int(signal['port']) != expected_port:
                        raise RuntimeError("The editor reopened at a different browser address.")
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
    progress: UpdateProgress | None = None
    existed: dict[str, bool] = {}
    replaced: list[str] = []
    try:
        _wait_old_process(old_pid, seconds=30)
        progress = UpdateProgress(home, port)
        _wait_executable_stopped(home)
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
        with editor_instance(home) as owner:
            if not owner:
                raise RuntimeError('Another editor opened during the update; no files were replaced.')
            progress.update('backup', 'Backing up the current application files…')
            backup.mkdir(parents=True)
            for name in UPDATE_FILES:
                target = home / name
                existed[name] = target.is_file()
                if existed[name]:
                    destination = backup / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, destination)
            for name in UPDATE_FILES:
                progress.update('installing', 'Installing ' + Path(name).name + '…')
                _replace(stage / name, home / name)
                replaced.append(name)
            progress.update('verifying', 'Verifying the installed application files…')
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
        progress.update('restarting', 'Starting editor v' + expected + ' on the same browser address…')
        progress.close()
        process = subprocess.Popen(command, cwd=home,
                                   env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, close_fds=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        signal = _wait_ready(home, ticket, expected, process, expected_port=port)
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
        if progress is not None:
            progress.update('recovering', 'Restoring the application files changed by this update…')
        rollback_ok = True
        if replaced:
            for name in reversed(replaced):
                try:
                    target = home / name
                    if existed.get(name):
                        _replace(backup / name, target)
                    else:
                        target.unlink(missing_ok=True)
                except OSError as rollback_error:
                    rollback_ok = False
                    failure += f"; restoring {name} failed: {rollback_error}"
        recovery = ("restored" if replaced else "unchanged") if rollback_ok else "incomplete"
        # Publish the cause before reopening so the restored UI sees it immediately.
        _record(home, "failure", failure, recovery=recovery)
        if (rollback_ok and not _pid_alive(old_pid) and
                (home / "LekmodLocalizationEditor.exe").is_file()):
            try:
                if progress is not None:
                    progress.close()
                recovery_ticket = uuid.uuid4().hex
                command = [str(home / "LekmodLocalizationEditor.exe"), '--update-ticket', recovery_ticket]
                if port:
                    command.extend(("--port", str(port)))
                if no_browser:
                    command.append("--no-browser")
                restored = subprocess.Popen(command, cwd=home,
                                 env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
                _wait_ready(home, recovery_ticket, editor_manifest(home)['version'], restored,
                            expected_port=port)
                _record(home, "recovered", "Previous editor reopened after failed update")
            except (OSError, RuntimeError, ValueError) as restart_error:
                failure += f"; could not reopen previous editor: {restart_error}"
                _record(home, "failure", failure, recovery=recovery)
        raise RuntimeError(failure) from error
    finally:
        if progress is not None:
            progress.close()


def _publish_helper_ready(marker: Path) -> None:
    """Expose the acknowledgement only after its write handle has closed."""
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                         dir=marker.parent, prefix=".helper-ready-",
                                         suffix=".tmp") as output:
            temporary = Path(output.name)
            output.write("ready")
        temporary.replace(marker)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
    installing = False
    pending = None
    active = args.editor_root / 'localization/workspace/editor-updates/helper-active.json'
    try:
        if args.handoff_ticket:
            verify_editor_processes(args.editor_root, args.old_pid)
        active.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False,
                                         dir=active.parent, prefix='.helper-active-') as output:
            pending = Path(output.name)
            json.dump({'pid': os.getpid(), 'port': args.port}, output)
        pending.replace(active)
        if args.handoff_ticket:
            marker = args.editor_root / "localization/workspace/editor-updates" / (
                "helper-ready-" + args.handoff_ticket)
            _publish_helper_ready(marker)
        installing = True
        install_update(args.editor_root, args.stage, args.old_pid, port=args.port,
                       no_browser=args.no_browser, repair=args.repair)
    except Exception as error:
        # The GUI EXE has no stdout. The helper already wrote update.log.
        if not installing:
            try:
                _record(args.editor_root, "failure", f"Update helper exited: {error}",
                        recovery="unchanged")
            except OSError:
                pass
        return 1
    finally:
        if pending is not None:
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            if json.loads(active.read_text(encoding='utf-8')).get('pid') == os.getpid():
                active.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    return 0
