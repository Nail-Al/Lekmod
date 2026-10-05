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
from urllib.error import URLError
import unittest
from unittest.mock import patch, Mock
from urllib.request import Request, urlopen
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.editor_update import (
    UPDATE_FILES, latest_release, stage_release, installer_command, launch_update, _record,
    _wait_ready, verify_installation, _publish_helper_ready,
)
from lekmod_localization.connections import editor_manifest


class UpdateTests(unittest.TestCase):
    def test_verification_and_same_version_repair_stage_exclude_translations(self):
        """Damaged application files are found; all personal files stay untouched."""
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            manifest = json.dumps({"version": "0.13", "release_tag": "editor-v0.13",
                                   "compatible_releases": ["v35.3"]}).encode()
            expected = {name: (manifest if name.endswith("version.json") else b"published file")
                        for name in UPDATE_FILES}
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                for name, content in expected.items():
                    archive.writestr(name, content)
            data = stream.getvalue()
            for name, content in expected.items():
                target = home / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            # These files may live beside the portable editor or in its workspace.
            personal = {"localization/translations/RU_RU.csv": b"my saved Russian text",
                        "localization/en_US/primary.xml": b"developer English text",
                        "localization/workspace/vanilla-snapshot.json.gz": b"private reference",
                        "localization/workspace/projects/v35.3/localization/translations/RU_RU.csv":
                            b"another contributor's translation"}
            for name, content in personal.items():
                path = home / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            (home / "localization/editor/app.js").write_bytes(b"corrupted UI")
            (home / "README-START.txt").unlink()
            release = {"current": "0.13", "latest": "0.13", "available": False,
                       "can_auto_update": True,
                       "download_url": "https://github.com/Nail-Al/Lekmod/releases/download/editor-v0.13/"
                                       "LekmodLocalizationEditor-Windows.zip",
                       "digest": "sha256:" + sha256(data).hexdigest()}
            with patch("lekmod_localization.editor_update.latest_release", return_value=release), \
                 patch("lekmod_localization.editor_update._download", return_value=data):
                report = verify_installation(home)
                self.assertEqual(report["damaged_files"],
                                 ["README-START.txt", "localization/editor/app.js"])
                stage = stage_release(release, home, repair=True)
            self.assertEqual((stage / "localization/editor/app.js").read_bytes(),
                             expected["localization/editor/app.js"])
            self.assertEqual((home / "localization/editor/app.js").read_bytes(), b"corrupted UI")
            for name, content in personal.items():
                self.assertEqual((home / name).read_bytes(), content)

    def test_slow_new_editor_can_open_after_old_sixty_second_limit(self):
        """A cold first launch beyond a minute must not trigger rollback."""
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            marker = home / "localization/workspace/editor-updates/ready-slow.json"
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({"ticket": "slow", "port": 51234, "pid": 1234}))
            clock = [0.0]

            def respond(request, *, timeout):
                if clock[0] < 65:
                    raise URLError("editor is still opening")
                if request.endswith("/api/health"):
                    return io.BytesIO(b'{"editor_version":"0.13"}')
                if request.endswith("/app.js"):
                    return io.BytesIO(b"function renderTable() {}")
                return io.BytesIO(b"Lekmod Localization Editor")

            fake_time = Mock(monotonic=lambda: clock[0],
                             sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
            process = Mock()
            process.poll.return_value = None
            with patch("lekmod_localization.editor_update.time", fake_time), \
                 patch("lekmod_localization.editor_update._pid_alive", return_value=True), \
                 patch("lekmod_localization.editor_update.urllib.request.urlopen", side_effect=respond):
                signal = _wait_ready(home, "slow", "0.13", process)
            self.assertEqual(signal["port"], 51234)
            self.assertGreaterEqual(clock[0], 65)
            self.assertFalse(marker.exists())
            self.assertIn("still opening after 60s", (
                home / "localization/workspace/editor-updates/update.log").read_text())

    def test_failed_installer_is_explained_in_local_action_log(self):
        """A rollback preserves its cause in Logs without disclosing a password."""
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            _record(home, "failure", "EXE was locked at C:\\User\\Private\\editor.exe; password=private-value")
            _record(home, "recovered", "Previous editor reopened after failed update")
            path = home / "localization/workspace/editor-actions.jsonl"
            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(events[0]["result"], "failure")
            self.assertIn("EXE was locked", events[0]["detail"])
            self.assertNotIn("Private", events[0]["detail"])
            self.assertNotIn("private-value", path.read_text())
            self.assertNotIn("private-value", (home / "localization/workspace/editor-updates/update.log").read_text())
            self.assertEqual(events[-1]["result"], "recovered")

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
        editor.integrity_state = {"state": "idle"}
        token = "test-token"
        server = HTTPServer(("127.0.0.1", 0), make_handler(editor, token, 0))
        server.RequestHandlerClass = make_handler(editor, token, server.server_port)
        real_shutdown = server.shutdown
        server.shutdown = Mock()  # Observe the handoff before actually stopping.
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        release = {"available": True, "latest": "0.8"}

        def stage(release, progress, *, repair=False):
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

    def test_browser_repairs_damaged_current_version(self):
        """The wrench report allows Fix version when no newer version exists."""
        from editor_server import make_handler

        editor = Mock()
        editor.update_state = {"state": "idle"}
        editor.integrity_state = {"state": "idle"}
        server = HTTPServer(("127.0.0.1", 0), make_handler(editor, "test-token", 0))
        server.RequestHandlerClass = make_handler(editor, "test-token", server.server_port)
        real_shutdown = server.shutdown
        server.shutdown = Mock()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        headers = {"Origin": base, "X-Editor-Token": "test-token",
                   "Content-Type": "application/json"}
        current = editor_manifest()["version"]
        report = {"current": current, "latest": current, "available": False,
                  "can_auto_update": True, "damaged_files": ["README-START.txt"],
                  "verified": False}
        try:
            with patch("editor_server.verify_installation", return_value=report), \
                 patch("editor_server.latest_release", return_value=report), \
                 patch("editor_server.stage_release", return_value=Path("staged")) as stage, \
                 patch("editor_server.launch_update") as launch:
                request = Request(base + "/api/editor-verify", data=b"{}", headers=headers)
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                for _ in range(100):
                    with urlopen(base + "/api/editor-verify-status", timeout=5) as response:
                        status = json.load(response)
                    if status["state"] == "complete":
                        break
                    time.sleep(.01)
                self.assertEqual(status["damaged_files"], ["README-START.txt"])
                request = Request(base + "/api/editor-update", data=b'{"repair":true}',
                                  headers=headers)
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                for _ in range(100):
                    if server.shutdown.called:
                        break
                    time.sleep(.01)
                self.assertTrue(server.shutdown.called)
                self.assertTrue(stage.call_args.kwargs["repair"])
                launch.assert_called_once_with(Path("staged"), port=server.server_port,
                                               repair=True)
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

    def test_helper_acknowledgement_is_published_after_closing_its_handle(self):
        """Old Windows editors can delete the marker as soon as it is visible."""
        handles = []
        create_temporary = tempfile.NamedTemporaryFile
        replace = Path.replace
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "helper-ready-ticket"

            def capture_handle(*args, **kwargs):
                handle = create_temporary(*args, **kwargs)
                handles.append(handle)
                return handle

            def publish(path, destination):
                self.assertFalse(marker.exists())
                self.assertTrue(handles[0].closed)
                self.assertEqual(path.read_text(encoding="utf-8"), "ready")
                return replace(path, destination)

            with patch("lekmod_localization.editor_update.tempfile.NamedTemporaryFile",
                       side_effect=capture_handle), patch.object(Path, "replace", publish):
                _publish_helper_ready(marker)
            self.assertEqual(marker.read_text(encoding="utf-8"), "ready")
            self.assertEqual(list(marker.parent.iterdir()), [marker])

    def test_failed_helper_acknowledgement_has_no_partial_marker(self):
        """A failed atomic publish leaves no marker for the old editor to trust."""
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "helper-ready-ticket"
            with patch.object(Path, "replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    _publish_helper_ready(marker)
            self.assertEqual(list(marker.parent.iterdir()), [])

    def test_editor_retries_a_temporarily_locked_helper_acknowledgement(self):
        """A scanner locking the marker cannot immediately fail a running update."""
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "localization/workspace/editor-updates/editor-v0.19"
            stage.mkdir(parents=True)
            ticket = "a" * 32
            marker = stage.parent / ("helper-ready-" + ticket)
            marker.write_text("ready", encoding="utf-8")
            unlink = Path.unlink
            attempts = []

            def remove_marker(path, *args, **kwargs):
                attempts.append(path)
                if len(attempts) == 1:
                    raise PermissionError("Windows sharing violation")
                return unlink(path, *args, **kwargs)

            with patch("lekmod_localization.editor_update.sys.platform", "win32"), \
                 patch.object(sys, "frozen", True, create=True), \
                 patch.object(subprocess, "CREATE_NO_WINDOW", 0, create=True), \
                 patch("lekmod_localization.editor_update.subprocess.Popen") as process, \
                 patch("lekmod_localization.editor_update.uuid.uuid4") as uuid_mock, \
                 patch("lekmod_localization.editor_update.time.sleep") as pause, \
                 patch.object(Path, "unlink", remove_marker):
                process.return_value.poll.return_value = None
                uuid_mock.return_value.hex = ticket
                launch_update(stage, root, 123, 8123)
                pause.assert_called_once_with(.2)
            self.assertEqual(attempts, [marker, marker])
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
