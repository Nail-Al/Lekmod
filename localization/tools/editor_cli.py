"""Use LLE's drafts, Save history, formatting review and Apply from an IDE terminal."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from lekmod_localization.common import CatalogError, REPO_ROOT, WORKSPACE, PLACEHOLDER_RE, formatting_approval, token_difference
from merge_localization import candidate_sources
from merge_translation_handoff import csv_records, encoded_records


def accept_project_formatting(locale: str, key: str) -> dict:
    """Review an IDE-edited CSV before prepare, using the generator's source rules."""
    from editor_server import atomic_bytes
    if not locale or not key or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' for ch in locale):
        raise CatalogError('Choose a language and TXT_KEY for project formatting review')
    path = REPO_ROOT / 'localization/translations' / (locale + '.csv')
    original = path.read_bytes()
    records = csv_records(original, path.name)
    row = records.get(key)
    primary = REPO_ROOT / 'localization/en_US/primary.xml'
    english = primary.read_bytes()
    source = candidate_sources(REPO_ROOT, english.decode('utf-8-sig')).get(key)
    if not row or not source or row['source_fingerprint'] != source['source_fingerprint']:
        raise CatalogError('Translation or current English is missing/stale; review the English source first')
    if PLACEHOLDER_RE.search(row['text']):
        raise CatalogError('Replace the language placeholder with a translation before formatting review')
    tokens = json.loads(source['required_format_tokens'])
    missing, extra = token_difference(row['text'], tokens)
    if not missing and not extra:
        return {'accepted': False, 'reason': 'Formatting already matches English'}
    row['formatting_approval'] = formatting_approval(row['text'], tokens, source['source_fingerprint'])
    if path.read_bytes() != original or primary.read_bytes() != english:
        raise CatalogError('Project changed during review; review the current row again')
    backup = WORKSPACE / 'formatting-backups' / (locale + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.csv')
    atomic_bytes(backup, original)
    atomic_bytes(path, encoded_records(records))
    return {'accepted': True, 'locale': locale, 'key': key, 'missing': missing,
            'unexpected': extra, 'backup': str(backup), 'next': 'Run manage.py prepare and check'}


def save_form(editor, args) -> dict:
    """Create the exact same revision-checked payload as a browser row form."""
    translator = bool(args.locale)
    if args.create and translator:
        raise CatalogError('Create the English key before translating it')
    if args.create:
        row = {'draft_slot': f'P:-1:{args.key}', 'draft_revision': 0, 'draft_base': {},
               'index': -1, 'key': args.key, 'applied_edit': {'text': '', 'identifier': args.key}}
        previous = editor.drafts.get(row['draft_slot'])
        if previous:
            row['draft_revision'] = previous['revision']
            if previous['payload']:
                row['applied_edit'] = previous['payload']['edit']
    else:
        result = (editor.rows(args.locale, 'all', args.key, 0, 'all') if translator else
                  editor.primary(args.key, 0, 'all'))
        matches = [item for item in result['rows'] if (item['key'] == args.key or item.get('draft_key') == args.key) and
                   (args.index is None or item.get('index') == args.index)]
        if len(matches) != 1:
            raise CatalogError('Choose one existing key; use --create for a new key or --index for repeated English operations')
        row = matches[0]
    edit = {name: '' for name in ('text', 'gender', 'plurality', 'note', 'identifier')}
    edit.update(row.get('applied_edit', {}))
    if row.get('has_local_draft'):
        edit.update(editor.drafts.get(row['draft_slot'])['payload']['edit'])
    if args.text_file is not None:
        edit['text'] = args.text_file.read_text(encoding='utf-8-sig')
    elif args.text is not None:
        edit['text'] = args.text
    if args.remove and translator:
        edit.update(text='', gender='', plurality='', note='')
    for field in ('gender', 'plurality', 'note', 'identifier'):
        if getattr(args, field) is not None:
            edit[field] = getattr(args, field)
    payload = {'mode': 'translator' if translator else 'developer', 'key': args.key,
               'locale': args.locale or '', 'slot': row['draft_slot'], 'index': row.get('index', -1),
               'revision': row['draft_revision'], 'base': row['draft_base'], 'edit': edit,
               'create': args.create or row.get('draft_create', False), 'delete': args.remove and not translator}
    if translator:
        payload['source_fingerprint'] = row['source_fingerprint']
    else:
        payload['key'] = row.get('draft_key', args.key)
    return editor.save_draft(payload)


def execute(editor, args) -> dict:
    action = args.action
    if action == 'status':
        return {'project': str(REPO_ROOT), **editor.drafts.status()}
    if action == 'rows':
        return (editor.rows(args.locale, args.category, args.search, 0, 'all') if args.locale else
                editor.primary(args.search, 0, 'all'))
    if action == 'drafts':
        return {'entries': editor.drafts.entries(), **editor.drafts.status()}
    if action == 'draft':
        return save_form(editor, args)
    if action == 'save':
        return editor.drafts.save_checkpoint()
    if action == 'saves':
        return {'saves': editor.drafts.saved_versions(), **editor.drafts.status()}
    if action == 'load':
        return editor.drafts.load_saved_version(args.save_id)
    if action == 'restore':
        return editor.drafts.restore_checkpoint()
    if action in ('undo', 'redo'):
        return editor.drafts.replay(undo=action == 'undo')
    if action == 'issues':
        return {'issues': editor.apply_issues(), **editor.drafts.status()}
    if action == 'accept-formatting':
        return editor.accept_formatting({'slot': args.slot, 'revision': args.revision,
                                         'source_fingerprint': args.source_fingerprint})
    if action == 'review':
        return editor.review_draft(args.slot)
    if action == 'rebase':
        # The reviewed JSON is the same optimistic-concurrency check as the UI.
        review = json.loads(args.review_file.read_text(encoding='utf-8-sig'))
        return editor.rebase_draft({'slot': review['entry']['slot'], 'revision': review['entry']['revision'],
                                    'base': review['base']})
    if action == 'discard':
        return editor.discard_draft({'slot': args.slot, 'revision': args.revision})
    if action == 'export':
        content = editor.export_english() if args.english else editor.export_locales(args.locale)
        with args.output.open('xb') as handle:
            handle.write(content)
        return {'output': str(args.output)}
    if action == 'backup':
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump({'schema_version': 1, 'entries': editor.drafts.entries()}, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        return {'output': str(args.output)}
    if action == 'apply':
        if args.target in ('game', 'all') and args.game_folder is None:
            raise CatalogError('--game-folder is required for Game/All Apply from the CLI')
        editor.start_apply(target=args.target)
        previous = None
        while editor.save_state.get('state') == 'running':
            phase = editor.apply_state.get('phase')
            if phase != previous:
                print(phase, file=sys.stderr, flush=True); previous = phase
            time.sleep(.1)
        result = editor.apply_state
        if result['state'] == 'error':
            raise CatalogError(result['error'])
        return result
    raise CatalogError('Unknown editor operation')


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--game-folder', type=Path, help='Explicit matching Civ V installation for Game/All Apply')
    commands = result.add_subparsers(dest='action', required=True)
    for name in ('status', 'drafts', 'save', 'saves', 'restore', 'undo', 'redo', 'issues'):
        commands.add_parser(name)
    rows = commands.add_parser('rows'); rows.add_argument('--locale'); rows.add_argument('--search', default='')
    rows.add_argument('--category', default='all')
    draft = commands.add_parser('draft'); draft.add_argument('--key', required=True); draft.add_argument('--locale')
    texts = draft.add_mutually_exclusive_group(); texts.add_argument('--text'); texts.add_argument('--text-file', type=Path)
    for field in ('gender', 'plurality', 'note', 'identifier'):
        draft.add_argument('--' + field)
    draft.add_argument('--index', type=int); draft.add_argument('--create', action='store_true')
    draft.add_argument('--remove', action='store_true', help='Draft a translation removal or an unreferenced English deletion')
    commands.add_parser('load').add_argument('save_id', type=int)
    commands.add_parser('review').add_argument('--slot', required=True)
    commands.add_parser('rebase').add_argument('--review-file', type=Path, required=True)
    discard = commands.add_parser('discard'); discard.add_argument('--slot', required=True); discard.add_argument('--revision', type=int, required=True)
    accept = commands.add_parser('accept-formatting'); accept.add_argument('--slot', required=True)
    accept.add_argument('--revision', type=int, required=True); accept.add_argument('--source-fingerprint', required=True)
    canonical = commands.add_parser('accept-project-formatting', help='Approve an intentional mismatch in an IDE-edited translation CSV')
    canonical.add_argument('--locale', required=True); canonical.add_argument('--key', required=True)
    apply = commands.add_parser('apply'); apply.add_argument('--target', choices=('project', 'game', 'all'), default='project')
    export = commands.add_parser('export'); selection = export.add_mutually_exclusive_group(required=True)
    selection.add_argument('--locale', action='append'); selection.add_argument('--english', action='store_true')
    export.add_argument('--output', type=Path, required=True)
    commands.add_parser('backup').add_argument('--output', type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    cli = parser(); args = cli.parse_args(argv)
    try:
        from editor_server import Editor, running_editor_session
        if (WORKSPACE / 'editor-session.json').is_file() and running_editor_session():
            raise CatalogError('Close the LLE session for this checkout before using editor_cli.py; direct IDE edits remain available')
        if args.action == 'accept-project-formatting':
            output = accept_project_formatting(args.locale, args.key)
        else:
            preferences = {'mode': 'developer', 'project_path': str(REPO_ROOT),
                           'game_path': str(args.game_folder.resolve()) if args.game_folder else ''}
            # Keep stdout machine-readable; preparation reports belong on stderr.
            with redirect_stdout(sys.stderr):
                editor = Editor(preferences=preferences)
                if not editor.ready:
                    raise CatalogError(editor.connection_error)
                output = execute(editor, args)
        print(json.dumps(output, ensure_ascii=False, indent=2))
    except (CatalogError, OSError, ValueError, KeyError) as error:
        cli.exit(1, f'LLE command failed: {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
