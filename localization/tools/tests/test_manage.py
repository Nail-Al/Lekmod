"""Check the contributor-facing feature switches stay strict and documented."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manage
from lekmod_localization.common import CatalogError


class ConfigTests(unittest.TestCase):
    def setUp(self):
        """Use a temporary copy so the tracked settings are never changed."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "config.json"
        self.config = json.loads(manage.CONFIG.read_text(encoding="utf-8"))

    def read(self):
        """Write the edited fixture and parse it with the production loader."""
        self.path.write_text(json.dumps(self.config), encoding="utf-8")
        return manage.read_config(self.path)

    def test_switch_has_english_help_and_can_be_turned_off(self):
        """Descriptions do not change what On and Off mean to the runner."""
        self.config["build"]["catalog"] = "Off"
        result = self.read()
        self.assertFalse(result["build"]["catalog"])
        self.assertTrue(result["build"]["shipped"])
        self.assertNotIn("_help", result)

    def test_typo_cannot_silently_disable_a_check(self):
        """Accidental values outside On and Off must stop preparation."""
        self.config["checks"]["shipped"] = "Of"
        with self.assertRaisesRegex(CatalogError, "only On or Off"):
            self.read()

    def test_missing_help_is_rejected(self):
        """A new switch must remain understandable to non-programmers."""
        del self.config["_help"]["build"]["english"]
        with self.assertRaisesRegex(CatalogError, "must describe each switch"):
            self.read()

    def test_tracked_workspace_file_is_rejected(self):
        """A force-added private file stops the public CI check."""
        root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        private = root / "localization" / "workspace" / "private.txt"
        private.parent.mkdir(parents=True)
        private.write_text("fixture", encoding="utf-8")
        manage.ensure_workspace_private(root)
        subprocess.run(["git", "add", "--", str(private)], cwd=root, check=True)
        with self.assertRaisesRegex(CatalogError, "tracked private files"):
            manage.ensure_workspace_private(root)


if __name__ == "__main__":
    unittest.main()
