# Lekmod Localization Editor

LLE edits Lekmod English and translations. It is a contributor tool; players only need the installed mod. All contributor sources are under `localization/`; the game loads generated language sections in `LEKMOD/Override/CIV5Units_Mongol.xml`.

## Start without an IDE

1. Extract the [Windows editor ZIP](https://github.com/Nail-Al/Lekmod/releases/latest). Run `LekmodLocalizationEditor.exe`. No Python, IDE, or command window is required.
2. In **Settings**, browse to a complete compatible Lekmod project, or select **v35.3 / v35.4 → Download**. A plain Civilization V folder cannot be used as the source project. Downloads create separate folders under the editor's `localization/workspace/projects/`; they never replace an existing project.
3. Click **Save connections**. The editor shows a loading screen while preparing the project, then opens in **Translator** mode on first use.
4. Optional: expand **Vanilla reference**, keep the prefilled encrypted Dropbox link, enter the password supplied by the maintainer, and click **Download, decrypt and verify**. The link and verified snapshot are retained; the password is not saved.

Keep the complete extracted editor folder. **Settings → Editor updates → Download and update editor** updates it in place. The wrench checks missing or changed app files; **Fix version** repairs them. Updates replace only packaged application files and retain connected projects, saved English, translations, snapshot, preferences, column widths and pending Merge reviews. An unsaved row prompts **Save and continue / Discard and continue / Keep editing**. The new server opens before its project is prepared; a loading screen is expected during preparation. On failure the old app is restored. Open **Logs** for the cause; detailed diagnostics are in `localization/workspace/editor-updates/update.log` and `editor-startup.log`.

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

For a game test, first install the **same Lekmod release** using its official installer. In Settings, select the Civilization V folder. Red means invalid files; yellow means vanilla Civ V or a mismatched mod; green means one matching Lekmod installation. **Apply to installed game** becomes available only for a verified match, backs up the installed XML, and copies the prepared localization. Restart Civilization V to test. A game installation is optional for editing and exchanging work.

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

For a prepared new release checkout, `python -B localization/tools/sync_lekmod_versions.py --carry-from "C:\path\to\old-project"` carries saved translations and prepares the new project. `--since v35.1` enables all reviewed changes between that version and the current release; `--version v35.2` adds one optional comparison. Shared change-index updates are a maintainer task using pinned official commits, not something every contributor recomputes.

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


The current reference already contains English and Russian. Keep the full `localization/workspace/vanilla-snapshot.json.gz` beside these tools; do not remake English from a different game build. Close the game, keep its DLC unmodded, and work in a current Git checkout. In PowerShell, discover the most recently updated game cache after launching the selected language:

```powershell
Set-Location C:\Projects\Lekmod
$documents = [Environment]::GetFolderPath('MyDocuments')
$myGamesFolders = @( (Join-Path $documents 'My Games'),
  (Join-Path $env:USERPROFILE 'Documents\My Games') ) | Select-Object -Unique
$databases = @( $myGamesFolders | Where-Object { Test-Path -LiteralPath $_ } |
  ForEach-Object { Get-ChildItem -LiteralPath $_ -Recurse -File `
    -Filter 'Localization-Merged.db' -ErrorAction SilentlyContinue } |
  Sort-Object LastWriteTime -Descending )
if ($databases.Count -eq 0) { throw 'Launch Civ V once, then find Localization-Merged.db in Documents\My Games.' }
$mergedDatabase = $databases[0].FullName
Write-Host "Using $mergedDatabase"
if (-not (Test-Path -LiteralPath '.\localization\workspace\vanilla-snapshot.json.gz')) {
  throw 'Import the existing encrypted team snapshot before capturing any new languages.'
}
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

After each inspection, save **that one** language using `py -3.13 -B .\localization\tools\collect_vanilla.py capture --vanilla-db $mergedDatabase --locale DE_DE`, replacing `DE_DE` with its table code. The collector creates a separate ignored `localization/workspace/vanilla-captures/<locale>.json.gz` and refuses an existing filename, changed English, or an incomplete table. If it fails, resolve the reason; do not overwrite an earlier capture. Changing Steam language does not replace captures already saved in the workspace. The terminal may keep `$mergedDatabase` across all eight captures; rerun the discovery block after opening a new PowerShell session.

After all eight captures, run `py -3.13 -B .\localization\tools\collect_vanilla.py merge`. It creates **proposals** in `localization/workspace/`: a full `vanilla-snapshot-proposed.json.gz` and a text-free `vanilla-fingerprints-proposed.json.gz`. It refuses missing languages and never overwrites the current baseline. Review the counts for all ten languages, then encrypt the proposed full snapshot without changing the current reference yet:

```powershell
py -3.13 -B .\localization\tools\snapshot_cloud.py encrypt `
  --input .\localization\workspace\vanilla-snapshot-proposed.json.gz `
  --reference .\localization\workspace\vanilla-fingerprints-proposed.json.gz `
  --output .\localization\workspace\vanilla-snapshot-complete.enc
```

Upload **only** the new `.enc` to Dropbox, copy its direct link (`dl=1`), and verify the download with `py -3.13 -B .\localization\tools\snapshot_cloud.py fetch --reference .\localization\workspace\vanilla-fingerprints-proposed.json.gz --url "<direct link>" --output .\localization\workspace\verified-proposal.json.gz`. Enter the password at the terminal prompt, never as a command argument. After team review, replace the tracked fingerprint index with the proposal, install the proposed full snapshot locally, update the editor's team link, and release those changes together. Keep the old encrypted archive until collaborators have migrated. Ordinary translators only enter the team's link and password; they never need to switch game languages.

</details>

For terminal downloads use `py -3.13 -B localization/tools/snapshot_cloud.py fetch --url "<direct encrypted link>"`. Enter the password at its prompt. The Windows editor includes its encryption dependency; terminal users need a matching prebuilt `cryptography` package for their Python interpreter. The snapshot encryption/share form is hidden in the editor; maintainers use the CLI when preparing a new baseline.

**Recovery:** If an older editor's page/updater is broken, update the Git checkout and run `python -B localization/tools/repair_editor.py --editor-root "C:\path\to\editor"`. It verifies/repairs app files without deleting the workspace. Normal updates use Settings. Do not delete a folder containing your translations to recover the executable.
