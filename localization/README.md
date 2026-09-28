# Lekmod localization

The editor is for contributors. Civilization V reads the generated language sections in `LEKMOD/Override/CIV5Units_Mongol.xml`; player packages need the mod, not the editor, Python tools, tests, or private workspace.

## Translator: start without an IDE

1. Download the ZIP from the latest [Windows editor release](https://github.com/Nail-Al/Lekmod/releases?q=editor-v), extract it into a writable folder, and run `LekmodLocalizationEditor.exe`. The editor opens in your browser without a console window; use **Settings → Quit editor** when finished. No Git, Python, or game installation is required to translate.
2. Open **Settings**. Select an existing checkout of this [localization branch](https://github.com/Nail-Al/Lekmod/tree/localization-infrastructure), or choose compatible `v35.3`, click **Download**, wait for the progress bar, and click **Save connections**. Downloads go into the editor's `localization/workspace/projects/v35.3` folder; a valid project there is reused, never overwritten. The upstream mod does not yet have this localization system. A game installation by itself is not a project.
3. Choose **Translator**, language and category. Search or select a row, edit **My translation text**, and click **Save and apply**. This changes `localization/translations/<language>.csv` in the connected project and rebuilds its game XML. It does **not** copy anything into an installed game or send anything online. Blank translation text removes an existing approval.
4. If you do not use Git, open **Exports → Download translation ZIP** and give that ZIP to a maintainer for review. If your connected project is a Git checkout, you may instead review the changes there and commit/push them on your contributor branch. Do not share the private `localization/workspace/` folder.

The English column is the current Lekmod text. Optional **Vanilla EN** and **Vanilla <language>** columns show official game text only after importing the matching snapshot. The language column is off initially; enable it through **Columns** when data exists. Its name follows the selected language. **The current shared snapshot contains vanilla sentences for EN and RU only**; the other eight tables are empty. An empty cell in those languages says nothing about the actual game translation. See **Completing the team baseline** below.

**Status:** `missing` means no approved translation; `stale` means English changed since approval and the game falls back to English; `applied` means the CSV text is in the project's generated XML; `saved` means XML generation is Off. Neither `applied` nor `saved` means the installed game was updated. Formatting tokens such as icons, colors, and `{1_Name}` must keep their names and counts; the editor checks them. Character counts are information, not a length limit. Notes stay in the CSV and do not appear in game.

Use the filter icon for type/status/edit dates, the column icon to show columns, and drag a column's right edge to change its width. **Done** saves column choices; **×** or Escape cancels them. The table scrolls inside its own area. Column widths, visible columns per mode, page size, wrapping, language and other settings survive a normal editor update. **Save and apply** becomes available only after you change the selected row. If you change a row and navigate away, choose **Save and continue**, **Discard and continue**, or **Keep editing**. Undo/redo reverse saved edits from the current session; the trash icon discards only the current unsaved form. **Logs** lists actions and redacted warnings/errors without recording passwords or translation text.

## Developer: English source and team handoff

The canonical English text and operations are in `localization/en_US/primary.xml`. Its `Language_en_US` block in `LEKMOD/Override/CIV5Units_Mongol.xml` is generated and must not be edited directly. In **Developer** mode, select an English row and change its text or **Change TXT_KEY identifier**, then use the single **Save and apply** button. A rename is allowed only for a key with no gameplay references or approved translations; the text and ID are saved together. **Create new key** opens a separate form showing the destination file and insertion line. New IDs use `Row`; `Replace` is for existing game keys and cannot be selected for a new ID. A new key must also be referenced by gameplay XML, SQL, or Lua before it appears in game. Changing English makes old approvals stale until reviewed. An editor user without Git can use **Exports → Download English source ZIP**; a maintainer must compare the included file with their current branch and merge only the intended changes.

Developers who prefer VS Code can edit `primary.xml` and `localization/translations/*.csv` in a **Git checkout**. After direct edits, run this from the repository root:

```powershell
python -B .\localization\tools\manage.py prepare
python -B .\localization\tools\manage.py check
git diff --check
```

`prepare` refreshes generated XML and review data; `check` runs enabled tests and audits. `localization/config.json` describes each `On`/`Off` switch under `_help`. CI checks committed outputs; it cannot fix a contributor's files automatically. **Run checks** in an editor connected to a Git checkout with Python and Git runs this full suite. In a portable downloaded project without `.git`, it runs the checks that do not require Git and explicitly names the skipped inventory and unit-test gates. A maintainer must still run the complete suite in a checkout before merging.

Each contributor should work in a separate checkout or branch. The editor writes to whichever project is connected; do not let an IDE and an editor make unreviewed simultaneous writes to the same working directory. The editor rejects a translation save if its English source or approved CSV changed since the row was loaded. It cannot merge two contributors' conflicting Git pushes: review the differing keys, resolve any Git conflict, regenerate, check, and then commit. For handoff ZIPs, check the manifest's source commit and vanilla reference hash, review changed CSV rows or English operations, and merge changes rather than replacing newer team files. No export includes the private vanilla text.

| File | Role | Commit to contributor branch? |
| --- | --- | --- |
| `localization/en_US/primary.xml` | Canonical English | Yes |
| `localization/translations/*.csv` | Reviewed translations, fingerprints and notes | Yes |
| `localization/reference/*.json.gz` | Shared text-free fingerprints and English date index | Yes |
| `LEKMOD/Override/CIV5Units_Mongol.xml` | Generated game language sections | Yes |
| `localization/editor/`, `localization/tools/`, `localization/config.json` | Contributor UI, generators, tests, configuration | Contributor source only; omit from player downloads |
| `localization/workspace/` | Private snapshot, settings, projects, backups and logs | Never |

## Shared vanilla reference

The tracked `reference/vanilla-fingerprints.json.gz` pins one team baseline without publishing official game sentences. The full `vanilla-snapshot.json.gz` stays in each contributor's ignored workspace. In **Settings → Vanilla reference**, the editor prefills the team's [encrypted Dropbox download link](https://www.dropbox.com/scl/fi/dquiyoh5k77v8qhip4u70/vanilla-snapshot.enc?rlkey=fwcgddzwanyaljhe9tja6ytk1&dl=1). Ask a maintainer for the separate password (at least 16 characters), enter it, then click **Download, decrypt and verify**. The editor checks the snapshot against the pinned reference before using it. Clear the link with the × button if your team uses a different location. The link is saved; **the password is never saved**. The verified local snapshot remains through editor updates. When a reviewed team baseline supersedes it, the editor opens without the old reference and asks for the new encrypted copy; importing it backs up the old snapshot.

Maintainers may import an already verified local `.json.gz` in Settings and use **Encrypt and download .enc** to prepare a new encrypted copy. Upload only `.enc`, and share its password separately. A contributor using a terminal can fetch the same direct `https://...&dl=1` link with `python -B .\localization\tools\snapshot_cloud.py fetch --url "<direct link>"`; install a current prebuilt `cryptography` package for that interpreter. Windows ARM64 with Python 3.14 may need a separate x64 Python for the current package, or use the Windows editor without installing Python packages. If you already have the verified snapshot, do not rebuild it from a possibly different game database. A new shared baseline needs a separate review of every locale.

The full snapshot copies official game text. Keep it out of this public Git history unless the team has permission to redistribute it. Deleting it in a later commit would not remove earlier public copies. Anyone holding both the download link and password can decrypt it; give the password only to intended collaborators.

### Completing the team baseline (maintainers)

The current reference already contains English and Russian. Keep the full `localization/workspace/vanilla-snapshot.json.gz` beside these tools; do not remake English from a different game build. Close the game, keep its DLC unmodded, and work in a current Git checkout. In PowerShell, find the cache database (select the correct path if several are shown):

```powershell
Set-Location C:\Projects\Lekmod
Get-ChildItem "$env:USERPROFILE\Documents\My Games" -Recurse -File `
  -Filter Localization-Merged.db -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty FullName
$mergedDatabase = 'C:\path\shown\above\Localization-Merged.db'
py -3.13 -B .\localization\tools\collect_vanilla.py inspect --vanilla-db $mergedDatabase
```

Start with **English** in Steam and confirm `en_US` shows at least 1,000 rows and `matches baseline`. Russian is already pinned too; there is no new EN or RU capture to make. For each missing locale below, choose that language in **Steam Library → Civilization V → Properties → Language** (some Steam layouts put it under **General**), wait for any download, launch the **unmodded** game once, then close it. Run `inspect` again and confirm that locale has at least 1,000 rows and English still matches. If a locale was already populated in the clean cache, capture it without changing Steam language.

| Steam language | `--locale` |
| --- | --- |
| German | `DE_DE` |
| Spanish - Spain | `ES_ES` |
| French | `FR_FR` |
| Italian | `IT_IT` |
| Japanese | `JA_JP` |
| Korean | `KO_KR` |
| Polish | `PL_PL` |
| Traditional Chinese | `ZH_HANT_HK` |

After each inspection, save **that one** language using `py -3.13 -B .\localization\tools\collect_vanilla.py capture --vanilla-db $mergedDatabase --locale DE_DE`, replacing `DE_DE` with its table code. The collector creates a separate ignored `localization/workspace/vanilla-captures/<locale>.json.gz` and refuses an existing filename, changed English, or an incomplete table. If it fails, resolve the reason; do not overwrite an earlier capture. Changing Steam language does not replace captures already saved in the workspace.

After all eight captures, run `py -3.13 -B .\localization\tools\collect_vanilla.py merge`. It creates **proposals** in `localization/workspace/`: a full `vanilla-snapshot-proposed.json.gz` and a text-free `vanilla-fingerprints-proposed.json.gz`. It refuses missing languages and never overwrites the current baseline. Review the counts for all ten languages, then encrypt the proposed full snapshot without changing the current reference yet:

```powershell
py -3.13 -B .\localization\tools\snapshot_cloud.py encrypt `
  --input .\localization\workspace\vanilla-snapshot-proposed.json.gz `
  --reference .\localization\workspace\vanilla-fingerprints-proposed.json.gz `
  --output .\localization\workspace\vanilla-snapshot-complete.enc
```

Upload **only** the new `.enc` to Dropbox, copy its direct link (`dl=1`), and verify the download with `py -3.13 -B .\localization\tools\snapshot_cloud.py fetch --reference .\localization\workspace\vanilla-fingerprints-proposed.json.gz --url "<direct link>" --output .\localization\workspace\verified-proposal.json.gz`. Enter the password at the terminal prompt, never as a command argument. After team review, replace the tracked fingerprint index with the proposal, install the proposed full snapshot locally, update the editor's team link, and release those changes together. Keep the old encrypted archive until collaborators have migrated. Ordinary translators only enter the team's link and password; they never need to switch game languages.

## Updates and game test

Use **Settings → Editor updates → Check latest version → Download and update**. The Windows editor displays download progress in the browser. The downloaded GUI `.exe` verifies the installed files, replaces only the EXE and listed UI files **in the same folder**, and restarts the editor on the **same browser address**, so this tab reloads automatically. No command window or PowerShell script is needed. Projects, preferences and `localization/workspace/` remain in place. If the new editor fails to serve its page, the updater restores the backup and restarts the previous version. Details are in **Logs** and `localization/workspace/editor-updates/update.log`; startup errors also go to `localization/workspace/editor-startup.log`. Use **Settings → Quit editor** to stop the background process when finished.

An already installed **legacy** EXE with broken updater code cannot repair itself retroactively. For that **one-time recovery**, close it, update your Git checkout, and run `python -B localization/tools/repair_editor.py --editor-root "C:\path\to\the\old\editor"`. This command downloads and verifies the release, installs it in the same folder, and starts it; no manual ZIP download or workspace move is needed. Normal updates thereafter use the button in Settings. If the old EXE has no complete folder, use the release ZIP once to establish one; do not delete a folder containing your downloaded project.

For an in-game test, install one complete matching Lekmod DLC with its official launcher first. In Settings choose the Civilization V folder and **Find / verify game**. Green means one Lekmod DLC was found; yellow means the game has no Lekmod; red means the game folder is invalid. **Save connections** checks that its version matches the project. After saving a translation, click **Apply to installed game**: the editor compares versions and gameplay data, backs up the installed XML, copies the generated XML, and asks you to restart Civilization V. It does not install a full mod. The game is optional for editing or exporting.

**Need help?** A missing source means select a complete compatible project, not the plain game folder. An empty vanilla cell can mean the snapshot lacks that language. A blank edit date means there is no reliable history for that row; English commit dates are indexed in the project, and new editor saves record their time. A rejected save after another contributor's edit protects their work: copy your draft, reload, and reconcile the changed row. A public Git ZIP download has no Git history, so developers who need full checks should clone the branch.
