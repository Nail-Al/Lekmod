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
        self.root = Path(self.temporary.name)

    def database(self, include_sentinel: bool = False) -> Path:
        path = self.root / "Localization-Merged.db"

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
                    ("TXT_KEY_UNIT_SAME", "Same [ICON_FOOD]", None, None),
                    (
                        "TXT_KEY_UNIT_METADATA_SAME",
                        "Same metadata",
                        "neuter:an",
                        "2",
                    ),
                    ("TXT_KEY_BUILDING_CHANGED", "Old text", "masculine", None),
                    ("TXT_KEY_POLICY_METADATA", "Metadata only", "masculine", None),
                ],
            )

            if include_sentinel:
                database.execute(
                    "INSERT INTO LocalizedText_en_US VALUES (?, ?, ?, ?)",
                    ("TXT_KEY_LEKMOD_VERSION", "Contaminated", None, None),
                )

            database.commit()

        return path

    def source(self) -> Path:
        path = self.root / "source.xml"
        path.write_text(
            '''<GameData><Language_en_US>
<Replace Tag="TXT_KEY_UNIT_SAME"><Text>
    Same [ICON_FOOD]
</Text></Replace>
<Replace Tag="TXT_KEY_UNIT_METADATA_SAME">
    <Text>Same metadata</Text>
    <Gender>
        neuter:an
    </Gender>
    <Plurality>
        2
    </Plurality>
</Replace>
<Replace Tag="TXT_KEY_BUILDING_CHANGED"><Text>New [ICON_PRODUCTION] text</Text><Gender>feminine</Gender></Replace>
<Replace Tag="TXT_KEY_POLICY_METADATA"><Text>Metadata only</Text><Gender>
    feminine
</Gender></Replace>
<Row Tag="TXT_KEY_PROMOTION_NEW"><Text>New {1_Num} %s</Text></Row>
</Language_en_US></GameData>''',
            encoding="utf-8",
        )
        return path

    def test_clean_database_is_loaded_read_only(self):
        database = self.database()
        original = database.read_bytes()

        entries, locales, fingerprint = catalog_builder.load_vanilla_database(database)

        self.assertEqual(len(entries), 4)
        self.assertEqual(locales, ["DE_DE", "en_US", "RU_RU"])
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(database.read_bytes(), original)

    def test_catalog_excludes_unchanged_and_groups_required_entries(self):
        database = self.database()
        vanilla, locales, fingerprint = catalog_builder.load_vanilla_database(database)
        report = primary_audit.parse_source(self.source(), "en_US")

        catalog = catalog_builder.build_catalog(
            report,
            vanilla,
            locales,
            database.name,
            fingerprint,
        )

        self.assertEqual(
            catalog["summary"]["classifications"],
            {
                "lekmod_new": 1,
                "vanilla_metadata_only": 1,
                "vanilla_modified": 1,
                "vanilla_unchanged": 2,
            },
        )
        self.assertEqual(catalog["summary"]["requires_translation"], 2)
        self.assertFalse(any(
            "TXT_KEY_UNIT_SAME" in entries
            for entries in catalog["source_categories"].values()
        ))
        changed = catalog["source_categories"]["buildings"][
            "TXT_KEY_BUILDING_CHANGED"
        ]
        self.assertEqual(changed["classification"], "vanilla_modified")
        self.assertEqual(changed["fields"]["Gender"], "feminine")
        self.assertEqual(changed["tokens"], {"[ICON_PRODUCTION]": 1})

        new = catalog["source_categories"]["promotions"][
            "TXT_KEY_PROMOTION_NEW"
        ]
        self.assertEqual(new["classification"], "lekmod_new")
        self.assertEqual(new["tokens"], {"%s": 1, "{1_Num}": 1})
        self.assertEqual(set(catalog["translations"]), {"DE_DE", "RU_RU"})
        metadata_review = catalog["review"]["metadata_only"]
        self.assertEqual(
            metadata_review["TXT_KEY_POLICY_METADATA"]["fields"]["Gender"],
            "feminine",
        )
        self.assertNotIn("TXT_KEY_UNIT_METADATA_SAME", metadata_review)

    def test_contaminated_database_is_rejected(self):
        with self.assertRaisesRegex(
            catalog_builder.CatalogError,
            "Lekmod sentinel keys",
        ):
            catalog_builder.load_vanilla_database(
                self.database(include_sentinel=True)
            )

    def test_catalog_write_is_atomic_and_protects_inputs(self):
        database = self.database()
        source = self.source()
        output = self.root / "build" / "catalog.json"
        catalog = {"schema_version": 1, "example": "Пример"}

        catalog_builder.write_catalog(output, catalog, source, database)

        self.assertEqual(
            json.loads(output.read_text(encoding="utf-8")),
            catalog,
        )
        with self.assertRaises(catalog_builder.CatalogError):
            catalog_builder.write_catalog(source, catalog, source, database)
        with self.assertRaises(catalog_builder.CatalogError):
            catalog_builder.write_catalog(database, catalog, source, database)


if __name__ == "__main__":
    unittest.main()
