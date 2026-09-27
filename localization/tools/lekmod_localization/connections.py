"""Persist editor connections and guard writes to a matching Civ V DLC copy."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
import xml.etree.ElementTree as ET


APP_HOME = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[3])
SETTINGS_FILE = APP_HOME / "localization" / "workspace" / "editor-settings.json"
TEAM_SNAPSHOT_URL = (
    "https://www.dropbox.com/scl/fi/dquiyoh5k77v8qhip4u70/vanilla-snapshot.enc"
    "?rlkey=fwcgddzwanyaljhe9tja6ytk1&dl=1"
)
DEFAULTS = {
    "project_path": "", "game_path": "", "game_mod": "", "onboarded": False,
    "mode": "translator", "prefill": True, "wrap": True, "locale": "RU_RU",
    "category": "", "visible_columns": [], "translator_visible_columns": [],
    "developer_visible_columns": [], "column_widths": {},
    "snapshot_url": TEAM_SNAPSHOT_URL, "snapshot_url_cleared": False,
    "page_size": "100",
}
KEY = re.compile(r"^v?\d+(?:\.\d+)+$", re.IGNORECASE)
GAME_EXES = ("CivilizationV.exe", "CivilizationV_DX11.exe")


def settings(home: Path = APP_HOME) -> dict:
    """Use validated preference shapes even after a partial or older save."""
    path = home / "localization" / "workspace" / "editor-settings.json"
    if not path.is_file():
        return DEFAULTS.copy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULTS.copy()
    if not isinstance(raw, dict):
        return DEFAULTS.copy()
    result = DEFAULTS.copy()
    for name in ("project_path", "game_path", "game_mod", "locale", "category", "snapshot_url"):
        if isinstance(raw.get(name), str) and len(raw[name]) < 4096:
            result[name] = raw[name]
    for name in ("onboarded", "prefill", "wrap", "snapshot_url_cleared"):
        if type(raw.get(name)) is bool:
            result[name] = raw[name]
    if raw.get("mode") in ("translator", "developer"):
        result["mode"] = raw["mode"]
    if raw.get("page_size") in ("25", "50", "100", "250", "500", "1000", "all"):
        result["page_size"] = raw["page_size"]
    for name in ("visible_columns", "translator_visible_columns", "developer_visible_columns"):
        if isinstance(raw.get(name), list):
            result[name] = [x for x in raw[name]
                            if isinstance(x, str) and len(x) < 64][:40]
    if isinstance(raw.get("column_widths"), dict):
        result["column_widths"] = {k: v for k, v in raw["column_widths"].items()
                                   if isinstance(k, str) and type(v) is int
                                   and 100 <= v <= 1500}
    if not result["snapshot_url"] and not result["snapshot_url_cleared"]:
        # Older editor releases persisted an empty link before the team URL existed.
        result["snapshot_url"] = TEAM_SNAPSHOT_URL
    return result


def save_settings(values: dict, home: Path = APP_HOME) -> dict:
    """Atomically keep browser preferences outside the tracked source tree."""
    current = settings(home)
    if set(values) - set(DEFAULTS):
        raise ValueError("unknown editor setting")
    candidate = {**current, **values}
    if "snapshot_url" in values:
        candidate["snapshot_url_cleared"] = values["snapshot_url"] == ""
    # Validate the whole object through the same schema used at startup.
    if candidate["mode"] not in ("translator", "developer") or any(
        type(candidate[k]) is not bool for k in ("onboarded", "prefill", "wrap",
                                              "snapshot_url_cleared")
    ):
        raise ValueError("invalid editor mode or preference")
    if candidate["page_size"] not in ("25", "50", "100", "250", "500", "1000", "all"):
        raise ValueError("invalid table page size")
    if any(not isinstance(candidate[k], str) or len(candidate[k]) > 4096
           for k in ("project_path", "game_path", "game_mod", "locale", "category", "snapshot_url")):
        raise ValueError("invalid editor path or selection")
    if (any(not isinstance(candidate[name], list) or any(
        not isinstance(x, str) or len(x) > 64 for x in candidate[name])
            for name in ("visible_columns", "translator_visible_columns",
                         "developer_visible_columns")) or
        not isinstance(candidate["column_widths"], dict) or
        any(not isinstance(k, str) or type(v) is not int or not 100 <= v <= 1500
            for k, v in candidate["column_widths"].items())):
        raise ValueError("invalid table preferences")
    path = home / "localization" / "workspace" / "editor-settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".settings.", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(candidate, handle, indent=2)
        handle.write("\n")
    temporary.replace(path)
    return candidate


def project_root(home: Path = APP_HOME) -> Path:
    """Select the contributor's full project or the included source bundle."""
    configured = settings(home)["project_path"]
    return Path(configured).expanduser().resolve() if configured else home


