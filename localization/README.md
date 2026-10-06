# Lekmod Localization Editor

LLE edits Lekmod English and translations. It is a contributor tool; players only need the installed mod. All contributor sources are under `localization/`; the game loads generated language sections in `LEKMOD/Override/CIV5Units_Mongol.xml`.

## Start without an IDE

1. Extract the [Windows editor ZIP](https://github.com/Nail-Al/Lekmod/releases/latest). Run `LekmodLocalizationEditor.exe`. No Python, IDE, or command window is required.
2. In **Settings**, browse to a complete compatible Lekmod project, or select a version under **Get a compatible Lekmod version → Download/Update**. A plain Civilization V folder cannot be used as the source project. Downloads create separate folders under the editor's `localization/workspace/projects/`; they never replace an existing project.
3. Click **Save connections**. The editor shows a loading screen while preparing the project, then opens in **Translator** mode on first use.
4. Optional: expand **Vanilla reference**, keep the prefilled encrypted Dropbox link (or restore it with **Use team link**), enter the snapshot encryption password supplied by the maintainer, and click **Download, decrypt and verify**. This is not your Dropbox account password. The link and verified snapshot are retained; the password is not saved. An empty link displays a gray example only; choose **Use team link** to fill the actual address.

Keep the complete extracted editor folder. **Settings → Editor updates → Download and update editor** updates it in place. The wrench checks missing or changed app files; **Fix version** repairs them. Updates replace only packaged application files and retain connected projects, saved English, translations, snapshot, preferences, column widths/order, local drafts and pending Merge reviews. Edited rows are auto-saved locally before reconnecting. Installation shows its current stage on the same browser address; each reconnect request has a timeout. The new server opens before its project is prepared, so a loading screen is expected during preparation.

Only one editor server runs from each installation folder; double-clicking its EXE reopens that server and retains your current form. Older releases may have left extra processes running after their browser tabs were closed. The downloaded updater now closes verified idle copies from that same folder automatically, keeping the session that requested the update. It refuses to interrupt another copy's save, download or update. A serverless leftover older than two minutes can be retired; a newly starting copy is kept. After acknowledged shutdown, a stuck bootloader is terminated using its original process handle. Editors in other installation folders are untouched. Save text in any other open legacy editor tabs before updating: an old server cannot report text held only in those tabs.

A blocked file stops installation; only files actually replaced are restored, and reopening is verified. Open **Logs** for the cause; detailed diagnostics are in `localization/workspace/editor-updates/update.log` and `editor-startup.log`. A temporary browser `NetworkError` during restart means its local server is reconnecting. It does not by itself indicate an Internet failure. If a pre-v0.21 downloaded helper still refuses extra copies, save your work, close both `LekmodLocalizationEditor` groups in Task Manager, launch the EXE once, and retry the update. Each portable editor normally has two Windows processes (bootloader and server); two separate groups mean two editor sessions.

**Lekmod versions and editor versions are separate.** LLE checks the official mod list at launch and every ten minutes; the refresh icon checks immediately. Settings shows the connected version and warns about newer releases. Checks never download, switch your project, or install anything in the game. Choose any supported version, click **Download/Update**, then **Save connections** to switch. Future releases use verified official source commits and small per-release English comparisons without requiring a new editor executable. Invalid sources, incomplete comparisons or an incompatible format stop the download; the previous project remains intact. An offline check keeps the saved list and explains that it could not confirm updates.

## Translate, save and test

Choose a language and category, or **All categories**. Select a row and edit **My translation text**. Text, grammar and notes auto-save to a private local draft after a short typing pause and before changing rows, searches, languages or modes. **Save** flushes that draft immediately. It does not rebuild XML or change project CSVs. Incomplete text, including missing formatting tokens, can be saved and finished later.

**Apply to Lekmod project** validates all pending English and translation drafts and rebuilds once according to `config.json`. English applies first; old translations become stale unless a matching new translation is in the same batch. **Apply to installed game** does the same project batch and then copies it to the verified matching game. Only those Apply actions change the mod files. While a batch builds, keep editing: newer drafts stay pending for the next Apply.

Drafts survive restarts and editor updates in the connected project's ignored `localization/workspace/editor-drafts.sqlite3`. **Local drafts** shows pending work, including keys removed in a newer mod, and downloads a backup of incomplete drafts. SQLite is built into the EXE; no database installation is needed. A failed local save keeps the form open and shows Retry Save rather than losing text.

**My translation · RU**, **DE**, etc. names the language you are editing; the form shows the same language. **Vanilla EN** and **Vanilla <selected language>** show official reference sentences from the imported snapshot. Enable the optional translation column in **Visible columns**. The shared baseline contains English and all nine supported translation languages: DE, ES, FR, IT, JA, KO, PL, RU and Traditional Chinese (ZH-Hant-HK). A new Lekmod key may have no vanilla counterpart.

| Status | Meaning |
| --- | --- |
| `draft` | Saved locally; not yet applied to the project or game. |
| `missing` | No saved translation for this English source. |
| `stale` | English changed. Your old CSV text is retained, but the game uses the updated English until you translate again. |
| `applied` | Saved translation is in the project's generated XML. |
| `saved` | CSV is saved; XML generation is Off in the project configuration. |
| `needs_source_review` | English sources conflict; a developer must resolve them before translating. |

Icons, colors, `[NEWLINE]` and `{1_Name}` are game formatting tokens. Keep their names and counts; click a token chip to copy it. Character counts do not limit text length. Gender and Plurality accept listed or custom values. Translator notes stay in the CSV and do not appear in game.

Drag column headings to change their order; use the separate right-edge handle to resize. Alt+Left/Right on a focused heading also reorders it. Default order is unchanged; saved order is separate for Translator and Developer. Vanilla columns start wider, without overriding your custom widths. Change page size and collapse the row panel on small displays. Settings and mode-specific filters survive updates; **Clear filter** clears only the current mode.

Undo/redo changes local drafts without a rebuild. Consecutive typing on one selected row is one undo step; it remains available after restarting. The trash icon confirms before discarding that row's local draft. After Apply, its batch leaves local undo history; Git and private `workspace/draft-apply-backups/` retain the applied source history. Actual same-row IDE conflicts stop Apply and preserve drafts: open **Local drafts → Review current project**, compare source and draft, then explicitly keep the draft against that baseline. Different-row IDE edits are preserved automatically.

Edit dates and activity logs use your computer's time zone, displayed as `dd.mm.yyyy / HH:mm`; hover an edit date for the time zone. Date filters use your local calendar days, including daylight-saving changes. Files and exported logs keep their original ISO timestamps/offsets, so importing an Argentine translator's edit shows its corresponding local time to a contributor in Prague. Records without a time zone are identified rather than guessed.

For a game test, first install the **same Lekmod release** using the [official launcher and instructions](https://docs.google.com/document/d/18tsjg2C1wKA7I41GktDRr6R83eUrhn4FHi9EUEtpKvI/edit?tab=t.0#heading=h.sgkkio8aa458). In Settings, select the Civilization V folder. Red means invalid files; yellow means vanilla Civ V or a mismatched mod; green means one matching Lekmod installation. A mismatch names the installed and required versions. Connections are checked when a project opens, when the game folder changes, when Settings opens, when focus returns to LLE, and again immediately before writing to the game. **Apply to installed game** becomes available only for a verified match, backs up the installed XML, and copies the prepared localization. Restart Civilization V to test. A game installation is optional for editing and exchanging work.

**Before applying, close Civilization V completely.** The editor checks language operations against both the core localization tables and DLC `LocalizedText` views/triggers before it replaces the installed XML. Colors, icons and numbered placeholders remain text values; intentional blank help text is materialized as a non-breaking space in runtime XML. Canonical English and saved translations are unchanged. A paired `<Text></Text>` and a self-closing `<Text />` are both empty and may reach the game binder as SQL NULL; physical inactive-language tables require `Text NOT NULL`. Rejected XML leaves the installed file untouched. This database check supplements an actual game test; it does not run the Civilization V engine.

**If game text becomes a `TXT_KEY_*`:** close the game, update LLE through **Settings → Editor updates**, keep the same connected project, wait for preparation, then click **Apply to installed game** and launch the game again. Reopening v0.24 rebuilds generated text from the existing English/CSVs; it does not erase translations or local drafts. It replaces both forms of empty runtime Text with nonempty blank text in every language, including English. The v0.23 paired-tag change alone was insufficient. Tests cover the observed mixed cache structure (EN/RU views and a physical German table), transaction rollback, and recovery of earlier generated XML. These checks do not execute the closed-source game parser. Private installed-file backups remain in `localization/workspace/game-backups/` beside the editor.

If a key is still missing, close the game after testing and choose **Logs → Download game diagnostics**. Attach that JSON together with the action log. It compares installed/project file hashes, checks whether the game's actual cache contains the menu and Bazaar keys (through either `Language_*` tables or merged `LocalizedText` records), reports configured language choices, and includes relevant loader errors with bounded neighboring SQL context. The report also distinguishes physical tables from views and identifies empty Text keys, including in a rejected installed file. It never clears or writes the cache, and excludes full game text and local drafts. Cache timestamps identify a prior run; a missing database is reported rather than created. If logs are absent, set `LoggingEnabled = 1` in your Civilization V profile's `config.ini`, run the game once more, close it and collect again.

**Bazaar example:** `LEKMOD/Override/CIV5Units.xml` assigns `BUILDING_BAZAAR.Help` to `TXT_KEY_BUILDING_BAZAAR_HELP`. The `Replace` entry in `primary.xml` is copied into the loaded `CIV5Units_Mongol.xml`; the building tooltip resolves that key through `Locale.ConvertTextKey`. An eligible translation changes the selected language's text, not the building's balance. Changing English makes its old translation stale, so that language receives the new English until it is translated again.

## Upgrade Lekmod and find new work

In **Settings → Get a compatible Lekmod version**, choose the new release and download it. Keep **Carry saved translations when switching projects** checked, then **Save connections**. Current forms auto-save locally before switching. Checked transfer also copies local drafts without deleting the old copies; changed English or conflicting project rows require review before Apply. Existing destination translations take priority; conflicting and removed old rows remain available in the old project and private `workspace/carried-translations/` archives. Unfinished Merge reviews stay with their original project. Local English drafts keep their old per-row baseline: a mod update cannot silently overwrite changed upstream English. Export them for review when appropriate.

The new project copies eligible saved rows with their original English fingerprints. Unchanged rows keep their translations; changed rows become `stale` and use new English in the generated XML. A different vanilla reference stops automatic transfer, except the reviewed EN/RU-to-ten-language extension, which preserves all existing English and Russian data. The previous project is not deleted or overwritten.

Use **All categories → Filter rows → Changed in Lekmod version → Since last project update** to see changed keys across every intermediate release. Add **Needs localization only** to show missing/stale translations. **Changed in Lekmod** lists the release labels for each row. **Settings → Release comparisons → Synchronize** enables additional reviewed versions in the filter. These comparisons use a small bundled XML/SQL change index; they do not download older mod archives or run a full build per version. An intermediate edit followed by a later revert is still listed, although a translation matching the final English may already be valid.

The reviewed index covers v35.0–v35.4; complete downloadable projects currently support v35.3 and v35.4. A future unreviewed release requires an editor/source migration before it is enabled. This protects contributors from silently mixing incompatible builds.

## Import/Export and Merge

**Translator → Import/Export** exports only checked languages. Only the currently selected language starts checked. A ZIP includes applied rows and valid local drafts for those languages, with source fingerprints, notes and timestamps; it excludes private vanilla text. Required local English edits are included in the same package so dependent translations can be reviewed in order. Invalid/incomplete drafts remain saved; finish them or use **Local drafts → Download draft backup** instead of a merge package. Send it to a maintainer, or commit the saved CSV and generated XML from your connected Git checkout.

**Developer → Import/Export** exports/imports canonical English. The source is `localization/en_US/primary.xml` (`en_US`, not `en_en`). English exports include a baseline of per-key hashes so unchanged sender rows cannot overwrite another developer's independent edits. Older exports without that baseline require explicit conflict review.

Import ZIPs with **Preview merge**. Separate English and translation imports join one pending **Merge** review, even after switching mode. **Merge** and **Logs** toggle open/closed; Developer/Translator returns to the corresponding table. Pending packages and checkbox choices stay in the project's ignored workspace across normal editor updates. **Clear review** removes the pending review only.

Review current/incoming text, status and **Translations reset**. Check **Include** to accept a row; uncheck to retain team text. Source-mismatched translations cannot be included. English applies first; matching incoming translations apply afterwards. A row labelled **English first** depends on the selected English change. If that English is skipped, its new translation becomes unavailable. Reset language labels identify old translations that fall back to English; their CSV text is kept for later revision.

Apply your local drafts before applying an imported review; neither is silently overwritten by the other. Choose **Project folder** or **Project and installed game**, then **Apply reviewed changes**. The project is always saved and rebuilt first; the game option additionally verifies and updates the matching installation. A game-only copy would leave the project's source outdated and get overwritten at the next build. A failed project rebuild restores source/CSVs/XML from backups. If the later game copy fails, the reviewed project remains saved and the editor explains that the game needs another apply. Files changed by an IDE after preview require a fresh review.

New text keys can be added in Developer mode. A new key needs a gameplay XML/SQL/Lua reference before it appears in game. Renaming a referenced key, deleting keys, or changing non-text selectors requires a reviewed IDE migration; a source ZIP cannot silently perform it.

## Developers using Git or an IDE

Use a Git checkout. Each contributor should use their own checkout/branch; avoid simultaneous IDE/editor writes to the same files. The browser first edits private drafts; **Apply to Lekmod project** updates the connected checkout. Apply before committing or using CLI exports, because Git intentionally ignores drafts. CLI tools read canonical files in their own checkout. Git contributors review conflicts and commit source/translation changes together with generated XML. English is edited in `primary.xml`; the game file's generated language sections are not edited directly.

After direct edits, run from the repository root:

```powershell
python -B .\localization\tools\manage.py prepare
python -B .\localization\tools\manage.py check
git diff --check
```

`config.json` explains each `On`/`Off` switch under `_help`. CI checks committed outputs; it cannot update a contributor's files automatically. **Run checks** uses the full suite in a Git checkout with Python and Git. A portable project runs applicable source/XML checks and names the skipped Git/test gates.

Every Windows build runs a separate updater regression gate before publication: real EXE/HTML file locks, bounded rollback and reopening on the same URL, duplicate launch, and preservation of applied translations, English, local draft database, column order, preferences and the snapshot. The fast-save regression also runs on Windows: 100 local saves perform no catalog/XML builds; batch Apply, typing during a build and rollback are checked. Run that gate locally on Windows with `python -B localization/tools/test_windows_updater.py --archive dist/LekmodLocalizationEditor-Windows.zip --locks-only`.

The game-text regression checks all ten localization tables, intentional empty strings, the real Bazaar reference/override, English changes, five Russian version-status translations, Discord/GitHub keys and formatting tokens. The frozen EXE gate also migrates an old generated project and applies to an isolated installed-DLC fixture with a backup. These are compatibility tests, not execution of the Civilization V engine. `python -B -W error::ResourceWarning -m unittest discover -s localization/tools/tests -p test_runtime_xml.py -q` runs that gate separately. Developers can collect the same read-only game report with `python -B localization/tools/manage.py diagnose-game --game-folder "C:\\Path\\To\\Civilization V"`; an optional `--profile-folder` selects a redirected profile folder.

Routine builds verify upgrades from the current release and v0.17/v0.19, which cover the legacy duplicate-process cases. The **full_upgrade_matrix** option on the Windows workflow runs every historical release when updater changes require that broader sweep.

CI regenerates runtime XML from the reviewed English, reference and translation CSVs before validating it. After the frozen Windows checks pass, the release workflow commits a changed generated XML with a fast-forward push and tags that verified commit. This keeps large generated files reproducible from their inputs without including private drafts or cache files.

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

Expand **Settings → Vanilla reference**, keep the prefilled encrypted Dropbox link, enter the team's password, then click **Download, decrypt and verify**. LLE verifies all ten languages against `reference/vanilla-fingerprints.json.gz` and reuses the local copy on future launches. No Steam language switching or Python installation is needed for ordinary translators.

**Upgrading from the old EN/RU reference:** v0.18 replaces only the recognized old default link and known old project fingerprint file. Custom links, deliberately cleared links, translations and English work stay intact. The previous project index is backed up under `workspace/reference-backups/`. The old snapshot is kept, but needs one download of the complete reference using its new password; importing it backs up the previous snapshot. Existing handoffs and project transfers remain compatible with this specifically reviewed extension. Other baseline changes still require separate review.

### FAQ

**Do I need to send my password to a developer?** No. Ask the maintainer for the password and enter it locally. LLE does not save it or include it in logs or exports. The downloaded, verified snapshot stays on your computer, so the password is needed again only if that copy is missing or the team changes its baseline.

**Why are the vanilla columns empty?** Import the shared snapshot and enable **Vanilla <language>** in **Visible columns**. A new Lekmod key has no vanilla text. Switching language changes the reference column and My translation heading together; it never writes another language's translation into your selected CSV.

**Why keep fingerprints in Git and full text elsewhere?** The fingerprints give every developer one common reference without publishing the game's official sentences. The encrypted file provides those sentences to intended collaborators. Keep official text out of public Git unless the team has redistribution permission; anyone with both the link and password can decrypt it. Deleting a tracked file later does not remove it from Git history.

**What if my password, file or reference is wrong?** Verification stops before replacing the local snapshot. Check the link and password with the maintainer, update the project checkout, and retry. Unknown or modified baselines are never adopted automatically. A missing reference does not delete your saved translations.

**Does the reference replace my translations or the installed game?** No. It supplies comparison sentences. English fingerprints decide whether each saved Lekmod translation is current. The installed game changes only after **Apply to installed game**, with a matching Lekmod version and a backup.

**How do IDE users obtain the same reference?** Update the `localization-infrastructure` checkout, then run these commands in its PowerShell terminal. Use the Python 3.13 installation with `cryptography` already installed. The first command asks for the password; the second refreshes the comparison workspace:

```powershell
Set-Location C:\Projects\Lekmod
$snapshotUrl = 'https://www.dropbox.com/scl/fi/998d6o71w2og8x9facylu/vanilla-snapshot-complete.enc?rlkey=ex0bn7c9hjecmu6ayy7qpxaip&dl=1'
py -3.13 -B .\localization\tools\snapshot_cloud.py fetch --url $snapshotUrl
if ($LASTEXITCODE -ne 0) { throw 'Snapshot verification failed; stop here.' }
py -3.13 -B .\localization\tools\manage.py prepare
if ($LASTEXITCODE -ne 0) { throw 'Project preparation failed; review the message.' }
```

**Should we make a fresh snapshot on each computer?** No. All contributors use this one shared reference. `collect_vanilla.py inspect`, `capture` and `merge` are optional maintainer tools for a future reviewed baseline. Capture from one clean game build, verify English at every step, and review all locale data before publishing a proposal. `snapshot_cloud.py encrypt --input <proposal> --reference <proposed-fingerprints> --output <new.enc>` prompts for a password of at least 16 characters. Upload the encrypted file, verify its direct-download link using `fetch --reference <proposed-fingerprints>`, and release the new hashes and default link together. Do not commit the full snapshot or password.

**Recovery:** If an older editor's page/updater is broken, update the Git checkout and run `python -B localization/tools/repair_editor.py --editor-root "C:\path\to\editor"`. It verifies/repairs app files without deleting the workspace. Normal updates use Settings. Do not delete a folder containing your translations to recover the executable.
