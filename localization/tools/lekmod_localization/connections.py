"""Persist editor connections and guard writes to a matching Civ V DLC copy."""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import urllib.request
from urllib.parse import parse_qs, urlsplit
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
import xml.etree.ElementTree as ET


APP_HOME = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[3])
SETTINGS_FILE = APP_HOME / "localization" / "workspace" / "editor-settings.json"
SETTINGS_LOCK = threading.RLock()
TEAM_SNAPSHOT_URL = (
    "https://www.dropbox.com/scl/fi/998d6o71w2og8x9facylu/vanilla-snapshot-complete.enc"
    "?rlkey=ex0bn7c9hjecmu6ayy7qpxaip&dl=1"
)
DEFAULTS = {
    "project_path": "", "game_path": "", "game_mod": "", "onboarded": False,
    "mode": "translator", "prefill": True, "wrap": True, "panel_expanded": True, "locale": "RU_RU",
    "category": "", "visible_columns": [], "translator_visible_columns": [],
    "developer_visible_columns": [], "column_widths": {},
    "translator_column_order": [], "developer_column_order": [],
    "snapshot_url": TEAM_SNAPSHOT_URL, "snapshot_url_cleared": False,
    "page_size": "100",
    "translator_filters": {}, "developer_filters": {},
}
FILTER_DEFAULTS = {"kind": "", "status": "", "date_field": "english_edited_at",
                   "date_from": "", "date_to": "", "version": "", "needs_translation": ""}
KEY = re.compile(r"^v?\d+(?:\.\d+)+$", re.IGNORECASE)
GAME_EXES = ("CivilizationV.exe", "CivilizationV_DX11.exe")


def table_filters(values: dict, *, developer: bool = False) -> dict:
    """Validate persistent filters independently for the two editing modes."""
    if not isinstance(values, dict) or set(values) - set(FILTER_DEFAULTS):
        raise ValueError("invalid table filter settings")
    result = {**FILTER_DEFAULTS, **values}
    kinds = ("", "Row", "Replace") if developer else (
        "", "lekmod_new", "vanilla_modified", "source_conflict")
    if (any(not isinstance(value, str) or len(value) > 64 for value in result.values()) or
            result['kind'] not in kinds or result['status'] not in (
                "", "missing", "stale", "applied", "saved", "draft", "needs_source_review") or
            result['date_field'] not in ("english_edited_at", "translation_updated_at") or
            result['needs_translation'] not in ("", "true")):
        raise ValueError("invalid table filter selection")
    for name in ('date_from', 'date_to'):
        if result[name]:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', result[name]):
                raise ValueError("invalid table filter date")
            datetime.strptime(result[name], '%Y-%m-%d')
    if result['date_from'] and result['date_to'] and result['date_from'] > result['date_to']:
        raise ValueError("table filter date range is reversed")
    if result['version'] and result['version'] != 'upgrade' and not re.fullmatch(
            r'v\d+(?:\.\d+)+', result['version']):
        raise ValueError("invalid table filter version")
    if developer and (result['status'] or result['needs_translation'] or
                      result['date_field'] != 'english_edited_at'):
        raise ValueError("translation-only filters cannot be applied in Developer mode")
    return result


