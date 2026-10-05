"""Small shared per-release English change index and safe translation transfer."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
import re
import sys
import tempfile

from .common import CatalogError


def atomic_write(path: Path, data: bytes) -> None:
    """Keep old bytes intact if writing a comparison record or CSV fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_history(project: Path) -> dict:
    """Use the reviewed index bundled with the editor when a project is older."""
    path = (Path(sys._MEIPASS) / 'lekmod-versions.json.gz' if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[2] / 'reference/lekmod-versions.json.gz')
    if not path.is_file(): path = project / 'localization/reference/lekmod-versions.json.gz'
    if not path.is_file(): return {'releases': {}}
    with gzip.open(path, 'rt', encoding='utf-8') as handle: value = json.load(handle)
    if value.get('schema_version') != 1 or not isinstance(value.get('releases'), dict):
        raise CatalogError('Invalid Lekmod release history')
    from .connections import APP_HOME
    caches = [project / 'localization/workspace/online-release-history.json',
              APP_HOME / 'localization/workspace/online-release-history.json']
    for cached in dict.fromkeys(caches):
        if not cached.is_file(): continue
        try:
            online = json.loads(cached.read_text(encoding='utf-8'))
            if online.get('schema_version') != 1 or not isinstance(online.get('releases'), dict):
                raise ValueError('Invalid online release history')
            for version, record in online['releases'].items():
                if (not re.fullmatch(r'v\d+\.\d+', version) or not isinstance(record, dict)
                    or not re.fullmatch(r'[0-9a-f]{40}', str(record.get('commit', '')))
                    or not isinstance(record.get('changes'), dict)
                    or any(not re.fullmatch(r'TXT_KEY_[A-Za-z0-9_]+', key) or state not in
                           ('new', 'updated', 'removed') for key, state in record['changes'].items())):
                    raise ValueError('Invalid online release record')
            for version, record in online['releases'].items():
                value['releases'].setdefault(version, {**record, 'online': True})
        except (OSError, ValueError, TypeError, AttributeError):
            raise CatalogError('Invalid cached online release history; the shared baseline is unchanged')
    return value


def order(version: str) -> tuple[int, ...]:
    """Sort v35.10 after v35.9 without lexical ordering mistakes."""
    return tuple(map(int, version.lstrip('vV').split('.')))


def between(history: dict, old: str, new: str) -> list[str]:
    """Include every known intermediate release, even changes later reverted."""
    return sorted((version for version in history['releases']
                   if order(old) < order(version) <= order(new)), key=order)


def synchronize(project: Path, versions: list[str], *, since: str = '') -> dict:
    """Record requested comparisons only; no mod archives or scripts are run."""
    history = read_history(project)
    if any(version not in history['releases'] for version in versions):
        raise CatalogError('Release has no reviewed English change index yet')
    path = project / 'localization/workspace/version-comparisons.json'
    value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    value['versions'] = sorted(set(value.get('versions', [])) | set(versions), key=order)
    if since: value['since'] = since
    atomic_write(path, json.dumps(value, indent=2).encode())
    return value


def change_map(project: Path, current: str) -> tuple[dict[str, list[str]], dict]:
    """Attach release labels to keys; the UI can filter a release or upgrade range."""
    history = read_history(project)
    path = project / 'localization/workspace/version-comparisons.json'
    value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    versions = value.get('versions', [current] if current in history['releases'] else [])
    result = {}
    for version in versions:
        for key in history['releases'].get(version, {}).get('changes', {}):
            result.setdefault(key, []).append(version)
    return result, {'available': sorted(history['releases'], key=order),
                    'synced': versions, 'since': value.get('since', ''),
                    'upgrade_versions': between(history, value['since'], current)
                        if value.get('since') else []}


def carry_translations(old: Path, new: Path) -> dict:
    """Keep old files intact and prefer any existing work in the new project."""
    from merge_translation_handoff import csv_records, encoded_records
    reference = 'localization/reference/vanilla-fingerprints.json.gz'
    if (old / reference).read_bytes() != (new / reference).read_bytes():
        raise CatalogError('Projects use different vanilla references; review a ZIP instead of automatic transfer')
    from .connections import release_version
    old_version, new_version = release_version(old), release_version(new)
    summary = {'from': old_version, 'to': new_version, 'copied': 0, 'conflicts': 0, 'archived': 0}
    catalog = new / 'localization/workspace/editor'
    manifest = json.loads((catalog / 'manifest.json').read_text(encoding='utf-8'))
    import csv
    originals, writes = {}, {}
    for source in (old / 'localization/translations').glob('*.csv'):
        target = new / 'localization/translations' / source.name
        if not target.is_file(): continue
        old_data, original = source.read_bytes(), target.read_bytes()
        originals[target] = original
        old_rows, current = csv_records(old_data, source.name), csv_records(original, target.name)
        keys = set()
        for filename in manifest['locales'].get(source.stem, {}).get('files', {}):
            path = catalog / source.stem / filename
            if not path.resolve().is_relative_to(catalog.resolve()):
                raise CatalogError('Unsafe comparison catalog path')
            with path.open(encoding='utf-8-sig', newline='') as handle:
                keys.update(row['key'] for row in csv.DictReader(handle))
        archive = new / 'localization/workspace/carried-translations' / old_version / source.name
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists(): atomic_write(archive, old_data)
        for key, row in old_rows.items():
            if key not in keys: summary['archived'] += 1
            elif key not in current: current[key] = row; summary['copied'] += 1
            elif any(current[key].get(field) != row.get(field) for field in
                     ('text', 'source_fingerprint', 'gender', 'plurality', 'translator_note')):
                summary['conflicts'] += 1
        if old_rows and encoded_records(current) != original:
            writes[target] = encoded_records(current)
    if any(path.read_bytes() != data for path, data in originals.items()):
        raise CatalogError('Destination translations changed during transfer; retry after reviewing them')
    history = read_history(new)
    written = []
    try:
        for path, data in writes.items():
            if path.read_bytes() != originals[path]:
                raise CatalogError('Destination changed before transfer')
            atomic_write(path, data)
            written.append(path)
        synchronize(new, between(history, old_version, new_version), since=old_version)
    except Exception:
        for path in written: atomic_write(path, originals[path])
        raise
    return summary
