from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_primary_localization as primary_audit
import build_localization_catalog as catalog_builder


class LocalizationCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def database(
        self,
        include_sentinel: bool = False,
        name: str = "Localization-Merged.db",
    ) -> Path:
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

    def test_unknown_and_contaminated_database_are_rejected(self):
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

    def test_generated_workspace_is_atomic_and_protects_inputs(self):
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
        self.assertEqual(workspace_manifest["schema_version"], 2)
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


if __name__ == "__main__":
    unittest.main()