def migrate_snapshot_link(url: str) -> str:
    """Replace the superseded team link while retaining custom and cleared links."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.netloc == "www.dropbox.com" and
                parsed.path == "/scl/fi/dquiyoh5k77v8qhip4u70/vanilla-snapshot.enc" and
                parse_qs(parsed.query).get("rlkey") == ["fwcgddzwanyaljhe9tja6ytk1"]):
            return TEAM_SNAPSHOT_URL
    except ValueError:
        pass
    return url


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
    for name in ("onboarded", "prefill", "wrap", "panel_expanded", "snapshot_url_cleared"):
        if type(raw.get(name)) is bool:
            result[name] = raw[name]
    if raw.get("mode") in ("translator", "developer"):
        result["mode"] = raw["mode"]
    if raw.get("page_size") in ("25", "50", "100", "250", "500", "1000", "all"):
        result["page_size"] = raw["page_size"]
    for name in ("visible_columns", "translator_visible_columns", "developer_visible_columns",
                 "translator_column_order", "developer_column_order"):
        if isinstance(raw.get(name), list):
            result[name] = [x for x in raw[name]
                            if isinstance(x, str) and len(x) < 64][:40]
            result[name] = list(dict.fromkeys(result[name]))
    if isinstance(raw.get("column_widths"), dict):
        result["column_widths"] = {k: v for k, v in raw["column_widths"].items()
                                   if isinstance(k, str) and type(v) is int
                                   and 100 <= v <= 1500}
    for name in ('translator_filters', 'developer_filters'):
        try:
            result[name] = table_filters(raw.get(name, {}), developer=name == 'developer_filters')
        except ValueError:
            result[name] = FILTER_DEFAULTS.copy()
    if not result["snapshot_url"] and not result["snapshot_url_cleared"]:
        # Older editor releases persisted an empty link before the team URL existed.
        result["snapshot_url"] = TEAM_SNAPSHOT_URL
    result["snapshot_url"] = migrate_snapshot_link(result["snapshot_url"])
    return result


def save_settings(values: dict, home: Path = APP_HOME) -> dict:
    """Merge concurrent preference writes so resizing cannot erase a language change."""
    with SETTINGS_LOCK:
        return _save_settings(values, home)


def _save_settings(values: dict, home: Path) -> dict:
    """Atomically keep browser preferences outside the tracked source tree."""
    current = settings(home)
    if set(values) - set(DEFAULTS):
        raise ValueError("unknown editor setting")
    candidate = {**current, **values}
    if "snapshot_url" in values:
        candidate["snapshot_url_cleared"] = values["snapshot_url"] == ""
    # Validate the whole object through the same schema used at startup.
    if candidate["mode"] not in ("translator", "developer") or any(
        type(candidate[k]) is not bool for k in ("onboarded", "prefill", "wrap", "panel_expanded",
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
                         "developer_visible_columns", "translator_column_order", "developer_column_order")) or
        not isinstance(candidate["column_widths"], dict) or
        any(not isinstance(k, str) or type(v) is not int or not 100 <= v <= 1500
            for k, v in candidate["column_widths"].items())):
        raise ValueError("invalid table preferences")
    for name in ('translator_column_order', 'developer_column_order'):
        if len(candidate[name]) > 40 or len(set(candidate[name])) != len(candidate[name]):
            raise ValueError('invalid column order')
    candidate["snapshot_url"] = migrate_snapshot_link(candidate["snapshot_url"])
    for name in ('translator_filters', 'developer_filters'):
        candidate[name] = table_filters(candidate[name], developer=name == 'developer_filters')
    path = home / "localization" / "workspace" / "editor-settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".settings.", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(candidate, handle, indent=2)
        handle.write("\n")
    temporary.replace(path)
    return candidate


def project_root(home: Path = APP_HOME, *, editor: bool = True) -> Path:
    """Select the contributor's full project or the included source bundle."""
    if not editor:
        return home
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
    """Find installed Civ V through Windows Steam roots and all configured libraries."""
    drives = "CDE"
    if sys.platform == "win32":
        import ctypes
        import winreg
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        drives = "".join(chr(65 + index) for index in range(2, 26)
                         if mask & (1 << index))
    steam_roots = [Path(f"{drive}:/{folder}") for drive in drives
                   for folder in ("Program Files (x86)/Steam", "Program Files/Steam",
                                  "Steam", "SteamLibrary")]
    paths = []
    if sys.platform == "win32":
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for key_name in (r"SOFTWARE\Valve\Steam", r"SOFTWARE\WOW6432Node\Valve\Steam"):
                try:
                    with winreg.OpenKey(hive, key_name) as key:
                        steam, _ = winreg.QueryValueEx(key, "InstallPath")
                    steam_roots.append(Path(steam))
                except OSError:
                    pass
            for key_name in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 8930",
                             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 8930"):
                try:
                    with winreg.OpenKey(hive, key_name) as key:
                        location, _ = winreg.QueryValueEx(key, "InstallLocation")
                    paths.append(Path(location))
                except OSError:
                    pass
    paths.extend(steam_game_candidates(steam_roots))
    fallback = ""
    seen = set()
    for path in paths:
        if str(path).casefold() in seen:
            continue
        seen.add(str(path).casefold())
        try:
            valid = str(validate_game(path))
            if installed_mods(path):
                return valid
            if not fallback:
                fallback = valid
        except (OSError, ValueError):
            continue
    return fallback


