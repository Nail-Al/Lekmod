# Localization Change Review

Localization audits are read-only. A source change is kept only after a developer approves it, and the result is then checked in the game.

| Finding | Why it is a problem | Proposed change | Developer approval | In-game test |
| --- | --- | --- | --- | --- |
| Automation Resets Turn Timer used the Relative Turn Timer localization keys | Two different options wrote different text to the same keys | Use `TXT_KEY_GAME_OPTION_AUTOMATION_RESETS_TIMER` and its `_HELP` key for every locale in that option file | Pending | Pending |
| Russian Louvre theming text used `TXT_KEY_BUILDING_STONE_WORKS_HELP` | Louvre text overwrote the Stone Works help entry | Use `TXT_KEY_LOUVRE_THEMING_BONUS_HELP` | Pending | Pending |
| German, Polish, and Russian Cathedral text used `TXT_KEY_BELIEF_MISSIONARY_ZEAL` | Cathedral text overwrote the Missionary Zeal belief entry | Use `TXT_KEY_BELIEF_CATHEDRALS` | Pending | Pending |
