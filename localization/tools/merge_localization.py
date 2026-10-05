"""Review English source and translation handoffs as one ordered transaction."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile

import audit_primary_localization
import sync_primary_english
from merge_translation_handoff import (MAX_ARCHIVE, MAX_CSV, csv_records,
                                      encoded_records, merge_handoff, LOCALE)
from lekmod_localization.common import CatalogError, KEY_RE, normalize_text
from lekmod_localization.catalog import build_catalog
from lekmod_localization.sources import load_repository_localizations
from lekmod_localization.vanilla_reference import read_reference
from lekmod_localization.workspace import editor_source_fingerprint

PRIMARY = "localization/en_US/primary.xml"
REFERENCE = "localization/reference/vanilla-fingerprints.json.gz"
GAME = "LEKMOD/Override/CIV5Units_Mongol.xml"
OP = re.compile(r'(?ms)^[ \t]*<(?P<kind>Row|Replace)\b(?P<attrs>[^>]*)>'
                r'(?P<body>.*?)</(?P=kind)>')
TAG = re.compile(r'\bTag="(TXT_KEY_[A-Za-z0-9_]+)"')


def digest(data: bytes) -> str:
    """Hash exact bytes for concurrent-edit checks and package integrity."""
    return hashlib.sha256(data).hexdigest()


def semantic(element: ET.Element) -> object:
    """Ignore XML layout while retaining operation, selectors and grammar."""
    return [element.tag, sorted(element.attrib.items()), normalize_text(element.text),
            [semantic(child) for child in element]]


def english_groups(document: str) -> dict[str, dict]:
    """Index whole Row/Replace groups so repeated keys are never partly replaced."""
    sync_primary_english.validate_source(document)
    groups = {}
    for match in OP.finditer(document):
        tag = TAG.search(match['attrs'])
        if not tag:
            continue
        try:
            node = ET.fromstring(match.group())
        except ET.ParseError as error:
            raise CatalogError('Unsupported nested English text operation: ' + str(error)) from error
        if node.find('Text') is None:
            continue
        key = tag.group(1)
        group = groups.setdefault(key, {'parts': [], 'spans': [], 'values': [], 'text': []})
        group['parts'].append(match.group())
        group['spans'].append((match.start(), match.end()))
        group['values'].append(semantic(node))
        group['text'].append(normalize_text(node.findtext('Text')) or '')
    for group in groups.values():
        group['hash'] = digest(json.dumps(group['values'], sort_keys=True).encode())
        group['text'] = '\n'.join(dict.fromkeys(group['text']))
    return groups


def opaque_hash(document: str) -> str:
    """Pin selectors not editable in LLE; those require an IDE migration."""
    root = ET.fromstring(document)
    nodes = [semantic(node) for node in root[0]
             if not (node.tag in ('Row', 'Replace') and KEY_RE.fullmatch(node.get('Tag', ''))
                     and node.find('Text') is not None)]
    return digest(json.dumps(nodes, sort_keys=True).encode())


def english_base(project: Path) -> dict:
    """Remember source hashes before editing; Git HEAD supplies a legacy baseline."""
    path = project / 'localization/workspace/english-handoff-base.json'
    if path.is_file():
        return json.loads(path.read_text(encoding='utf-8'))
    document = (project / PRIMARY).read_text(encoding='utf-8-sig')
    if (project / '.git').exists():
        import subprocess
        result = subprocess.run(['git', 'show', 'HEAD:' + PRIMARY], cwd=project,
                                capture_output=True, text=True)
        if result.returncode == 0:
            document = result.stdout
    value = {'keys': {key: group['hash'] for key, group in english_groups(document).items()},
             'opaque': opaque_hash(document)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    return value


def candidate_sources(project: Path, document: str) -> dict[str, dict]:
    """Use the same catalog and fingerprints as the shipped XML generator."""
    ref = read_reference(project / REFERENCE)
    with tempfile.TemporaryDirectory(prefix='lle-english-') as folder:
        source = Path(folder) / 'primary.xml'
        source.write_text(document, encoding='utf-8')
        report = audit_primary_localization.parse_source(source, 'en_US')
        localizations, references, summary = load_repository_localizations(
            source, project / 'LEKMOD/Art', project)
        catalog = build_catalog(report, {key: {} for key in ref['english']},
                                sorted(ref['locales']), 'shared reference', ref['locales']['en_US'],
                                localizations, references, summary, vanilla_hashes=ref['english'])
    result = {}
    for categories in catalog['source_categories'].values():
        for entries in categories.values():
            for key, entry in entries.items():
                if entry['lekmod_en_US']['status'] == 'present':
                    result[key] = {'source_fingerprint': editor_source_fingerprint(key, entry),
                                   'lekmod_en_US': entry['lekmod_en_US']['text'],
                                   'required_format_tokens': json.dumps(
                                       entry['lekmod_en_US']['format_tokens'])}
    return result


def read_packages(archives: list[bytes | Path], reference: bytes) -> tuple[dict, dict]:
    """Accumulate separate EN and language ZIPs without discarding pending review."""
    english, translations = {}, {}
    for archive in archives:
        content = archive.read_bytes() if isinstance(archive, Path) else archive
        if not isinstance(content, bytes) or len(content) > MAX_ARCHIVE:
            raise CatalogError('handoff ZIP is missing or too large')
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as package:
                names = package.namelist()
                allowed = {'manifest.json', 'README.txt', PRIMARY}
                if (len(names) > 12 or len(names) != len(set(names)) or
                        not {'manifest.json', 'README.txt'} <= set(names) or
                        any(name not in allowed and not re.fullmatch(
                            r'translations/[A-Z0-9_]+\.csv', name) for name in names) or
                        any(item.file_size > 12 * 1024 * 1024 for item in package.infolist())):
                    raise CatalogError('unexpected or oversized handoff ZIP member')
                meta = json.loads(package.read('manifest.json'))
                if not isinstance(meta, dict) or meta.get('vanilla_reference_sha256') != digest(reference):
                    raise CatalogError('handoff team vanilla reference differs from this project')
                if PRIMARY in names:
                    raw = package.read(PRIMARY)
                    if meta.get('english_sha256') != digest(raw):
                        raise CatalogError('English source differs from its manifest')
                    document = raw.decode('utf-8-sig')
                    groups = english_groups(document)
                    base = meta.get('english_base')
                    if base is not None and (not isinstance(base, dict) or
                            not isinstance(base.get('keys'), dict) or
                            not isinstance(base.get('opaque'), str) or not re.fullmatch(r'[0-9a-f]{64}', base['opaque']) or
                            any(not isinstance(key, str) or not isinstance(value, str) or
                                not KEY_RE.fullmatch(key) or not re.fullmatch(r'[0-9a-f]{64}', value)
                                for key, value in base['keys'].items())):
                        raise CatalogError('invalid English handoff baseline')
                    value = {'document': document, 'groups': groups, 'base': base}
                    if english and english['document'] != document:
                        raise CatalogError('Two different English sources are pending; finish or clear this review first')
                    english = value
                declared = meta.get('locales', {})
                members = [name for name in names if name.startswith('translations/')]
                locales = {Path(name).stem for name in members}
                if any(not LOCALE.fullmatch(locale) for locale in locales):
                    raise CatalogError('invalid handoff language')
                if locales and not (isinstance(declared, dict) and set(declared) == locales):
                    if len(locales) == 1 and meta.get('locale') in locales:
                        declared = {meta['locale']: meta.get('translation_csv_sha256')}
                    else:
                        raise CatalogError('handoff languages differ from its manifest')
                for name in names:
                    if not name.startswith('translations/'):
                        continue
                    locale = Path(name).stem
                    data = package.read(name)
                    if len(data) > MAX_CSV:
                        raise CatalogError('translation CSV is too large')
                    expected = declared.get(locale) if isinstance(declared, dict) else None
                    if meta.get('locale') == locale:
                        expected = meta.get('translation_csv_sha256')
                    if expected is not None and expected != digest(data):
                        raise CatalogError(f'{locale} CSV differs from its manifest')
                    rows = csv_records(data, name)
                    target = translations.setdefault(locale, {})
                    for key, row in rows.items():
                        if key in target and target[key] != row:
                            raise CatalogError(f'Two incoming versions of {locale}:{key}; finish or clear the pending review')
                        target[key] = row
        except (OSError, ValueError, UnicodeError, zipfile.BadZipFile) as error:
            raise CatalogError(str(error)) from error
    if not english and not translations:
        raise CatalogError('ZIP contains no English or translated rows')
    return english, translations


def patch_english(document: str, incoming: dict, selected: set[str]) -> str:
    """Replace only reviewed keys, preserving all other team source bytes."""
    current = english_groups(document)
    replacements = []
    additions = []
    for key in sorted(selected):
        group = incoming.get(key)
        text = '\n'.join(group['parts']) if group else ''
        if key in current:
            for index, (start, end) in enumerate(current[key]['spans']):
                replacements.append((start, end, text if index == 0 else ''))
        elif group:
            additions.append(text)
    for start, end, text in sorted(replacements, reverse=True):
        document = document[:start] + text + document[end:]
    if additions:
        document = re.sub(r'[ \t]*</Language_en_US>', lambda _: '\n'.join(additions) +
                          '\n\t</Language_en_US>', document, count=1)
    sync_primary_english.validate_source(document)
    return document


def export_handoff(project: Path, *, english: bool = False, locales: list[str] | None = None) -> bytes:
    """Offer IDE contributors the same checksummed source/language ZIP format."""
    entries = {'README.txt': b'Import this ZIP into Merge or merge_localization.py; do not replace a team CSV.\n'}
    metadata = {'schema_version': 2, 'vanilla_reference_sha256': digest((project / REFERENCE).read_bytes()),
                'english_sha256': digest((project / PRIMARY).read_bytes())}
    if english:
        entries[PRIMARY] = (project / PRIMARY).read_bytes()
        metadata['english_base'] = english_base(project)
    if locales:
        if len(locales) != len(set(locales)):
            raise CatalogError('Repeated export language')
        metadata['locales'] = {}
        for locale in locales:
            if not LOCALE.fullmatch(locale): raise CatalogError('Invalid export language')
            data = (project / 'localization/translations' / (locale + '.csv')).read_bytes()
            csv_records(data, locale)
            entries['translations/' + locale + '.csv'] = data
            metadata['locales'][locale] = digest(data)
    if not english and not locales: raise CatalogError('Select English or at least one language')
    entries['manifest.json'] = json.dumps(metadata).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in entries.items(): package.writestr(name, data)
    return stream.getvalue()


def review_merge(archives: list[bytes | Path], project: Path, *,
                 choices: dict[str, str] | None = None, apply: bool = False,
                 expected: dict[str, str] | None = None) -> dict:
    """Validate EN first, then translated rows against the selected resulting EN."""
    project = project.resolve()
    choices = choices or {}
    reference = (project / REFERENCE).read_bytes()
    incoming, translated = read_packages(archives, reference)
    primary = project / PRIMARY
    original = primary.read_bytes()
    document = original.decode('utf-8-sig')
    current = english_groups(document)
    all_translations = {path.stem: csv_records(path.read_bytes(), path.name)
                        for path in (project / 'localization/translations').glob('*.csv')}
    files = {PRIMARY: original, GAME: (project / GAME).read_bytes(),
             **{'localization/translations/' + locale + '.csv': encoded_records(rows)
                for locale, rows in all_translations.items()}}
    # Compare original CSV bytes, including legacy six-column files.
    files.update({name: (project / name).read_bytes() for name in files if name.endswith('.csv')})
    hashes = {name: digest(data) for name, data in files.items()}
    if expected is not None and hashes != expected:
        raise CatalogError('Project changed since preview; import or refresh the review again')
    items, selected, pending = [], set(), []
    if incoming:
        base = incoming['base']
        if opaque_hash(incoming['document']) != (base['opaque'] if base else opaque_hash(document)):
            raise CatalogError('English selectors changed outside editable text rows; review that migration in an IDE')
        keys = set(incoming['groups']) | (set(base['keys']) if base else set())
        for key in sorted(keys):
            group, team = incoming['groups'].get(key), current.get(key)
            new_hash, old_hash = group['hash'] if group else None, team['hash'] if team else None
            base_hash = base['keys'].get(key) if base else None
            if new_hash == old_hash or base and new_hash == base_hash:
                continue
            blocked = group is None  # Deleting/renaming gameplay keys needs a migration.
            status = 'blocked' if blocked else 'new' if team is None else (
                'ready' if base and old_hash == base_hash else 'conflict')
            item_id = 'en_US:' + key
            choice = choices.get(item_id, 'keep' if blocked else
                                 'review' if status == 'conflict' else 'incoming')
            if choice not in ('incoming', 'keep', 'review') or blocked and choice == 'incoming':
                raise CatalogError('invalid English merge choice: ' + key)
            if choice == 'review': pending.append(item_id)
            if choice == 'incoming': selected.add(key)
            items.append({'id': item_id, 'locale': 'en_US', 'key': key, 'status': status,
                          'choice': choice, 'english': '', 'team': team['text'] if team else '',
                          'incoming': group['text'] if group else '(key removed)', 'resets': [],
                          'reason': 'Removing or renaming a key needs a reviewed gameplay migration' if blocked else
                          'Changes English first; older translations fall back until reviewed'})
    candidate = patch_english(document, incoming.get('groups', {}), selected) if selected else document
    sources = candidate_sources(project, candidate) if selected else None
    if items:
        projected = sources or candidate_sources(project, candidate)
        for item in items:
            entry = projected.get(item['key'])
            if item['key'] in selected:
                item['resets'] = sorted(locale for locale, rows in all_translations.items()
                    if item['key'] in rows and (not entry or rows[item['key']]['source_fingerprint'] != entry['source_fingerprint']))
    translation_report = {'items': [], 'pending': [], 'identical_count': 0}
    stream = io.BytesIO()
    if translated:
        contents = {locale: encoded_records(rows) for locale, rows in translated.items()}
        with zipfile.ZipFile(stream, 'w') as package:
            package.writestr('README.txt', 'Combined review')
            package.writestr('manifest.json', json.dumps({
                'vanilla_reference_sha256': digest(reference),
                'locales': {locale: digest(data) for locale, data in contents.items()}}))
            for locale, data in contents.items():
                package.writestr('translations/' + locale + '.csv', data)
        translation_choices = {key: value for key, value in choices.items() if not key.startswith('en_US:')}
        translation_report = merge_handoff(stream.getvalue(), project, choices=translation_choices,
                                           sources=sources)
        for item in translation_report['items']:
            item['resets'] = []
            item['reason'] = ('English must be merged first; this translation matches the selected new English'
                              if item['key'] in selected and item['status'] != 'stale' else
                              'English or formatting tokens do not match; skip or translate again'
                              if item['status'] == 'stale' else 'Review this translation before replacing team text')
            if item['key'] in selected and item['status'] in ('new', 'conflict'):
                item['status_label'] = 'English first'
    items += translation_report['items']
    pending += translation_report['pending']
    if set(choices) - {item['id'] for item in items}:
        raise CatalogError('Merge choices contain an unknown row')
    result = {'items': items, 'pending': pending,
              'locales': (['en_US'] if incoming else []) + sorted(translated),
              'identical_count': translation_report['identical_count'],
              'target_sha256': hashes, 'applied': False, 'backups': {}}
    if not apply: return result
    if pending: raise CatalogError('Resolve every conflicting row before applying')
    if any((project / name).read_bytes() != data for name, data in files.items()):
        raise CatalogError('Project changed during review; refresh before applying')
    writes = {}
    if selected: writes[PRIMARY] = sync_primary_english.encoded_text(primary, candidate)
    for locale, rows in translated.items():
        chosen = {item['key'] for item in translation_report['items']
                  if item['locale'] == locale and item['choice'] == 'incoming'}
        if chosen:
            writes['localization/translations/' + locale + '.csv'] = encoded_records(
                {**all_translations[locale], **{key: rows[key] for key in chosen}})
    if not writes: return result
    backup = project / 'localization/workspace/handoff-backups' / uuid.uuid4().hex
    for name in [*writes, GAME]:
        path = backup / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(files[name])
        result['backups'][name] = str(path)
    written = []
    try:
        for name, data in writes.items():
            path = project / name
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name); handle.write(data)
            try: temporary.replace(path)
            finally: temporary.unlink(missing_ok=True)
            written.append(name)
    except Exception:
        for name in written: (project / name).write_bytes(files[name])
        raise
    result['applied'] = True
    return result


def main() -> int:
    """Use the same ordered review from a Git checkout without opening the UI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zip', type=Path, nargs='*')
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--use-incoming', action='append', default=[], metavar='LOCALE:TXT_KEY')
    parser.add_argument('--keep', action='append', default=[], metavar='LOCALE:TXT_KEY')
    parser.add_argument('--export-english', action='store_true')
    parser.add_argument('--export-locale', action='append', default=[])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if set(args.use_incoming) & set(args.keep): parser.error('One row cannot be kept and replaced')
    choices = {**{key: 'incoming' for key in args.use_incoming}, **{key: 'keep' for key in args.keep}}
    try:
        if args.export_english or args.export_locale:
            if args.zip or args.apply or not args.output:
                parser.error('Export needs --output and cannot include import ZIPs or --apply')
            if args.output.exists(): raise CatalogError('Export output already exists; choose a new filename')
            data = export_handoff(args.project, english=args.export_english, locales=args.export_locale)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('xb') as handle: handle.write(data)
            print('Exported:', args.output); return 0
        if not args.zip: parser.error('Choose import ZIPs or export options')
        result = review_merge(args.zip, args.project, choices=choices, apply=args.apply)
        if result['applied']:
            command = [sys.executable, '-B', str(args.project.resolve() / 'localization/tools/manage.py'), 'prepare']
            try:
                subprocess.run(command, cwd=args.project, check=True)
            except Exception:
                for name, backup in result['backups'].items():
                    from lekmod_localization.version_history import atomic_write
                    atomic_write(args.project / name, Path(backup).read_bytes())
                # Recover generated workspace too; retain the original failure
                # if a pre-existing project problem also prevents this rebuild.
                subprocess.run(command, cwd=args.project, check=False)
                raise
        for item in result['items']:
            print(item['id'], item['status'], item['choice'],
                  'resets: ' + ', '.join(item['resets']),
                  repr(item['team'][:140]), '->', repr(item['incoming'][:140]))
        if result['pending']: return 2
        print('Merged English, then translations, and prepared project XML. Run manage.py check before committing.' if result['applied']
              else 'Preview only; pass --apply with the same reviewed choices.')
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, 'Localization merge failed: ' + str(error) + '\n')


if __name__ == '__main__': raise SystemExit(main())