def steam_game_candidates(roots: list[Path]) -> list[Path]:
    """Follow Steam libraryfolders.vdf and the game's install manifest."""
    libraries = list(roots)
    for root in roots:
        manifest = root / "steamapps/libraryfolders.vdf"
        try:
            if manifest.stat().st_size > 2 * 1024 * 1024:
                continue
            content = manifest.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        for directory in re.findall(r'^\s*"path"\s*"([^"]+)"', content, re.MULTILINE):
            libraries.append(Path(directory.replace("\\\\", "\\")))
    candidates = []
    for library in libraries:
        steamapps = library / "steamapps"
        names = ["Sid Meier's Civilization V"]
        manifest = steamapps / "appmanifest_8930.acf"
        try:
            if manifest.stat().st_size <= 1024 * 1024:
                content = manifest.read_text(encoding="utf-8-sig")
                names = re.findall(r'^\s*"installdir"\s*"([^"]+)"', content,
                                   re.MULTILINE) + names
        except (OSError, UnicodeError):
            pass
        candidates.extend(steamapps / "common" / name for name in names)
    return candidates


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


def inspect_game(root: Path, project: Path | None = None) -> dict:
    """Report whether this folder can safely receive the project's localization."""
    path = str(root)
    result = {"path": path, "mods": [], "state": "missing_game", "error": ""}
    try:
        game = validate_game(root)
    except (OSError, ValueError) as error:
        result["error"] = str(error)
        return result
    result["path"] = str(game)
    try:
        folders = [folder for folder in (game / "Assets/DLC").iterdir()
                   if folder.is_dir() and folder.name.upper().startswith("LEKMOD_")]
    except OSError as error:
        result["error"] = "Cannot read the game's DLC folder: " + str(error)
        return result
    if not folders:
        result["state"] = "vanilla"
        return result
    if len(folders) != 1:
        result.update(state="multiple", error="Several Lekmod DLC folders found. Keep one installed version.")
        return result
    folder = folders[0]
    xml = folder / "Override/CIV5Units_Mongol.xml"
    stamp = folder / "VERSION"
    if not xml.is_file() or not stamp.is_file():
        result.update(state="damaged", error="This Lekmod DLC is missing VERSION or "
                      "Override/CIV5Units_Mongol.xml. Reinstall the matching mod.")
        return result
    try:
        mod = installed_mods(game)[0]
        result["mods"] = [mod]
        game_rules = _gameplay_digest(xml)
        if not mod["version"] or not KEY.fullmatch(mod["version"]):
            raise ValueError("This Lekmod DLC has an invalid VERSION file.")
        if project is not None:
            info = validate_project(project, full=False)
            release = release_version(project)
            if (mod["version"].casefold() != info["version"].casefold() or
                    mod["release"].casefold() != release.casefold()):
                result.update(state="mismatch", error=
                              f"Installed Lekmod: {mod['release']} (build {mod['version']}). "
                              f"This project requires {release} (build {info['version']}). "
                              "Install the matching version using the official Lekmod launcher.")
                return result
            source = project / "LEKMOD/Override/CIV5Units_Mongol.xml"
            if _gameplay_digest(source) != game_rules:
                result.update(state="mismatch", error="Installed Lekmod gameplay XML differs "
                              f"from this project ({release}, build {info['version']}). "
                              "Reinstall its matching release before applying text.")
                return result
    except (OSError, ValueError, ET.ParseError, IndexError) as error:
        result.update(state="damaged", error="Cannot verify Lekmod XML or version: " + str(error))
        return result
    result["state"] = "installed"
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
    # This lazy import avoids the shared-path module's startup dependency.
    # Validate even a no-op: older LLE releases may have installed invalid XML.
    from .runtime_xml import validate_runtime_xml
    runtime = validate_runtime_xml(new.decode('utf-8-sig'))
    if not runtime['counts'].get('en_US'):
        raise ValueError('The prepared XML contains no English texts; rebuild the project before applying.')
    if old == new:
        return {"changed": False, "target": str(target), "backup": None,
                "validated_locales": sorted(runtime['counts'])}
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
    return {"changed": True, "target": str(target), "backup": str(backup),
            "validated_locales": sorted(runtime['counts'])}


def game_profile() -> Path:
    """Resolve Windows' actual Documents folder, including a redirected one."""
    documents = Path.home() / 'Documents'
    if sys.platform == 'win32':
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:
            documents = Path(buffer.value)
    return documents / "My Games/Sid Meier's Civilization 5"


