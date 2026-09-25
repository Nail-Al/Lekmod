# Localization Tools

This directory contains tools for checking and maintaining Lekmod localization data.

## Requirements

- Python 3.10 or newer
- No third-party Python packages

Run all commands from the repository root.

## Art Localization Audit

`audit_localization.py` checks localization files under `LEKMOD/Art`.

- `python tools/localization/audit_localization.py` - show the summary
- `python tools/localization/audit_localization.py --locale RU_RU` - show details for one locale
- `python tools/localization/audit_localization.py --strict` - run strict validation
- `python tools/localization/audit_localization.py --help` - show all options

The audit reports XML parsing errors, locale coverage, fallback placeholders, Cyrillic text, duplicate writes, conflicting writes, and SQL files that reference language tables.

Strict mode fails when an XML file cannot be parsed or when different texts are assigned to the same key in one locale. Missing translations and fallback placeholders are reported but do not fail validation.

## Primary Localization Audit

`audit_primary_localization.py` checks the main English localization source:

`LEKMOD/Override/CIV5Units_Mongol.xml`

Run the validation with:

`python tools/localization/audit_primary_localization.py --strict`

Inspect all operations for one key with:

`python tools/localization/audit_primary_localization.py --key TXT_KEY_LEKMOD_VERSION`

Create a complete local JSON report with:

`python tools/localization/audit_primary_localization.py --strict --json build/localization/primary.json`

The primary audit:

- reads `Row`, `Replace`, `Update`, and `Delete` operations
- preserves their source order
- retains `Text`, `Gender`, `Plurality`, and other columns
- reports repeated write targets
- accepts column names with different casing while reporting a warning
- never modifies the source XML

The generated JSON report belongs under `build/localization` and is not committed.

## Translation Workspace

`build_localization_catalog.py` builds the working material used for translation review. This is separate from the audits above.

The generator reads:

- the main English source at `LEKMOD/Override/CIV5Units_Mongol.xml`
- XML and supported localization SQL definitions under `LEKMOD/Art`
- the official language tables in a clean Civilization V `Localization-Merged.db`

Generate one target language with:

`python tools/localization/build_localization_catalog.py --vanilla-db "PATH_TO_CLEAN_Localization-Merged.db" --locale RU_RU`

Repeat `--locale` to generate several target languages. Omit it to generate every non-English locale found in the clean database.

Generated files are placed under `build/localization`:

- `catalog.json` is the categorized English source index
- `review/manifest.json` lists generated locales and source conflicts
- `review/source-conflicts.json` contains only English source disagreements that require developer review
- `review/<locale>/manifest.json` summarizes one target language
- `review/<locale>/<category>.json` contains review entries grouped by game category and subcategory

Every review entry shows:

- `official_game` - the official target-language text, or `not_in_vanilla`
- `lekmod_en_US` - the current Lekmod English text
- `lekmod_target` - the current Lekmod text for the selected language
- normalized character counts and Civilization V formatting tokens
- source paths and database references used for classification

Target text is marked `present`, `missing`, `placeholder`, or `source_conflict`. If English definitions in `Art`, SQL, and the main Override source disagree, the catalog keeps every variant and marks the entry `source_conflict`; it never guesses the runtime winner. These unresolved English entries are excluded from the per-language translation files and placed only in `review/source-conflicts.json` until a developer approves the source resolution.

Categories follow the main Civilization V text areas, including civilizations, city-states, units, buildings, wonders, improvements, resources, technologies, policies, religion, great people, great works, diplomacy, multiplayer, world congress, terrain, scenarios, Civilopedia, gameplay, game options, and UI. Subcategories split large areas into review-sized groups such as city names, leader dialogue, belief names, belief descriptions, help, strategy, Civilopedia text, and interface messages.

Database references are the primary classification source. Deterministic key-name rules refine broad database groups and classify entries that have no database reference. Unknown key families remain visible under `unclassified`; they are never dropped or assigned by guessing from their English prose.

The clean database is opened read-only. Repository SQL is parsed only for a restricted set of localization `INSERT` and `UPDATE` statements and is never executed. Unsupported SQL fails the build instead of being guessed.

All output under `build/localization` is generated and ignored by Git. Official Civilization V strings therefore remain local and are not committed to the repository.

## Repository-wide Source Inventory

`inventory_localization.py` scans repository source areas that may contain localization definitions, key references, or hardcoded text:

- `LEKMOD`, including `Art`, `Override`, Lua, standard UI and EUI `.ignore` templates
- `Lekmap`
- `LEKMOD_DLL`
- `LekmodInstaller`

Run the validation and write the complete local report with:

`python tools/localization/inventory_localization.py --strict --json build/localization/inventory.json`

The inventory records source paths and line numbers. It also reports keys written in multiple source areas and English Art keys absent from the main Override file, without guessing their runtime load order. It does not edit source files or label every code string as player-facing. String candidates are reviewed before any change is proposed.

## Change Workflow

1. The scripts report a problem or a text candidate with its source location.
2. A developer approves the specific change or a clearly defined bulk update.
3. A developer tests the approved change in the game.

The tools never perform automatic source fixes. Start source review with `build/localization/review/source-conflicts.json`. Proposed changes and their test result are recorded in `docs/localization-change-review.md`.

## Tests

Run the localization tool tests with:

`python -m unittest discover -s tools/localization/tests -v`

## Continuous Integration

The `.github/workflows/localization-audit.yml` workflow runs the strict audits, the repository-wide inventory, and the test suite when relevant source files or tools are changed.

## Localization Guidelines

- Use the correct `Language_<locale>` table
- Keep every `Tag` identical to its corresponding English key
- Do not translate identifiers such as `Type`, `Tag`, or `TXT_KEY_*`
- Preserve formatting tokens such as `[ICON_*]`, `[NEWLINE]`, and `[COLOR_*]`
- Preserve grammar metadata such as `Gender` and `Plurality`
- Save XML and SQL files as UTF-8
- Keep the existing English fallback and locale marker until a proper translation is available
- Run all strict audits and the tests before committing

## Current Scope

The Art audit covers XML and SQL localization data under `LEKMOD/Art`.

The primary audit covers `LEKMOD/Override/CIV5Units_Mongol.xml`, which identifies itself as generated by MP Modpacks Maker. The audit treats the checked-in file as the current released localization source. It does not recreate the modpack generation process.

The tools do not currently:

- determine runtime load order
- validate translation quality or gameplay accuracy
- generate translated game files

The inventory finds literal candidates in source code, but static scanning cannot prove whether every literal is displayed to a player. Text embedded in images or generated only at runtime still requires manual and in-game review.

These areas can be added incrementally after the initial infrastructure is reviewed.
