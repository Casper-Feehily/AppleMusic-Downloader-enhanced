import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from gamdl.cli import cli as gamdl_cli
from gamdl.downloader import AppleMusicDownloader
from gamdl.interface import AppleMusicInterface, AppleMusicMedia, MediaTags
from gamdl.interface.enums import MediaFileFormat
from gamdl.interface.types import Cover, Lyrics, PlaylistTags, StreamInfoAv

from amdl import cli, core_downloader
from amdl.server import DownloadRequest
from amdl.task_manager import DownloadTask, TaskManager


async def _get_media(_interface, _url):
    for index, album in enumerate(("First Album", "Second Album", None), start=1):
        media_id = str(index)
        yield AppleMusicMedia(
            media_id,
            partial=False,
            media_metadata={"id": media_id, "type": "songs"},
            tags=MediaTags(
                title="Same Song", title_id=index, track=1,
                album=album, artist="Singer", album_artist="Album Artist",
                compilation=index == 2,
            ),
            cover=Cover(file_extension=".jpg", url=media_id),
            lyrics=Lyrics(synced=media_id),
            playlist_tags=(
                PlaylistTags(playlist_id=10, title="Mix", artist="Curator", track=1)
                if index == 1 else None
            ),
            stream_info=StreamInfoAv(file_format=MediaFileFormat.M4A),
        )

async def _stage_download(_downloader, item):
    staged_path = Path(item.staged_path)
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path.write_text(item.media.media_id, encoding="utf-8")


class DownloadOutputLayoutTests(IsolatedAsyncioTestCase):
    async def test_download_paths_follow_the_task_layout(self):
        cases = {
            None: [
                "Custom/First Album/01 Same Song",
                "Compilations/Second Album/01 Same Song",
                "Singer/Unknown Album/Same Song",
            ],
            "artist": ["Singer/Same Song", "Singer/Same Song（1）", "Singer/Same Song（2）"],
            "flat": ["Same Song（2）", "Same Song（3）", "Same Song（4）"],
        }

        for layout, expected in cases.items():
            with self.subTest(layout=layout), TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "music"
                if layout == "flat":
                    output.mkdir()
                    (output / "same song.mp3").write_bytes(b"existing audio")
                    (output / "Same Song（1）.lrc").write_text("existing lyrics", encoding="utf-8")
                cookies = root / "cookies.txt"
                cookies.touch()
                layout_fields = {"output_layout": layout} if layout is not None else {}
                request = DownloadRequest(
                    urls=["https://music.apple.com/us/playlist/test/pl.test"],
                    cookies_path=str(cookies),
                    output_path=str(output), temp_path=str(root / "temp"),
                    template_folder_album="Custom/{album}",
                    save_cover=True, save_playlist=True, overwrite=layout == "flat",
                    **layout_fields,
                )
                api = SimpleNamespace(
                    active_subscription=True,
                    get_playlist=AsyncMock(),
                    get_library_playlist=AsyncMock(),
                )
                interface = SimpleNamespace(get_cover_bytes=AsyncMock(side_effect=lambda url: url.encode()))
                manager = TaskManager()
                task = DownloadTask("layout", request.model_dump())
                manager._tasks[task.id] = task
                with (
                    patch.object(core_downloader.AppleMusicApi, "create_from_netscape_cookies", return_value=api),
                    patch.object(core_downloader.AppleMusicBaseInterface, "create", return_value=interface),
                    patch.object(AppleMusicInterface, "get_media_from_url", _get_media),
                    patch.object(AppleMusicDownloader, "_download", _stage_download),
                ):
                    await manager._execute_download(task.id)

                self.assertEqual(task.error_count, 0)
                self.assertEqual(task.progress, (3, 3))
                self.assertEqual(
                    sorted(path.relative_to(output).as_posix() for path in output.rglob("*.m4a")),
                    sorted(f"{stem}.m4a" for stem in expected),
                )
                for index, stem in enumerate(expected, start=1):
                    self.assertEqual((output / f"{stem}.lrc").read_text(encoding="utf-8"), str(index))
                    if layout is not None:
                        self.assertEqual((output / f"{stem}.jpg").read_bytes(), str(index).encode())
                if layout == "flat":
                    self.assertEqual((output / "same song.mp3").read_bytes(), b"existing audio")
                    self.assertEqual((output / "Same Song（1）.lrc").read_text(encoding="utf-8"), "existing lyrics")
                playlist = (
                    output / "Playlists/Curator/Mix/Mix.m3u" if layout is None
                    else output / "Mix.m3u"
                )
                self.assertEqual(
                    (playlist.parent / playlist.read_text(encoding="utf-8").strip()).resolve(),
                    (output / f"{expected[0]}.m4a").resolve(),
                )


