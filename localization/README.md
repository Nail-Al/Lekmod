# Lekmod Localization Editor

LLE is a local editor for Lekmod English and translations. It provides source comparison, drafts and Save history, reviewed ZIP handoffs, Merge, validation and separate Apply actions for Project and an installed Civilization V game. Developers can use the same services from an IDE/terminal.

Players only need the mod: the game reads generated language sections in `LEKMOD/Override/CIV5Units_Mongol.xml` using its language settings.

## Documentation

| Guide | Contents |
| --- | --- |
| [LLE user manual](https://github.com/Nail-Al/Lekmod/blob/localization-infrastructure/localization/docs/LLE-manual.md) | Illustrated setup, source/game/reference connections, Translator/Developer, Save/Apply, Merge and troubleshooting. |
| [IDE and terminal guide](https://github.com/Nail-Al/Lekmod/blob/localization-infrastructure/localization/docs/terminal-guide.md) | Canonical XML/CSV, commands, shared local workflow, handoffs, generation, validation and contributing. |

## Start

Download the Windows ZIP from [editor releases](https://github.com/Nail-Al/Lekmod/releases), extract it fully and run `LekmodLocalizationEditor.exe`. In Settings, select/download a compatible source project and **Save connections**. Game and vanilla-reference connections are optional.

From a checkout with Python 3.13+:

```sh
python -m pip install "cryptography>=45,<47"
python -B localization/tools/manage.py prepare
python -B localization/tools/editor_server.py
```

Before contributing, prepare/check the checkout and review canonical sources plus generated XML. Keep `localization/workspace/` and full vanilla snapshots private. See the terminal guide for the complete workflow.
