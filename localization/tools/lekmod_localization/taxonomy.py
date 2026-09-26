"""Classify text keys into stable, game-oriented review groups."""

from __future__ import annotations

import re

TABLE_CATEGORY_RULES = (
    ("great_people", re.compile(
        r"^Unit_UniqueNames(?:_|$)", re.IGNORECASE
    )),
    ("promotions", re.compile(
        r"^(?:Unit)?Promotions?(?:_|$)", re.IGNORECASE
    )),
    ("units", re.compile(
        r"^(?:Units?|UnitClasses|UnitCombatInfos)(?:_|$)", re.IGNORECASE
    )),
    ("wonders", re.compile(
        r"^(?:Projects)(?:_|$)", re.IGNORECASE
    )),
    ("buildings", re.compile(
        r"^(?:Buildings?|BuildingClasses|Specialists|Processes)(?:_|$)",
        re.IGNORECASE,
    )),
    ("city_states", re.compile(
        r"^(?:MinorCivilizations?)(?:_|$)", re.IGNORECASE
    )),
    ("civilizations", re.compile(
        r"^(?:Civilizations?|Leaders?|Traits?|CityNames|"
        r"SpyNames)(?:_|$)",
        re.IGNORECASE,
    )),
    ("great_works", re.compile(
        r"^(?:GreatWorks?|GreatWorkClasses)(?:_|$)", re.IGNORECASE
    )),
    ("technologies", re.compile(
        r"^(?:Technologies|Technology)(?:_|$)", re.IGNORECASE
    )),
    ("policies", re.compile(
        r"^(?:Policies|PolicyBranchTypes)(?:_|$)", re.IGNORECASE
    )),
    ("religion", re.compile(
        r"^(?:Beliefs|Religions|Religious)(?:_|$)", re.IGNORECASE
    )),
    ("improvements", re.compile(
        r"^(?:Improvements|Builds)(?:_|$)", re.IGNORECASE
    )),
    ("resources", re.compile(
        r"^(?:Resources)(?:_|$)", re.IGNORECASE
    )),
    ("terrain", re.compile(
        r"^(?:Features|Terrains)(?:_|$)", re.IGNORECASE
    )),
    ("world_congress", re.compile(
        r"^(?:League|Resolutions?)(?:_|$)", re.IGNORECASE
    )),
    ("diplomacy", re.compile(
        r"^(?:Diplomacy|Responses)(?:_|$)", re.IGNORECASE
    )),
    ("game_options", re.compile(
        r"^(?:GameOptions|Victories|Eras|HandicapInfos|GameSpeeds|Worlds|"
        r"Maps)(?:_|$)",
        re.IGNORECASE,
    )),
    ("concepts", re.compile(r"^(?:Concepts)(?:_|$)", re.IGNORECASE)),
    ("ui", re.compile(
        r"^(?:Notifications|Popup|Interface|Controls|Colors)(?:_|$)",
        re.IGNORECASE,
    )),
)

