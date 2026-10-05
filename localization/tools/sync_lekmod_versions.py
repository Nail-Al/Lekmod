"""Build or activate reviewed Lekmod release comparisons without game installs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from lekmod_localization.common import normalize_text
from lekmod_localization.sources import parse_language_sql
from lekmod_localization.version_history import order, synchronize, carry_translations, atomic_write


def release_index(root: Path, revision: str) -> dict[str, str]:
    """Read English XML blobs only; art binaries and full mod ZIPs are unnecessary."""
    files = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', revision, '--',
                                    'LEKMOD/Override/CIV5Units_Mongol.xml', 'LEKMOD/Art'], cwd=root, text=True)
    entries = {}
    for filename in files.splitlines():
        if not filename.lower().endswith(('.xml', '.sql')): continue
        content = subprocess.check_output(['git', 'show', revision + ':' + filename], cwd=root)
        if filename.lower().endswith('.sql'):
            text = content.decode('utf-8-sig')
            if not re.search(r'\bLanguage_[A-Za-z0-9_]+\b', text, re.IGNORECASE): continue
            languages, _ = parse_language_sql(text)
            for locale, rows in languages.items():
                if locale.casefold() != 'en_us': continue
                for key, fields in rows.items():
                    entries.setdefault(key, []).append([filename, {name: normalize_text(value)
                                                                 for name, value in fields.items()}])
            continue
        document = ET.fromstring(content)
        for language in document.iter('Language_en_US'):
            for row in language:
                key = row.get('Tag') or row.findtext('Tag') or row.findtext('Where/Tag')
                if not key or not re.fullmatch(r'TXT_KEY_[A-Za-z0-9_]+', key): continue
                def norm(node):
                    return [node.tag, sorted(node.attrib.items()), normalize_text(node.text),
                            [norm(child) for child in node]]
                entries.setdefault(key, []).append([filename, norm(row)])
    return {key: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            for key, value in entries.items()}


def main() -> int:
    """Maintainers pin commits; contributors activate cheap comparisons in their project."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--version', action='append', default=[])
    parser.add_argument('--since', default='')
    parser.add_argument('--carry-from', type=Path,
                        help='copy saved translations from this older complete project; keep its files unchanged')
    parser.add_argument('--build', action='append', default=[], metavar='VERSION=COMMIT')
    args = parser.parse_args()
    if args.carry_from:
        command = [sys.executable, '-B', str(args.project.resolve() / 'localization/tools/manage.py'), 'prepare']
        subprocess.run(command, cwd=args.project, check=True)
        paths = [args.project / 'LEKMOD/Override/CIV5Units_Mongol.xml',
                 * (args.project / 'localization/translations').glob('*.csv')]
        originals = {path: path.read_bytes() for path in paths}
        try:
            result = carry_translations(args.carry_from, args.project)
            subprocess.run(command, cwd=args.project, check=True)
        except Exception:
            for path, data in originals.items(): atomic_write(path, data)
            subprocess.run(command, cwd=args.project, check=True)
            raise
        print(json.dumps(result, indent=2))
    if not args.build:
        if args.since:
            from lekmod_localization.version_history import read_history, between
            from lekmod_localization.connections import release_version
            args.version += between(read_history(args.project), args.since, release_version(args.project))
        print(json.dumps(synchronize(args.project, args.version, since=args.since), indent=2)); return 0
    history, previous = {}, {}
    for value in sorted(args.build, key=lambda x: order(x.split('=')[0])):
        version, revision = value.split('=', 1)
        revision = subprocess.check_output(['git', 'rev-parse', revision], cwd=args.project, text=True).strip()
        current = release_index(args.project, revision)
        changes = {key: 'new' if key not in previous else 'removed' if key not in current else 'updated'
                   for key in set(previous) | set(current) if previous.get(key) != current.get(key)}
        history[version] = {'commit': revision, 'changes': changes,
                            'date': subprocess.check_output(['git', 'show', '-s', '--format=%cI', revision], cwd=args.project, text=True).strip(),
                            'internal_version': subprocess.check_output(['git', 'show', revision + ':LEKMOD/VERSION'],
                                                                        cwd=args.project, text=True).strip(),
                            'keys': len(current)}
        previous = current
        print(version, len(changes), 'changed keys', flush=True)
    path = args.project / 'localization/reference/lekmod-versions.json.gz'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps({'schema_version': 1, 'releases': history},
                                             sort_keys=True, separators=(',', ':')).encode(), mtime=0))
    return 0


if __name__ == '__main__': raise SystemExit(main())
