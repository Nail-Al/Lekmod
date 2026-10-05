"""Regression tests for the frozen official-language input."""

from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.common import CatalogError
from lekmod_localization.vanilla_snapshot import (
    read_snapshot,
    verify_snapshot,
    write_snapshot,
)
from lekmod_localization.vanilla_reference import (
    adopt_reference_extension, compatible_reference_digest,
    read_reference, verify_snapshot_reference, write_reference,
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

    def test_pinned_reference_rejects_a_different_locale_without_storing_text(self):
        """A team member cannot silently switch to another vanilla database."""
        write_snapshot(self.database, self.snapshot)
        reference = self.snapshot.parent / "vanilla-fingerprints.json.gz"
        write_reference(self.snapshot, reference)
        verify_snapshot_reference(self.snapshot, reference)
        data = read_reference(reference)
        self.assertEqual(len(data["english"]), 1)
        self.assertNotIn("Рынок", str(data))
        with closing(sqlite3.connect(self.database)) as database:
            database.execute(
                "UPDATE Language_RU_RU SET Text=? WHERE Tag=?",
                ("Другой текст", "TXT_KEY_BUILDING_MARKET"),
            )
            database.commit()
        changed = self.snapshot.parent / "changed.json.gz"
        write_snapshot(self.database, changed)
        with self.assertRaisesRegex(CatalogError, "pinned team reference"):
            verify_snapshot_reference(changed, reference)

    def test_reviewed_reference_extension_preserves_work_and_private_snapshot(self):
        """Known reference migration changes only its index and backs up the old bytes."""
        write_snapshot(self.database, self.snapshot)
        project = self.snapshot.parent
        reference = project / "localization/reference/vanilla-fingerprints.json.gz"
        write_reference(self.snapshot, reference)
        previous = reference.read_bytes()
        with closing(sqlite3.connect(self.database)) as database:
            database.execute('CREATE TABLE Language_DE_DE (Tag TEXT, Text TEXT)')
            database.execute('INSERT INTO Language_DE_DE VALUES (?, ?)',
                             ('TXT_KEY_BUILDING_MARKET', 'Markt'))
            database.commit()
        full = project / "complete-snapshot.json.gz"
        bundled = project / "bundled-reference.json.gz"
        write_snapshot(self.database, full)
        write_reference(full, bundled)
        old_digest = hashlib.sha256(previous).hexdigest()
        new_digest = hashlib.sha256(bundled.read_bytes()).hexdigest()
        work = project / "localization/translations/RU_RU.csv"
        work.parent.mkdir(parents=True)
        work.write_bytes(b"saved contributor work")
        originals = {path: path.read_bytes() for path in (work, self.snapshot, full)}
        self.assertFalse(adopt_reference_extension(project, bundled))
        with patch("lekmod_localization.vanilla_reference.COMPATIBLE_REFERENCE_EXTENSIONS",
                   {(old_digest, new_digest)}):
            self.assertTrue(adopt_reference_extension(project, bundled))
            self.assertFalse(adopt_reference_extension(project, bundled))
            self.assertTrue(compatible_reference_digest(old_digest, bundled.read_bytes()))
            self.assertFalse(compatible_reference_digest(new_digest, previous))
            self.assertFalse(compatible_reference_digest(None, bundled.read_bytes()))
            self.assertFalse(compatible_reference_digest("f" * 64, bundled.read_bytes()))
        backup = project / "localization/workspace/reference-backups" / (old_digest + ".json.gz")
        self.assertEqual(backup.read_bytes(), previous)
        self.assertEqual(reference.read_bytes(), bundled.read_bytes())
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)
        verify_snapshot_reference(full, reference)
        with self.assertRaisesRegex(CatalogError, "pinned team reference"):
            verify_snapshot_reference(self.snapshot, reference)

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