KEY_CATEGORY_RULES = (
    ("city_states", re.compile(
        r"^TXT_KEY_(?:CITYSTATE|CITY_STATE|MINOR_CIV|BUY_CITY_STATE|"
        r"MINOR_CITY|POP_CSTATE|BASE_INFLUENCE|INFLUENCE_.*CSTATE|"
        r"LEKMOD_CITY_STATE)"
    )),
    ("great_works", re.compile(r"^TXT_KEY_GREAT_WORK_")),
    ("great_people", re.compile(
        r"^TXT_KEY_(?:GREAT_(?:PERSON|ADMIRAL|GENERAL)_|DALAILAMA|"
        r"CHOOSEGP|SWEDISH_HUMANITARIAN|MISC_GREAT_PERSON)"
    )),
    ("wonders", re.compile(
        r"^TXT_KEY_(?:WONDER|GREAT_LIBRARY|HERMITAGE|LOUVRE|"
        r"GREAT_ZIMBABWE|OXFORD_UNIVERSITY|ISRAEL_NATIONAL_COLLEGE|"
        r"THEMING_BONUS)"
    )),
    ("civilopedia", re.compile(
        r"^TXT_KEY_(?:CIVLOPEDIA|CIVILOPEDIA|CIV5PEDIA|PEDIA_)"
    )),
    ("civilizations", re.compile(
        r"^TXT_KEY_(?:CIVILIZATION|CIV_|LEADER|TRAIT|CITY_NAME|SPY_NAME|"
        r"CIV5_DOM|CIV5_[A-Z0-9]+_(?:DOM|DAWN_OF_MAN)|CITY_(?!STATE_)|"
        r"GAUL_CITY|PHOENICIAN_CITY|MC_|DAWN_OF_MAN|UA_|LITE_AKKAD|"
        r"UAE(?:_|$)|AKSUM(?:_|$)|US_WALES|PMMM_LEADER|WELSH_LANGUAGE|"
        r"DOM_|GENERIC_LITE_AKKAD|MANNERHEIM_|NZ_MEET|ZABONAH_|"
        r"THP_BOLIVIA|UKRAINE_)"
    )),
    ("units", re.compile(
        r"^TXT_KEY_(?:UNIT|UNITCLASS|UNITCOMBAT|CIV5_UNIT|MISSION|RETRAIN)"
    )),
    ("buildings", re.compile(
        r"^TXT_KEY_(?:BUILDING|BUILDINGCLASS|SPECIALIST|PROJECT|"
        r"BURMA_BUILDING|CIV5_BUILDINGS)"
    )),
    ("promotions", re.compile(
        r"^TXT_KEY_(?:PROMOTION|ANTINAVAL_PROMO|RAIDER)"
    )),
    ("technologies", re.compile(r"^TXT_KEY_(?:TECH|TECHNOLOGY)")),
    ("policies", re.compile(
        r"^TXT_KEY_(?:POLICY|POLICY_BRANCH|SOCIAL_POLICY)"
    )),
    ("religion", re.compile(
        r"^TXT_KEY_(?:BELIEF|RELIGION|PANTHEON)"
    )),
    ("improvements", re.compile(
        r"^TXT_KEY_(?:IMPROVEMENT|BUILD_|MC_MAORI_PA)"
    )),
    ("resources", re.compile(
        r"^TXT_KEY_(?:RESOURCE|CIV5_RESOURCE)"
    )),
    ("diplomacy", re.compile(
        r"^TXT_KEY_(?:DIPLO|LEADER_MESSAGE|AI_DIPLO|ABLTY_D_PACT|"
        r"ALLOWS_DEFENSIVE_PACTS|DO_PACT|"
        r"MISC_PLAYERS_SIGN_DEFENSIVE_PACT)"
    )),
    ("game_options", re.compile(
        r"^TXT_KEY_(?:GAME_OPTION|GAMEOPTION|VICTORY|ERA|HANDICAP|"
        r"GAME_SPEED|GAMESPEED|WORLD|MAP_)"
    )),
    ("multiplayer", re.compile(
        r"^TXT_KEY_(?:MP_|WARN_MP|MESSAGE_MP|MISC_TURN_TIMER|"
        r"MULTIPLAYER|LEKMOD_MP|RESET_TURN_TIMER|CC_VOTE|"
        r"POSITIVE_VOTE|NEGATIVE_VOTE)"
    )),
    ("world_congress", re.compile(
        r"^TXT_KEY_(?:LEAGUE|RESOLUTION|LEKMOD_VOTE)"
    )),
    ("terrain", re.compile(
        r"^TXT_KEY_(?:FEATURE|BUGANDA_LAKE)"
    )),
    ("scenarios", re.compile(
        r"^TXT_KEY_(?:MEDIEVAL_SCENARIO|STEAMPUNK_SCENARIO)"
    )),
    ("gameplay", re.compile(
        r"^TXT_KEY_(?:COMBATMOD|ATTACKMOD|DEFENSEMOD|PRODMOD|GOLDMOD|"
        r"YIELD|PRODUCTION|GOODY|LEKMOD_(?:CHOOSE_)?GOODY|NO_ACTION|"
        r"CAN_PARADROP|JFD_GOLD|FAITH_FROM)"
    )),
    ("ui", re.compile(
        r"^TXT_KEY_(?:UI|MENU|OPTIONS|NOTIFICATION|POPUP|LEKMOD_MENU|"
        r"LEKMOD_MP|LEKMOD_|REPLAY|TP_|ACTIONTYPE|CONTROL|OPSCREEN|"
        r"CHOOSE_|"
        r"CO_|CITYVIEW|EUPANEL|COMBATPANEL|HOTKEY|RIGHT_BUTTON|"
        r"TOGGLE|TOP_PANEL|AD_SETUP|GAME_SELECTION|EO_|LARGE_ESPIONAGE|"
        r"OPEN_VICTORY|RANDOM_|INTERFACEMODE|TREASURY|HP$)"
    )),
    ("concepts", re.compile(
        r"^TXT_KEY_(?:CONCEPT|CIV5_|CULTURE_|GOLDEN_AGE_POINTS|"
        r"HAPPINESS_|TOURISM_)"
    )),
)

