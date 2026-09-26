# Lekmod localization

This folder contains the English source, translation CSVs, a local browser
editor, and contributor tools. The game reads the generated language sections
in `LEKMOD/Override/CIV5Units_Mongol.xml`.

## Quick start for a translator (Windows, no IDE)

1. Use a Git clone of the project. GitHub's "Download ZIP" omits the Git
   history needed for row dates and some source scans. Install Git for Windows
   and Python 3.10 or newer if necessary; no VS Code or Python packages are
   needed. Ask a maintainer for the matching full vanilla snapshot
   and place it at `localization/workspace/vanilla-snapshot.json.gz`. If you
   used the earlier version of these tools, your existing snapshot and drafts
   in `build/localization` move automatically on first launch.
2. Double-click `localization/start_editor.cmd`. Leave the command window
   open; your browser should open `http://127.0.0.1:8765/` on the same PC.
   If the browser does not open by itself, type that address into it. If the
   command window reports a missing or mismatched snapshot, ask the
   maintainer for the pinned version. Do not substitute a different game DB.
3. Pick a language (for example `RU_RU`) and a category. Search for a key
   or words. Compare **Vanilla EN**, **Vanilla translation**, **Lekmod EN**,
   and **My translation**. Select a row, type your translation, preserve tokens
   such as `[ICON_...]` and `{1_...}`, then click **Save and apply**. The
   editor writes the approved CSV and regenerates the game XML. Saving an
   empty translation removes its approval. **Primary · English** edits the
   canonical English source and is usually a maintainer task.
4. **Undo saved edit** and **Redo saved edit** reverse saved changes from this
   editor session (up to 20; they reset when the command window closes).
   Browser/textarea Ctrl+Z handles typing before a save. **Download translation
   ZIP** produces one language CSV plus a manifest for a developer to review;
   attach the ZIP to a message or review yourself. It does not send anything.
   **Download test game XML** downloads the generated Override file, not a
   complete mod. Use a separate local Lekmod test installation and follow its
   normal installation instructions before checking text in game.
5. Close the command window, or press Ctrl+C in it, when finished.

Columns can be hidden and resized, long lines can wrap, and the table shows
60 results per page. Selecting a row displays its English source's last
**committed** Git date. A new uncommitted edit has no Git date. A changed
English row keeps the old translation for review but labels it `stale`; the
game uses English until someone reviews and saves the translation again.

The translator ZIP contains approved text, not draft notes from other rows or
the private vanilla snapshot. The developer compares its manifest, copies
`translations/<locale>.csv` into this folder, runs preparation and checks,
reviews the generated XML, and commits those files.

## Paths and ownership

| Path | Purpose | Git |
| --- | --- | --- |
| `en_US/primary.xml` | Editable canonical English Civ V operations | Track |
| `translations/<locale>.csv` | Approved translations and source fingerprints for nine languages | Track |
| `reference/vanilla-fingerprints.json.gz` | Shared baseline hashes of English fields and ten locale tables | Track |
| `config.json` | `On`/`Off` steps and English help text | Track |
| `editor/index.html`, `tools/`, `start_editor.cmd` | Contributor editor, launcher, generators and tests | Track for development |
| `workspace/editor/<locale>/*.csv` | Generated comparison tables and local drafts | Ignore |
| `workspace/review/`, `workspace/catalog.json` | Generated reports and source conflicts | Ignore |
| `workspace/vanilla-snapshot.json.gz` | Full local vanilla text for comparison and preparation | Ignore |

`workspace/` is a local working directory. The first run moves the old
`build/localization` directory there if the new directory does not exist.
If both directories exist and the new snapshot is missing, move your snapshot
and drafts manually before launching the editor. Do not edit the pinned hash
reference during ordinary translation work. Its content checks prevent a
developer's different or mixed game installation from silently changing the
shared baseline.

The full snapshot includes official game text. A public pull request exposes
its files immediately; later deleting a file from the branch does not remove
it from Git history or downloaded copies. Keep the full snapshot out of a
public PR unless the team has permission to redistribute it. Maintainers can
provide the same verified file through an appropriately restricted channel,
or a contributor can generate one from a clean game installation and verify
it against the tracked hashes. The optional creation command is:

```text
python -B localization/tools/freeze_vanilla.py --vanilla-db "PATH_TO_Localization-Merged.db"
```

If the team explicitly changes the vanilla base version, review the new full
snapshot and run `freeze_vanilla.py --refresh-reference` in a separate change.
The pinned hash reference alone cannot show the original vanilla words in the
browser, so the full local snapshot is needed for editing.

## Developer workflow

Edit English through **Primary · English** or directly in
`en_US/primary.xml`. Do not hand-edit its generated block in
`LEKMOD/Override/CIV5Units_Mongol.xml`. After a direct edit run:

```text
python -B localization/tools/manage.py prepare
python -B localization/tools/manage.py check
git diff --check
```

Commit the changed English source, approved CSVs, and generated game XML.
`prepare` synchronizes English, refreshes editor/review CSVs, and builds the
nine target-language sections. Balance values in gameplay files cannot tell
the tool whether prose should change; a maintainer must review affected text.
English XML is canonical: Git checkouts change filesystem timestamps, so
there is no reliable last-modified-wins rule between independent XML and CSV
copies. The browser editor changes that same canonical XML.

