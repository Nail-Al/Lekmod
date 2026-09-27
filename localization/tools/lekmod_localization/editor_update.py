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
    if stage.exists():
        raise ValueError("a pending editor update already exists; restart the editor")
    with tempfile.TemporaryDirectory(dir=folder, prefix=".stage-") as temporary:
        temp = Path(temporary)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if len(names) != len(archive.infolist()):
                raise ValueError("release archive has duplicate file names")
            if not set(UPDATE_FILES).issubset(names):
                raise ValueError("release archive is missing editor files")
            if any(name not in UPDATE_FILES for name in names):
                raise ValueError("release archive contains unexpected files")
            for name in UPDATE_FILES:
                entry = archive.getinfo(name)
                if entry.file_size > 90 * 1024 * 1024:
                    raise ValueError("oversized editor file in release")
                target = temp / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
        if editor_manifest(temp)["version"] != release["latest"]:
            raise ValueError("downloaded editor version differs from release tag")
        temp.rename(stage)
    return stage


def write_windows_updater(stage: Path, home: Path = APP_HOME) -> Path:
    """Create a separate PowerShell process that can replace a closed EXE."""
    script = home / "localization/workspace/editor-updates/install-update.ps1"
    files = ",\n  ".join("'" + name + "'" for name in UPDATE_FILES)
    content = r"""param([int]$OldPid, [string]$Stage, [string]$Home)
$ErrorActionPreference = 'Stop'
try { Wait-Process -Id $OldPid -Timeout 90 -ErrorAction SilentlyContinue } catch {}
$files = @(
  FILES
)
$backup = Join-Path $Home ('localization/workspace/editor-updates/previous-editor-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
New-Item -ItemType Directory -Force -Path $backup | Out-Null
foreach ($file in $files) {
  $old = Join-Path $Home $file
  if (Test-Path -LiteralPath $old) {
    $saved = Join-Path $backup $file
    New-Item -ItemType Directory -Force -Path (Split-Path $saved) | Out-Null
    Copy-Item -LiteralPath $old -Destination $saved -Force
  }
}
try {
  foreach ($file in $files) {
    $source = Join-Path $Stage $file
    $target = Join-Path $Home $file
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
  }
  Start-Process -FilePath (Join-Path $Home 'LekmodLocalizationEditor.exe') -WorkingDirectory $Home
} catch {
  foreach ($file in $files) {
    $saved = Join-Path $backup $file
    if (Test-Path -LiteralPath $saved) {
      Copy-Item -LiteralPath $saved -Destination (Join-Path $Home $file) -Force
    } else {
      Remove-Item -LiteralPath (Join-Path $Home $file) -Force -ErrorAction SilentlyContinue
    }
  }
  Write-Host "Editor update failed and previous files were restored: $_"
  Read-Host 'Press Enter to close'
}
""".replace("FILES", files)
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(content, encoding="utf-8")
    return script
