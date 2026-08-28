from unittest import TestCase
from unittest.mock import patch

from pathlib import Path

from amdl.dependency_manager import _ffmpeg_urls, _nm3u8dlre_urls, find_system_binary


class DependencyManagerTests(TestCase):
    def test_desktop_ffmpeg_has_no_runtime_download(self) -> None:
        for system in ("macos", "windows"):
            with patch("amdl.dependency_manager._os", return_value=system):
                self.assertEqual(_ffmpeg_urls(), [])

    def test_macos_finds_homebrew_outside_path(self) -> None:
        with (
            patch("amdl.dependency_manager._os", return_value="macos"),
            patch("amdl.dependency_manager.shutil.which", return_value=None),
            patch.object(Path, "is_file", side_effect=[True]),
        ):
            self.assertEqual(find_system_binary("ffmpeg"), "/opt/homebrew/bin/ffmpeg")

    def test_apple_silicon_nm3u8dlre_release_url(self) -> None:
        with (
            patch("amdl.dependency_manager._os", return_value="macos"),
            patch("amdl.dependency_manager._arch", return_value="arm64"),
        ):
            self.assertEqual(
                _nm3u8dlre_urls(),
                [
                    "https://github.com/nilaoda/N_m3u8DL-RE/releases/download/"
                    "v0.6.0-beta/N_m3u8DL-RE_v0.6.0-beta_osx-arm64_20260629.tar.gz"
                ],
            )
