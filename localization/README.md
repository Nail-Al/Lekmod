# Lekmod Localization Editor

LLE edits Lekmod English and translations. It is a contributor tool; players only need the installed mod. All contributor sources are under `localization/`; the game loads generated language sections in `LEKMOD/Override/CIV5Units_Mongol.xml`.

## Start without an IDE

1. Extract the [Windows editor ZIP](https://github.com/Nail-Al/Lekmod/releases/latest). Run `LekmodLocalizationEditor.exe`. No Python, IDE, or command window is required.
2. In **Settings**, browse to a complete compatible Lekmod project, or select a version under **Get a compatible Lekmod version → Download/Update**. A plain Civilization V folder cannot be used as the source project. Downloads create separate folders under the editor's `localization/workspace/projects/`; they never replace an existing project.
3. Click **Save connections**. The editor shows a loading screen while preparing the project, then opens in **Translator** mode on first use.
4. Optional: expand **Vanilla reference**, keep the prefilled encrypted Dropbox link, enter the password supplied by the maintainer, and click **Download, decrypt and verify**. The link and verified snapshot are retained; the password is not saved.

Keep the complete extracted editor folder. **Settings → Editor updates → Download and update editor** updates it in place. The wrench checks missing or changed app files; **Fix version** repairs them. Updates replace only packaged application files and retain connected projects, saved English, translations, snapshot, preferences, column widths and pending Merge reviews. An unsaved row prompts **Save and continue / Discard and continue / Keep editing**. The new server opens before its project is prepared; a loading screen is expected during preparation. On failure the old app is restored. Open **Logs** for the cause; detailed diagnostics are in `localization/workspace/editor-updates/update.log` and `editor-startup.log`.

**Lekmod versions and editor versions are separate.** LLE checks the official mod list at launch and every ten minutes; the refresh icon checks immediately. Settings shows the connected version and warns about newer releases. Checks never download, switch your project, or install anything in the game. Choose any supported version, click **Download/Update**, then **Save connections** to switch. Future releases use verified official source commits and small per-release English comparisons without requiring a new editor executable. Invalid sources, incomplete comparisons or an incompatible format stop the download; the previous project remains intact. An offline check keeps the saved list and explains that it could not confirm updates.

## Translate, save and test

Choose a language and category, or **All categories**. Select a row, edit **My translation text**, then **Save and apply**. The editor writes that row to the connected project's `localization/translations/<LANGUAGE>.csv` and rebuilds its game XML according to `config.json`. It does not replace the whole language with just your row, commit to Git, or update the installed game automatically.

**Vanilla EN** and **Vanilla <selected language>** show official reference sentences when that language exists in the imported snapshot. Enable the optional translation column in **Visible columns**. The current team reference contains EN and RU sentences; the other eight tables are empty until a maintainer completes the baseline. Empty reference cells do not prove the game has no translation.

| Status | Meaning |
| --- | --- |
| `missing` | No saved translation for this English source. |
| `stale` | English changed. Your old CSV text is retained, but the game uses the updated English until you translate again. |
| `applied` | Saved translation is in the project's generated XML. |
| `saved` | CSV is saved; XML generation is Off in the project configuration. |
| `needs_source_review` | English sources conflict; a developer must resolve them before translating. |

Icons, colors, `[NEWLINE]` and `{1_Name}` are game formatting tokens. Keep their names and counts; click a token chip to copy it. Character counts do not limit text length. Gender and Plurality accept listed or custom values. Translator notes stay in the CSV and do not appear in game.

Resize columns at their right edge, change page size, and collapse the selected-row panel on small displays. Settings survive editor updates. While a translation saves, its row and conflicting actions are locked; you can edit other rows. Failed saves retain a recoverable draft. Undo/redo reverse saved changes in the current session; the trash icon discards the current unsaved form. Leaving edited text prompts before it is lost.

Edit dates and activity logs use your computer's time zone, displayed as `dd.mm.yyyy / HH:mm`; hover an edit date for the time zone. Date filters use your local calendar days, including daylight-saving changes. Files and exported logs keep their original ISO timestamps/offsets, so importing an Argentine translator's edit shows its corresponding local time to a contributor in Prague. Records without a time zone are identified rather than guessed.

For a game test, first install the **same Lekmod release** using the [official launcher and instructions](https://docs.google.com/document/d/18tsjg2C1wKA7I41GktDRr6R83eUrhn4FHi9EUEtpKvI/edit?tab=t.0#heading=h.sgkkio8aa458). In Settings, select the Civilization V folder. Red means invalid files; yellow means vanilla Civ V or a mismatched mod; green means one matching Lekmod installation. A mismatch names the installed and required versions. Connections are checked when a project opens, when the game folder changes, when Settings opens, when focus returns to LLE, and again immediately before writing to the game. **Apply to installed game** becomes available only for a verified match, backs up the installed XML, and copies the prepared localization. Restart Civilization V to test. A game installation is optional for editing and exchanging work.

## Upgrade Lekmod and find new work

In **Settings → Get a compatible Lekmod version**, choose the new release and download it. Keep **Carry saved translations when switching projects** checked, then **Save connections**. Unsaved text must be saved, discarded, or kept in the old project before switching. Existing destination translations take priority; conflicting and removed old rows remain available in the old project and private `workspace/carried-translations/` archives. Developer English changes and unfinished Merge reviews stay with their original project; export them for review rather than replacing a new release's English wholesale.

The new project copies eligible saved rows with their original English fingerprints. Unchanged rows keep their translations; changed rows become `stale` and use new English in the generated XML. A different vanilla reference stops automatic transfer. The previous project is not deleted or overwritten.

Use **All categories → Filter rows → Changed in Lekmod version → Since last project update** to see changed keys across every intermediate release. Add **Needs localization only** to show missing/stale translations. **Changed in Lekmod** lists the release labels for each row. **Settings → Release comparisons → Synchronize** enables additional reviewed versions in the filter. These comparisons use a small bundled XML/SQL change index; they do not download older mod archives or run a full build per version. An intermediate edit followed by a later revert is still listed, although a translation matching the final English may already be valid.

The reviewed index covers v35.0–v35.4; complete downloadable projects currently support v35.3 and v35.4. A future unreviewed release requires an editor/source migration before it is enabled. This protects contributors from silently mixing incompatible builds.

## Import/Export and Merge

**Translator → Import/Export** exports only checked languages. Only the currently selected language starts checked. A ZIP contains all saved rows for those languages, with source fingerprints, notes and timestamps; it excludes private vanilla text. Send it to a maintainer, or commit the saved CSV and generated XML from your connected Git checkout.

**Developer → Import/Export** exports/imports canonical English. The source is `localization/en_US/primary.xml` (`en_US`, not `en_en`). English exports include a baseline of per-key hashes so unchanged sender rows cannot overwrite another developer's independent edits. Older exports without that baseline require explicit conflict review.

Import ZIPs with **Preview merge**. Separate English and translation imports join one pending **Merge** review, even after switching mode. **Merge** and **Logs** toggle open/closed; Developer/Translator returns to the corresponding table. Pending packages and checkbox choices stay in the project's ignored workspace across normal editor updates. **Clear review** removes the pending review only.

Review current/incoming text, status and **Translations reset**. Check **Include** to accept a row; uncheck to retain team text. Source-mismatched translations cannot be included. English applies first; matching incoming translations apply afterwards. A row labelled **English first** depends on the selected English change. If that English is skipped, its new translation becomes unavailable. Reset language labels identify old translations that fall back to English; their CSV text is kept for later revision.

Choose **Project folder** or **Project and installed game**, then **Apply reviewed changes**. The project is always saved and rebuilt first; the game option additionally verifies and updates the matching installation. A game-only copy would leave the project's source outdated and get overwritten at the next build. A failed project rebuild restores source/CSVs/XML from backups. If the later game copy fails, the reviewed project remains saved and the editor explains that the game needs another apply. Files changed by an IDE after preview require a fresh review.

New text keys can be added in Developer mode. A new key needs a gameplay XML/SQL/Lua reference before it appears in game. Renaming a referenced key, deleting keys, or changing non-text selectors requires a reviewed IDE migration; a source ZIP cannot silently perform it.

## Developers using Git or an IDE

Use a Git checkout. Each contributor should use their own checkout/branch; avoid simultaneous IDE/editor writes to the same files. The browser edits the connected project, while CLI tools operate on their own checkout. Git contributors review conflicts and commit source/translation changes together with generated XML. English is edited in `primary.xml`; the game file's generated language sections are not edited directly.

After direct edits, run from the repository root:

```powershell
python -B .\localization\tools\manage.py prepare
python -B .\localization\tools\manage.py check
git diff --check
```

`config.json` explains each `On`/`Off` switch under `_help`. CI checks committed outputs; it cannot update a contributor's files automatically. **Run checks** uses the full suite in a Git checkout with Python and Git. A portable project runs applicable source/XML checks and names the skipped Git/test gates.

To receive one or more ZIPs, preview them together, then repeat the same reviewed choices with `--apply`. English is validated/applied before translations. Replace these example filenames with the received files; omit `$english` if there is no English ZIP:

```powershell
Set-Location C:\Projects\Lekmod
python -B .\localization\tools\manage.py prepare
$english = "$env:USERPROFILE\Downloads\lekmod-english-source.zip"
$translations = "$env:USERPROFILE\Downloads\lekmod-RU_RU-translations.zip"
python -B .\localization\tools\merge_localization.py $english $translations
# For every conflicting row, repeat --use-incoming LOCALE:TXT_KEY_NAME
# or --keep LOCALE:TXT_KEY_NAME on both preview and apply. Stale rows can only be kept.
python -B .\localization\tools\merge_localization.py $english $translations --apply
if ($LASTEXITCODE -ne 0) { throw 'Resolve the reported merge error before continuing.' }
python -B .\localization\tools\manage.py check
git diff --check
git diff -- localization/en_US localization/translations LEKMOD/Override/CIV5Units_Mongol.xml
```

`--apply` rebuilds the project using its configuration and restores changed inputs if that rebuild fails. Private backups stay in `workspace/handoff-backups/`. CLI export is available too: `python -B localization/tools/merge_localization.py --export-locale RU_RU --output localization/workspace/ru-handoff.zip`, or `--export-english` for English. Existing output files are not overwritten. Git history supplies the baseline for an IDE English export. `updated_at` is information, not permission to replace another translator's text.

For a prepared new release checkout, `python -B localization/tools/sync_lekmod_versions.py --carry-from "C:\path\to\old-project"` carries saved translations and prepares the new project. `--since v35.1` enables changes between that version and the current release; `--version v35.2` adds one optional comparison. Add `--online` to cache missing official release comparisons without updating LLE; the Git-tracked baseline stays unchanged. Shared change-index updates are a maintainer task using pinned official commits.

| Files | Role / distribution |
| --- | --- |
| `localization/en_US/primary.xml` | Canonical English; commit. |
| `localization/translations/*.csv` | Reviewed translations and notes; commit. |
| `localization/reference/*.json.gz` | Shared hashes, release changes and date index; commit. |
| `LEKMOD/Override/CIV5Units_Mongol.xml` | Generated runtime language XML; commit and ship to players. |
| `localization/editor/`, `tools/`, `config.json` | Contributor UI, generators and tests; omit from player downloads. |
| `localization/workspace/` | Private snapshot, settings, downloaded projects, drafts, pending reviews, backups and logs; never commit. |

## Vanilla reference: team setup

Ordinary contributors only use the prefilled encrypted link and the separately supplied password. The editor downloads, decrypts and compares the file against the tracked `reference/vanilla-fingerprints.json.gz`. A wrong password or different reference cannot replace a valid local snapshot. The verified local copy is reused on restart/update; no game-language switching is needed for translators.

**Why two files?** Git contains a common text-free fingerprint reference; the full official sentences stay in an ignored local snapshot. Keep official text out of public Git unless the team has redistribution permission. Anyone with both encrypted link and password can decrypt it; share the password separately with intended collaborators. Deleting a tracked file later does not remove it from Git history.

**What changes when the baseline is completed?** A maintainer reviews every added language, publishes a new fingerprint reference and encrypted snapshot together, and updates the team link. Existing local snapshots are backed up when replaced. A different baseline is a separate team migration, not an ordinary editor update.

<details>
<summary>Maintainers: capture missing vanilla languages, combine and encrypt them</summary>


The current reference already contains English and Russian. Close LLE while collecting the baseline. In the same VS Code PowerShell terminal, update the tools and check the Python 3.13 installation used for encryption:

```powershell
Set-Location C:\Projects\Lekmod
git status --short
git switch localization-infrastructure
if ($LASTEXITCODE -ne 0) { throw 'Git switch failed; stop here.' }
git pull --ff-only origin localization-infrastructure
if ($LASTEXITCODE -ne 0) { throw 'Git pull failed; stop here.' }
py -3.13 -c "import cryptography; print('Encryption ready:', cryptography.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'Use the Python 3.13 installation with cryptography.' }
if (-not (Test-Path -LiteralPath '.\localization\workspace\vanilla-snapshot.json.gz')) {
  $snapshotUrl = 'https://www.dropbox.com/scl/fi/dquiyoh5k77v8qhip4u70/vanilla-snapshot.enc?rlkey=fwcgddzwanyaljhe9tja6ytk1&dl=1'
  py -3.13 -B .\localization\tools\snapshot_cloud.py fetch --url $snapshotUrl `
    --output .\localization\workspace\vanilla-snapshot.json.gz
  if ($LASTEXITCODE -ne 0) { throw 'The existing team snapshot could not be verified.' }
}
```

Select **English** in **Steam Library → Civilization V → Properties → General → Language**. Wait for downloads, launch the unmodded game to its main menu, then exit it. Keep its DLC unmodded throughout capture. Define these two helpers once; they rediscover the cache after each game launch:

```powershell
Set-Location C:\Projects\Lekmod
function Get-VanillaDatabase {
  $documents = [Environment]::GetFolderPath('MyDocuments')
  $folders = @((Join-Path $documents 'My Games'),
    (Join-Path $env:USERPROFILE 'Documents\My Games')) | Select-Object -Unique
  $databases = @($folders | Where-Object { Test-Path -LiteralPath $_ } |
    ForEach-Object { Get-ChildItem -LiteralPath $_ -Recurse -File `
      -Filter 'Localization-Merged.db' -ErrorAction SilentlyContinue } |
    Sort-Object LastWriteTime -Descending)
  if ($databases.Count -eq 0) { throw 'Launch unmodded Civ V once, then close it.' }
  Write-Host "Using $($databases[0].FullName), updated $($databases[0].LastWriteTime)"
  return $databases[0].FullName
}
function Save-VanillaLocale([string]$Locale) {
  $database = Get-VanillaDatabase
  py -3.13 -B .\localization\tools\collect_vanilla.py inspect --vanilla-db $database
  if ($LASTEXITCODE -ne 0) { throw 'Cache inspection failed; no capture was saved.' }
  py -3.13 -B .\localization\tools\collect_vanilla.py capture `
    --vanilla-db $database --locale $Locale
  if ($LASTEXITCODE -ne 0) { throw "Capture failed for $Locale; stop and review the message." }
}
$mergedDatabase = Get-VanillaDatabase
py -3.13 -B .\localization\tools\collect_vanilla.py inspect --vanilla-db $mergedDatabase
if ($LASTEXITCODE -ne 0) { throw 'English cache inspection failed.' }
```

Confirm `en_US` has at least 1,000 rows and says `matches baseline`. Russian is already pinned too; there is no new EN or RU capture to make. Optionally switch to Russian, launch/exit, and repeat the last two inspection commands: `RU_RU` must match too. For each missing locale below, select that language in Steam, wait for the download to finish, launch the unmodded game to its main menu, exit, then run **only its table command**. If a locale was already populated in the clean cache, capture it without changing Steam language.

| Steam language | Command after launch and exit |
| --- | --- |
| German | `Save-VanillaLocale 'DE_DE'` |
| Spanish - Spain | `Save-VanillaLocale 'ES_ES'` |
| French | `Save-VanillaLocale 'FR_FR'` |
| Italian | `Save-VanillaLocale 'IT_IT'` |
| Japanese | `Save-VanillaLocale 'JA_JP'` |
| Korean | `Save-VanillaLocale 'KO_KR'` |
| Polish | `Save-VanillaLocale 'PL_PL'` |
| Traditional Chinese | `Save-VanillaLocale 'ZH_HANT_HK'` |

Each command creates an ignored `localization/workspace/vanilla-captures/<locale>.json.gz` and rejects changed English, an incomplete table, or an existing capture. Stop on errors; do not delete earlier captures to bypass validation. Steam language changes cannot replace your saved captures. Keep this terminal open, or redefine the helpers when opening a new session.

After all eight captures, merge and confirm that all ten languages contain rows. These are **proposals**: the current baseline stays unchanged. Enter a password of at least 16 characters twice when encrypting; it is not displayed or stored:

```powershell
py -3.13 -B .\localization\tools\collect_vanilla.py merge
if ($LASTEXITCODE -ne 0) { throw 'Snapshot merge failed; do not encrypt or upload it.' }
py -3.13 -B .\localization\tools\snapshot_cloud.py encrypt `
  --input .\localization\workspace\vanilla-snapshot-proposed.json.gz `
  --reference .\localization\workspace\vanilla-fingerprints-proposed.json.gz `
  --output .\localization\workspace\vanilla-snapshot-complete.enc
if ($LASTEXITCODE -ne 0) { throw 'Encryption failed; do not upload a partial file.' }
```

Upload **only** `C:\Projects\Lekmod\localization\workspace\vanilla-snapshot-complete.enc` to Dropbox as a new file. Keep the old encrypted archive until migration. Copy the new shared link, change `dl=0` to `dl=1`, and paste the plain URL (no Markdown brackets) at the prompt:

```powershell
$newSnapshotUrl = Read-Host 'Paste the new Dropbox URL ending in dl=1'
py -3.13 -B .\localization\tools\snapshot_cloud.py fetch `
  --url $newSnapshotUrl `
  --reference .\localization\workspace\vanilla-fingerprints-proposed.json.gz `
  --output .\localization\workspace\verified-proposal.json.gz
if ($LASTEXITCODE -ne 0) { throw 'Dropbox copy did not verify; keep the existing team link.' }
```

After team review, publish the proposed text-free fingerprint file and new encrypted link together, install the proposed full snapshot locally, and release those changes together. The existing reference rejects a completed snapshot until its matching fingerprint file is adopted. Do not replace just the old Dropbox file or commit the full snapshot/password. Ordinary translators only enter the updated team's link and password; they never need to switch game languages.

</details>

For terminal downloads use `py -3.13 -B localization/tools/snapshot_cloud.py fetch --url "<direct encrypted link>"`. Enter the password at its prompt. The Windows editor includes its encryption dependency; terminal users need a matching prebuilt `cryptography` package for their Python interpreter. The snapshot encryption/share form is hidden in the editor; maintainers use the CLI when preparing a new baseline.

**Recovery:** If an older editor's page/updater is broken, update the Git checkout and run `python -B localization/tools/repair_editor.py --editor-root "C:\path\to\editor"`. It verifies/repairs app files without deleting the workspace. Normal updates use Settings. Do not delete a folder containing your translations to recover the executable.