KEY_CATEGORY_REFINEMENTS = {
    "wonders": {"buildings"},
    "great_people": {"units"},
    "city_states": {"civilizations"},
    "improvements": {"resources"},
}

FIELD_SUBCATEGORIES = {
    "description": "names",
    "shortdescription": "names",
    "name": "names",
    "adjective": "adjectives",
    "help": "help",
    "strategy": "strategy",
    "civilopedia": "civilopedia",
    "quote": "quotes",
    "title": "headings",
    "heading": "headings",
    "caption": "headings",
    "response": "dialogue",
    "uniquenames": "names",
}



def category_from_table(table: str) -> str | None:
    """Map a referenced game-data table to a translator category."""
    for category, pattern in TABLE_CATEGORY_RULES:
        if pattern.match(table):
            return category
    return None


def category_from_key(key: str) -> str | None:
    """Infer a category from a key when no table is available."""
    for category, pattern in KEY_CATEGORY_RULES:
        if pattern.match(key):
            return category
    return None


def subcategory_from_key(
    key: str,
    category: str,
    include_default: bool = True,
) -> str | None:
    """Choose a narrower subject within a translator category."""
    if category == "civilizations":
        if (
            "_SPY_NAME_" in key
            or key.startswith("TXT_KEY_SPY_NAME_")
        ):
            return "spy_names"
        if (
            "DAWN_OF_MAN" in key
            or key.startswith("TXT_KEY_DOM_")
            or "_DOM" in key
        ):
            return "dawn_of_man"
        if key.startswith(("TXT_KEY_TRAIT_", "TXT_KEY_UA_")):
            if any(token in key for token in (
                "_PEDIA",
                "_TEXT",
                "_HEADING",
            )):
                return "civilopedia"
            if "_SHORT" in key:
                return "trait_names"
            return "trait_descriptions"
        if key.startswith("TXT_KEY_LEADER_"):
            if any(token in key for token in (
                "_PEDIA",
                "_TEXT",
                "_HEADING",
                "_LIVED",
                "_SUBTITLE",
                "_TITLES",
                "_FACTOID",
                "_STATBOX",
                "_FACT_",
            )):
                return "civilopedia"
            if any(token in key for token in (
                "FIRSTGREETING",
                "FIRST_GREETING",
                "GREETING",
                "DEFEATED",
                "DECLARE_WAR",
                "ATTACKED",
                "RESPONSE_",
                "DIPLO_",
            )) or re.search(r"_[0-9]+$", key):
                return "dialogue"
            return "leader_names"
        if (
            "_CITY_NAME_" in key
            or key.startswith("TXT_KEY_CITY_NAME_")
            or key.startswith("TXT_KEY_GAUL_CITY_")
            or key.startswith("TXT_KEY_PHOENICIAN_CITY_")
            or re.match(r"^TXT_KEY_CITY_(?!STATE_)", key)
        ):
            return "city_names"
        if "_ADJECTIVE" in key or "_ADJ" in key:
            return "adjectives"
        if any(token in key for token in (
            "_PEDIA",
            "_TEXT",
            "_HEADING",
        )):
            return "civilopedia"
        return "names" if include_default else None

    if category == "city_states":
        if "DIPLO_" in key:
            return "dialogue"
        if "_PEDIA" in key or "_TEXT" in key:
            return "civilopedia"
        if "_HELP" in key or key.startswith("TXT_KEY_BUY_"):
            return "help"
        if "_ADJ" in key or "_ADJECTIVE" in key:
            return "adjectives"
        if "PERSONALITY" in key:
            return "personalities"
        if key.startswith((
            "TXT_KEY_POP_CSTATE_",
            "TXT_KEY_BASE_INFLUENCE_",
            "TXT_KEY_INFLUENCE_",
            "TXT_KEY_LEKMOD_CITY_STATE_",
        )):
            return "interface"
        return "names" if include_default else None

    if category == "civilopedia":
        if "PROMOTION" in key:
            return "promotions"
        if "LEADER" in key:
            return "leaders"
        if "CIVILIZATION" in key or "DAWN_OF_MAN" in key:
            return "civilizations"
        if "UNIT" in key:
            return "units"
        if "BUILDING" in key or "IMPROVEMENT" in key:
            return "buildings_and_improvements"
        if "SCENARIO" in key:
            return "scenarios"
        if "CATEGORY" in key:
            return "headings"
        if "FACTOID" in key:
            return "factoids"
        if "STATBOX" in key:
            return "statboxes"
        if "_HEADING" in key:
            return "headings"
        if "_TITLE" in key:
            return "titles"
        if "_TEXT" in key or key.endswith("_PEDIA"):
            return "articles"
        return "articles" if include_default else None

    if category == "religion":
        if key.startswith("TXT_KEY_BELIEF_"):
            if "_SHORT" in key:
                return "belief_names"
            return "belief_descriptions"
        if key.startswith("TXT_KEY_RELIGION_"):
            return "religion_names"

    if category == "ui":
        prefixes = (
            ("TXT_KEY_NOTIFICATION_", "notifications"),
            ("TXT_KEY_POPUP_", "popups"),
            ("TXT_KEY_REPLAY_", "replay"),
            ("TXT_KEY_CONTROL_", "controls_and_hotkeys"),
            ("TXT_KEY_HOTKEY_", "controls_and_hotkeys"),
            ("TXT_KEY_OPSCREEN_", "controls_and_hotkeys"),
            ("TXT_KEY_ACTIONTYPE_", "controls_and_hotkeys"),
            ("TXT_KEY_INTERFACEMODE_", "controls_and_hotkeys"),
            ("TXT_KEY_RIGHT_BUTTON_", "controls_and_hotkeys"),
            ("TXT_KEY_TOGGLE_", "controls_and_hotkeys"),
            ("TXT_KEY_TP_", "top_panel"),
            ("TXT_KEY_TOP_PANEL_", "top_panel"),
            ("TXT_KEY_TREASURY_", "top_panel"),
            ("TXT_KEY_CITYVIEW_", "city_view"),
            ("TXT_KEY_COMBATPANEL_", "combat_panel"),
            ("TXT_KEY_EUPANEL_", "combat_panel"),
            ("TXT_KEY_HP", "combat_panel"),
            ("TXT_KEY_CHOOSE_INTERNATIONAL_", "trade_routes"),
            ("TXT_KEY_LEKMOD_MENU_", "menus"),
            ("TXT_KEY_LEKMOD_VERSION", "menus"),
            ("TXT_KEY_AD_SETUP_", "game_setup"),
            ("TXT_KEY_GAME_SELECTION_", "game_setup"),
            ("TXT_KEY_RANDOM_", "game_setup"),
            ("TXT_KEY_CO_", "diplomacy_overview"),
            ("TXT_KEY_EO_", "diplomacy_overview"),
            ("TXT_KEY_LEKMOD_RESPOND_", "diplomacy_overview"),
            ("TXT_KEY_LARGE_ESPIONAGE_", "espionage"),
            ("TXT_KEY_OPEN_VICTORY_", "victory"),
            ("TXT_KEY_UI_CHECK_", "compatibility"),
        )
        for prefix, subcategory in prefixes:
            if key.startswith(prefix):
                return subcategory

    if category == "gameplay":
        prefixes = (
            ("TXT_KEY_COMBATMOD_", "combat_modifiers"),
            ("TXT_KEY_ATTACKMOD_", "combat_modifiers"),
            ("TXT_KEY_DEFENSEMOD_", "combat_modifiers"),
            ("TXT_KEY_YIELD_", "yield_breakdowns"),
            ("TXT_KEY_FAITH_FROM_", "yield_breakdowns"),
            ("TXT_KEY_JFD_GOLD_", "yield_breakdowns"),
            ("TXT_KEY_PRODMOD_", "production_modifiers"),
            ("TXT_KEY_PRODUCTION_", "production_modifiers"),
            ("TXT_KEY_GOLDMOD_", "gold_modifiers"),
            ("TXT_KEY_GOODY_", "goody_huts"),
            ("TXT_KEY_LEKMOD_GOODY_", "goody_huts"),
            ("TXT_KEY_LEKMOD_CHOOSE_GOODY_", "goody_huts"),
            ("TXT_KEY_CAN_PARADROP", "unit_actions"),
            ("TXT_KEY_NO_ACTION_", "unit_actions"),
        )
        for prefix, subcategory in prefixes:
            if key.startswith(prefix):
                return subcategory

    if category == "diplomacy":
        if key.startswith("TXT_KEY_DIPLOSTACK_"):
            return "interface"
        if "_ITEM_" in key:
            return "trade_items"
        if key.endswith("_TT"):
            return "tooltips"
        if any(token in key for token in (
            "PACT",
            "EMBASSY",
            "OPENBORDERS",
            "AGREEMENT",
        )):
            return "agreements"
        if "REQUEST" in key:
            return "requests"

    if category == "concepts" and "_SUMMARY" in key:
        return "summaries"

    if category == "multiplayer":
        if any(token in key for token in (
            "PROPOSAL",
            "PROPOSE",
            "VOTE_CHART",
            "CC_VOTE",
        )):
            return "proposals"
        if "TURN_TIMER" in key:
            return "turn_timer"
        if key.startswith("TXT_KEY_MULTIPLAYER_"):
            return "lobby"
        if key.startswith("TXT_KEY_LEKMOD_MP_"):
            return "status"

    if category == "world_congress":
        if key.startswith("TXT_KEY_RESOLUTION_"):
            return "resolutions"
        return "interface"

    if category == "great_people":
        if "POINTS_GAINED" in key:
            return "progress"
        return "names" if include_default else None

    if category == "great_works":
        return "names" if include_default else None

    if category == "scenarios":
        if "MEDIEVAL_SCENARIO" in key:
            return "medieval"
        if "STEAMPUNK_SCENARIO" in key:
            return "steampunk"

    if category == "terrain":
        if "_HELP" in key:
            return "help"
        if "_TEXT" in key or "_PEDIA" in key:
            return "civilopedia"
        return "features" if include_default else None

    suffixes = (
        ("_STRATEGY", "strategy"),
        ("_HELP", "help"),
        ("_PEDIA", "civilopedia"),
        ("_QUOTE", "quotes"),
        ("_ADJ", "adjectives"),
        ("_SHORT_DESC", "names"),
        ("_DESC", "names"),
        ("_NAME", "names"),
        ("_TITLE", "headings"),
        ("_HEADING", "headings"),
        ("_BODY", "civilopedia"),
        ("_TEXT", "civilopedia"),
    )
    for suffix, subcategory in suffixes:
        if key.endswith(suffix) or f"{suffix}_" in key:
            return subcategory

    if not include_default:
        return None
    if category in {
        "buildings",
        "game_options",
        "improvements",
        "policies",
        "promotions",
        "resources",
        "technologies",
        "units",
        "wonders",
    }:
        return "names"
    return "general"


