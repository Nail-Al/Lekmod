# Lekmod localization

This directory is for contributors. The game loads the generated language
sections in `LEKMOD/Override/CIV5Units_Mongol.xml`. Players installing the
mod need the `LEKMOD` distribution folder, not Python, CSV, this editor, or
the private vanilla reference. A public GitHub repository still exposes
tracked development files: `.gitignore` cannot conceal them. Assemble a
player release from `LEKMOD` (and Lekmap if wanted), not from the repository
root.

## One place for contributor files

| Path | Purpose | Include in Git? |
| --- | --- | --- |
| `en_US/primary.xml` | Editable canonical English; ordered Civ V operations | Yes |
| `translations/<locale>.csv` | Approved translations: key, English fingerprint, text, grammar, note | Yes |
| `reference/vanilla-fingerprints.json.gz` | Pinned hashes of vanilla English fields and all 10 language tables; contains no original sentences | Yes |
| `config.json` | Plain `On`/`Off` switches for preparation and checks | Yes |
| `editor/index.html`, `tools/` | Browser UI, local server, generators, audits, tests | Yes, for developers |
| `workspace/editor/<locale>/*.csv` | Rich comparison with vanilla EN and target text; editable drafts | No |
| `workspace/review/`, `workspace/catalog.json` | Source conflicts and reports | No |
| `workspace/vanilla-snapshot.json.gz` | Full vanilla text required by the editor and local preparation | No |

The first run moves the old `build/localization` workspace into
`localization/workspace`, including your snapshot and CSV drafts. It refuses
to overwrite an existing new workspace. If both locations already exist,
move your draft files and snapshot yourself before running a command.

## Translator: three steps

Run commands from the repository root, with Python 3.10 or newer. No pip
packages are required.

1. Obtain the team's full snapshot privately, or create one from a clean
   game installation once:
   `python -B localization/tools/freeze_vanilla.py --vanilla-db "PATH_TO_CLEAN_Localization-Merged.db"`.
   If you already have a snapshot in the old `build/localization`, skip this.
   A different or mixed snapshot is rejected against the tracked reference.
2. Start the browser editor:
   `python -B localization/tools/editor_server.py`. Open the printed
   `http://127.0.0.1:8765/` address in the **same computer**. Stop with
   Ctrl+C. A standalone `file://` page cannot save CSV files in Firefox;
   this standard-library server is local only.
3. Select `RU_RU` or another language and a category; search a key or text.
   Compare `Vanilla EN`, `Vanilla перевод`, `Lekmod EN`, and `Мой перевод`.
   Save a translation to validate tokens and write both its workspace CSV
   and the small tracked `translations/<locale>.csv`. The local server also
   rebuilds the loaded game XML. A blank translation removes the approval.

You can hide columns, resize headers and text fields, toggle line wrapping,
search, and move through 60 rows at a time. The `Primary · английский`
category edits the canonical English XML itself. The displayed date comes
from the last **committed** edit to that XML line in Git; an uncommitted edit
has no Git date. A balance change in gameplay code does not by itself tell
us whether text should change.

Each translation is pinned to a fingerprint of its English source. If the
English row changes, its old translation is kept for review, labelled
`stale`, and withheld from the next game build. English is displayed in
the game until you review and save the translation again. Preserve Civ V
tokens such as `[ICON_...]` and `{1_...}`; invalid tokens cannot be saved.

The rich CSVs contain official vanilla strings and stay in the ignored
workspace. The tracked CSVs contain only Lekmod keys and your own approved
translation text, not copied vanilla tables. The pinned reference is the
same for every contributor and CI; hashes cannot show the original text, so
the browser still needs a matching private full snapshot. Do not update the
pinned reference as part of ordinary translation work. If the team explicitly
changes the official base version, review a new full snapshot and run
`python -B localization/tools/freeze_vanilla.py --refresh-reference` once
in a separate change. Existing Art localization
outside this directory remains in place; 126 English source conflicts are
withheld for developer review in `workspace/review/source-conflicts.json`.

## Developer: edit, build, push

You can edit `en_US/primary.xml` directly or through the editor. The
generated English block in `LEKMOD/Override/CIV5Units_Mongol.xml` must not
be edited by hand. After a direct edit, run one command:

```text
python -B localization/tools/manage.py prepare
```

It runs the enabled steps in `config.json`: synchronize English to the
loaded XML, refresh the comparison CSVs, and rebuild shipped translations.
It requires your local vanilla snapshot for the last two steps. The browser
editor invokes this preparation when it starts or changes Primary. No
format based on filesystem modification time wins over another: a checkout
changes timestamps. The XML is canonical; the editor edits that same XML.

Before pushing, run:

```text
python -B localization/tools/manage.py check
git diff --check
```

Commit `localization/en_US/primary.xml` if changed, any approved
`localization/translations/*.csv`, and the generated
`LEKMOD/Override/CIV5Units_Mongol.xml`. Do not commit
`localization/workspace/`; it contains a private copy of official game
strings. Game verification and source decisions belong in
`CHANGE_REVIEW.md`.

`config.json` accepts exactly `On` or `Off` for each named check or build
step. `checks` controls audits, inventory, tests, and shipped verification.
`build` controls what `prepare` updates. Normally leave them all `On`;
switch off one named action only when you intend to skip it. An `Off` does
not remove existing game text, and a skipped check cannot validate it.
The individual scripts remain available under `tools/` for debugging.

GitHub Actions runs `manage.py check --ci` on push and pull request. It is
**read-only**: it does not rewrite a pushed commit. The tracked fingerprint
reference lets CI regenerate and compare all 6,035 fallback keys without
publishing official vanilla sentences. The local `check` additionally
verifies the full private snapshot. Save or `prepare` before committing;
the later in-game test still checks actual load order and UI.

The full snapshot copies official game text. Take-Two's
[terms](https://www.take2games.com/legal/en-US/) reserve rights in game text
and restrict distribution without separate permission. The repository keeps
the shared fingerprints, while the full-text file is transferred only within
the team by an authorized method. This is also why `workspace/` is ignored.
