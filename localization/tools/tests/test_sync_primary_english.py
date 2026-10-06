"""Tests for the editable English source and generated game block."""

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_primary_localization as primary_audit
import sync_primary_english as sync
from lekmod_localization.common import CatalogError


class EnglishSourceTests(unittest.TestCase):
    def setUp(self):
        """Create isolated source and game XML paths for each test."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.source = root / "localization" / "en_US" / "primary.xml"
        self.game_xml = root / "LEKMOD" / "Override" / "CIV5Units_Mongol.xml"
        self.game_xml.parent.mkdir(parents=True)
        self.original = (
            "<GameData>\n<Units><Row Description=\"TXT_KEY_NEW\"/></Units>\n"
            "\t<Language_en_US>\n"
            "\t\t<Row Tag=\"TXT_KEY_NEW\"><Text>New</Text></Row>\n"
            "\t\t<Replace Tag=\"TXT_KEY_OLD\"><Text>Changed</Text></Replace>\n"
            "\t</Language_en_US>\n"
            "<Language_RU_RU/>\n</GameData>\n"
        )
        self.game_xml.write_text(self.original, encoding="utf-8")

    def test_bootstrap_preserves_operations_and_non_language_tables(self):
        """Extraction adds markers but leaves all game operations unchanged."""
        before = primary_audit.parse_source(self.game_xml, "en_US")
        sync.bootstrap(self.source, self.game_xml)
        after = primary_audit.parse_source(self.game_xml, "en_US")
        self.assertEqual(before["operations"], after["operations"])
        self.assertEqual(before["entries"], after["entries"])
        self.assertEqual(
            self.game_xml.read_text(encoding="utf-8").replace(
                sync.BEGIN, ""
            ).replace(sync.END, ""),
            self.original,
        )
        self.assertFalse(sync.synchronize(self.source, self.game_xml, write=False))

    def test_check_rejects_drift_and_write_updates_only_english(self):
        """Manual game edits fail CI; source changes can regenerate the block."""
        sync.bootstrap(self.source, self.game_xml)
        source = self.source.read_text(encoding="utf-8")
        self.source.write_text(source.replace("Changed", "Revised"), encoding="utf-8")
        with self.assertRaisesRegex(CatalogError, "out of sync"):
            sync.synchronize(self.source, self.game_xml, write=False)
        self.assertTrue(sync.synchronize(self.source, self.game_xml, write=True))
        game = self.game_xml.read_text(encoding="utf-8")
        self.assertIn("<Text>Revised</Text>", game)
        self.assertIn('<Language_RU_RU/>', game)
        self.assertIn('<Units><Row Description="TXT_KEY_NEW"/></Units>', game)

    def test_invalid_source_never_overwrites_game(self):
        """Malformed XML, unsupported operations, and stray tables fail closed."""
        sync.bootstrap(self.source, self.game_xml)
        game_before = self.game_xml.read_bytes()
        valid = self.source.read_text(encoding="utf-8")
        for broken in (
            valid.replace("<GameData>", "<!DOCTYPE unsafe><GameData>"),
            valid.replace("<Row Tag=", "<Unknown Tag="),
            valid.replace("</GameData>", "<Units/></GameData>"),
        ):
            with self.subTest(broken=broken[:50]):
                self.source.write_text(broken, encoding="utf-8")
                with self.assertRaises(CatalogError):
                    sync.synchronize(self.source, self.game_xml, write=True)
                self.assertEqual(self.game_xml.read_bytes(), game_before)

    def test_bootstrap_and_missing_markers_cannot_replace_sources(self):
        """A second migration or unmarked target requires manual review."""
        sync.bootstrap(self.source, self.game_xml)
        with self.assertRaisesRegex(CatalogError, "already exists"):
            sync.bootstrap(self.source, self.game_xml)
        self.game_xml.write_text(self.original, encoding="utf-8")
        with self.assertRaisesRegex(CatalogError, "markers"):
            sync.synchronize(self.source, self.game_xml, write=True)

    def test_generated_xml_preserves_windows_checkout_line_endings(self):
        """Save/undo hashes must remain stable for CRLF files on Windows."""
        self.game_xml.write_bytes(self.original.replace("\n", "\r\n").encode("utf-8"))
        original = self.game_xml.read_bytes()
        sync.atomic_text(self.game_xml, self.original)
        self.assertEqual(self.game_xml.read_bytes(), original)
        self.assertEqual(sync.encoded_text(self.game_xml, self.original), original)

    def test_intentional_blank_is_materialized_only_in_game_output(self):
        """English sources stay editable and exact; the game never receives empty Text."""
        self.game_xml.write_text(self.original.replace('<Text>New</Text>', '<Text></Text>'), encoding='utf-8')
        sync.bootstrap(self.source, self.game_xml)
        source = self.source.read_bytes()
        self.assertIn(b'<Text></Text>', source)
        self.assertIn(b'<Text>&#160;</Text>', self.game_xml.read_bytes())
        self.assertFalse(sync.synchronize(self.source, self.game_xml, write=False))
        self.game_xml.write_bytes(self.game_xml.read_bytes().replace(b'<Text>&#160;</Text>', b'<Text></Text>'))
        self.assertTrue(sync.synchronize(self.source, self.game_xml, write=True))
        self.assertEqual(self.source.read_bytes(), source)

    def test_blank_materialization_cannot_hide_invalid_nested_fields(self):
        """Preparing blanks must not turn malformed Text children into valid input."""
        sync.bootstrap(self.source, self.game_xml)
        valid = self.source.read_text(encoding='utf-8')
        self.source.write_text(valid.replace('<Text>New</Text>', '<Text><Bad/></Text>'), encoding='utf-8')
        with self.assertRaisesRegex(CatalogError, 'plain escaped text'):
            sync.synchronize(self.source, self.game_xml, write=True)


if __name__ == "__main__":
    unittest.main()
