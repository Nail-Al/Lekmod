# Lekmod localization

This folder contains the English source, translation CSVs, a local browser
editor, and contributor tools. The game reads the generated language sections
in `LEKMOD/Override/CIV5Units_Mongol.xml`.

## Quick start for a translator (Windows)

1. Open [Localization Editor for Windows](https://github.com/Nail-Al/Lekmod/actions/workflows/localization-editor-windows.yml?query=branch%3Alocalization-infrastructure),
   choose the latest successful run on `localization-infrastructure`, and
   download the `LekmodLocalizationEditor-Windows` artifact at the bottom.
   Unzip the downloaded artifact, then unzip the
   `LekmodLocalizationEditor-Windows.zip` inside it into a new folder. A
   maintainer may also send you that inner ZIP directly. No Git, Python,
   VS Code, or installed game is required to start the editor.
2. Double-click `LekmodLocalizationEditor.exe` in the extracted folder.
   Leave its command window open. The browser opens automatically; if it
   does not, use the `http://127.0.0.1:<port>/` address shown in the window.
   The app chooses an available port. Do not move the EXE away from its
   neighboring `localization` and `LEKMOD` folders.
3. Pick a language (for example `RU_RU`) and a category. Search for a key
   or words. Compare **Vanilla EN**, **Vanilla translation**, **Lekmod EN**,
   and **My translation** when original vanilla text is available. Select a
   row, type your translation, preserve tokens
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
5. Click **Close editor** when finished, or close its command window. Your saved
   CSV remains in `localization/translations/` inside that extracted folder.

The download contains a shared **hash baseline**, so you can translate Lekmod
rows immediately. For the original vanilla sentences, copy the team's matching
`vanilla-snapshot.json.gz` to `localization/workspace/` inside the extracted
folder before starting the editor again. A different snapshot is refused.
The currently pinned snapshot has English and Russian entries, but its other
eight locale tables are empty. In those languages, the vanilla translation
column remains unavailable even when the snapshot is present. This limitation
will require a separately reviewed baseline update from complete official
language databases; do not treat a blank cell as proof that the game lacks a
translation.

Columns can be hidden and resized, long lines can wrap, and the table shows
60 results per page. Selecting a row displays its English source's last
**committed** Git date when using a developer clone. The portable download
has no Git history and marks this date unavailable. A changed
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
| `reference/vanilla-fingerprints.json.gz` | Shared baseline hashes of English fields and ten locale tables (eight currently empty) | Track |
| `config.json` | `On`/`Off` steps and English help text | Track |
| `editor/index.html`, `tools/`, `start_editor.cmd` | Contributor editor, source launcher, generators and tests | Track for development |
| `workspace/editor/<locale>/*.csv` | Generated comparison tables and local drafts | Ignore |
| `workspace/review/`, `workspace/catalog.json` | Generated reports and source conflicts | Ignore |
| `workspace/vanilla-snapshot.json.gz` | Full local vanilla text for comparison and preparation | Ignore |

The portable EXE is built by GitHub Actions and delivered as an artifact; its
ZIP includes the data files used by the editor, but excludes the Python tools,
tests, complete vanilla text, and unrelated game assets. It is a translation
workspace, not a complete mod installation. A developer working in a Git
clone can still double-click `localization/start_editor.cmd` with Python 3.10+
and Git installed. This source launcher also chooses an available local port.

`workspace/` is a local working directory. The first run moves the old
`build/localization` directory there if the new directory does not exist.
If both directories exist and the new snapshot is missing, move your snapshot
and drafts manually before launching the editor. Do not edit the pinned hash
reference during ordinary translation work. Its content checks prevent a
developer's different or mixed game installation from silently changing the
shared baseline.

The local snapshot includes official game text. A public pull request exposes
its files immediately; later deleting a file from the branch does not remove
it from Git history or downloaded copies. Keep the full snapshot out of a
public PR unless the team has permission to redistribute it. Maintainers can
provide the same verified file through an appropriately restricted channel,
or a contributor can generate one from a clean game installation and verify
it against the tracked hashes. The optional creation command is:

```text
python -B localization/tools/freeze_vanilla.py --vanilla-db "PATH_TO_Localization-Merged.db"
```

If the team explicitly changes the vanilla base version, review the new
snapshot and run `freeze_vanilla.py --refresh-reference` in a separate change.
The pinned hash reference cannot show original vanilla words in the browser.
Translations and English source classification still work without that file.

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

This specific file copies roughly 24,525 English and 24,524 Russian game
entries; its other eight locale tables are empty. It is materially different
from distributing only original, fan-written Lekmod text. 2K's
[fan-content policy](https://support.2k.com/hc/en-us/articles/201335153-Policy-on-posting-copyrighted-2K-material)
generally tolerates some noncommercial fan uses, but discusses footage and
fan sites, not publication of a whole extracted language table. Take-Two's
[terms of service](https://www.take2games.com/legal/en-US/)
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
Only the English and Russian locale tables contain text in the pinned source;
the other eight empty-table fingerprints cannot supply missing translations.
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

Yes. Download and extract the Windows artifact as described above. Its EXE
includes Python internally and runs against its neighboring portable project
files. You can edit and export translations without Git, Python, an IDE, a
game installation, or a local vanilla snapshot. Git commit dates and original
vanilla sentences need the corresponding Git clone and local snapshot,
respectively. The ZIP is a contributor tool; it does not install the complete
mod in Civilization V.

## Contributor files and player releases

The public source repository contains tracked development tools. `.gitignore`
cannot hide or erase a file already committed to Git; deleting it in a later
release also leaves it in earlier history. For a player download, package
the required mod files from `LEKMOD` (and Lekmap if that release needs it),
not this entire repository. The localization editor, Python scripts, CSVs,
tests, and local snapshot are contributor materials, not game dependencies.
