from unittest import TestCase
from unittest.mock import patch

from amdl.dependency_manager import _nm3u8dlre_urls


class DependencyManagerTests(TestCase):
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
