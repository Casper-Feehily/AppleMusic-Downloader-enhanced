from __future__ import annotations

import sys
import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from amdl import cli as cli_module


PLAYLIST_URL = (
    "https://music.apple.com/us/playlist/test/"
    "pl.1234567890abcdef1234567890abcdef"
)


class CliRoutingTests(unittest.TestCase):
    def test_passthrough_reports_parse_progress_and_restores_upstream(self) -> None:
        from gamdl.cli import cli as upstream

        logs = []
        original_argv = sys.argv

        class Downloader:
            async def get_download_item_from_url(self, url):
                yield SimpleNamespace(media=SimpleNamespace(
                    partial=True, error=None, media_metadata={
                        "type": "playlists", "relationships": {"tracks": {"data": [{"id": "1"}]}},
                    },
                ))
                media = SimpleNamespace(partial=True, error=None, media_metadata={
                    "type": "songs", "attributes": {"name": "Test Song"},
                })
                yield SimpleNamespace(media=media)
                media.partial = False
                yield SimpleNamespace(media=media)

        def main():
            self.assertEqual(sys.argv, ["gamdl", PLAYLIST_URL])

            async def parse():
                return [item async for item in upstream.AppleMusicDownloader().get_download_item_from_url(PLAYLIST_URL)]

            self.assertEqual(len(asyncio.run(parse())), 3)

        logger = SimpleNamespace(bind=lambda **kwargs: SimpleNamespace(info=logs.append))
        with (
            patch.object(upstream, "AppleMusicDownloader", Downloader),
            patch.object(upstream, "main", main),
            patch.object(upstream, "logger", logger),
        ):
            cli_module._passthrough_to_gamdl([PLAYLIST_URL])
            self.assertIs(upstream.AppleMusicDownloader, Downloader)

        self.assertIs(sys.argv, original_argv)
        self.assertTrue(any('Resolving track 1/1: "Test Song"' in line for line in logs))
        self.assertTrue(any('Parsed 1/1: "Test Song"' in line for line in logs))

    def test_passthrough_restores_upstream_after_exception(self) -> None:
        from gamdl.cli import cli as upstream

        downloader = upstream.AppleMusicDownloader
        argv = sys.argv
        with patch.object(upstream, "main", side_effect=RuntimeError("test failure")):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                cli_module._passthrough_to_gamdl([PLAYLIST_URL])
        self.assertIs(upstream.AppleMusicDownloader, downloader)
        self.assertIs(sys.argv, argv)

    def test_gamdl_arguments_are_forwarded_unchanged(self) -> None:
        invocations = [
            [PLAYLIST_URL],
            ["-c", "/tmp/cookies.txt", PLAYLIST_URL],
            ["-c", "/tmp/cookies.txt", "-o", "/tmp/music", PLAYLIST_URL],
            [
                "--use-wrapper",
                "--wrapper-url", "http://127.0.0.1:8080",
                "--wrapper-decrypt-host", "localhost",
                "--wrapper-decrypt-port", "11020",
                "--song-codec-priority", "alac,aac-web",
                PLAYLIST_URL,
            ],
        ]

        for args in invocations:
            with self.subTest(args=args):
                with (
                    patch.object(sys, "argv", ["amdl", *args]),
                    patch.object(cli_module, "_passthrough_to_gamdl") as passthrough,
                ):
                    cli_module.main()

                passthrough.assert_called_once_with(args)

    def test_local_commands_are_not_forwarded(self) -> None:
        for command in ("server", "desktop", "--version"):
            with self.subTest(command=command):
                with (
                    patch.object(sys, "argv", ["amdl", command]),
                    patch.object(cli_module, "cli") as local_cli,
                    patch.object(cli_module, "_passthrough_to_gamdl") as passthrough,
                ):
                    cli_module.main()

                local_cli.assert_called_once_with(
                    args=[command], standalone_mode=False
                )
                passthrough.assert_not_called()

    def test_help_is_not_forwarded(self) -> None:
        with (
            patch.object(sys, "argv", ["amdl", "--help"]),
            patch.object(cli_module.click, "echo") as echo,
            patch.object(cli_module, "_passthrough_to_gamdl") as passthrough,
        ):
            cli_module.main()

        echo.assert_called_once_with(cli_module._HELP)
        passthrough.assert_not_called()


if __name__ == "__main__":
    unittest.main()
