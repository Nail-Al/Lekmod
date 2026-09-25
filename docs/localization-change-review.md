# Localization Change Review

Localization audits are read-only. A source change is kept only after a developer approves it, and the result is then checked in the game.

| Finding | Why it is a problem | Proposed change | Developer approval | In-game test |
| --- | --- | --- | --- | --- |
| Automation Resets Turn Timer used the Relative Turn Timer localization keys | Two different options wrote different text to the same keys | Use `TXT_KEY_GAME_OPTION_AUTOMATION_RESETS_TIMER` and its `_HELP` key for every locale in that option file | Pending | Pending |
| Russian Louvre theming text used `TXT_KEY_BUILDING_STONE_WORKS_HELP` | Louvre text overwrote the Stone Works help entry | Use `TXT_KEY_LOUVRE_THEMING_BONUS_HELP` | Pending | Pending |
| German, Polish, and Russian Cathedral text used `TXT_KEY_BELIEF_MISSIONARY_ZEAL` | Cathedral text overwrote the Missionary Zeal belief entry | Use `TXT_KEY_BELIEF_CATHEDRALS` | Pending | Pending |

## Infrastructure Notes

- The translation workspace is generated under `build/localization`; it does not modify shipped game files.
- Review files compare the official target-language text, Lekmod English, and current Lekmod target-language text with character counts.
- English source disagreements are retained as `source_conflict` variants in `build/localization/review/source-conflicts.json` and withheld from per-language translation files; the generator does not choose a runtime winner or apply a fix.
- The workspace uses game-oriented categories and deterministic subcategories. Database references take priority, while key rules refine broad areas and cover entries with no database reference.
- Repository localization SQL is parsed as data and is never executed.
