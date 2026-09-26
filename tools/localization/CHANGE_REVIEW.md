# Localization Change Review

Localization checks are read-only. A source change is retained only after
developer approval and an in-game test.

## Source Corrections in This Branch

| Finding | Reason | Implemented change | Developer approval | In-game test |
| --- | --- | --- | --- | --- |
| Automation Resets Turn Timer used the Relative Turn Timer keys | Two game options wrote unrelated text to the same keys | Assigned `TXT_KEY_GAME_OPTION_AUTOMATION_RESETS_TIMER` and its `_HELP` key in every locale block of that option file | Pending | Pending |
| Russian Louvre theming text used the Stone Works help key | Louvre text overwrote Stone Works help | Assigned `TXT_KEY_LOUVRE_THEMING_BONUS_HELP` | Pending | Pending |
| German, Polish, and Russian Cathedral text used the Missionary Zeal key | Cathedral text overwrote the Missionary Zeal belief | Assigned `TXT_KEY_BELIEF_CATHEDRALS` | Pending | Pending |

## Infrastructure Result

- Audits and inventory report evidence but never alter game sources.
- The generated review workspace compares vanilla English, vanilla target text,
  Lekmod English, and Lekmod target text.
- UTF-8 CSV files are split by locale and game category; only translation text,
  grammar metadata, and translator notes are editable.
- Regeneration preserves edits while the English source fingerprint is stable
  and stops when a draft has become stale.
- Conflicting English sources remain in `review/source-conflicts.json`; the
  generator neither chooses a winner nor applies a fix.
- The editor and review workspaces remain under ignored `build/localization`
  paths. The separate `build_shipped_localization.py --write` command updates
  only a marked section of the loaded Override XML after source checks.
- Game verification of fallback, Art load order, and approved translations is
  still pending.