class CliOutputLayoutTests(TestCase):
    def test_cli_layout_sets_paths_with_an_existing_config_file(self):
        cases = (
            ([], ["Saved/First Album/01 Same Song", "Compilations/Second Album/01 Same Song",
                  "Singer/Unknown Album/Same Song"]),
            (["--output-layout", "artist_album"], [
                "Saved/First Album/01 Same Song", "Compilations/Second Album/01 Same Song",
                "Singer/Unknown Album/Same Song",
            ]),
            (["--output-layout", "artist"], [
                "Singer/Same Song", "Singer/Same Song（1）", "Singer/Same Song（2）",
            ]),
            (["--output-layout=flat"], ["Same Song（1）", "Same Song（2）", "Same Song（3）"]),
        )

        for options, expected in cases:
            with self.subTest(options=options), TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "music"
                output.mkdir()
                if options == ["--output-layout=flat"]:
                    (output / "Same Song.mp3").write_bytes(b"already downloaded")
                cookies = root / "cookies.txt"
                cookies.touch()
                config = root / "config.ini"
                config.write_text(
                    "[gamdl]\nalbum_folder_template = Saved/{album}\n"
                    "use_wrapper = false\nno_synced_lyrics = false\nsynced_lyrics_only = false\n",
                    encoding="utf-8",
                )
                args = [
                    "amdl", "--config-path", str(config), "-c", str(cookies),
                    "-o", str(output), "--temp-path", str(root / "temp"),
                    "--save-cover", "--save-playlist", *options,
                    "https://music.apple.com/us/playlist/test/pl.test",
                ]
                api = SimpleNamespace(active_subscription=True, account_restrictions=False)
                interface = SimpleNamespace(
                    get_cover_bytes=AsyncMock(side_effect=lambda url: url.encode()),
                )
                with (
                    patch.object(sys, "argv", args),
                    patch.object(gamdl_cli.WrapperApi, "create", side_effect=AssertionError("Expected cookies mode")),
                    patch.object(gamdl_cli.AppleMusicApi, "create_from_netscape_cookies", return_value=api),
                    patch.object(gamdl_cli.AppleMusicBaseInterface, "create", return_value=interface),
                    patch.object(AppleMusicInterface, "get_media_from_url", _get_media),
                    patch.object(AppleMusicDownloader, "_download", _stage_download),
                    self.assertRaises(SystemExit) as exit_result,
                ):
                    cli.main()

                self.assertEqual(exit_result.exception.code, 0)
                self.assertEqual(
                    sorted(path.relative_to(output).as_posix() for path in output.rglob("*.m4a")),
                    sorted(f"{stem}.m4a" for stem in expected),
                )
                if options and "artist_album" not in options:
                    for index, stem in enumerate(expected, start=1):
                        self.assertEqual((output / f"{stem}.lrc").read_text(encoding="utf-8"), str(index))
                        self.assertEqual((output / f"{stem}.jpg").read_bytes(), str(index).encode())
                    self.assertEqual(
                        (output / "Mix.m3u").read_text(encoding="utf-8").strip(),
                        f"{expected[0]}.m4a",
                    )
                    if options == ["--output-layout=flat"]:
                        self.assertEqual((output / "Same Song.mp3").read_bytes(), b"already downloaded")
