"""Stage an editor release without overwriting any translator's project data."""

from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.editor_update import (
    UPDATE_FILES, latest_release, stage_release, write_windows_updater,
)


class UpdateTests(unittest.TestCase):
    def test_check_stage_and_reject_modified_archive(self):
        """A verified ZIP stages only editor files; private workspace remains."""
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            version = home / "localization/editor/version.json"
            version.parent.mkdir(parents=True)
            version.write_text(json.dumps({"version": "0.3", "compatible_releases": ["v35.3"]}))
            with io.BytesIO() as stream:
                with zipfile.ZipFile(stream, "w") as archive:
                    for name in UPDATE_FILES:
                        archive.writestr(name, json.dumps({"version": "0.4",
                            "compatible_releases": ["v35.3"]}) if name.endswith("version.json")
                            else "fixture")
                content = stream.getvalue()
            link = "https://github.com/Nail-Al/Lekmod/releases/download/editor-v0.4/" + \
                   "LekmodLocalizationEditor-Windows.zip"
            payload = [{"tag_name": "v35.3", "assets": []},
                       {"tag_name": "editor-v0.4", "html_url": "https://github.com/release",
                        "assets": [{"name": "LekmodLocalizationEditor-Windows.zip",
                                    "browser_download_url": link,
                                    "digest": "sha256:" + sha256(content).hexdigest()}]}]
            private = home / "localization/workspace/editor-settings.json"
            private.parent.mkdir(parents=True)
            private.write_text("private settings")
            with patch("lekmod_localization.editor_update._download",
                       side_effect=[json.dumps(payload).encode(), content]):
                found = latest_release(home)
                self.assertTrue(found["available"])
                found["can_auto_update"] = True
                stage = stage_release(found, home)
            self.assertTrue((stage / "LekmodLocalizationEditor.exe").is_file())
            self.assertEqual(private.read_text(), "private settings")
            with patch("lekmod_localization.editor_update._download", return_value=content):
                self.assertEqual(stage_release(found, home), stage)
            script = write_windows_updater(stage, home)
            self.assertIn("Wait-Process", script.read_text())
            self.assertIn("[string]$EditorRoot", script.read_text())
            self.assertNotIn("[string]$Home", script.read_text())
            self.assertIn("editor-update-install", script.read_text())
            bad = dict(found, digest="sha256:" + "0" * 64)
            with tempfile.TemporaryDirectory() as other:
                with patch("lekmod_localization.editor_update._download", return_value=content):
                    with self.assertRaisesRegex(ValueError, "SHA-256"):
                        stage_release(bad, Path(other))


if __name__ == "__main__":
    unittest.main()