def validate_project(root: Path, *, full: bool) -> dict:
    """Reject unrelated directories and projects lacking the localization tools."""
    root = root.expanduser().resolve()
    needed = ["LEKMOD/VERSION", "LEKMOD/Override/CIV5Units_Mongol.xml",
              "LEKMOD/Art", "localization/en_US/primary.xml",
              "localization/reference/vanilla-fingerprints.json.gz",
              "localization/config.json"]
    if full:
        needed.extend(("LEKMOD/Lua/tmp", "localization/tools/manage.py",
                       "localization/tools/tests"))
    missing = [item for item in needed if not (root / item).exists()]
    if missing:
        raise ValueError("Not a compatible Lekmod localization project; missing: "
                         + ", ".join(missing[:4]))
    version = (root / "LEKMOD/VERSION").read_text(encoding="utf-8-sig").strip()
    if not KEY.fullmatch(version):
        raise ValueError("LEKMOD/VERSION has an invalid version")
    return {"path": str(root), "version": version,
            "full": (root / "LEKMOD/Lua/tmp").is_dir() and
                    (root / "localization/tools/manage.py").is_file()}


def release_version(root: Path) -> str:
    """Read the displayed release number, distinct from the internal build."""
    source = (root / "localization/en_US/primary.xml").read_text(encoding="utf-8")
    labels = re.findall(r"\bLEKMOD v(\d+\.\d+)\b", source[:5000])
    if not labels:
        raise ValueError("English source has no Lekmod release label")
    return "v" + min(labels, key=len)


def validate_game(root: Path) -> Path:
    """Require both a Civ V executable and its DLC destination."""
    root = root.expanduser().resolve()
    if not root.is_dir() or not any((root / exe).is_file() for exe in GAME_EXES) or not (
        root / "Assets" / "DLC"
    ).is_dir():
        raise ValueError("Choose the Civilization V installation folder containing "
                         "CivilizationV.exe and Assets/DLC")
    return root


def detect_game() -> str:
    """Use the launcher's standard Steam locations and Windows registry."""
    paths = [Path(f"{drive}:/Program Files (x86)/Steam/steamapps/common/Sid Meier's Civilization V")
             for drive in "CDE"]
    paths += [Path("C:/Program Files/Steam/steamapps/common/Sid Meier's Civilization V")]
    if sys.platform == "win32":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for key_name in (r"SOFTWARE\Valve\Steam", r"SOFTWARE\WOW6432Node\Valve\Steam"):
                try:
                    with winreg.OpenKey(hive, key_name) as key:
                        steam, _ = winreg.QueryValueEx(key, "InstallPath")
                    paths.append(Path(steam) / "steamapps/common/Sid Meier's Civilization V")
                except OSError:
                    pass
    for path in paths:
        try:
            return str(validate_game(path))
        except ValueError:
            continue
    return ""


def installed_mods(game: Path) -> list[dict]:
    """List only actual DLC copies, not similarly named unrelated folders."""
    game = validate_game(game)
    result = []
    for folder in sorted((game / "Assets/DLC").iterdir()):
        if not folder.is_dir() or not folder.name.upper().startswith("LEKMOD_"):
            continue
        xml = folder / "Override/CIV5Units_Mongol.xml"
        if not xml.is_file():
            continue
        stamp = folder / "VERSION"
        internal = stamp.read_text(encoding="utf-8-sig").strip() if stamp.is_file() else ""
        result.append({"name": folder.name, "version": internal,
                       "release": folder.name[len("LEKMOD_"):]})
    return result


def _gameplay_digest(path: Path) -> str:
    """Compare all non-language XML so a game copy with different rules fails."""
    root = ET.parse(path).getroot()
    if root.tag != "GameData":
        raise ValueError("game Override XML is not GameData")
    for child in list(root):
        if child.tag.startswith("Language_"):
            root.remove(child)
    for element in root.iter():
        if element.text is not None and not element.text.strip():
            element.text = None
        if element.tail is not None and not element.tail.strip():
            element.tail = None
    return hashlib.sha256(ET.tostring(root, encoding="utf-8")).hexdigest()


def apply_game(project: Path, game: Path, mod_name: str, home: Path = APP_HOME) -> dict:
    """Back up and replace only the matching installed localization XML."""
    info = validate_project(project, full=False)
    mods = {item["name"]: item for item in installed_mods(game)}
    if mod_name not in mods:
        raise ValueError("Choose an installed LEKMOD_* DLC folder in Settings")
    installed = mods[mod_name]
    expected_release = release_version(project)
    if installed["release"].casefold() != expected_release.casefold() or (
        not installed["version"] or installed["version"].casefold() != info["version"].casefold()
    ):
        raise ValueError(f"Version mismatch: editor {info['version']} ({expected_release}), "
                         f"game {installed['version'] or 'unknown'} ({installed['release']})")
    source = project / "LEKMOD/Override/CIV5Units_Mongol.xml"
    target = game / "Assets/DLC" / mod_name / "Override/CIV5Units_Mongol.xml"
    if _gameplay_digest(source) != _gameplay_digest(target):
        raise ValueError("The installed Override XML has different gameplay data. "
                         "Install the matching Lekmod release before applying text.")
    old = target.read_bytes()
    new = source.read_bytes()
    if old == new:
        return {"changed": False, "target": str(target), "backup": None}
    folder = home / "localization/workspace/game-backups" / mod_name
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = folder / f"{stamp}-{hashlib.sha256(old).hexdigest()[:12]}.xml"
    backup.write_bytes(old)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".lekmod-localization.",
                                     delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(new)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"changed": True, "target": str(target), "backup": str(backup)}


