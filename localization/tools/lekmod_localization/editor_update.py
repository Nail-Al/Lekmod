"""Stage a verified Windows editor release without touching a connected project."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import urllib.request
import uuid
import zipfile

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


def _download(url: str, limit: int) -> bytes:
    """Bound network input and refuse a transport downgrade on redirects."""
    request = urllib.request.Request(url, headers={"User-Agent": "Lekmod-Localization-Editor"})
    with urllib.request.urlopen(request, timeout=45) as response:
        if not response.url.startswith("https://"):
            raise ValueError("editor release was redirected away from HTTPS")
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError("editor release exceeds its size limit")
    return content


def latest_release(home: Path = APP_HOME) -> dict:
    """Discover the newest tagged editor release, ignoring unrelated mod tags."""
    releases = json.loads(_download(RELEASES, 2 * 1024 * 1024))
    current = editor_manifest(home)["version"]
    candidates = []
    for release in releases:
        match = VERSION.fullmatch(str(release.get("tag_name", "")))
        if not match or release.get("draft") or release.get("prerelease"):
            continue
        asset = next((a for a in release.get("assets", []) if a.get("name") == ARCHIVE), None)
        if not asset:
            continue
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


def stage_release(release: dict, home: Path = APP_HOME) -> Path:
    """Check a GitHub release archive and leave project and private files intact."""
    if not release["available"] or not release["can_auto_update"]:
        raise ValueError("automatic update is available only for an older Windows EXE")
    tag = "editor-v" + release["latest"]
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{ARCHIVE}"
    if release["download_url"] != expected_url:
        raise ValueError("unexpected editor release download URL")
    digest = release["digest"]
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("editor release has no SHA-256 digest; ask a maintainer to republish it")
    data = _download(expected_url, 100 * 1024 * 1024)
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
            if archive.getinfo(name).file_size > 90 * 1024 * 1024:
                raise ValueError("oversized editor file in release")
        if stage.is_symlink():
            raise ValueError("pending editor update path is a link; choose a fresh folder")
        if stage.exists():
            # Never trust a previous attempt. Preserve a damaged staging folder for
            # diagnosis, but let the next click create a fresh verified copy.
            existing = list(stage.rglob("*")) if stage.is_dir() else []
            valid = stage.is_dir() and not any(p.is_symlink() for p in existing) and {
                p.relative_to(stage).as_posix() for p in existing if p.is_file()} == names
            if valid:
                valid = all(not (stage / name).is_symlink() and
                            (stage / name).read_bytes() == archive.read(name)
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


def write_windows_updater(stage: Path, home: Path = APP_HOME) -> Path:
    """Create a separate PowerShell process that can replace a closed EXE."""
    script = home / "localization/workspace/editor-updates/install-update.ps1"
    files = ",\n  ".join("'" + name + "'" for name in UPDATE_FILES)
    content = r"""param([int]$OldPid, [string]$Stage, [string]$EditorRoot, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$files = @(
  FILES
)
$updates = Join-Path $EditorRoot 'localization/workspace/editor-updates'
$log = Join-Path $updates 'update.log'
$events = Join-Path $EditorRoot 'localization/workspace/editor-actions.jsonl'
$backup = $null
$copyStarted = $false
$rollbackOk = $true
$existed = @{}
$started = $null
$ticket = [Guid]::NewGuid().ToString('N')
$ready = Join-Path $updates ("ready-$ticket.json")
function Record-Update([string]$state, [string]$detail) {
  $when = [DateTime]::UtcNow.ToString('o')
  Add-Content -LiteralPath $log -Value "$when  $state  $detail" -Encoding UTF8
  $record = @{at=$when; action='editor-update-install'; result=$state} | ConvertTo-Json -Compress
  [System.IO.File]::AppendAllText($events, $record + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}
try {
  if (-not (Test-Path -LiteralPath $EditorRoot -PathType Container)) {
    throw 'Editor folder does not exist.'
  }
  try { Wait-Process -Id $OldPid -Timeout 90 -ErrorAction SilentlyContinue } catch {}
  if (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) {
    throw 'The old editor is still running; close it before retrying.'
  }
  foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath (Join-Path $Stage $file) -PathType Leaf)) {
      throw "Staged editor file is missing: $file"
    }
  }
  $backup = Join-Path $updates ('previous-editor-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
  New-Item -ItemType Directory -Force -Path $backup | Out-Null
  foreach ($file in $files) {
    $old = Join-Path $EditorRoot $file
    $existed[$file] = Test-Path -LiteralPath $old -PathType Leaf
    if ($existed[$file]) {
      $saved = Join-Path $backup $file
      New-Item -ItemType Directory -Force -Path (Split-Path $saved) | Out-Null
      Copy-Item -LiteralPath $old -Destination $saved -Force
    }
  }
  $copyStarted = $true
  foreach ($file in $files) {
    $source = Join-Path $Stage $file
    $target = Join-Path $EditorRoot $file
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
  }
  $launchArgs = @('--update-ticket', $ticket)
  if ($NoBrowser) { $launchArgs += '--no-browser' }
  Record-Update 'starting' "Starting updated editor from $Stage"
  $started = Start-Process -FilePath (Join-Path $EditorRoot 'LekmodLocalizationEditor.exe') -WorkingDirectory $EditorRoot -ArgumentList $launchArgs -PassThru
  $confirmed = $false
  $signal = $null
  for ($attempt = 0; $attempt -lt 300; $attempt++) {
    if (Test-Path -LiteralPath $ready -PathType Leaf) {
      $signal = Get-Content -LiteralPath $ready -Raw | ConvertFrom-Json
      # A PyInstaller onefile EXE has a bootloader PID and a different app PID.
      if ($signal.ticket -eq $ticket -and
          (Get-Process -Id $signal.pid -ErrorAction SilentlyContinue)) {
        $confirmed = $true
        break
      }
    }
    if ($started.HasExited) { break }
    Start-Sleep -Milliseconds 200
  }
  if (-not $confirmed) { throw 'The updated editor did not start within 60 seconds; restoring the previous version.' }
  Record-Update 'success' "Editor installed from $Stage; backup: $backup; pid: $($signal.pid); launcher_pid: $($started.Id); port: $($signal.port)"
} catch {
  $failure = $_.ToString()
  if ($signal -and $signal.pid -and (Get-Process -Id $signal.pid -ErrorAction SilentlyContinue)) {
    try { Stop-Process -Id $signal.pid -Force -ErrorAction Stop }
    catch { $failure += "; could not stop new app process: $_" }
  }
  if ($started) {
    try {
      & taskkill.exe /PID $started.Id /T /F 2>$null | Out-Null
      $started.WaitForExit(10000) | Out-Null
    } catch { $failure += "; could not stop new editor process tree: $_" }
  }
  if ($copyStarted) {
    foreach ($file in $files) {
      try {
        $target = Join-Path $EditorRoot $file
        for ($retry = 0; $retry -lt 20; $retry++) {
          try {
            if ($existed[$file]) {
              Copy-Item -LiteralPath (Join-Path $backup $file) -Destination $target -Force
            } else {
              if (Test-Path -LiteralPath $target) {
                Remove-Item -LiteralPath $target -Force -ErrorAction Stop
              }
            }
            break
          } catch {
            if ($retry -eq 19) { throw }
            Start-Sleep -Milliseconds 500
          }
        }
      } catch { $rollbackOk = $false; $failure += "; rollback failed for ${file}: $_" }
    }
  }
  Record-Update 'failure' $failure
  if ($rollbackOk -and -not (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) -and
      (Test-Path -LiteralPath (Join-Path $EditorRoot 'LekmodLocalizationEditor.exe') -PathType Leaf)) {
    try {
      if ($NoBrowser) {
        $restored = Start-Process -FilePath (Join-Path $EditorRoot 'LekmodLocalizationEditor.exe') -WorkingDirectory $EditorRoot -ArgumentList @('--no-browser') -PassThru
      } else {
        $restored = Start-Process -FilePath (Join-Path $EditorRoot 'LekmodLocalizationEditor.exe') -WorkingDirectory $EditorRoot -PassThru
      }
      Record-Update 'failure' "Previous editor reopened after failed update; pid: $($restored.Id)"
    } catch { Record-Update 'failure' "Could not restart the previous editor: $_" }
  }
  [Console]::Error.WriteLine("Editor update failed. See $log. $failure")
  exit 1
}
Remove-Item -LiteralPath $ready -Force -ErrorAction SilentlyContinue
try { Remove-Item -LiteralPath $Stage -Recurse -Force } catch {
  Record-Update 'warning' "Installed editor, but could not remove staged files: $_"
}
""".replace("FILES", files)
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(content, encoding="utf-8")
    return script
