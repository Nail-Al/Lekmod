"""Check official releases and cache their English changes without replacing work."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .common import normalize_text
from .version_history import atomic_write, order, read_history

OFFICIAL = 'https://api.github.com/repos/EnormousApplePie/Lekmod/'
RAW = 'https://raw.githubusercontent.com/EnormousApplePie/Lekmod/'
FEED = RAW + 'main/LekmodInstaller/github_setup/versions.json'
PRIMARY = 'LEKMOD/Override/CIV5Units_Mongol.xml'
VERSION = re.compile(r'^v\d+\.\d+$')
COMMIT = re.compile(r'^[0-9a-f]{40}$')
REFRESH_SECONDS = 600


def fetch_bytes(url: str, *, limit: int = 16 * 1024 * 1024, prefix: bool = False) -> bytes:
    """Bound official metadata/source reads; prefix reads only identify a release."""
    request = urllib.request.Request(url, headers={'User-Agent': 'Lekmod-Localization-Editor'})
    with urllib.request.urlopen(request, timeout=20) as response:
        content = response.read(limit if prefix else limit + 1)
    if not prefix and len(content) > limit:
        raise ValueError('Official release metadata exceeds the supported size')
    return content


def fetch_json(url: str) -> object:
    """Read JSON only from a fixed official endpoint, not a link inside the feed."""
    return json.loads(fetch_bytes(url, limit=4 * 1024 * 1024))


def validated_feed(value: object) -> dict:
    """Reject malformed version/date data before replacing a valid offline cache."""
    if not isinstance(value, dict) or not value or len(value) > 500:
        raise ValueError('Invalid official Lekmod release list')
    result = {}
    for version, info in value.items():
        if not VERSION.fullmatch(version) or not isinstance(info, dict):
            raise ValueError('Invalid official Lekmod release entry')
        date = info.get('release_date', '')
        if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            raise ValueError('Invalid official Lekmod release date')
        result[version] = {'release_date': date}
    return result


def catalog(home: Path, *, refresh: bool = False) -> dict:
    """Refresh at launch/every ten minutes; checking never switches a project."""
    path = home / 'localization/workspace/official-releases.json'
    cached = {}
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
        cached['releases'] = validated_feed(cached['releases'])
        if not isinstance(cached.get('attempted_at'), (int, float)):
            cached = {}
    except (OSError, ValueError, KeyError, TypeError):
        cached = {}
    if refresh or time.time() - cached.get('attempted_at', 0) >= REFRESH_SECONDS:
        try:
            releases = validated_feed(fetch_json(FEED))
            cached = {'releases': releases, 'attempted_at': time.time(),
                      'checked_at': datetime.now(timezone.utc).isoformat(), 'warning': ''}
        except Exception as error:
            if not cached:
                bundled = home / 'LekmodInstaller/github_setup/versions.json'
                cached = {'releases': validated_feed(json.loads(bundled.read_text(encoding='utf-8')))
                          if bundled.is_file() else {}, 'checked_at': ''}
            cached.update(attempted_at=time.time(), warning=
                          'Could not check official releases; showing the saved list. ' + str(error))
        atomic_write(path, json.dumps(cached, indent=2).encode())
    return {'versions': [{'version': version, 'date': info['release_date'],
                          'supported': order(version) >= (35, 0)}
                         for version, info in sorted(cached.get('releases', {}).items(),
                                                     key=lambda item: order(item[0]), reverse=True)],
            'checked_at': cached.get('checked_at', ''), 'warning': cached.get('warning', '')}


def source_revision(version: str, home: Path) -> str:
    """Pin a matching official commit; older projects cannot silently become latest."""
    if not VERSION.fullmatch(version):
        raise ValueError('Invalid Lekmod release')
    known = read_history(home)['releases'].get(version, {}).get('commit', '')
    if COMMIT.fullmatch(known):
        return known
    if version not in {item['version'] for item in catalog(home)['versions'] if item['supported']}:
        raise ValueError('This release is not in the official supported source list')
    # Current main normally matches the newest release. Historical VERSION commits
    # cover skipped intermediate releases without downloading their art archives.
    candidates = [fetch_json(OFFICIAL + 'commits/main')['sha']]
    for page in range(1, 4):
        for revision in candidates:
            if not COMMIT.fullmatch(str(revision)):
                raise ValueError('Invalid official source revision')
            header = fetch_bytes(RAW + revision + '/' + PRIMARY, limit=16384, prefix=True).decode('utf-8-sig')
            labels = re.findall(r'\bLEKMOD v(\d+\.\d+)\b', header)
            if labels and 'v' + min(labels, key=len) == version:
                return revision
        candidates = [entry['sha'] for entry in fetch_json(
            OFFICIAL + 'commits?path=LEKMOD/VERSION&per_page=100&page=' + str(page))]
        if not candidates:
            break
    raise ValueError('No matching official source commit found for ' + version +
                     '; the connected project is unchanged')


def english_rows(content: bytes, filename: str) -> dict:
    """Normalize game markup and indentation exactly like the shared change index."""
    result = {}
    text = content.decode('utf-8-sig')
    if not re.search(r'\bLanguage_en_US\b', text, re.IGNORECASE):
        return result
    if filename.lower().endswith('.sql'):
        from .sources import parse_language_sql
        languages, _ = parse_language_sql(text)
        for locale, rows in languages.items():
            if locale.casefold() == 'en_us':
                result.update({key: [{name: normalize_text(value) for name, value in fields.items()}]
                               for key, fields in rows.items()})
        return result

    def normalized(node):
        return [node.tag, sorted(node.attrib.items()), normalize_text(node.text),
                [normalized(child) for child in node]]

    document = ET.fromstring(content)
    for language in document.iter('Language_en_US'):
        for row in language:
            key = row.get('Tag') or row.findtext('Tag') or row.findtext('Where/Tag')
            if key and re.fullmatch(r'TXT_KEY_[A-Za-z0-9_]+', key):
                result.setdefault(key, []).append(normalized(row))
    return result


def release_changes(previous: str, revision: str, *, cancelled=None) -> dict:
    """Compare changed English files only; retain changes in every intermediate release."""
    comparison = fetch_json(OFFICIAL + 'compare/' + previous + '...' + revision)
    files = comparison.get('files', [])
    if comparison.get('status') not in ('ahead', 'identical') or len(files) >= 300:
        raise ValueError('Official changes require a reviewed migration; no project was replaced')
    before, after = {}, {}
    for file in files:
        if cancelled and cancelled():
            from .connections import DownloadCancelled
            raise DownloadCancelled('Source comparison canceled; earlier projects are unchanged')
        current = file['filename']
        old = file.get('previous_filename', current)
        for path, commit, target, absent in (
            (old, previous, before, file['status'] == 'added'),
            (current, revision, after, file['status'] == 'removed'),
        ):
            if absent or not (path == PRIMARY or path.startswith('LEKMOD/Art/')):
                continue
            if not path.lower().endswith(('.xml', '.sql')):
                continue
            rows = english_rows(fetch_bytes(RAW + commit + '/' + urllib.parse.quote(path)), path)
            for key, values in rows.items():
                target.setdefault(key, []).append([path, values])
    return {key: 'new' if key not in before else 'removed' if key not in after else 'updated'
            for key in before.keys() | after.keys() if before.get(key) != after.get(key)}


def ensure_history(version: str, home: Path, *, progress=None, cancelled=None) -> str:
    """Cache missing comparison records before a newly downloaded project becomes usable."""
    history = read_history(home)['releases']
    if version in history:
        return history[version]['commit']
    feed = catalog(home)['versions']
    versions = sorted((row['version'] for row in feed if row['supported'] and
                       order(row['version']) <= order(version)), key=order)
    known = sorted((v for v in history if order(v) < order(version)), key=order)
    if not known:
        raise ValueError('Missing shared release baseline; download a complete localization project')
    previous = known[-1]
    pending = {}
    for current in (v for v in versions if order(v) > order(previous)):
        if cancelled and cancelled():
            from .connections import DownloadCancelled
            raise DownloadCancelled('Source comparison canceled; earlier projects are unchanged')
        if progress:
            progress('comparing English changes in ' + current, 0, None)
        revision = source_revision(current, home)
        record = {'commit': revision, 'date': next(row['date'] for row in feed if row['version'] == current),
                  'changes': release_changes((pending.get(previous) or history[previous])['commit'],
                                             revision, cancelled=cancelled)}
        pending[current] = record
        previous = current
    if version not in pending:
        raise ValueError('Selected version is missing from the official release list')
    if cancelled and cancelled():
        from .connections import DownloadCancelled
        raise DownloadCancelled('Source comparison canceled; earlier projects are unchanged')
    path = home / 'localization/workspace/online-release-history.json'
    private = {v: info for v, info in history.items() if info.get('online')}
    private.update({v: {**info, 'online': True} for v, info in pending.items()})
    atomic_write(path, json.dumps({'schema_version': 1, 'releases': private}, sort_keys=True).encode())
    return pending[version]['commit']


def verify_source(project: Path) -> None:
    """Exercise the real catalog parser before accepting an unbundled mod version."""
    import audit_primary_localization
    from .catalog import build_catalog
    from .sources import load_repository_localizations
    from .vanilla_reference import read_reference
    primary = project / 'localization/en_US/primary.xml'
    report = audit_primary_localization.parse_source(primary, 'en_US')
    reference_path = project / 'localization/reference/vanilla-fingerprints.json.gz'
    reference = read_reference(reference_path)
    variants, references, summary = load_repository_localizations(
        primary, project / 'LEKMOD/Art', project)
    build_catalog(report, {}, list(reference['locales']), reference_path.name,
                  reference['locales']['en_US'], variants, references, summary,
                  vanilla_hashes=reference['english'])