def release_catalog(home: Path = APP_HOME) -> list[dict]:
    """Show official installer releases, marking only this branch's match usable."""
    path = home / "LekmodInstaller/github_setup/versions.json"
    versions = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    supported = set(editor_manifest(home)["compatible_releases"])
    return [{"version": version, "supported": version in supported,
             "date": info.get("release_date", "")}
            for version, info in versions.items()]


def editor_manifest(home: Path = APP_HOME) -> dict:
    """Read the independently versioned editor and its reviewed game releases."""
    path = home / "localization/editor/version.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or not re.fullmatch(r"\d+\.\d+", str(data.get("version", "")))
        or not isinstance(data.get("compatible_releases"), list)
        or not all(KEY.fullmatch(release) for release in data["compatible_releases"])):
        raise ValueError("invalid editor version manifest")
    return data


class DownloadCancelled(ValueError):
    """The contributor canceled a transfer before it touched a project."""


def extract_source_archive(archive: Path, destination: Path, *,
                           progress: Callable[[str, int, int | None], None] | None = None,
                           cancelled: Callable[[], bool] | None = None) -> None:
    """Extract only localization and LEKMOD files without ZIP path traversal."""
    total = 0
    with zipfile.ZipFile(archive) as zipped:
        entries = [entry for entry in zipped.infolist()
                   if len(Path(entry.filename).parts) >= 3 and
                   Path(entry.filename).parts[1] in ("LEKMOD", "localization") and
                   not entry.is_dir()]
        size = sum(entry.file_size for entry in entries)
        for entry in entries:
            if cancelled and cancelled():
                raise DownloadCancelled("download canceled; temporary files removed")
            parts = Path(entry.filename).parts
            if any(part in ("..", "") for part in parts) or entry.file_size > 128 * 1024 * 1024:
                raise ValueError("unsafe project ZIP entry")
            # Symlinks and device entries must never be followed or materialized.
            mode = (entry.external_attr >> 16) & 0o170000
            if mode not in (0, 0o100000):
                raise ValueError("project ZIP contains a non-regular file")
            total += entry.file_size
            if total > 1024 * 1024 * 1024:
                raise ValueError("project ZIP is too large")
            target = destination.joinpath(*parts[1:])
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("project ZIP entry leaves the destination")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(entry) as incoming, target.open("wb") as outgoing:
                while block := incoming.read(1024 * 1024):
                    if cancelled and cancelled():
                        raise DownloadCancelled("download canceled; temporary files removed")
                    outgoing.write(block)
                    if progress:
                        progress("extracting", total - entry.file_size + outgoing.tell(), size)


def download_compatible_source(version: str, home: Path = APP_HOME, *,
                               progress: Callable[[str, int, int | None], None] | None = None,
                               cancelled: Callable[[], bool] | None = None) -> Path:
    """Download the localization branch for the one reviewed release only."""
    if version not in editor_manifest(home)["compatible_releases"]:
        raise ValueError("This version has no compatible localization baseline. "
                         "A maintainer must migrate it before it can be edited.")
    destination = home / "localization/workspace/projects" / version
    if destination.exists():
        validate_project(destination, full=True)
        if release_version(destination) != version:
            raise ValueError(f"Existing project has a different release: {destination}")
        return destination  # Reuse; never replace a translator's files.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".download-") as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / "project.zip"
        url = "https://api.github.com/repos/Nail-Al/Lekmod/zipball/localization-infrastructure"
        request = urllib.request.Request(url, headers={"User-Agent": "Lekmod-Localization-Editor"})
        with urllib.request.urlopen(request, timeout=45) as response, archive.open("wb") as output:
            size = 0
            expected = int(response.headers.get("Content-Length") or 0) or None
            if progress:
                progress("downloading", 0, expected)
            while block := response.read(1024 * 1024):
                if cancelled and cancelled():
                    raise DownloadCancelled("download canceled; temporary files removed")
                size += len(block)
                if size > 750 * 1024 * 1024:
                    raise ValueError("project download exceeds 750 MB")
                output.write(block)
                if progress:
                    progress("downloading", size, expected)
        if cancelled and cancelled():
            raise DownloadCancelled("download canceled; temporary files removed")
        content = temporary_path / "project"
        content.mkdir()
        extract_source_archive(archive, content, progress=progress, cancelled=cancelled)
        if progress:
            progress("verifying", 0, None)
        info = validate_project(content, full=True)
        if release_version(content) != version:
            raise ValueError("downloaded project release differs from the selected version")
        if cancelled and cancelled():
            raise DownloadCancelled("download canceled; temporary files removed")
        content.rename(destination)
    return destination
