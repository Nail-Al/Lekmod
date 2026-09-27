# Lekmod localization

This folder contains the English source, translation CSVs, a local browser
editor, and contributor tools. The game reads the generated language sections
in `LEKMOD/Override/CIV5Units_Mongol.xml`.

## Quick start for a translator (Windows)

1. Open [Localization Editor for Windows](https://github.com/Nail-Al/Lekmod/actions/workflows/localization-editor-windows.yml?query=branch%3Alocalization-infrastructure),
   choose the latest successful run on `localization-infrastructure`, and
   download the `LekmodLocalizationEditor-Windows` artifact at the bottom.
   Extract the inner `LekmodLocalizationEditor-Windows.zip` to a writable
   folder. A maintainer may also send you that ZIP directly. No Git, Python,
   VS Code, or installed game is required for the included translation source.
2. Double-click `LekmodLocalizationEditor.exe` in the extracted folder.
   Leave its command window open. The browser opens automatically; if it
   does not, use the `http://127.0.0.1:<port>/` address shown in the window.
   The app chooses an available port. Do not move the EXE away from its
   neighboring `localization` and `LEKMOD` folders.
3. On first launch, **Settings** opens. Leave **Lekmod project folder** blank
   to use the included source, select your own *compatible* full checkout,
   or choose the supported release and **Download selected source** into a
   separate folder. Click **Save connections**. The official upstream main
   branch does not yet contain this editor's localization files. Earlier
   releases need a reviewed migration before they can be edited here.
4. Choose **Translator**, a language, and a category. Select a row; its
   existing translation or English text appears in the edit box. Change it
   and click **Save and apply**. This writes the approved language CSV and
   generated game XML in the connected project. A blank translation removes
   approval. Keep every displayed formatting token (for example `[ICON_CULTURE]`,
   `[COLOR_YELLOW]`, and `{1_Num}`) with the same spelling and count. These
   are working game markup, and the editor validates them. **Missing** means
   no approved translation; **stale** means English changed and the game
   currently falls back to English until the translation is reviewed.
5. Optionally find Civilization V with **Detect game**, or browse for its
   installation folder, then **Check game folder** and select the installed
   Lekmod DLC. After saving, use **Apply to installed game**. The app checks
   the release and internal version, compares non-language game data, backs
   up the old XML under the editor folder's
   `localization/workspace/game-backups/`, and replaces
   only the matching Override XML. Restart the game to test. Install the
   matching full mod with the official Lekmod installer first; this editor
   does not install the complete mod.
6. Use the arrow icons for undo/redo of up to 20 saved edits in this session;
   Ctrl+Z inside the text box undoes typing. **Exports** can download a
   translation ZIP for a developer to review and a generated XML for manual
   testing. Exports do not send files anywhere. Close the browser tab and
   command window when finished. The CSV remains in your selected project.

The download contains a shared **hash baseline**, so you can translate Lekmod
rows immediately. For the original vanilla sentences, obtain the team's matching
`vanilla-snapshot.json.gz` and import it in **Settings**. A different snapshot
is refused. No network password is built into this editor.
The currently pinned snapshot has English and Russian entries, but its other
eight locale tables are empty. In those languages, the vanilla translation
column remains unavailable even when the snapshot is present. This limitation
will require a separately reviewed baseline update from complete official
language databases; do not treat a blank cell as proof that the game lacks a
translation.

Use **Columns** to hide fields and drag a table header's right edge to change
its width. These choices persist across launches. The table scrolls in both
directions inside its panel, including on narrow screens. The page buttons
stay above it. **Wrap lines** toggles multiline cells. **Fill the editor from
the selected row** can be turned off, and that choice persists. Gender and
plurality allow suggested values or free text, since game data may contain
other values. **Translator note** appears in its table column and in the
selected row form; it is stored in the CSV for reviewers, never shown in game.
The number below the edit box counts characters and whitespace without an
English-length limit. The table shows 60 results per page. Selecting a row
displays its English source's last
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

Switch the header from **Translator** to **Developer** in a connected full
project. Search and edit English, create a new `TXT_KEY_*` text entry, or
rename an unreferenced key. Adding a key to the dictionary does not make a
gameplay entity use it: add the reference in the relevant mod XML, SQL, or
Lua and review it. The editor blocks renaming a referenced key because its
gameplay references must be migrated together. Saving English runs
preparation and configured checks. Changing English makes older approved
translations stale; the generated game text falls back to English until
translation review. You can also edit `en_US/primary.xml` directly. Do not
hand-edit its generated block in
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
mod in Civilization V. The included source works without the game; optional
in-game testing requires an installed, matching Lekmod DLC release. The editor
can download its compatible source into a fresh folder and connect to your
own checkout; it will not overwrite either.

### Why is there no password `111` or automatic cloud snapshot download?

An embedded password shared with everyone using a public EXE cannot restrict
access to an external file. A link plus a known password would expose the
complete vanilla game text just as publishing the file would. The editor
accepts an explicitly provided local snapshot, checks it against pinned team
fingerprints, and keeps it outside Git. If the team sets up a private
authenticated store and grants access to contributors, its login and download
can be integrated without shipping shared credentials in the EXE.

## Contributor files and player releases

The public source repository contains tracked development tools. `.gitignore`
cannot hide or erase a file already committed to Git; deleting it in a later
release also leaves it in earlier history. For a player download, package
the required mod files from `LEKMOD` (and Lekmap if that release needs it),
not this entire repository. The localization editor, Python scripts, CSVs,
tests, and local snapshot are contributor materials, not game dependencies.
