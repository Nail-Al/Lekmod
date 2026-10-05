"""Guard the editor's filesystem and game deployment boundaries."""

from pathlib import Path
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.connections import (
    DownloadCancelled, apply_game, download_compatible_source, extract_source_archive,
    inspect_game, save_settings, settings, steam_game_candidates, TEAM_SNAPSHOT_URL,
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

    def test_game_folder_must_contain_game_and_matching_lekmod(self):
        """A selected Downloads folder or broken DLC must never enable Apply."""
        downloads = self.home / "Downloads"
        downloads.mkdir()
        self.assertEqual(inspect_game(downloads, self.project)["state"], "missing_game")
        self.assertIn("CivilizationV.exe", inspect_game(downloads)["error"])
        self.assertEqual(inspect_game(self.game, self.project)["state"], "installed")
        self.target.write_text("<GameData><Rules>", encoding="utf-8")
        self.assertEqual(inspect_game(self.game, self.project)["state"], "damaged")
        self.target.unlink()
        self.assertEqual(inspect_game(self.game, self.project)["state"], "damaged")
        self.installed.rename(self.home / "temporarily-uninstalled")
        self.assertEqual(inspect_game(self.game, self.project)["state"], "vanilla")

    def test_changed_game_rules_are_not_approved_for_apply(self):
        """Even a valid XML from another mod build is a mismatch."""
        self.target.write_bytes(self.target.read_bytes().replace(b'Id="1"', b'Id="2"'))
        result = inspect_game(self.game, self.project)
        self.assertEqual(result["state"], "mismatch")
        self.assertIn("gameplay XML differs", result["error"])

    def test_version_warning_names_installed_and_required_builds(self):
        """A source upgrade or launcher update disables applying unrelated game text."""
        original = self.target.read_bytes()
        (self.installed / 'VERSION').write_text('v35.4003', encoding='utf-8')
        result = inspect_game(self.game, self.project)
        self.assertEqual(result['state'], 'mismatch')
        self.assertIn('v35.4003', result['error'])
        self.assertIn('v35.3000', result['error'])
        self.assertIn('official Lekmod launcher', result['error'])
        with self.assertRaisesRegex(ValueError, 'Version mismatch'):
            apply_game(self.project, self.game, self.installed.name, self.home)
        self.assertEqual(self.target.read_bytes(), original)

    def test_steam_libraries_include_custom_game_directory(self):
        """Automatic discovery follows libraryfolders and appmanifest names."""
        root = self.home / "Steam"
        library = self.home / "Other Games"
        (root / "steamapps").mkdir(parents=True)
        (root / "steamapps/libraryfolders.vdf").write_text(
            f'"libraryfolders" {{\n"1" {{ "path" "{library}" }}\n'
            f'"path" "{library}"\n}}', encoding="utf-8")
        (library / "steamapps").mkdir(parents=True)
        (library / "steamapps/appmanifest_8930.acf").write_text(
            '"AppState"\n{\n"installdir" "Civ5 Custom"\n}', encoding="utf-8")
        self.assertIn(library / "steamapps/common/Civ5 Custom",
                      steam_game_candidates([root]))

    def test_settings_survive_restart_without_touching_repo(self):
        """Connections, column widths, and prefilling use a private file."""
        save_settings({"prefill": False, "panel_expanded": False, "column_widths": {"key": 420},
                       "game_path": str(self.game)}, self.home)
        self.assertFalse(settings(self.home)["prefill"])
        self.assertFalse(settings(self.home)["panel_expanded"])
        self.assertEqual(settings(self.home)["column_widths"]["key"], 420)
        self.assertEqual(settings(self.home)["game_path"], str(self.game))
        self.assertEqual(settings(self.home)["snapshot_url"], TEAM_SNAPSHOT_URL)
        save_settings({"snapshot_url": ""}, self.home)
        self.assertEqual(settings(self.home)["snapshot_url"], "")
        # A pre-team settings file with an empty link gets the new default once.
        old = self.home / "localization/workspace/editor-settings.json"
        old.write_text(json.dumps({"snapshot_url": ""}), encoding="utf-8")
        self.assertEqual(settings(self.home)["snapshot_url"], TEAM_SNAPSHOT_URL)

    def test_filters_persist_independently_until_the_active_mode_is_cleared(self):
        """Switching mode, saving another preference, and restart preserve both views."""
        translator = {'kind': 'vanilla_modified', 'status': 'stale',
                      'date_field': 'translation_updated_at', 'date_from': '2026-10-01',
                      'date_to': '2026-10-05', 'version': 'upgrade', 'needs_translation': 'true'}
        developer = {'kind': 'Replace', 'date_field': 'english_edited_at', 'version': 'v35.4'}
        save_settings({'translator_filters': translator, 'developer_filters': developer}, self.home)
        save_settings({'mode': 'developer', 'wrap': False}, self.home)
        self.assertEqual(settings(self.home)['translator_filters'], translator)
        self.assertEqual(settings(self.home)['developer_filters']['kind'], 'Replace')
        save_settings({'mode': 'translator', 'translator_filters': {}}, self.home)
        self.assertEqual(settings(self.home)['translator_filters']['status'], '')
        self.assertEqual(settings(self.home)['developer_filters']['version'], 'v35.4')

    def test_invalid_filter_save_preserves_valid_preferences(self):
        """Bad dates and translation-only Developer filters never overwrite settings."""
        original = save_settings({'translator_filters': {'status': 'missing'}}, self.home)
        for field, invalid in (
                ('translator_filters', {'date_from': '2026-02-30'}),
                ('translator_filters', {'date_from': '2026-10-05', 'date_to': '2026-10-01'}),
                ('developer_filters', {'status': 'missing'})):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                save_settings({field: invalid}, self.home)
            self.assertEqual(settings(self.home)['translator_filters'], original['translator_filters'])

    def test_snapshot_default_migration_keeps_custom_links_and_all_preferences(self):
        """Only the old team file changes; cleared links and contributor settings stay."""
        path = self.home / "localization/workspace/editor-settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = "https://www.dropbox.com/scl/fi/dquiyoh5k77v8qhip4u70/vanilla-snapshot.enc"
        original = {"project_path": str(self.project), "locale": "DE_DE", "prefill": False,
                    "column_widths": {"translation": 720}, "onboarded": True,
                    "snapshot_url": previous + "?st=zfeaj3dx&dl=0&rlkey=fwcgddzwanyaljhe9tja6ytk1"}
        path.write_text(json.dumps(original), encoding="utf-8")
        migrated = settings(self.home)
        self.assertEqual(migrated["snapshot_url"], TEAM_SNAPSHOT_URL)
        for name in ("project_path", "locale", "prefill", "column_widths", "onboarded"):
            self.assertEqual(migrated[name], original[name])
        self.assertEqual(save_settings({"wrap": False}, self.home)["snapshot_url"], TEAM_SNAPSHOT_URL)
        for custom in ("https://example.invalid/team.enc", "",
                       previous + "?rlkey=a-different-access-key&dl=1"):
            save_settings({"snapshot_url": custom}, self.home)
            self.assertEqual(settings(self.home)["snapshot_url"], custom)

    def test_downloaded_archive_cannot_escape_destination(self):
        """Reject ZIP traversal before opening a project file for writing."""
        archive = self.home / "archive.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("root/LEKMOD/../../outside.txt", "bad")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            extract_source_archive(archive, self.home / "fresh")
        self.assertFalse((self.home / "outside.txt").exists())

    def test_download_progress_cancel_and_existing_project_reuse(self):
        """A canceled transfer cannot replace a valid project or leave a partial one."""
        manifest = self.home / "localization/editor/version.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"version": "0.3", "release_tag": "editor-v0.3",
                                        "compatible_releases": ["v35.3"]}), encoding="utf-8")
        (self.project / "LEKMOD/Lua/tmp").mkdir(parents=True)
        tests = self.project / "localization/tools/tests"
        tests.mkdir(parents=True)
        (tests.parent / "manage.py").touch()
        (self.project / "LEKMOD/Art/localization.xml").touch()
        (self.project / "LEKMOD/Lua/tmp/script.lua").touch()
        (tests / "test_sample.py").touch()
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            for path in self.project.rglob("*"):
                if path.is_file():
                    archive.write(path, "source/" + path.relative_to(self.project).as_posix())
        payload = archive_bytes.getvalue()

        class Response(io.BytesIO):
            headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        phases = []
        with patch("urllib.request.urlopen", return_value=Response(payload)) as fetch:
            path = download_compatible_source("v35.3", self.home,
                                              progress=lambda phase, done, size: phases.append(phase))
            self.assertEqual(fetch.call_count, 1)
            self.assertIn("downloading", phases)
            self.assertIn("extracting", phases)
            self.assertEqual(phases[-1], "verifying")
            self.assertTrue((path / "localization/en_US/primary.xml").is_file())
        marker = path / "localization/translator-draft.txt"
        marker.write_text("unsaved work", encoding="utf-8")
        with patch("urllib.request.urlopen", side_effect=AssertionError("should reuse")):
            self.assertEqual(download_compatible_source("v35.3", self.home), path)
        self.assertEqual(marker.read_text(encoding="utf-8"), "unsaved work")
        other_manifest = self.home / "other/localization/editor/version.json"
        other_manifest.parent.mkdir(parents=True)
        other_manifest.write_bytes(manifest.read_bytes())
        with patch("urllib.request.urlopen", return_value=Response(payload)):
            with self.assertRaises(DownloadCancelled):
                download_compatible_source("v35.3", self.home / "other",
                                           cancelled=lambda: True)
        self.assertFalse((self.home / "other/localization/workspace/projects/v35.3").exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "unsaved work")


if __name__ == "__main__":
    unittest.main()
