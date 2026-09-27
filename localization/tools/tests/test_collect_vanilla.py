"""Prevent team snapshots from mixing game builds or silently replacing text."""

from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect_vanilla import capture_locale, merge_captures
from lekmod_localization.common import CatalogError
from lekmod_localization.vanilla_snapshot import read_snapshot, write_snapshot
from lekmod_localization.vanilla_reference import read_reference, write_reference


def database(path: Path, texts: dict[str, str | None]) -> None:
    """Build a tiny game cache where unavailable languages have empty tables."""
    with closing(sqlite3.connect(path)) as connection:
        for locale, text in texts.items():
            connection.execute(f"CREATE TABLE Language_{locale} (Tag TEXT PRIMARY KEY, Text TEXT)")
            if text is not None:
                connection.execute(f"INSERT INTO Language_{locale} VALUES (?, ?)",
                                   ("TXT_KEY_BUILDING_MARKET", text))
        connection.commit()


class CollectVanillaTests(unittest.TestCase):
    def test_capture_and_merge_preserve_existing_english_and_russian(self):
        """Only empty languages are filled, and the English baseline stays fixed."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base_db, base = root / "base.db", root / "base.json.gz"
            database(base_db, {"en_US": "Market", "RU_RU": "Рынок",
                               "DE_DE": None, "ES_ES": None})
            write_snapshot(base_db, base)
            pinned = root / "pinned.json.gz"
            write_reference(base, pinned)
            captures = []
            for locale, text in (("DE_DE", "Markt"), ("ES_ES", "Mercado")):
                game = root / f"{locale}.db"
                database(game, {"en_US": "Market", locale: text})
                capture = root / f"{locale}.json.gz"
                self.assertEqual(capture_locale(game, locale, base, capture, 1, pinned), 1)
                captures.append(capture)
            combined, reference = root / "proposal.json.gz", root / "proposed-index.json.gz"
            with self.assertRaisesRegex(CatalogError, "still missing languages"):
                merge_captures(base, captures[:1], combined, reference, 1, pinned)
            self.assertFalse(combined.exists())
            counts = merge_captures(base, captures, combined, reference, 1, pinned)
            self.assertEqual(counts, {"DE_DE": 1, "ES_ES": 1, "RU_RU": 1, "en_US": 1})
            locales, hashes = read_snapshot(combined)
            baseline, baseline_hashes = read_snapshot(base)
            for name in ("en_US", "RU_RU"):
                self.assertEqual(locales[name], baseline[name])
                self.assertEqual(hashes[name], baseline_hashes[name])
            self.assertEqual(locales["DE_DE"]["TXT_KEY_BUILDING_MARKET"]["Text"], "Markt")
            self.assertEqual(read_reference(reference)["locales"], hashes)

    def test_reject_mixed_game_build_and_incomplete_locale(self):
        """A new Steam build or an empty selected table cannot join the team baseline."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base_db, base = root / "base.db", root / "base.json.gz"
            database(base_db, {"en_US": "Market", "DE_DE": None})
            write_snapshot(base_db, base)
            pinned = root / "pinned.json.gz"
            write_reference(base, pinned)
            changed = root / "changed.db"
            database(changed, {"en_US": "New Market", "DE_DE": "Markt"})
            with self.assertRaisesRegex(CatalogError, "English game text"):
                capture_locale(changed, "DE_DE", base, root / "de.json.gz", 1, pinned)
            empty = root / "empty.db"
            database(empty, {"en_US": "Market", "DE_DE": None})
            with self.assertRaisesRegex(CatalogError, "fewer than"):
                capture_locale(empty, "DE_DE", base, root / "de.json.gz", 1, pinned)


if __name__ == "__main__":
    unittest.main()
