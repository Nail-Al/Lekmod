"""Stage an editor release without overwriting any translator's project data."""

from hashlib import sha256
import io
import json
from http.server import HTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
from urllib.request import Request, urlopen
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.editor_update import (
    UPDATE_FILES, latest_release, stage_release, installer_command, launch_update,
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
            (stage / "localization/editor/app.js").write_text("damaged")
            with patch("lekmod_localization.editor_update._download", return_value=content):
                self.assertEqual(stage_release(found, home), stage)
            self.assertEqual((stage / "localization/editor/app.js").read_bytes(),
                             b"fixture")
            self.assertEqual(len(list(stage.parent.glob("editor-v0.4.failed-*"))), 1)
            command = installer_command(stage, home, 42, 8123, no_browser=True)
            self.assertEqual(command[0], str(stage / "LekmodLocalizationEditor.exe"))
            self.assertIn("--install-update", command)
            self.assertIn("--editor-root", command)
            self.assertIn("8123", command)
            self.assertNotIn("powershell.exe", command)
            bad = dict(found, digest="sha256:" + "0" * 64)
            with tempfile.TemporaryDirectory() as other:
                with patch("lekmod_localization.editor_update._download", return_value=content):
                    with self.assertRaisesRegex(ValueError, "SHA-256"):
                        stage_release(bad, Path(other))

    def test_download_reports_bytes_and_rejects_oversized_data(self):
        """The UI can show real progress and an oversized response cannot stage."""
        from lekmod_localization.editor_update import _download
        class Response(io.BytesIO):
            url = "https://github.com/test"
            headers = {"Content-Length": "5"}

        progress = []
        with patch("lekmod_localization.editor_update.urllib.request.urlopen",
                   return_value=Response(b"hello")):
            self.assertEqual(_download("https://github.com/test", 5,
                                       lambda done, total: progress.append((done, total))), b"hello")
        self.assertEqual(progress, [(5, 5)])
        with patch("lekmod_localization.editor_update.urllib.request.urlopen",
                   return_value=Response(b"hello")):
            with self.assertRaisesRegex(ValueError, "size limit"):
                _download("https://github.com/test", 4)

    def test_browser_action_streams_progress_then_launches_helper(self):
        """Exercise the real HTTP endpoint, including a second status request."""
        from editor_server import make_handler

        editor = Mock()
        editor.update_state = {"state": "idle"}
        token = "test-token"
        server = HTTPServer(("127.0.0.1", 0), make_handler(editor, token, 0))
        server.RequestHandlerClass = make_handler(editor, token, server.server_port)
        real_shutdown = server.shutdown
        server.shutdown = Mock()  # Observe the handoff before actually stopping.
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        release = {"available": True, "latest": "0.8"}

        def stage(release, progress):
            progress(1048576, 2097152)
            return Path("staged-editor")

        try:
            with patch("editor_server.latest_release", return_value=release), \
                 patch("editor_server.stage_release", side_effect=stage), \
                 patch("editor_server.launch_update") as launch:
                request = Request(base + "/api/editor-update", data=b"{}", headers={
                    "Origin": base, "X-Editor-Token": token, "Content-Type": "application/json"})
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                for _ in range(100):
                    if server.shutdown.called:
                        break
                    time.sleep(.01)
                self.assertTrue(server.shutdown.called, "HTTP update never handed off")
                launch.assert_called_once_with(Path("staged-editor"), port=server.server_port)
                with urlopen(base + "/api/editor-update-status", timeout=5) as response:
                    self.assertEqual(json.load(response)["state"], "installing")
        finally:
            server.shutdown = real_shutdown
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_old_editor_waits_for_helper_acknowledgement(self):
        """A failed-to-launch new EXE cannot make the current server shut down."""
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "localization/workspace/editor-updates/editor-v0.9"
            stage.mkdir(parents=True)
            with patch("lekmod_localization.editor_update.sys.platform", "win32"), \
                 patch.object(sys, "frozen", True, create=True), \
                 patch.object(subprocess, "CREATE_NO_WINDOW", 0, create=True), \
                 patch("lekmod_localization.editor_update.subprocess.Popen") as process:
                process.return_value.poll.return_value = 1
                with self.assertRaisesRegex(RuntimeError, "exited early"):
                    launch_update(stage, root, 123, 8123)
                command = process.call_args.args[0]
                self.assertIn("--handoff-ticket", command)
                ticket = command[command.index("--handoff-ticket") + 1]
                marker = stage.parent / ("helper-ready-" + ticket)
                marker.write_text("ready", encoding="utf-8")
                launch_update_ack = patch("lekmod_localization.editor_update.uuid.uuid4")
                with launch_update_ack as uuid_mock:
                    uuid_mock.return_value.hex = ticket
                    launch_update(stage, root, 123, 8123)
                self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
