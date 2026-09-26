from __future__ import annotations

from contextlib import closing, redirect_stdout
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_primary_localization as primary_audit
import build_localization_catalog as catalog_builder
import build_fallback_preview as fallback_builder
import build_shipped_localization as shipped_builder
import sync_primary_english
from lekmod_localization.vanilla_snapshot import read_snapshot, write_snapshot
from lekmod_localization.fallback import fallback_entries, preview_files, write_preview
from lekmod_localization.shipped import approved_entries, install_candidate, read_approvals
from lekmod_localization.workspace import editor_source_fingerprint
import xml.etree.ElementTree as ET


class LocalizationCatalogTests(unittest.TestCase):
    """Verify source comparison, classification, and review workspaces."""

    def setUp(self):
        """Give every test an isolated game database and source tree."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def database(
        self,
        include_sentinel: bool = False,
        name: str = "Localization-Merged.db",
    ) -> Path:
        """Create a minimal multilingual vanilla database fixture."""
        path = self.root / name

        with closing(sqlite3.connect(path)) as database:
            for locale in ("en_US", "RU_RU", "DE_DE"):
                database.execute(
                    f'''CREATE TABLE "LocalizedText_{locale}" (
                        Tag TEXT PRIMARY KEY,
                        Text TEXT,
                        Gender TEXT,
                        Plurality TEXT
                    )'''
                )
                database.execute(
                    f'''CREATE VIEW "Language_{locale}" AS
                        SELECT Tag, Text, Gender, Plurality
                        FROM "LocalizedText_{locale}"'''
                )

            database.executemany(
                "INSERT INTO LocalizedText_en_US VALUES (?, ?, ?, ?)",
                [
                    (
                        "TXT_KEY_UNIT_SAME",
                        "Same [ICON_FOOD]",
                        None,
                        None,
                    ),
                    (
                        "TXT_KEY_UNIT_METADATA_SAME",
                        "Same metadata",
                        "neuter:an",
                        "2",
                    ),
                    (
                        "TXT_KEY_BUILDING_CHANGED",
                        "Old building",
                        "masculine",
                        None,
                    ),
                    (
                        "TXT_KEY_BUILDING_CONFLICT",
                        "Old conflict",
                        None,
                        None,
                    ),
                    (
                        "TXT_KEY_POLICY_METADATA",
                        "Metadata only",
                        "masculine",
                        None,
                    ),
                    (
                        "TXT_KEY_UNPREFIXED_QUOTE",
                        "Old quote",
                        None,
                        None,
                    ),
                ],
            )
            database.executemany(
                "INSERT INTO LocalizedText_RU_RU VALUES (?, ?, ?, ?)",
                [
                    (
                        "TXT_KEY_BUILDING_CHANGED",
                        "Старое здание",
                        "masculine",
                        None,
                    ),
                    (
                        "TXT_KEY_BUILDING_CONFLICT",
                        "Старый конфликт",
                        None,
                        None,
                    ),
                ],
            )

            if include_sentinel:
                database.execute(
                    "INSERT INTO LocalizedText_en_US VALUES (?, ?, ?, ?)",
                    (
                        "TXT_KEY_LEKMOD_VERSION",
                        "Contaminated",
                        None,
                        None,
                    ),
                )

            database.commit()

        return path

    def source(self) -> Path:
        """Create a primary source with unchanged, changed, and new keys."""
        path = self.root / "LEKMOD" / "Override" / "source.xml"
        path.parent.mkdir(parents=True)
        path.write_text(
            '''<GameData>
<Buildings>
  <Row Description="TXT_KEY_BUILDING_CHANGED"/>
  <Row Description="TXT_KEY_BUILDING_CONFLICT"/>
</Buildings>
<UnitPromotions>
  <Row Description="TXT_KEY_PROMOTION_NEW"
       Help="TXT_KEY_PROMOTION_NEW_HELP"/>
</UnitPromotions>
<Technologies>
  <Row Quote="TXT_KEY_UNPREFIXED_QUOTE"/>
</Technologies>
<Language_en_US>
  <Replace Tag="TXT_KEY_UNIT_SAME">
    <Text>
      Same [ICON_FOOD]
    </Text>
  </Replace>
  <Replace Tag="TXT_KEY_UNIT_METADATA_SAME">
    <Text>Same metadata</Text>
    <Gender>neuter:an</Gender>
    <Plurality>2</Plurality>
  </Replace>
  <Replace Tag="TXT_KEY_BUILDING_CHANGED">
    <Text>New building</Text>
    <Gender>feminine</Gender>
  </Replace>
  <Replace Tag="TXT_KEY_BUILDING_CONFLICT">
    <Text>Primary conflict</Text>
  </Replace>
  <Replace Tag="TXT_KEY_POLICY_METADATA">
    <Text>Metadata only</Text>
    <Gender>feminine</Gender>
  </Replace>
  <Row Tag="TXT_KEY_PROMOTION_NEW">
    <Text>New {1_Num} %s</Text>
  </Row>
  <Row Tag="TXT_KEY_PROMOTION_NEW_HELP">
    <Text>English help</Text>
  </Row>
  <Replace Tag="TXT_KEY_UNPREFIXED_QUOTE">
    <Text>New quote</Text>
  </Replace>
</Language_en_US>
</GameData>''',
            encoding="utf-8",
        )
        return path

    def art(self) -> Path:
        """Create Art definitions with translations and deliberate conflicts."""
        root = self.root / "LEKMOD" / "Art"
        root.mkdir(parents=True)
        (root / "one.xml").write_text(
            '''<GameData>
<Units>
  <Row Description="TXT_KEY_UNIT_ART_ONLY"/>
</Units>
<Language_en_US>
  <Delete Tag="TXT_KEY_UNIT_ART_ONLY"/>
  <Row Tag="TXT_KEY_UNIT_ART_ONLY">
    <Text>Art-only unit</Text>
  </Row>
  <Row Tag="TXT_KEY_BUILDING_CONFLICT">
    <Text>Art conflict</Text>
  </Row>
</Language_en_US>
<Language_RU_RU>
  <Row Tag="TXT_KEY_BUILDING_CHANGED">
    <Text>Новое здание</Text>
  </Row>
  <Row Tag="TXT_KEY_PROMOTION_NEW">
    <Text>[COLOR_WARNING_TEXT](RU_RU text) [ENDCOLOR]New {1_Num} %s</Text>
  </Row>
  <Row Tag="TXT_KEY_PROMOTION_NEW_HELP">
    <Text>English help</Text>
  </Row>
</Language_RU_RU>
</GameData>''',
            encoding="utf-8",
        )
        (root / "two.xml").write_text(
            '''<GameData>
<Language_RU_RU>
  <Row Tag="TXT_KEY_PROMOTION_NEW_HELP">
    <Text>Другая подсказка</Text>
  </Row>
</Language_RU_RU>
</GameData>''',
            encoding="utf-8",
        )
        return root

    def build(self):
        """Assemble the shared catalog fixture through production APIs."""
        database = self.database()
        all_vanilla, fingerprints = (
            catalog_builder.load_vanilla_locales(database)
        )
        source = self.source()
        art = self.art()
        report = primary_audit.parse_source(source, "en_US")
        localizations, references, repository_summary = (
            catalog_builder.load_repository_localizations(
                source,
                art,
                self.root,
            )
        )
        catalog = catalog_builder.build_catalog(
            report,
            all_vanilla["en_US"],
            sorted(all_vanilla),
            database.name,
            fingerprints["en_US"],
            localizations,
            references,
            repository_summary,
        )
        return (
            database,
            source,
            all_vanilla,
            localizations,
            references,
            catalog,
        )

    def test_clean_database_loads_all_locales_read_only(self):
        """Load every official locale without mutating the SQLite input."""
        database = self.database()
        original = database.read_bytes()

        entries, fingerprints = (
            catalog_builder.load_vanilla_locales(database)
        )
        legacy_entries, locales, legacy_fingerprint = (
            catalog_builder.load_vanilla_database(database)
        )

        self.assertEqual(
            sorted(entries),
            ["DE_DE", "RU_RU", "en_US"],
        )
        self.assertEqual(len(entries["en_US"]), 6)
        self.assertEqual(len(fingerprints["RU_RU"]), 64)
        self.assertEqual(legacy_entries, entries["en_US"])
        self.assertEqual(locales, ["DE_DE", "en_US", "RU_RU"])
        self.assertEqual(
            legacy_fingerprint,
            fingerprints["en_US"],
        )
        self.assertEqual(database.read_bytes(), original)

    def test_frozen_snapshot_matches_direct_database_input(self):
        """The catalog receives identical rows and hashes from either input."""
        database = self.database()
        snapshot = self.root / "vanilla-snapshot.json.gz"
        write_snapshot(database, snapshot)
        direct = catalog_builder.load_vanilla_locales(database)
        self.assertEqual(direct, read_snapshot(snapshot))
        self.assertEqual(
            catalog_builder.create_parser().parse_args([
                "--vanilla-snapshot", str(snapshot), "--locale", "RU_RU",
            ]).vanilla_snapshot,
            snapshot,
        )

    def test_fallback_preview_keeps_vanilla_translations_and_conflicts_out(self):
        """Only Lekmod's new and changed keys receive English preview rows."""
        database, _source, _vanilla, _localizations, _references, catalog = self.build()
        entries = fallback_entries(catalog)
        self.assertEqual(len(entries), 5)
        self.assertNotIn("TXT_KEY_UNIT_SAME", entries)
        self.assertNotIn("TXT_KEY_UNIT_METADATA_SAME", entries)
        self.assertNotIn("TXT_KEY_BUILDING_CONFLICT", entries)
        self.assertEqual(entries["TXT_KEY_BUILDING_CHANGED"]["Text"], "New building")
        self.assertEqual(entries["TXT_KEY_BUILDING_CHANGED"]["Gender"], "feminine")
        self.assertEqual(entries["TXT_KEY_PROMOTION_NEW"]["Text"], "New {1_Num} %s")

        files = preview_files(catalog, ["en_US", "RU_RU", "DE_DE"])
        self.assertEqual(set(files), {"RU_RU.xml", "DE_DE.xml", "manifest.json"})
        root = ET.fromstring(files["RU_RU.xml"])
        rows = root.findall("./Language_RU_RU/Replace")
        self.assertEqual(len(rows), 5)
        self.assertNotIn("TXT_KEY_UNIT_SAME", {row.get("Tag") for row in rows})
        manifest = json.loads(files["manifest.json"])
        self.assertEqual(manifest["withheld_conflicts"], 1)
        self.assertEqual(manifest["empty_english_text_keys"], [])
        self.assertEqual(manifest["status"], "review_only_not_installed")

        output = self.root / "build" / "localization" / "fallback-preview"
        write_preview(output, files, database, root=self.root)
        self.assertEqual((output / "RU_RU.xml").read_bytes(), files["RU_RU.xml"])
        (output / "RU_RU.xml").write_text("manual edit", encoding="utf-8")
        with self.assertRaisesRegex(catalog_builder.CatalogError, "manually changed"):
            write_preview(output, files, database, root=self.root)

    def test_fallback_preview_preserves_explicit_empty_english_text(self):
        """An empty Text is a valid source value; a missing Text is not."""
        _database, _source, _vanilla, _localizations, _references, catalog = self.build()
        catalog = deepcopy(catalog)
        entries = next(iter(next(iter(catalog["source_categories"].values())).values()))
        key = "TXT_KEY_EMPTY_HELP"
        entries[key] = {
            "classification": "lekmod_new",
            "lekmod_en_US": {
                "status": "present", "text": "", "format_tokens": {},
                "gender": None, "plurality": None,
            },
        }
        catalog["summary"]["requires_translation"] += 1
        files = preview_files(catalog, ["en_US", "RU_RU"])
        self.assertEqual(json.loads(files["manifest.json"])["empty_english_text_keys"], [key])
        row = ET.fromstring(files["RU_RU.xml"]).find(
            f"./Language_RU_RU/Replace[@Tag='{key}']"
        )
        self.assertIsNotNone(row)
        self.assertIsNotNone(row.find("Text"))
        self.assertIsNone(row.find("Text").text)

        entries[key]["lekmod_en_US"]["text"] = None
        with self.assertRaisesRegex(catalog_builder.CatalogError, key):
            preview_files(catalog, ["en_US", "RU_RU"])

    def test_fallback_preview_requires_synced_english_and_frozen_vanilla(self):
        """End-to-end preview stops before replacing stale generated output."""
        database = self.database()
        snapshot = self.root / "vanilla-snapshot.json.gz"
        write_snapshot(database, snapshot)
        source = self.source()
        source.write_text(
            source.read_text(encoding="utf-8")
            .replace("<Language_en_US>", "\t<Language_en_US>")
            .replace("</Language_en_US>", "\t</Language_en_US>"),
            encoding="utf-8",
        )
        english_source = self.root / "localization" / "en_US" / "primary.xml"
        sync_primary_english.bootstrap(english_source, source)
        art = self.art()
        output = self.root / "build" / "localization" / "fallback-preview"
        summary = fallback_builder.build_preview(
            snapshot, output, source, english_source, art, root=self.root,
        )
        self.assertEqual(summary["requires_translation"], 5)
        self.assertEqual(summary["requires_source_review"], 1)
        before = (output / "RU_RU.xml").read_bytes()

        english_source.write_text(
            english_source.read_text(encoding="utf-8").replace(
                "New building", "Revised building"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(catalog_builder.CatalogError, "out of sync"):
            fallback_builder.build_preview(
                snapshot, output, source, english_source, art, root=self.root,
            )
        self.assertEqual((output / "RU_RU.xml").read_bytes(), before)

    def test_shipped_fallback_is_repeatable_and_approvals_are_pinned(self):
        """One checked translation wins; stale and malformed texts cannot ship."""
        database, source, _vanilla, _loc, _refs, catalog = self.build()
        snapshot = self.root / "vanilla-snapshot.json.gz"
        write_snapshot(database, snapshot)
        source.write_text(
            source.read_text(encoding="utf-8")
            .replace("<Language_en_US>", "\t<Language_en_US>")
            .replace("</Language_en_US>", "\t</Language_en_US>")
            .replace(
                "</GameData>",
                "<Language_RU_RU>\n</Language_RU_RU>\n</GameData>",
            ),
            encoding="utf-8",
        )
        english_source = self.root / "localization" / "en_US" / "primary.xml"
        sync_primary_english.bootstrap(english_source, source)
        key = "TXT_KEY_BUILDING_CHANGED"
        entry = next(
            entry for category in catalog["source_categories"].values()
            for subcategory in category.values()
            for candidate, entry in subcategory.items() if candidate == key
        )
        approval = {
            "source_fingerprint": editor_source_fingerprint(key, entry),
            "text": "Новое здание",
        }
        approval_path = self.root / "approved-translations.json"
        approval_path.write_text(json.dumps({
            "schema_version": 1,
            "translations": {"RU_RU": {key: approval}},
        }), encoding="utf-8")

        candidate, summary = shipped_builder.build_candidate(
            snapshot, source, english_source, self.root / "LEKMOD" / "Art", approval_path,
            root=self.root,
        )
        self.assertEqual(summary["entries_per_locale"], 5)
        self.assertEqual(summary["conflicts_withheld"], 1)
        self.assertEqual(summary["approved_translations"], 1)
        root = ET.fromstring(candidate)
        tags = {
            row.get("Tag"): row.findtext("Text")
            for row in root.findall("./Language_RU_RU/Replace")
        }
        self.assertEqual(tags[key], "Новое здание")
        self.assertEqual(tags["TXT_KEY_PROMOTION_NEW"], "New {1_Num} %s")
        self.assertNotIn("TXT_KEY_UNIT_SAME", tags)
        self.assertNotIn("TXT_KEY_BUILDING_CONFLICT", tags)
        self.assertEqual(len(root.findall("./Language_RU_RU")), 1)
        source.write_text(candidate, encoding="utf-8")
        second, second_summary = shipped_builder.build_candidate(
            snapshot, source, english_source, self.root / "LEKMOD" / "Art", approval_path,
            root=self.root,
        )
        self.assertEqual((candidate, summary), (second, second_summary))

        approval["source_fingerprint"] = "stale"
        with self.assertRaisesRegex(catalog_builder.CatalogError, "stale"):
            approved_entries(catalog, ["RU_RU", "DE_DE"], {"RU_RU": {key: approval}})
        approval["source_fingerprint"] = editor_source_fingerprint(key, entry)
        approval["text"] = "Оборвана {1_Num}"
        with self.assertRaisesRegex(catalog_builder.CatalogError, "tokens"):
            approved_entries(catalog, ["RU_RU", "DE_DE"], {"RU_RU": {key: approval}})

    def test_cli_build_from_snapshot_keeps_catalog_classifications(self):
        """Offline builds preserve classifications without rereading game DB."""
        database = self.database()
        snapshot = self.root / "vanilla-snapshot.json.gz"
        write_snapshot(database, snapshot)
        source = self.source()
        art = self.art()
        summaries = []
        for option, vanilla in (
            ("--vanilla-db", database),
            ("--vanilla-snapshot", snapshot),
        ):
            output = self.root / option.lstrip("-")
            arguments = [
                "build_localization_catalog.py", option, str(vanilla),
                "--source", str(source), "--art-root", str(art),
                "--locale", "RU_RU", "--output", str(output / "catalog.json"),
                "--review-output", str(output / "review"),
                "--editor-output", str(output / "editor"),
            ]
            with patch.object(sys, "argv", arguments), redirect_stdout(io.StringIO()):
                self.assertEqual(catalog_builder.main(), 0)
            catalog = json.loads((output / "catalog.json").read_text(encoding="utf-8"))
            summaries.append(catalog["summary"])
        self.assertEqual(summaries[0], summaries[1])

    def test_locale_selection_is_case_insensitive(self):
        """Resolve CLI locale requests without changing database spelling."""
        english, targets = catalog_builder.select_locales(
            ["ru_ru", "DE_de", "RU_RU"],
            ["DE_DE", "RU_RU", "en_US"],
        )
        self.assertEqual(english, "en_US")
        self.assertEqual(targets, ["RU_RU", "DE_DE"])
        with self.assertRaisesRegex(
            catalog_builder.CatalogError,
            "Language_ZZ_ZZ was not found",
        ):
            catalog_builder.select_locales(
                ["ZZ_ZZ"],
                ["en_US", "RU_RU"],
            )
        with self.assertRaisesRegex(
            catalog_builder.CatalogError,
            "non-English",
        ):
            catalog_builder.select_locales(
                ["en_us"],
                ["en_US", "RU_RU"],
            )

    def test_unknown_and_contaminated_database_are_rejected(self):
        """Reject absent locales and databases already polluted by Lekmod."""
        database = self.database()
        with self.assertRaisesRegex(
            catalog_builder.CatalogError,
            "Language_ZZ_ZZ",
        ):
            catalog_builder.load_vanilla_locales(
                database,
                ["en_US", "ZZ_ZZ"],
            )

        with self.assertRaisesRegex(
            catalog_builder.CatalogError,
            "Lekmod sentinel keys",
        ):
            catalog_builder.load_vanilla_database(
                self.database(
                    include_sentinel=True,
                    name="Contaminated.db",
                )
            )

    def test_language_sql_is_parsed_without_execution(self):
        """Accept supported localization SQL while never executing it."""
        entries, skipped = catalog_builder.parse_language_sql(
            """-- definitions only
INSERT OR REPLACE INTO Language_en_US (Tag, Text)
VALUES
('TXT_KEY_SQL', 'O''Brien'),
('TXT_KEY_SQL', 'Last value'),
('', 'Template');
UPDATE Language_en_US
SET Gender = 'feminine'
WHERE Tag IN ('TXT_KEY_SQL');
"""
        )

        self.assertEqual(skipped, 1)
        self.assertEqual(
            entries["en_US"]["TXT_KEY_SQL"],
            {
                "Tag": "TXT_KEY_SQL",
                "Text": "Last value",
                "Gender": "feminine",
            },
        )
        with self.assertRaisesRegex(
            catalog_builder.CatalogError,
            "unsupported SQL statement",
        ):
            catalog_builder.parse_language_sql(
                "DROP TABLE Language_en_US;"
            )

    def test_repository_loader_includes_xml_and_sql_definitions(self):
        """Cover all supported repository localization source formats."""
        if not catalog_builder.DEFAULT_SOURCE.is_file():
            self.skipTest("repository sources are not available")

        localizations, _references, summary = (
            catalog_builder.load_repository_localizations(
                catalog_builder.DEFAULT_SOURCE,
                catalog_builder.DEFAULT_ART_ROOT,
            )
        )
        english = catalog_builder.locale_entries(
            localizations,
            "en_US",
        )

        self.assertIn("TXT_KEY_LEKMOD_VERSION", english)
        self.assertIn("TXT_KEY_RESOURCE_TIN", english)
        self.assertIn("TXT_KEY_BUILDING_MC_DZIMBABWE", english)
        self.assertEqual(summary["sql_language_files"], 1)
        self.assertGreater(summary["skipped_empty_selectors"], 0)

    def test_repository_sources_are_combined_without_guessing_load_order(self):
        """Retain conflicting source variants instead of inventing a winner."""
        (
            _database,
            _source,
            _vanilla,
            localizations,
            references,
            catalog,
        ) = self.build()

        classifications = catalog["summary"]["classifications"]
        self.assertEqual(
            classifications,
            {
                "lekmod_new": 3,
                "source_conflict": 1,
                "vanilla_metadata_only": 1,
                "vanilla_modified": 2,
                "vanilla_unchanged": 2,
            },
        )
        self.assertEqual(
            catalog["summary"]["requires_translation"],
            5,
        )
        self.assertEqual(
            catalog["summary"]["requires_source_review"],
            1,
        )
        self.assertIn(
            "TXT_KEY_UNIT_ART_ONLY",
            localizations["en_US"],
        )
        self.assertEqual(
            references["TXT_KEY_UNPREFIXED_QUOTE"][0]["table"],
            "Technologies",
        )

        conflict = catalog["source_categories"]["buildings"][
            "names"
        ]["TXT_KEY_BUILDING_CONFLICT"]
        self.assertEqual(
            conflict["lekmod_en_US"]["status"],
            "source_conflict",
        )
        self.assertIsNone(conflict["lekmod_en_US"]["text"])
        self.assertEqual(
            {
                variant["text"]
                for variant in conflict["lekmod_en_US"]["variants"]
            },
            {"Art conflict", "Primary conflict"},
        )

    def test_categories_and_subcategories_prefer_database_references(self):
        """Use gameplay references before deterministic key-name fallbacks."""
        *_unused, catalog = self.build()

        quote = catalog["source_categories"]["technologies"][
            "quotes"
        ]["TXT_KEY_UNPREFIXED_QUOTE"]
        self.assertEqual(
            quote["context_source"],
            "database_reference/database_reference",
        )

        promotion = catalog["source_categories"]["promotions"][
            "names"
        ]["TXT_KEY_PROMOTION_NEW"]
        self.assertEqual(
            promotion["lekmod_en_US"]["format_tokens"],
            {"%s": 1, "{1_Num}": 1},
        )

        art_unit = catalog["source_categories"]["units"][
            "names"
        ]["TXT_KEY_UNIT_ART_ONLY"]
        self.assertEqual(
            art_unit["classification"],
            "lekmod_new",
        )

    def test_game_taxonomy_has_deterministic_key_fallbacks(self):
        """Keep representative key families in stable game categories."""
        expected = {
            "TXT_KEY_CITYSTATE_AUCKLAND": (
                "city_states",
                "names",
            ),
            "TXT_KEY_CIVLOPEDIA_LEADERS_HEADING_1": (
                "civilopedia",
                "leaders",
            ),
            "TXT_KEY_WONDER_LOUVRE_HELP": (
                "wonders",
                "help",
            ),
            "TXT_KEY_IMPROVEMENT_FORT": (
                "improvements",
                "names",
            ),
            "TXT_KEY_SWEDISH_HUMANITARIAN_1": (
                "great_people",
                "names",
            ),
            "TXT_KEY_DEFENSEMOD_FORTIFICATION": (
                "gameplay",
                "combat_modifiers",
            ),
            "TXT_KEY_MP_PROPOSAL_TITLE": (
                "multiplayer",
                "proposals",
            ),
            "TXT_KEY_TRAIT_AKKAD": (
                "civilizations",
                "trait_descriptions",
            ),
            "TXT_KEY_TRAIT_AKKAD_SHORT": (
                "civilizations",
                "trait_names",
            ),
            "TXT_KEY_BELIEF_CATHEDRALS": (
                "religion",
                "belief_descriptions",
            ),
            "TXT_KEY_BELIEF_CATHEDRALS_SHORT": (
                "religion",
                "belief_names",
            ),
            "TXT_KEY_LEADER_US_OWAIN_BULLIED_PROTECTED_CITY_STATE_1": (
                "civilizations",
                "dialogue",
            ),
            "TXT_KEY_GAUL_CITY_BIBRACTE": (
                "civilizations",
                "city_names",
            ),
            "TXT_KEY_MISC_GREAT_PERSON": (
                "great_people",
                "names",
            ),
            "TXT_KEY_MISC_PLAYERS_SIGN_DEFENSIVE_PACT": (
                "diplomacy",
                "agreements",
            ),
            "TXT_KEY_LEKMOD_GOODY_TITLE": (
                "gameplay",
                "goody_huts",
            ),
        }

        for key, classification in expected.items():
            with self.subTest(key=key):
                category, subcategory, source = (
                    catalog_builder.classify_context(key, [])
                )
                self.assertEqual(
                    (category, subcategory),
                    classification,
                )
                self.assertEqual(
                    source,
                    "key_fallback/key_fallback",
                )

        category, subcategory, source = (
            catalog_builder.classify_context(
                "TXT_KEY_IMPROVEMENT_FORT",
                [{"table": "Resources", "field": "Description"}],
            )
        )
        self.assertEqual(
            (category, subcategory, source),
            (
                "improvements",
                "names",
                "key_refinement/database_reference",
            ),
        )

    def test_source_conflicts_have_a_dedicated_review(self):
        """Withhold unresolved English conflicts from translator files."""
        *_unused, catalog = self.build()
        review = catalog_builder.build_source_conflict_review(
            catalog
        )

        self.assertEqual(review["status"], "developer_review_required")
        self.assertEqual(review["summary"]["entries"], 1)
        self.assertEqual(
            review["summary"]["categories"],
            {"buildings": 1},
        )
        conflict = review["categories"]["buildings"]["names"][
            "TXT_KEY_BUILDING_CONFLICT"
        ]
        self.assertEqual(
            {
                variant["text"]
                for variant in conflict["lekmod_en_US"]["variants"]
            },
            {"Art conflict", "Primary conflict"},
        )

    def test_locale_review_has_three_text_layers_and_character_counts(self):
        """Expose official and Lekmod text layers with review metadata."""
        (
            _database,
            _source,
            all_vanilla,
            localizations,
            _references,
            catalog,
        ) = self.build()
        documents, manifest = catalog_builder.build_locale_review(
            catalog,
            "RU_RU",
            all_vanilla["en_US"],
            all_vanilla["RU_RU"],
            localizations,
        )

        changed = documents["buildings"]["subcategories"][
            "names"
        ]["TXT_KEY_BUILDING_CHANGED"]
        self.assertEqual(documents["buildings"]["schema_version"], 2)
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(
            changed["official_game_en_US"]["text"],
            "Old building",
        )
        self.assertEqual(
            changed["official_game"]["text"],
            "Старое здание",
        )
        self.assertEqual(
            changed["lekmod_en_US"]["text"],
            "New building",
        )
        self.assertEqual(
            changed["lekmod_target"]["text"],
            "Новое здание",
        )
        self.assertEqual(
            changed["official_game"]["characters"],
            len("Старое здание"),
        )
        self.assertEqual(
            changed["lekmod_en_US"]["characters"],
            len("New building"),
        )
        self.assertEqual(
            changed["lekmod_target"]["characters"],
            len("Новое здание"),
        )

        new = documents["promotions"]["subcategories"][
            "names"
        ]["TXT_KEY_PROMOTION_NEW"]
        self.assertEqual(
            new["official_game"]["status"],
            "not_in_vanilla",
        )
        self.assertEqual(
            new["lekmod_target"]["status"],
            "placeholder",
        )

        help_entry = documents["promotions"]["subcategories"][
            "help"
        ]["TXT_KEY_PROMOTION_NEW_HELP"]
        self.assertEqual(
            help_entry["lekmod_target"]["status"],
            "source_conflict",
        )
        self.assertEqual(
            {
                variant["text"]
                for variant in help_entry["lekmod_target"]["variants"]
            },
            {"English help", "Другая подсказка"},
        )

        quote = documents["technologies"]["subcategories"][
            "quotes"
        ]["TXT_KEY_UNPREFIXED_QUOTE"]
        self.assertEqual(
            quote["official_game"]["status"],
            "missing_in_vanilla_locale",
        )
        self.assertEqual(
            quote["lekmod_target"]["status"],
            "missing",
        )
        self.assertEqual(manifest["summary"]["entries"], 5)
        self.assertEqual(
            manifest["summary"]["target_status"],
            {
                "missing": 2,
                "placeholder": 1,
                "present": 1,
                "source_conflict": 1,
            },
        )
        self.assertNotIn(
            "TXT_KEY_BUILDING_CONFLICT",
            documents["buildings"]["subcategories"]["names"],
        )

    def test_editor_csv_preserves_edits_and_detects_stale_sources(self):
        """Preserve drafts but stop refreshes after English source changes."""
        (
            database,
            source,
            all_vanilla,
            localizations,
            _references,
            catalog,
        ) = self.build()
        review = catalog_builder.build_locale_review(
            catalog,
            "RU_RU",
            all_vanilla["en_US"],
            all_vanilla["RU_RU"],
            localizations,
        )
        output = self.root / "build" / "editor"

        # Initial export must be spreadsheet-friendly and self-describing.
        catalog_builder.write_editor_workspace(
            output,
            {"RU_RU": review},
            source,
            database,
        )

        manifest = json.loads(
            (output / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(
            manifest["editable_fields"],
            list(catalog_builder.EDITOR_EDITABLE_FIELDS),
        )

        buildings_path = output / "RU_RU" / "buildings.csv"
        self.assertTrue(
            buildings_path.read_bytes().startswith(b"\xef\xbb\xbf")
        )
        with buildings_path.open(
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            rows = list(csv.DictReader(handle))

        changed = next(
            row
            for row in rows
            if row["key"] == "TXT_KEY_BUILDING_CHANGED"
        )
        self.assertEqual(changed["vanilla_en_US"], "Old building")
        self.assertEqual(changed["vanilla_target"], "Старое здание")
        self.assertEqual(changed["lekmod_en_US"], "New building")
        self.assertEqual(changed["lekmod_target"], "Новое здание")
        self.assertEqual(changed["translation"], "Новое здание")
        self.assertEqual(changed["translation_status"], "current")
        self.assertEqual(
            changed["translation_characters"],
            str(len("Новое здание")),
        )
        self.assertEqual(
            changed["target_change"],
            "modified_from_vanilla",
        )

        promotions_path = output / "RU_RU" / "promotions.csv"
        with promotions_path.open(
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            promotion_rows = list(csv.DictReader(handle))
        promotion = next(
            row
            for row in promotion_rows
            if row["key"] == "TXT_KEY_PROMOTION_NEW"
        )
        self.assertEqual(
            promotion["english_change"],
            "new_in_lekmod",
        )
        self.assertEqual(
            promotion["vanilla_en_US_status"],
            "not_in_vanilla",
        )
        self.assertEqual(promotion["translation"], "")
        self.assertEqual(promotion["translation_status"], "missing")
        self.assertEqual(
            json.loads(promotion["required_format_tokens"]),
            {"%s": 1, "{1_Num}": 1},
        )

        # An untouched row follows a newer repository translation.
        target_record = review[0]["buildings"]["subcategories"][
            "names"
        ]["TXT_KEY_BUILDING_CHANGED"]["lekmod_target"]
        target_record["text"] = "Обновлённое здание"
        target_record["characters"] = len("Обновлённое здание")
        catalog_builder.write_editor_workspace(
            output,
            {"RU_RU": review},
            source,
            database,
        )
        with buildings_path.open(
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            rows = list(csv.DictReader(handle))
        changed = next(
            row
            for row in rows
            if row["key"] == "TXT_KEY_BUILDING_CHANGED"
        )
        self.assertEqual(changed["lekmod_target"], "Обновлённое здание")
        self.assertEqual(changed["translation"], "Обновлённое здание")

        # Manual translation fields survive a normal workspace refresh.
        draft = "Перевод, с запятой\nи новой строкой"
        changed["translation"] = draft
        changed["translator_note"] = "Проверить в игре"
        with buildings_path.open(
            mode="w",
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=catalog_builder.EDITOR_FIELDNAMES,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)

        catalog_builder.write_editor_workspace(
            output,
            {"RU_RU": review},
            source,
            database,
        )
        with buildings_path.open(
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            refreshed = {
                row["key"]: row for row in csv.DictReader(handle)
            }
        changed = refreshed["TXT_KEY_BUILDING_CHANGED"]
        self.assertEqual(changed["translation"], draft)
        self.assertEqual(
            changed["translator_note"],
            "Проверить в игре",
        )
        self.assertEqual(changed["translation_status"], "draft")
        self.assertEqual(
            changed["translation_characters"],
            str(len("Перевод, с запятой и новой строкой")),
        )

        # A changed English source blocks stale drafts before replacement.
        before_failed_refresh = buildings_path.read_bytes()
        review[0]["buildings"]["subcategories"]["names"][
            "TXT_KEY_BUILDING_CHANGED"
        ]["lekmod_en_US"]["text"] = "Changed after translation"
        with self.assertRaisesRegex(
            catalog_builder.CatalogError,
            "English changed",
        ):
            catalog_builder.write_editor_workspace(
                output,
                {"RU_RU": review},
                source,
                database,
            )
        self.assertEqual(
            buildings_path.read_bytes(),
            before_failed_refresh,
        )

    def test_generated_workspace_is_atomic_and_protects_inputs(self):
        """Replace generated output atomically and never overwrite inputs."""
        (
            database,
            source,
            all_vanilla,
            localizations,
            _references,
            catalog,
        ) = self.build()
        review = catalog_builder.build_locale_review(
            catalog,
            "RU_RU",
            all_vanilla["en_US"],
            all_vanilla["RU_RU"],
            localizations,
        )
        source_conflicts = (
            catalog_builder.build_source_conflict_review(catalog)
        )
        output = self.root / "build" / "catalog.json"
        review_output = self.root / "build" / "review"

        catalog_builder.write_catalog(
            output,
            catalog,
            source,
            database,
        )
        catalog_builder.write_review_workspace(
            review_output,
            {"RU_RU": review},
            source,
            database,
            source_conflicts,
        )
        (review_output / "stale.json").write_text(
            "{}",
            encoding="utf-8",
        )
        catalog_builder.write_review_workspace(
            review_output,
            {"RU_RU": review},
            source,
            database,
            source_conflicts,
        )

        self.assertEqual(
            json.loads(
                output.read_text(encoding="utf-8")
            )["schema_version"],
            2,
        )
        self.assertFalse((review_output / "stale.json").exists())
        workspace_manifest = json.loads(
            (review_output / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(workspace_manifest["schema_version"], 3)
        self.assertEqual(
            workspace_manifest["source_conflicts"],
            {"file": "source-conflicts.json", "entries": 1},
        )
        source_review = json.loads(
            (review_output / "source-conflicts.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(source_review["summary"]["entries"], 1)
        self.assertTrue(
            (review_output / "RU_RU" / "buildings.json").is_file()
        )
        self.assertEqual(
            json.loads(
                (
                    review_output
                    / "RU_RU"
                    / "manifest.json"
                ).read_text(encoding="utf-8")
            )["locale"],
            "RU_RU",
        )
        with self.assertRaises(catalog_builder.CatalogError):
            catalog_builder.write_catalog(
                source,
                catalog,
                source,
                database,
            )
        with self.assertRaises(catalog_builder.CatalogError):
            catalog_builder.write_review_workspace(
                source,
                {"RU_RU": review},
                source,
                database,
                source_conflicts,
            )
        with self.assertRaises(catalog_builder.CatalogError):
            catalog_builder.write_review_workspace(
                source.parent,
                {"RU_RU": review},
                source,
                database,
                source_conflicts,
            )
        with self.assertRaises(catalog_builder.CatalogError):
            catalog_builder.write_editor_workspace(
                source.parent,
                {"RU_RU": review},
                source,
                database,
            )
        with self.assertRaisesRegex(
            catalog_builder.CatalogError,
            "outputs overlap",
        ):
            catalog_builder.validate_generated_outputs(
                output,
                review_output,
                review_output / "editor",
            )


if __name__ == "__main__":
    unittest.main()
