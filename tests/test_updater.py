from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from amdl import updater


PAYLOAD = b"verified installer bytes"
NAME = "AppleMusicDownloader-Setup-windows-x64.exe"
URL = f"https://github.com/Casper-Feehily/AppleMusic-Downloader-enhanced/releases/download/v2.5.0/{NAME}"


def release(**overrides):
    asset = dict(name=NAME, size=len(PAYLOAD), digest="sha256:" + hashlib.sha256(PAYLOAD).hexdigest(),
                 browser_download_url=URL, state="uploaded")
    asset.update(overrides)
    return dict(tag_name="v2.5.0", draft=False, prerelease=False, assets=[asset])


class AssetTests(unittest.TestCase):
    def test_selects_windows_asset_and_rejects_wrong_architecture(self):
        asset = updater.select_asset(release(), "win32", "AMD64", "2.4.14")
        self.assertEqual(asset.name, NAME)
        with self.assertRaises(updater.UpdateError):
            updater.select_asset(release(), "darwin", "arm64", "2.4.14")
        with self.assertRaises(updater.UpdateError):
            updater.select_asset(release(), "win32", "ARM64", "2.4.14")

    def test_selects_apple_silicon_dmg(self):
        name = "AppleMusicDownloader-macos-arm64.dmg"
        data = release(name=name, browser_download_url=URL.rsplit("/", 1)[0] + "/" + name)
        self.assertEqual(updater.select_asset(data, "darwin", "arm64", "2.4.14").name, name)

    def test_refuses_unverifiable_or_untrusted_releases(self):
        cases = [release(digest=None), release(size=0), release(browser_download_url="https://evil.example/setup.exe"),
                 {**release(), "tag_name": "v2.5.0;evil"}, {**release(), "prerelease": True}]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(updater.UpdateError):
                updater.select_asset(data, "win32", "amd64", "2.4.14")
        with self.assertRaises(updater.UpdateError):
            updater.select_asset(release(), "win32", "amd64", "2.5.0")

    def test_streams_verifies_and_reports_progress(self):
        asset = updater.select_asset(release(), "win32", "amd64", "2.4.14")
        progress = []
        with tempfile.TemporaryDirectory() as temp, httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=PAYLOAD)
        )) as client:
            path = Path(temp) / NAME
            updater.download_asset(client, asset, path, lambda done, total: progress.append((done, total)))
            self.assertEqual(path.read_bytes(), PAYLOAD)
            self.assertEqual(progress[-1], (len(PAYLOAD), len(PAYLOAD)))

    def test_corrupt_or_truncated_download_is_removed(self):
        asset = updater.select_asset(release(), "win32", "amd64", "2.4.14")
        for content in (b"corrupt", b"x" * len(PAYLOAD), PAYLOAD + b"extra"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temp, httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))
            ) as client:
                path = Path(temp) / NAME
                with self.assertRaises(updater.UpdateError):
                    updater.download_asset(client, asset, path, lambda *_: None)
                self.assertFalse(path.exists())
                self.assertFalse(path.with_suffix(path.suffix + ".part").exists())

    def test_redirect_outside_github_is_rejected_before_request(self):
        asset = updater.select_asset(release(), "win32", "amd64", "2.4.14")
        requested = []

        def serve(request):
            requested.append(request.url.host)
            return httpx.Response(302, headers={"Location": "http://evil.example/setup.exe"})

        with tempfile.TemporaryDirectory() as temp, httpx.Client(transport=httpx.MockTransport(serve)) as client:
            with self.assertRaises(updater.UpdateError):
                updater.download_asset(client, asset, Path(temp) / NAME, lambda *_: None)
        self.assertEqual(requested, ["github.com"])


class LifecycleTests(unittest.TestCase):
    def test_post_install_error_survives_restart_without_release_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "updates").mkdir()
            (root / "updates/install-error.txt").write_text("Installer failed", encoding="utf-8-sig")
            manager = updater.UpdateManager(root, None, lambda: None)
            with (patch.object(updater, "installation_target", return_value=("win32", root / "App.exe")),
                  patch.object(updater, "__version__", "2.0.0")):
                status = manager.status()
            self.assertEqual(status["phase"], "error")
            self.assertEqual(status["error"], "Installer failed")
            self.assertEqual(status["current_version"], "2.0.0")

    def run_update(self, temp, *, corrupt=False, busy=False, helper_error=False):
        events = []
        target = Path(temp) / "Application.exe"
        target.write_bytes(b"old application")

        class Tasks:
            attempts = 0
            reserved = False

            def reserve_update(self):
                self.attempts += 1
                self.reserved = not (busy and self.attempts == 1)
                events.append("reserve" if self.reserved else "busy")
                return self.reserved

            def release_update(self):
                self.reserved = False
                events.append("release")

        tasks = Tasks()
        manager = updater.UpdateManager(Path(temp), tasks, lambda: events.append("close"))

        def serve(request):
            if request.url.host == "api.github.com":
                return httpx.Response(200, json=release())
            return httpx.Response(200, content=b"bad" if corrupt else PAYLOAD)

        client = httpx.Client(transport=httpx.MockTransport(serve))

        def launch(*args):
            events.append("helper")
            self.assertTrue(tasks.reserved)
            if helper_error:
                raise OSError("helper failed")

        with (patch.object(updater, "installation_target", return_value=("win32", target)),
              patch.object(updater, "__version__", "2.4.14"),
              patch.object(updater.platform, "machine", return_value="AMD64"),
              patch.object(updater.httpx, "Client", return_value=client),
              patch.object(updater.time, "sleep", return_value=None),
              patch("amdl.update_helpers.prepare_install", side_effect=lambda system, package, *args: package),
              patch("amdl.update_helpers.launch_helper", side_effect=launch)):
            manager._run()
        self.assertEqual(target.read_bytes(), b"old application")
        return manager, tasks, events

    def test_waits_for_tasks_before_helper_and_close(self):
        with tempfile.TemporaryDirectory() as temp:
            manager, tasks, events = self.run_update(temp, busy=True)
            self.assertEqual(events, ["busy", "reserve", "helper", "close"])
            self.assertEqual(manager._state["phase"], "installing")
            self.assertTrue(tasks.reserved)

    def test_corrupt_package_never_reserves_or_closes(self):
        with tempfile.TemporaryDirectory() as temp:
            manager, tasks, events = self.run_update(temp, corrupt=True)
            self.assertEqual(events, [])
            self.assertEqual(manager._state["phase"], "error")
            self.assertFalse(tasks.reserved)

    def test_helper_failure_releases_queue_and_keeps_application_open(self):
        with tempfile.TemporaryDirectory() as temp:
            manager, tasks, events = self.run_update(temp, helper_error=True)
            self.assertEqual(events, ["reserve", "helper", "release"])
            self.assertEqual(manager._state["phase"], "error")
            self.assertFalse(tasks.reserved)


if __name__ == "__main__":
    unittest.main()
