# Lekmod Localization Tools

This directory contains checks, source synchronization, and the translation
workspace generator. Game XML changes require an explicit write command;
conflicting English sources are still kept for developer review.

Requirements: Python 3.10 or newer. No third-party packages are required.
Run every command from the repository root.

This is the single entry point for maintainers. `localization/en_US/primary.xml`
and `localization/approved-translations.json` are editable inputs;
`LEKMOD/Override/CIV5Units_Mongol.xml` is the loaded game file with generated
language sections. Python files in `tests/` are current regression checks,
not obsolete scripts. `__pycache__/` and `*.pyc` are disposable Python caches
already ignored by Git. Git ignore rules do not hide files in VS Code's
Explorer. Use `python -B` for future runs if you do not want Python to create
those caches; existing caches can be removed without affecting source code.

## Practical scenarios

| When | Edit or run | Expected result |
| --- | --- | --- |
| Add a new civilization or a new `TXT_KEY_*` | Add gameplay references to the appropriate game-data file and English text to `localization/en_US/primary.xml`; run `sync_primary_english.py --write`, then `build_shipped_localization.py --vanilla-snapshot build/localization/vanilla-snapshot.json.gz --write` | English source stays editable in one place; generated game XML gains English and non-English fallback. |
| Change a vanilla description | Edit its English operation in `primary.xml`; run the two `--write` commands above | The changed key receives English fallback in target locales; untouched vanilla keys are omitted. |
| Prepare a translation | Run `build_localization_catalog.py --vanilla-snapshot build/localization/vanilla-snapshot.json.gz`, edit only the translator columns in `build/localization/editor/<locale>/*.csv`, then have a reviewer copy the chosen text and its `source_fingerprint` into `localization/approved-translations.json` | The next shipped build uses approved text for that locale and rejects a stale English fingerprint or broken formatting tokens. Draft CSV text never ships automatically. |
| Review a source conflict | Open `build/localization/review/source-conflicts.json`, compare its English variants with actual gameplay, then record the chosen correction in `CHANGE_REVIEW.md` | Conflicting keys remain withheld until the source disagreement is corrected and the catalog rebuilt. |
| Verify before a release | Run the commands in [Validation](#validation), `build_shipped_localization.py --vanilla-snapshot build/localization/vanilla-snapshot.json.gz --check`, and test a non-English client in Civ V | Static checks and generated game XML agree; the game test verifies load order and displayed text. |

Examples in the table use script names relative to `tools/localization/`;
prefix them with `python tools/localization/` when running from the repo root.

## Layout

| Path | Responsibility |
| --- | --- |
| `audit_localization.py` | Validate localization definitions under `LEKMOD/Art` |
| `audit_primary_localization.py` | Validate the primary English table in `LEKMOD/Override/CIV5Units_Mongol.xml` |
| `inventory_localization.py` | Find localization definitions, key references, and text candidates across the repository |
| `build_localization_catalog.py` | Build categorized JSON review data and per-language CSV files |
| `sync_primary_english.py` | Keep the shipped English block in sync with its editable source |
| `freeze_vanilla.py` | Create or verify one local reference of official language tables |
| `build_fallback_preview.py` | Preview English fallback for new and changed keys without installing it |
| `build_shipped_localization.py` | Generate and verify non-English rows in the loaded Override XML |
| `lekmod_localization/` | Internal source loading, taxonomy, catalog, and workspace modules |
| `tests/` | Regression and safety tests for the tools |
| `CHANGE_REVIEW.md` | Developer approval and in-game test log for source changes |

The top-level Python files are the supported command-line entry points.
Internal implementation is split by responsibility so source parsing, game
classification, and output writing can be reviewed independently.

## Validation

Run the checks individually:

- `python tools/localization/audit_localization.py --strict`
- `python tools/localization/audit_primary_localization.py --strict`
- `python tools/localization/sync_primary_english.py`
- `python tools/localization/inventory_localization.py --strict`
- `python -m unittest discover -s tools/localization/tests -v`

The Art audit fails on invalid XML or conflicting writes within one locale.
The primary audit validates ordered `Row`, `Replace`, `Update`, and `Delete`
operations in `CIV5Units_Mongol.xml`. The repository inventory reports source
locations and unsupported data without treating every code literal as
player-facing text.

The audits report evidence without automatic repairs. English synchronization
has a separate explicit `--write` mode; its default is read-only.

## English Source and Game XML

The editable primary English source is `localization/en_US/primary.xml`. Its
ordered `Language_en_US` operations were extracted verbatim from
`LEKMOD/Override/CIV5Units_Mongol.xml`. The latter remains the installed game
file: the editable file is not an XML include. Its English section is generated
and protected by CI, while its gameplay tables remain in place. There is one
manually maintained copy, although the game file necessarily contains a
generated copy.

For a new civilization, add gameplay rows and `TXT_KEY_*` references to the
appropriate game-data file as usual. Add its English `<Row Tag="TXT_KEY_...">`
entries **only** to `localization/en_US/primary.xml`. For a revised vanilla
description, edit the existing `<Replace Tag="TXT_KEY_...">` there. Preserve
formatting tokens, operation order, and any `Gender`/`Plurality` fields. Then:

```text
python tools/localization/sync_primary_english.py --write
python tools/localization/sync_primary_english.py
python tools/localization/audit_primary_localization.py --strict
python -m unittest discover -s tools/localization/tests -v
```

Commit both the source and generated game XML. Do not manually edit the
marked section of `CIV5Units_Mongol.xml`: CI rejects differences. Independent
English definitions in `LEKMOD/Art` are not consolidated yet; the existing
`source-conflicts.json` review remains necessary before that step.

## Translation Workspace

The generator compares three sources:

- official Civilization V language tables from a clean
  `Localization-Merged.db`;
- current Lekmod English from `LEKMOD/Override/CIV5Units_Mongol.xml` and
  supported localization files under `LEKMOD/Art`;
- current Lekmod text for each target locale found under `LEKMOD/Art`.

The database must come from an unmodified Civilization V cache. It is opened
read-only and rejected if known Lekmod keys are present. This sentinel check
cannot prove that every official string is pristine: verify the game
installation before freezing the reference. Repository SQL is parsed as data
using a restricted localization grammar and is never executed.

Freeze the official tables once, and optionally verify them later:

```text
python tools/localization/freeze_vanilla.py --vanilla-db "PATH_TO_CLEAN_Localization-Merged.db"
python tools/localization/freeze_vanilla.py --vanilla-db "PATH_TO_CLEAN_Localization-Merged.db" --check
```

This creates `build/localization/vanilla-snapshot.json.gz` with every locale
and a fingerprint per language. It refuses to overwrite an existing snapshot.
Subsequent catalog runs can use `--vanilla-snapshot` instead of `--vanilla-db`,
so changes to another installation cannot silently alter the reference. The
full-text snapshot is ignored by Git and is not bundled in this patch. Check
redistribution rights before adding official game strings to a public repo.

Generate every non-English locale present in the database:

- `python tools/localization/build_localization_catalog.py --vanilla-db "PATH_TO_CLEAN_Localization-Merged.db"`

Or use the frozen input:

- `python tools/localization/build_localization_catalog.py --vanilla-snapshot build/localization/vanilla-snapshot.json.gz`

Generate only selected locales by repeating `--locale`:

- `python tools/localization/build_localization_catalog.py --vanilla-db "PATH_TO_CLEAN_Localization-Merged.db" --locale RU_RU --locale DE_DE`

### Generated Output

All output is written under ignored `build/localization` paths.

| Output | Purpose |
| --- | --- |
| `catalog.json` | Language-independent categorized source index |
| `review/source-conflicts.json` | English definitions that disagree and require developer review |
| `review/<locale>/<category>.json` | Detailed comparison grouped by category and subcategory |
| `editor/<locale>/<category>.csv` | UTF-8 spreadsheet used for translation work |

The generator creates one editor directory for every requested vanilla locale.
Each locale receives the same game-oriented category files, including units,
buildings, civilizations, city-states, policies, religion, UI, and other text
areas found in the source.

Each CSV row shows:

- official vanilla English and its character count;
- official vanilla text for the target locale;
- current Lekmod English and its character count;
- current Lekmod target text;
- whether the entry is new, changed, missing, a placeholder, or already present;
- required Civilization V formatting tokens and source paths.

Character counts use normalized stored text and include formatting tokens. They
are comparison aids, not proven UI limits.

Only these columns are intended for manual editing:

- `translation`
- `translation_gender`
- `translation_plurality`
- `translator_note`

Regeneration preserves those fields while the corresponding English source
fingerprint is unchanged. If that source changes, generation stops before
overwriting the existing editor workspace so a stale translation cannot be
carried forward silently.

English source conflicts are excluded from translator CSV files and kept in
`review/source-conflicts.json`. A developer must approve the intended source
before those entries enter translation work.

### English Fallback Preview

After freezing vanilla, create a review-only XML per non-English locale:

```text
python tools/localization/build_fallback_preview.py --vanilla-snapshot build/localization/vanilla-snapshot.json.gz
```

The output under `build/localization/fallback-preview` contains one XML file
per locale and a manifest with source fingerprints, row counts, and withheld
conflicts. Every XML contains only keys classified as `lekmod_new` or
`vanilla_modified`; unchanged vanilla translations stay in the game. Each
row currently carries Lekmod English and its formatting metadata. Conflicting
English definitions are withheld, and the manifest records their count. An
explicitly empty English `<Text>` remains empty in the preview; the manifest
lists these keys under `empty_english_text_keys` for source review. A missing
`Text` still stops generation.

This preview is evidence for developer review, **not** a release artifact.
The preview files are ignored by Git and are not loaded by Civ V. The separate
shipped builder below generates a game file from the same comparison.

### Shipped Fallback and Approved Translations

Use the same frozen vanilla reference to build the non-English blocks in the
already loaded `LEKMOD/Override/CIV5Units_Mongol.xml`:

```text
python tools/localization/build_shipped_localization.py --vanilla-snapshot build/localization/vanilla-snapshot.json.gz --write
python tools/localization/build_shipped_localization.py --vanilla-snapshot build/localization/vanilla-snapshot.json.gz --check
python tools/localization/sync_primary_english.py
```

The default mode is a dry run. `--write` removes legacy empty language blocks
and writes a marked generated section for all nine target locales. Re-running
it with unchanged inputs makes no changes. It includes 5,620 new and 415
modified keys per locale. Unchanged official keys are omitted, so their
existing translations remain in the game. The 126 conflicting English source
keys are excluded until a developer decides which definition is correct.
The catalog loader ignores the generated target-language section on later
runs, keeping the comparison independent of its own output.

No draft translation from `build/localization/editor` ships automatically.
After review, copy the translated text and its `source_fingerprint` from the
CSV into `localization/approved-translations.json` like this:

```json
{
  "schema_version": 1,
  "translations": {
    "RU_RU": {
      "TXT_KEY_EXAMPLE": {
        "source_fingerprint": "64-character fingerprint from the editor CSV",
        "text": "Проверенный перевод"
      }
    }
  }
}
```

Optional `gender` and `plurality` fields override the English metadata.
Unknown keys, stale fingerprints, missing text, placeholders, and mismatched
formatting tokens stop the build. Approved translations take precedence over
English fallback in the generated XML. The game must be tested with at least
one non-English client before releasing: this repository cannot determine
which independently loaded Art files might later write the same key.

The editor is deliberately export-only at this stage: it does not write XML,
SQL, `CIV5Units_Mongol.xml`, or any other shipped file.

## Change Workflow

1. A script reports a source issue or translation candidate with evidence.
2. A developer approves the specific fix or a defined bulk update.
3. A developer tests the approved game-file change in Civilization V.

Record source changes and test results in `CHANGE_REVIEW.md`.

## Continuous Integration

`.github/workflows/localization-audit.yml` runs the strict audits, repository
inventory, and test suite when relevant source or tool files change. Workspace
generation is not run in CI because official full-text input is local and not
committed. CI also checks that the shipped primary English block matches its
editable source.

## Current Boundaries

The tools do not determine runtime load order, validate translation quality,
prove that every code literal is player-facing, or extract text embedded in
image assets. The generated Override XML requires an in-game test; 126
English source disagreements still need review. Unknown keys remain visible
under `unclassified`.