The `_help` section of `config.json` describes each switch in English. JSON
does not support `//` comments, so the descriptions are regular JSON data.
Only switch values `On` and `Off`. `checks` controls audits, inventory, tests,
and the generated XML check; `build` controls steps in `prepare`. `Off` skips
a step; it does not remove previously generated game text. Normally leave
all switches `On`. The individual scripts in `tools/` remain available when
diagnosing a failure.

GitHub Actions runs `manage.py check --ci` after pushes and on pull requests.
It checks the committed output but cannot rewrite the commit. CI uses the
tracked vanilla hashes; local preparation uses the matching full snapshot.
Existing Art localization stays in its game location. The 126 English source
conflicts remain withheld in `workspace/review/source-conflicts.json` until
reviewed; record decisions and in-game checks in `CHANGE_REVIEW.md`.

## FAQ for maintainers

### Why is the full vanilla snapshot absent from this public PR?

It contains complete language tables copied from the game, including the
original text for ten locales. Take-Two's [terms of service](https://www.take2games.com/legal/en-US/)
reserve rights in game text and limit copying and distribution without
separate permission. We do not assume that an absence of complaints grants
permission. A public PR exposes an uploaded file immediately. Deleting it in
a later commit does not remove old Git objects, clones, forks, or cached PR
views; see [GitHub's history-removal guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
If maintainers obtain the relevant redistribution permission, they can
propose the full snapshot in a separate reviewed change. A note in a README
is not a substitute for that permission.

### How can every developer use one baseline if its text is not in Git?

The tracked `reference/vanilla-fingerprints.json.gz` pins hashes of all
24,525 vanilla English keys plus content fingerprints for all ten locales.
The local snapshot is checked against **all** those locale fingerprints before
preparation. A different or mixed DB fails rather than silently changing the
catalog or game XML. CI needs only the tracked reference to classify English
changes and verify the generated XML; it cannot show vanilla sentences in
the editor. That side-by-side view requires the matching full local snapshot.

### What exactly must I obtain and where do I put it?

Obtain the matching `vanilla-snapshot.json.gz` from an authorized team source
and place it at `localization/workspace/vanilla-snapshot.json.gz`. Alternatively,
generate it **once** from a clean, matching game's `Localization-Merged.db`
with the command above. The editor and `manage.py prepare` verify it against
the pinned reference. Each developer does not choose an independent baseline;
every accepted copy must contain the same tables. The snapshot is not part
of a normal commit or player release. Its private sharing must itself follow
whatever permissions the team has for the game text.

### The editor says the snapshot is missing or does not match. What now?

For a missing file, check the exact `localization/workspace/` path. An old
`build/localization` directory is moved automatically if the new workspace
does not already exist. If both directories exist, move the old snapshot and
draft CSVs yourself. For a mismatch, check the game version, patch level,
mods, and database source, or get the matching snapshot from a maintainer.
Do **not** run `--refresh-reference` just to suppress this error: that would
change the team baseline and could reclassify thousands of strings.

### When should the pinned baseline itself change?

Only when the maintainers deliberately adopt a different official vanilla
version. Review a clean complete snapshot, regenerate its fingerprints in a
separate PR, inspect changed classifications and all generated language
sections, and coordinate the matching local full file for contributors.
Ordinary balance changes and translations do not update the baseline.

### Which files do I commit after editing?

Commit changed `localization/en_US/primary.xml`, approved
`localization/translations/*.csv`, and the generated
`LEKMOD/Override/CIV5Units_Mongol.xml` when applicable. Run `manage.py prepare`
after a direct English edit, then `manage.py check`. Never force-add
`localization/workspace/`: it holds local reports, drafts, and the full
snapshot. The CI check rejects a tracked workspace file. Changes to the
generated game XML by hand are overwritten by the next build.

### A translator sent me a ZIP. What should I review?

Read `manifest.json` for the repository commit and baseline SHA-256; make
sure the language and source match your project. Review the CSV's translation
and formatting tokens, then copy its `translations/<locale>.csv` into
`localization/translations/`. Run `manage.py prepare` and `manage.py check`,
review the resulting XML diff, and commit. The ZIP does not contain a
vanilla snapshot or replace the full Lekmod installation.

### Can someone translate without a coding environment or use an EXE?

No IDE is needed for this version: `start_editor.cmd` opens the browser, but
the machine still needs Python, Git, a project checkout, and a matching local
snapshot. A standalone Windows EXE is a separate packaging task: it needs a
portable data bundle, a supported way to provide vanilla text, and a safe
test-install path. Do not describe this launcher as a standalone EXE.

## Contributor files and player releases

The public source repository contains tracked development tools. `.gitignore`
cannot hide or erase a file already committed to Git; deleting it in a later
release also leaves it in earlier history. For a player download, package
the required mod files from `LEKMOD` (and Lekmap if that release needs it),
not this entire repository. The localization editor, Python scripts, CSVs,
tests, and local snapshot are contributor materials, not game dependencies.
