# Lekmod localization

This folder contains the canonical English text, approved language CSVs, the browser editor, the shared vanilla fingerprints, and contributor tools. Civilization V reads generated localization in `LEKMOD/Override/CIV5Units_Mongol.xml`. Players need the packaged mod files, not this editor or the contributor tools.

## Start the Windows editor (no programming tools required)

1. Download `LekmodLocalizationEditor-Windows.zip` from the latest [editor release](https://github.com/Nail-Al/Lekmod/releases?q=editor-v) and extract it into a writable folder. Double-click `LekmodLocalizationEditor.exe`. Keep its console window open while using the browser page. The editor version appears beside its title. You do not need Git, Python, VS Code, or an installed game to open the editor.
2. In **Settings → Lekmod project folder**, select a **complete** checkout of the [compatible localization branch](https://github.com/Nail-Al/Lekmod/tree/localization-infrastructure), if you have one. Otherwise choose the compatible `v35.3` entry and click **Download selected source**, then **Save connections**. The download creates a separate project; it never overwrites your edits. The upstream main branch and older releases do not yet include the matching localization system. A plain Civilization V installation alone is insufficient.
3. The source badge now names the connected project. Select **Translator**, a language and a category; search or pick a row. Change **My translation text** and click **Save and apply**. This saves the CSV in the connected project and rebuilds its game XML. A blank save withdraws that translation. **Developer** in the header opens the full English source, where developers may edit text or create a key. The mode button and Settings button are independent.
4. To test in the game, install the **matching complete Lekmod release** with its official launcher. In Settings, select the Civilization V folder and **Check game folder**. The installed Lekmod list will be empty for a vanilla-only game. Select the matching DLC, save, then use **Apply to installed game** after saving text. The editor compares release, internal version, and non-language game data, backs up the installed XML, and copies only the generated XML. Restart Civilization V. The editor does not install a complete mod.

The table stays inside its own horizontal and vertical scroll area. Drag a column's right edge to resize it; column widths and visibility, line wrapping, mode, and the fill preference persist across launches. The page selector stays above the table. On short or narrow screens, the controls and edit panel scroll within the application instead of widening the page.

Selecting a row fills the edit box from **My translation**, then **Existing Lekmod translation**, when either has text. It stays empty when both are empty; English is never inserted as a translation. The Developer edit box uses the selected English text. When disabling **Fill the editor from the selected row** while text exists, choose **Keep text** or **Clear text**. Copy buttons in nonempty source cells and on required formatting tokens copy their exact text. Keep token names and counts, including icons, colors, and placeholders, in a translation. These are valid game markup, and saving checks them. The character counter under the edit box has no English-length restriction. Gender and plurality have suggested values plus a **Custom** entry. Translator notes appear in the table and CSV; they are never exported into the game XML.

**Status** is `missing` without an approved translation, `stale` when its English fingerprint changed, `applied` when the translation is in the project's generated XML, or `saved` if XML generation is turned Off. `Applied` does not mean copied into an installed game. A stale translation is kept for review, but the game uses English until the translation is approved again. English and translation character counts, English edit date, and translation edit date are optional columns. A date is blank when no reliable timestamp exists: the included English date index covers committed source rows, editor saves record subsequent changes, and older CSV approvals have no timestamp. It is not an estimate of a gameplay file's last change.

The header **Logs** page lists local actions and failures without recording translation text or passwords; use **Download log** for a bug report and **Back to editor** to return. Success and failure notifications also appear near the top of the window. Undo and redo reverse up to 20 *saved* changes in the current session; Ctrl+Z in the text box only reverses typing. **Exports** contains a language ZIP for a maintainer and one XML for manual testing. Neither download sends data to anyone or installs the mod.

### Editor updates

**Settings → Editor updates** checks published `editor-v*` releases. If a newer Windows release exists, **Download and update editor** downloads its ZIP, verifies GitHub's SHA-256 digest and exact file list, stages it locally, closes the old process, replaces only the editor files, and opens the updated editor. Your connected project, CSVs, preferences, backups, and vanilla snapshot remain in place; the previous editor files are backed up for recovery. A Git source launcher shows the release but updates via Git instead. The earlier unversioned EXE predates this feature: install v0.2 manually once, then future updates can use this button. Network access to GitHub is needed for checks and updates.

## Vanilla comparison: one shared baseline

`reference/vanilla-fingerprints.json.gz` in Git pins the team's reference fingerprints. A full `vanilla-snapshot.json.gz` supplies the actual official English and translated sentences for the side-by-side comparison. This full-text file is **not** in this public repository or Windows download; it stays in `localization/workspace/` and is never committed. The editor and preparation verify it against the pinned fingerprints, so a different game database cannot silently replace the team's baseline. Without it you can still translate and classify English, but the original vanilla columns are hidden. The pinned snapshot presently has English and Russian text; the other eight locale tables are empty. Their original vanilla translations cannot be shown until maintainers review a complete new baseline.

### Prepare the matching file in VS Code / PowerShell

If you already created the snapshot in the earlier workflow, find it at `C:\Projects\Lekmod\build\localization\vanilla-snapshot.json.gz` or `C:\Projects\Lekmod\localization\workspace\vanilla-snapshot.json.gz`. Do not regenerate it unnecessarily. In VS Code, open **Terminal → New Terminal**, enter these commands, and replace the source path if yours differs:

```powershell
Set-Location C:\Projects\Lekmod
New-Item -ItemType Directory -Force .\localization\workspace | Out-Null
Copy-Item .\build\localization\vanilla-snapshot.json.gz .\localization\workspace\vanilla-snapshot.json.gz
python -B .\localization\tools\freeze_vanilla.py --vanilla-db "$mergedDatabase" --check
```

If the file is already in `localization\workspace`, skip `Copy-Item`. The last command requires the same clean `Localization-Merged.db` path in `$mergedDatabase` that you used to create your earlier snapshot; it checks both the database and the pinned team fingerprints. If the database is unavailable, verification against the pinned reference happens when you import the file in Settings or run `python -B .\localization\tools\manage.py prepare`. To create a missing snapshot from the **matching clean** database:

```powershell
python -B .\localization\tools\freeze_vanilla.py --vanilla-db "$mergedDatabase"
```

A different or partially populated database is rejected. Do not run `--refresh-reference` to bypass a mismatch. Updating the team baseline requires a separate maintainer review of all languages and changed classifications.

### Optional encrypted cloud handoff

A free [Dropbox Basic](https://www.dropbox.com/basic) account is enough for this small file. This is an **encrypted file** handoff, not a Dropbox account password or a password built into the EXE. The snapshot password must have at least 16 characters; `111` is not safe. The editor does not save it. Install the one extra Python library on the computer doing the encryption or terminal download (the Windows EXE already contains it):

```powershell
python -m pip install "cryptography>=45,<47"
python -B .\localization\tools\snapshot_cloud.py encrypt
```

The tool checks `localization/workspace/vanilla-snapshot.json.gz` against the pinned reference, prompts twice for the password without echoing it, and creates `localization/workspace/vanilla-snapshot.enc`. Upload **only the `.enc` file** to Dropbox. Copy its share link and change the ending `dl=0` to `dl=1` (or add `?dl=1` if no query exists) to obtain a direct HTTPS download. Give the link and password to authorized collaborators separately. In the editor, open **Settings → Vanilla reference**, paste the link and enter the password, then click **Download, decrypt and verify**. The editor saves only the link; the downloaded plaintext snapshot stays in its ignored workspace. A bad password, corrupted file, or different baseline is rejected before installation.

A developer using an IDE can fetch the same encrypted file in PowerShell:

```powershell
Set-Location C:\Projects\Lekmod
python -B .\localization\tools\snapshot_cloud.py fetch --url "https://www.dropbox.com/...&dl=1"
```

The command prompts for the password, verifies the shared baseline, and atomically installs `localization/workspace/vanilla-snapshot.json.gz`. Replace the example URL with the actual direct link. The editor can also import a matching local `.json.gz` in Settings without a cloud account. The person setting up Dropbox must create their own account and upload the encrypted file; the editor cannot create that account or recover a lost password. Whoever has both link and password can decrypt the file, so share those only with intended team members and follow the rights applicable to the official game text.

## Developer workflow and checked-in files

In a Git checkout, `localization/en_US/primary.xml` is the canonical editable English source. The `Language_en_US` block in `LEKMOD/Override/CIV5Units_Mongol.xml` is generated from it; changes made directly to that block will be overwritten. A new `TXT_KEY_*` entry also needs a reference in gameplay XML, SQL, or Lua before the game will use it. Developers can use **Developer** mode to edit/create a key or edit `primary.xml` directly. Saving English rebuilds outputs and makes earlier translations with changed fingerprints stale. After direct file edits:

```powershell
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

**Can a plain game installation unlock the editor?** No. The editor needs a complete compatible localization project as its source. A game with a matching installed Lekmod DLC is optional for in-game testing. The installed DLC selector is empty if no Lekmod is installed and does not pretend that a vanilla game contains the mod.

**Why is a date blank?** Existing approvals have no historic per-row timestamp, and a new key may have no committed source history. Saving a translation or English row in this editor records its edit time. The pinned English date index supplies commit dates to a downloaded source without Git history; a Git checkout reads its own blame history.

**What do I need to update my existing VS Code folder?** Commit or back up your own uncommitted edits first. In the VS Code terminal, run `git switch localization-infrastructure` and `git pull --ff-only origin localization-infrastructure`. This gets the **source** changes into your `C:\Projects\Lekmod` checkout; it does not install or upgrade an already extracted Windows EXE. For the EXE, download the new release once. Point it at your updated checkout in Settings, or let it download a separate compatible project.
