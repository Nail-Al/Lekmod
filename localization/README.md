# Lekmod localization

This folder contains the canonical English text, approved language CSVs, the browser editor, the shared vanilla fingerprints, and contributor tools. Civilization V reads generated localization in `LEKMOD/Override/CIV5Units_Mongol.xml`. Players need the packaged mod files, not this editor or the contributor tools.

## Start the Windows editor (no programming tools required)

1. Download `LekmodLocalizationEditor-Windows.zip` from the latest [editor release](https://github.com/Nail-Al/Lekmod/releases?q=editor-v) and extract it into a writable folder. Double-click `LekmodLocalizationEditor.exe`. Keep its console window open while using the browser page. The editor version appears beside its title. You do not need Git, Python, VS Code, or an installed game to open the editor.
2. In **Settings → Lekmod project folder**, select a **complete** checkout of the [compatible localization branch](https://github.com/Nail-Al/Lekmod/tree/localization-infrastructure), if you have one. Otherwise choose the compatible `v35.3` entry and click **Download**, watch the progress and destination, then **Save connections**. You can cancel and retry. The download creates a separate project in `localization/workspace/projects/v35.3`; an existing validated project is reused, never overwritten. The upstream main branch and older releases do not yet include the matching localization system. A plain Civilization V installation alone is insufficient.
3. The source badge now names the connected project. Select **Translator**, a language and a category; search or pick a row. Change **My translation text** and click **Save and apply**. This saves the CSV in the connected project and rebuilds its game XML. A blank save withdraws that translation. **Developer** in the header opens the full English source, where developers may edit text or create a key. The mode button and Settings button are independent.
4. To test in the game, install the **matching complete Lekmod release** with its official launcher. In Settings, choose the Civilization V folder and click **Find / verify game**. Red means the folder is not a game; yellow means the game is present without Lekmod; green means one Lekmod DLC is installed. Save connections, then use **Apply to installed game** after saving text. The editor compares release, internal version, and non-language game data, backs up the installed XML, and copies only the generated XML. Restart Civilization V. The editor does not install a complete mod. A game is optional when editing a standalone project.

The table stays inside its own horizontal and vertical scroll area. Drag a column's right edge to resize it; column widths and visibility, line wrapping, mode, page size, and the fill preference persist across launches. The page selector stays above the table; 100 rows show initially, with 25–1000 or All available. Use the filter icon for type/operation, status, or an inclusive edit-date range. Developer rows show their canonical source file and line. On short or narrow screens, the controls and edit panel scroll within the application instead of widening the page.

Selecting a row fills the edit box from **My translation**, then **Existing Lekmod translation**, when either has text. It stays empty when both are empty; English is never inserted as a translation. The Developer edit box uses the selected English text. XML indentation around a multiline English sentence is omitted from the edit box and reinstated when it is saved. When disabling **Fill the editor from the selected row** while text exists, choose **Keep text** or **Clear text**. The discard icon restores only unsaved form edits after confirmation. Copy buttons in nonempty source cells and on required formatting tokens copy their exact text. Keep token names and counts, including icons, colors, and placeholders, in a translation. These are valid game markup, and saving checks them. The character counter under the edit box has no English-length restriction. Gender and plurality have suggested values plus a **Custom** entry. Translator notes appear in the table and CSV; they are never exported into the game XML.

**Status** is `missing` without an approved translation, `stale` when its English fingerprint changed, `applied` when the translation is in the project's generated XML, or `saved` if XML generation is turned Off. `Applied` does not mean copied into an installed game. A stale translation is kept for review, but the game uses English until the translation is approved again. English and translation character counts, English edit date, and translation edit date are optional columns. A date is blank when no reliable timestamp exists: the included English date index covers committed source rows, editor saves record subsequent changes, and older CSV approvals have no timestamp. It is not an estimate of a gameplay file's last change.

The header **Logs** page lists local actions and failures without recording translation text or passwords; use **Download log** for a bug report and **Back to editor**, **Logs**, or the mode switch to return. Success and failure notifications appear near the bottom center of the window. Undo and redo reverse up to 20 *saved* changes in the current session; Ctrl+Z in the text box only reverses typing. **Exports** contains a language ZIP for a maintainer and one XML for manual testing. Neither download sends data to anyone or installs the mod.

### Editor updates

**Settings → Editor updates** checks published `editor-v*` releases. If a newer Windows release exists, **Download and update editor** downloads its ZIP, verifies GitHub's SHA-256 digest and exact file list, stages it locally, closes the old process, replaces only the editor files, and opens the updated editor. Your connected project, CSVs, preferences, backups, and vanilla snapshot remain in place; the previous editor files are backed up for recovery. A failed installation is written to `localization/workspace/editor-updates/update.log` and appears in the editor's Logs after restart. A valid pending download can be retried. A Git source launcher shows the release but updates via Git instead. Network access to GitHub is needed for checks and updates.

**Recovery for an automatic update started from v0.2 or v0.3:** Their Windows helper used a reserved PowerShell variable name and could stop before copying files. Restarting those versions may show `a pending editor update already exists`; retrying the same automatic update will not fix their helper. Download the latest `editor-v0.4` ZIP manually, extract it to a **new writable folder**, and start its EXE. In Settings, reconnect the existing complete Lekmod project folder. If the old editor downloaded that project, its path is `<old editor folder>/localization/workspace/projects/v35.3`; leave the old folder in place while using it. You may copy your private `localization/workspace/vanilla-snapshot.json.gz` to the new editor workspace or import it in Settings. Do not delete the old editor folder until you have checked your project, translations, and snapshot. The editor's location on a hard disk rather than in Downloads is not the cause of this failure.

## Vanilla comparison: one shared baseline

`reference/vanilla-fingerprints.json.gz` in Git pins the team's reference fingerprints. A full `vanilla-snapshot.json.gz` supplies the actual official English and translated sentences for the side-by-side comparison. This full-text file is **not** in this public repository or Windows download; it stays in `localization/workspace/` and is never committed. The editor and preparation verify it against the pinned fingerprints, so a different game database cannot silently replace the team's baseline. Without it you can still translate and classify English, but the original vanilla columns are hidden. The pinned snapshot presently has English and Russian text; the other eight locale tables are empty. Their original vanilla translations cannot be shown until maintainers review a complete new baseline.

### Prepare the matching file in VS Code / PowerShell

If you already created the snapshot in the earlier workflow, do not regenerate it. Open **Terminal → New Terminal** in VS Code and find the existing file:

```powershell
Set-Location C:\Projects\Lekmod
Get-Item .\build\localization\vanilla-snapshot.json.gz, .\localization\workspace\vanilla-snapshot.json.gz -ErrorAction SilentlyContinue
```

If the file is only in `build\localization`, copy it into the private editor workspace:

```powershell
New-Item -ItemType Directory -Force .\localization\workspace | Out-Null
Copy-Item .\build\localization\vanilla-snapshot.json.gz .\localization\workspace\vanilla-snapshot.json.gz
Get-Item .\localization\workspace\vanilla-snapshot.json.gz
```

If the file is already in `localization\workspace`, skip the copy. The **encrypt** command below verifies its content against the shared reference before creating a transferable file. You do not need `$mergedDatabase` for an existing snapshot. If neither path exists, locate the clean database you used earlier, assign that path to `$mergedDatabase`, and then create the snapshot:

```powershell
$mergedDatabase = "C:\path\to\your\clean\Localization-Merged.db"
python -B .\localization\tools\freeze_vanilla.py --vanilla-db "$mergedDatabase"
```

Replace the example path with the real file path; a vanilla game installation alone is not a guarantee that its database matches the shared reference. A different or partially populated database is rejected. Do not run `--refresh-reference` to bypass a mismatch. Updating the team baseline requires a separate maintainer review of all languages and changed classifications.

### Optional encrypted cloud handoff

A free [Dropbox Basic](https://www.dropbox.com/basic) account is enough for this small file. This is an **encrypted file** handoff, not a Dropbox account password or a password built into the EXE. The snapshot password must have at least 16 characters; `111` is not safe. The editor does not save it. Install the one extra Python library on the computer doing the encryption or terminal download (the Windows EXE already contains it):

```powershell
python -m pip install "cryptography>=45,<47"
python -B .\localization\tools\snapshot_cloud.py encrypt
```

The tool checks `localization/workspace/vanilla-snapshot.json.gz` against the pinned reference, prompts twice for the password without echoing it, and creates `localization/workspace/vanilla-snapshot.enc`. Open [Dropbox Basic](https://www.dropbox.com/basic), sign in, click **Upload → Files**, and upload **only the `.enc` file**. In Dropbox select that file, click **Share → Copy link**, then change `dl=0` to `dl=1` (or add `&dl=1` if the URL already has `?` and no `dl`; otherwise append `?dl=1`). Give the link and password to authorized collaborators separately. In the editor, open **Settings → Vanilla reference** (expand the section), paste the link and enter the password, then click **Download, decrypt and verify**. The editor saves only the link; the downloaded plaintext snapshot stays in its ignored workspace. A bad password, corrupted file, or different baseline is rejected before installation.

A developer using an IDE can fetch the same encrypted file in PowerShell:

```powershell
Set-Location C:\Projects\Lekmod
python -B .\localization\tools\snapshot_cloud.py fetch --url "https://www.dropbox.com/...&dl=1"
```

The command prompts for the password, verifies the shared baseline, and atomically installs `localization/workspace/vanilla-snapshot.json.gz`. Replace the example URL with the actual direct link. The editor can also import a matching local `.json.gz` in Settings without a cloud account. The person setting up Dropbox must create their own account and upload the encrypted file; the editor cannot create that account or recover a lost password. Whoever has both link and password can decrypt the file, so share those only with intended team members and follow the rights applicable to the official game text.

## Developer workflow and checked-in files

In a Git checkout, `localization/en_US/primary.xml` is the canonical editable English source. The `Language_en_US` block in `LEKMOD/Override/CIV5Units_Mongol.xml` is generated from it; changes made directly to that block will be overwritten. A new `TXT_KEY_*` entry also needs a reference in gameplay XML, SQL, or Lua before the game will use it. Developers can use **Developer** mode to edit/create a key or edit `primary.xml` directly. Saving English rebuilds outputs and makes earlier translations with changed fingerprints stale. After direct file edits:

```powershell
python -m pip install "cryptography>=45,<47"
python -B .\localization\tools\manage.py prepare
python -B .\localization\tools\manage.py check
git diff --check
```

The `_help` entries in `localization/config.json` describe each `On`/`Off` build or check switch; JSON has no comments. Leave switches On for normal development. `Off` skips a step and does not erase existing generated text. A gameplay balance change does not automatically identify which prose should change; review the corresponding English description. CI checks the committed output and cannot push automatic changes back into your branch.

| Path | Purpose | Commit? |
| --- | --- | --- |
| `localization/en_US/primary.xml` | Canonical English operations | Yes |
| `localization/translations/*.csv` | Approved text, source fingerprints, notes, edit timestamps | Yes |
| `localization/reference/vanilla-fingerprints.json.gz` | Pinned shared vanilla hashes | Yes |
| `localization/reference/english-edit-dates.json.gz` | Text-free portable date index | Yes |
| `localization/editor/`, `localization/tools/`, `localization/config.json` | Contributor UI, generators, tests and switches | In contributor source, not player package |
| `LEKMOD/Override/CIV5Units_Mongol.xml` | Generated language sections at the mod's existing path | Yes |
| `localization/workspace/` | Snapshot, preferences, drafts, downloads, logs, backups | Never |

The translator's **Exports → Download translation ZIP** packages a language CSV and a manifest. The maintainer compares its baseline hash and source commit, reviews translations and required tokens, places `translations/<locale>.csv` in this folder, runs `manage.py prepare` and `manage.py check`, reviews the game XML diff, then commits. The editor and Python tests are contributor materials; a player download should contain the actual mod payload from `LEKMOD` instead. `.gitignore` cannot hide already tracked contributor files from a public Git repository.

### FAQ

**Why not commit the full vanilla snapshot?** It copies official game text, unlike original fan-written Lekmod descriptions. A public PR and its Git history can be copied even if a later commit removes a file. Keep the full file outside this public branch unless the team has redistribution permission. The tracked fingerprints and authenticated encrypted handoff provide a single checked baseline without publishing the full text.

**What if the snapshot does not match?** Check that you used the matching clean database and the correct file. Ask a maintainer for the team's verified file. Do not change the pinned reference to silence the error. English and approved translations remain usable without original vanilla wording.

**Can a plain game installation unlock the editor?** No. The editor needs a complete compatible localization project as its source. A game with one matching installed Lekmod DLC is optional for in-game testing. The game checker reports a vanilla-only installation in yellow; no version selection is needed when there is one installed DLC.

**Why is a date blank?** Existing approvals have no historic per-row timestamp, and a new key may have no committed source history. Saving a translation or English row in this editor records its edit time. The pinned English date index supplies commit dates to a downloaded source without Git history; a Git checkout reads its own blame history.

**What do I need to update my existing VS Code folder?** Commit or back up your own uncommitted edits first. In the VS Code terminal, run `git switch localization-infrastructure` and `git pull --ff-only origin localization-infrastructure`. This gets the **source** changes into your `C:\Projects\Lekmod` checkout; it does not install or upgrade an already extracted Windows EXE. For the EXE, use **Settings → Editor updates → Download and update editor** after the versioned release appears, or download that new release once. Point it at your updated checkout in Settings, or let it download a separate compatible project. If VS Code still shows unresolved imports under the removed `tools/localization` directory, close those old tabs and run **Developer: Reload Window** from the Command Palette. The active scripts now reside in `localization/tools`.
