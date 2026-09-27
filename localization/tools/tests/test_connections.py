"""Guard the editor's filesystem and game deployment boundaries."""

from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.connections import (
    apply_game, extract_source_archive, save_settings, settings,
)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        """Build a small release and installed Civ V DLC in separate folders."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.project = self.home / "project"
        self.game = self.home / "game"
        (self.project / "LEKMOD/Art").mkdir(parents=True)
        (self.project / "localization/en_US").mkdir(parents=True)
        (self.project / "localization/reference").mkdir(parents=True)
        (self.project / "LEKMOD/VERSION").write_text("v35.3000", encoding="utf-8")
        (self.project / "localization/en_US/primary.xml").write_text(
            '<Text>LEKMOD v35.3</Text>', encoding="utf-8")
        (self.project / "localization/config.json").write_text("{}", encoding="utf-8")
        (self.project / "localization/reference/vanilla-fingerprints.json.gz").touch()
        source = self.project / "LEKMOD/Override/CIV5Units_Mongol.xml"
        source.parent.mkdir(parents=True)
        source.write_text('<GameData><Rules><Row Id="1"/></Rules>'
                          '<Language_en_US><Row Tag="TXT_KEY_TEST"><Text>new</Text></Row>'
                          '</Language_en_US></GameData>', encoding="utf-8")
        self.game.mkdir()
        (self.game / "CivilizationV.exe").touch()
        self.installed = self.game / "Assets/DLC/LEKMOD_v35.3"
        target = self.installed / "Override/CIV5Units_Mongol.xml"
        target.parent.mkdir(parents=True)
        (self.installed / "VERSION").write_text("v35.3000", encoding="utf-8")
        target.write_text('<GameData><Rules><Row Id="1"/></Rules>'
                          '<Language_en_US><Row Tag="TXT_KEY_TEST"><Text>old</Text></Row>'
                          '</Language_en_US></GameData>', encoding="utf-8")
        self.target = target

    def test_matching_release_updates_only_language_xml_with_backup(self):
        """A local game test is reversible and avoids unrelated game files."""
        previous = self.target.read_bytes()
        result = apply_game(self.project, self.game, self.installed.name, self.home)
        self.assertTrue(result["changed"])
        self.assertEqual(Path(result["backup"]).read_bytes(), previous)
        self.assertIn(b"new", self.target.read_bytes())
        self.assertFalse(apply_game(self.project, self.game, self.installed.name,
                                    self.home)["changed"])

    def test_mismatched_version_or_game_rules_rejects_write(self):
        """Even a same-named DLC cannot accept an unrelated rules build."""
        previous = self.target.read_bytes()
        (self.installed / "VERSION").write_text("v35.2000", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Version mismatch"):
            apply_game(self.project, self.game, self.installed.name, self.home)
        (self.installed / "VERSION").write_text("v35.3000", encoding="utf-8")
        self.target.write_bytes(previous.replace(b'Id="1"', b'Id="2"'))
        with self.assertRaisesRegex(ValueError, "different gameplay data"):
            apply_game(self.project, self.game, self.installed.name, self.home)
        self.assertIn(b'Id="2"', self.target.read_bytes())

    def test_settings_survive_restart_without_touching_repo(self):
        """Connections, column widths, and prefilling use a private file."""
        save_settings({"prefill": False, "column_widths": {"key": 420},
                       "game_path": str(self.game)}, self.home)
        self.assertFalse(settings(self.home)["prefill"])
        self.assertEqual(settings(self.home)["column_widths"]["key"], 420)
        self.assertEqual(settings(self.home)["game_path"], str(self.game))

    def test_downloaded_archive_cannot_escape_destination(self):
        """Reject ZIP traversal before opening a project file for writing."""
        archive = self.home / "archive.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("root/LEKMOD/../../outside.txt", "bad")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            extract_source_archive(archive, self.home / "fresh")
        self.assertFalse((self.home / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main()