def game_diagnostics(project: Path, game: Path, *, profile: Path | None = None) -> dict:
    """Read installed XML, loaded cache rows and loader errors without changing them.

    Return hashes and presence/match results, rather than a copy of game text
    or translator drafts. Cache dates distinguish a prior run from a fresh one.
    This is evidence from a game run, separate from our synthetic SQLite gate.
    """
    from .common import normalize_text, quote_identifier
    from .runtime_xml import LOCALES, validate_runtime_xml
    from .sources import language_tables, database_uri
    keys = ('TXT_KEY_BUILDING_BAZAAR_HELP', 'TXT_KEY_LEKMOD_MENU_DISCORD',
            'TXT_KEY_LEKMOD_MENU_GITHUB', *('TXT_KEY_LEKMOD_MENU_VERSION_' + state
            for state in ('CHECKING', 'OUTDATED', 'UNKNOWN', 'UNREACHABLE', 'UPTODATE')))
    profile = profile if profile is not None else game_profile()
    report = {'schema_version': 1, 'collected_at': datetime.now(timezone.utc).isoformat(),
              'game': inspect_game(game, project), 'profile': str(profile),
              'xml': {}, 'databases': [], 'loader_messages': [],
              'note': 'Read-only report. A cache may describe an earlier game run. '
                      'Close Civ V after testing before collecting this report.'}

    def digest(text):
        value = normalize_text(text)
        return hashlib.sha256(value.encode('utf-8')).hexdigest() if value is not None else None

    def inspect_xml(path):
        data = path.read_bytes()
        item = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
                'modified_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
        try:
            validated = validate_runtime_xml(data.decode('utf-8-sig'), keys=keys)
            item['counts'] = validated['counts']
            item['keys'] = {locale: {key: digest(text) for key, text in texts.items()}
                            for locale, texts in validated['texts'].items()}
        except (ValueError, UnicodeError) as error:
            item['error'] = str(error)
        return item

    report['xml']['project'] = inspect_xml(project / 'LEKMOD/Override/CIV5Units_Mongol.xml')
    mods = report['game']['mods']
    if len(mods) == 1:
        target = game / 'Assets/DLC' / mods[0]['name'] / 'Override/CIV5Units_Mongol.xml'
        report['xml']['installed'] = inspect_xml(target)
        report['xml']['same_file'] = (report['xml']['project']['sha256'] ==
                                      report['xml']['installed']['sha256'])
    expected = report['xml'].get('installed', report['xml']['project']).get('keys', {})
    cache = profile / 'cache'
    for path in sorted(cache.iterdir()) if cache.is_dir() else []:
        if (not path.is_file() or path.suffix.lower() not in ('.db', '.sqlite') or
                not any(word in path.name.lower() for word in ('localization', 'debugdatabase'))):
            continue
        item = {'path': str(path), 'modified_at': datetime.fromtimestamp(
            path.stat().st_mtime, timezone.utc).isoformat(), 'languages': {}}
        report['databases'].append(item)
        try:
            with closing(sqlite3.connect(database_uri(path), uri=True, timeout=2)) as database:
                database.execute('PRAGMA query_only = ON')
                names = {row[0].casefold(): row[0] for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
                readers = {locale: (table, None) for locale, table in language_tables(database).items()}
                localized = names.get('localizedtext')
                if localized:
                    columns = {row[1].casefold() for row in database.execute(
                        f'PRAGMA table_info({quote_identifier(localized)})')}
                    if {'language', 'tag', 'text'} <= columns:
                        # Merged and DLC caches can contain only LocalizedText,
                        # without any Language_* tables or views.
                        for (language,) in database.execute(
                                f'SELECT DISTINCT Language FROM {quote_identifier(localized)}'):
                            if isinstance(language, str):
                                readers.setdefault(language.casefold(), (localized, language))
                item['has_language_storage'] = bool(readers)
                for locale, (table, stored_language) in readers.items():
                    if locale not in {name.casefold() for name in LOCALES}:
                        continue
                    wanted = next((expected[name] for name in expected if name.casefold() == locale), {})
                    quoted = quote_identifier(table)
                    language_filter = ' AND Language=?' if stored_language is not None else ''
                    parameters = (*keys, stored_language) if stored_language is not None else keys
                    found = dict(database.execute(f'SELECT Tag,Text FROM {quoted} '
                        f'WHERE Tag IN ({",".join("?" for key in keys)}){language_filter}', parameters))
                    item['languages'][locale] = {'data_source': table, 'stored_language': stored_language,
                        'columns': [
                        {'name': row[1], 'not_null': bool(row[3])} for row in database.execute(
                        f'PRAGMA table_info({quoted})')], 'keys': {key: {
                        'present': key in found, 'expected_known': key in wanted,
                        'matches_installed': key in wanted and
                        key in found and digest(found[key]) == wanted[key],
                        'text_sha256': digest(found[key]) if key in found else None,
                    } for key in keys}}
                if 'buildings' in names:
                    item['bazaar_help_key'] = database.execute(
                        "SELECT Help FROM Buildings WHERE Type='BUILDING_BAZAAR'").fetchone()
                if 'scannedfiles' in names:
                    item['scanned_mongol_files'] = list(database.execute(
                        "SELECT Path,DateTime FROM ScannedFiles WHERE lower(Path) LIKE '%mongol%' LIMIT 20"))
        except sqlite3.Error as error:
            item['error'] = str(error)
    for name in ('Database.log', 'XML.log', 'Localization.log'):
        path = profile / 'Logs' / name
        if not path.is_file():
            continue
        # Limit the report even when logging has run for months; do not copy a
        # complete log or include unrelated rows containing official text.
        with path.open('rb') as handle:
            handle.seek(max(0, path.stat().st_size - 65536))
            lines = handle.read().decode('utf-8-sig', errors='replace').splitlines()
        matches = [line[:500] for line in lines if 'civ5units_mongol' in line.casefold()
                   or 'no such table: language_' in line.casefold()
                   or ('not null' in line.casefold() and 'language_' in line.casefold())]
        report['loader_messages'].append({'path': str(path), 'lines': matches[-40:]})
    report['configured_languages'] = {}
    for name in ('config.ini', 'UserSettings.ini'):
        path = profile / name
        if path.is_file():
            # Report only language choices; never include unrelated profile data.
            selected = re.findall(r'^\s*(Language|AudioLanguage)\s*=\s*([^\r\n;]+)',
                                  path.read_text(encoding='utf-8-sig', errors='replace'),
                                  flags=re.MULTILINE | re.IGNORECASE)
            if selected:
                report['configured_languages'][name] = {key: value.strip()[:80] for key, value in selected}
    return report


def release_catalog(home: Path = APP_HOME) -> list[dict]:
    """Check the official list independently of this editor's release number."""
    from .release_feed import catalog
    return catalog(home)['versions']


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
    """Build a separate verified release project; never overwrite earlier work."""
    if not re.fullmatch(r'v\d+\.\d+', version):
        raise ValueError('Invalid Lekmod release')
    destination = home / "localization/workspace/projects" / version
    if destination.exists():
        validate_project(destination, full=True)
        if release_version(destination) != version:
            raise ValueError(f"Existing project has a different release: {destination}")
        return destination  # Reuse; never replace a translator's files.
    reviewed = version in editor_manifest(home)['compatible_releases']
    if not reviewed and version not in {item['version'] for item in release_catalog(home)
                                       if item['supported']}:
        raise ValueError('This release needs a reviewed localization migration')
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
        from .release_feed import ensure_history
        revision = None
        if not reviewed:
            revision = ensure_history(version, home, progress=progress, cancelled=cancelled)
        if release_version(content) != version:
            revision = revision or editor_manifest(home).get('release_sources', {}).get(version)
            if not revision:
                revision = ensure_history(version, home, progress=progress, cancelled=cancelled)
            if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
                raise ValueError('Selected release has no reviewed source revision')
            upstream_archive = temporary_path / 'official.zip'
            request = urllib.request.Request(
                'https://api.github.com/repos/EnormousApplePie/Lekmod/zipball/' + revision,
                headers={'User-Agent': 'Lekmod-Localization-Editor'})
            with urllib.request.urlopen(request, timeout=45) as response, upstream_archive.open('wb') as output:
                size = 0
                expected = int(response.headers.get('Content-Length') or 0) or None
                while block := response.read(1024 * 1024):
                    if cancelled and cancelled(): raise DownloadCancelled('download canceled; earlier projects are unchanged')
                    size += len(block)
                    if size > 750 * 1024 * 1024: raise ValueError('official source exceeds 750 MB')
                    output.write(block)
                    if progress: progress('downloading official ' + version, size, expected)
            official = temporary_path / 'official'; official.mkdir()
            extract_source_archive(upstream_archive, official, progress=progress, cancelled=cancelled)
            shutil.rmtree(content / 'LEKMOD')
            shutil.move(str(official / 'LEKMOD'), str(content / 'LEKMOD'))
            from sync_primary_english import bootstrap
            english = content / 'localization/en_US/primary.xml'
            english.unlink()
            bootstrap(english, content / 'LEKMOD/Override/CIV5Units_Mongol.xml')
        if progress:
            progress("verifying", 0, None)
        info = validate_project(content, full=True)
        if release_version(content) != version:
            raise ValueError("downloaded project release differs from the selected version")
        if not reviewed:
            from .release_feed import verify_source
            verify_source(content)
        if cancelled and cancelled():
            raise DownloadCancelled("download canceled; temporary files removed")
        content.rename(destination)
    return destination