def classify_context(
    key: str,
    references: list[dict[str, str]],
) -> tuple[str, str, str]:
    """Prefer actual game-data references, then key-based inference."""
    context_categories = {
        category
        for reference in references
        if (
            category := category_from_table(reference["table"])
        ) is not None
    }
    key_category = category_from_key(key)

    if len(context_categories) == 1:
        context_category = next(iter(context_categories))
        if (
            key_category in KEY_CATEGORY_REFINEMENTS
            and context_category
            in KEY_CATEGORY_REFINEMENTS[key_category]
        ):
            category = key_category
            category_source = "key_refinement"
        else:
            category = context_category
            category_source = "database_reference"
    elif key_category is not None:
        category = key_category
        category_source = "key_fallback"
    else:
        category = "unclassified"
        category_source = "unclassified"

    subcategories = {
        FIELD_SUBCATEGORIES[field]
        for reference in references
        if (
            field := reference["field"].casefold()
        ) in FIELD_SUBCATEGORIES
    }
    key_subcategory = subcategory_from_key(
        key,
        category,
        include_default=False,
    )

    if len(subcategories) == 1:
        database_subcategory = next(iter(subcategories))
        if (
            key_subcategory is not None
            and key_subcategory != database_subcategory
        ):
            subcategory = key_subcategory
            subcategory_source = "key_refinement"
        else:
            subcategory = database_subcategory
            subcategory_source = "database_reference"
    elif key_subcategory is not None:
        subcategory = key_subcategory
        subcategory_source = "key_fallback"
    else:
        subcategory = subcategory_from_key(
            key,
            category,
            include_default=True,
        )
        subcategory_source = "key_fallback"

    return (
        category,
        subcategory,
        f"{category_source}/{subcategory_source}",
    )
