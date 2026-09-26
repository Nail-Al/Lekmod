"""Regression tests for the frozen official-language input."""

from contextlib import closing
import gzip
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.common import CatalogError
from lekmod_localization.vanilla_snapshot import (
    read_snapshot,
    verify_snapshot,
    write_snapshot,
)


class VanillaSnapshotTests(unittest.TestCase):
    def setUp(self):
        """Provide a small vanilla fixture with an English and target table."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.database = root / "Localization-Merged.db"
        self.snapshot = root / "vanilla-snapshot.json.gz"
        with closing(sqlite3.connect(self.database)) as database:
            for locale, text in (("en_US", "Market"), ("RU_RU", "Рынок")):
                database.execute(
                    f'CREATE TABLE Language_{locale} '
                    '(Tag TEXT PRIMARY KEY, Text TEXT, Gender TEXT, Plurality TEXT)'
                )
                database.execute(
                    f'INSERT INTO Language_{locale} VALUES (?, ?, ?, ?)',
                    ("TXT_KEY_BUILDING_MARKET", text, None, None),
                )
            database.commit()

    def test_freeze_is_repeatable_and_never_replaces_an_existing_copy(self):
        """The one-time archive has stable bytes and an overwrite guard."""
        write_snapshot(self.database, self.snapshot)
        first = self.snapshot.read_bytes()
        verify_snapshot(self.database, self.snapshot)
        with self.assertRaisesRegex(CatalogError, "already exists"):
            write_snapshot(self.database, self.snapshot)
        self.assertEqual(first, self.snapshot.read_bytes())
        self.assertEqual(read_snapshot(self.snapshot)[0]["RU_RU"][
            "TXT_KEY_BUILDING_MARKET"
        ]["Text"], "Рынок")

    def test_changed_local_database_does_not_alter_frozen_translation(self):
        """Local customizations cannot silently redefine the reference."""
        write_snapshot(self.database, self.snapshot)
        with closing(sqlite3.connect(self.database)) as database:
            database.execute(
                'UPDATE Language_RU_RU SET Text=? WHERE Tag=?',
                ("Custom", "TXT_KEY_BUILDING_MARKET"),
            )
            database.commit()
        with self.assertRaisesRegex(CatalogError, "differs"):
            verify_snapshot(self.database, self.snapshot)
        self.assertEqual(read_snapshot(self.snapshot)[0]["RU_RU"][
            "TXT_KEY_BUILDING_MARKET"
        ]["Text"], "Рынок")

    def test_tampered_snapshot_or_lekmod_sentinel_is_rejected(self):
        """Loaders reject changed rows and databases containing Lekmod text."""
        write_snapshot(self.database, self.snapshot)
        payload = json.loads(gzip.decompress(self.snapshot.read_bytes()))
        payload["locales"]["en_US"]["TXT_KEY_BUILDING_MARKET"]["Text"] = "Tampered"
        self.snapshot.write_bytes(gzip.compress(json.dumps(payload).encode()))
        with self.assertRaisesRegex(CatalogError, "fingerprint mismatch"):
            read_snapshot(self.snapshot)

        with closing(sqlite3.connect(self.database)) as database:
            database.execute(
                'INSERT INTO Language_en_US VALUES (?, ?, ?, ?)',
                ("TXT_KEY_LEKMOD_VERSION", "Custom", None, None),
            )
            database.commit()
        with self.assertRaisesRegex(CatalogError, "sentinel"):
            write_snapshot(self.database, self.database.parent / "other.json.gz")


if __name__ == "__main__":
    unittest.main()
