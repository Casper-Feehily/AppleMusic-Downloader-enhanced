import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from gamdl.downloader import AppleMusicDownloader
from gamdl.downloader.types import DownloadItem
from gamdl.interface.types import AppleMusicMedia, MediaTags, PlaylistTags
from mutagen.id3 import TALB, TIT2, TPE1
from mutagen.wave import WAVE

from amdl import core_downloader
from amdl.server import DownloadRequest
from amdl.task_manager import DownloadTask, TaskManager


class IncrementalDownloadTests(IsolatedAsyncioTestCase):
    async def test_task_skips_a_renamed_song_in_a_subfolder_and_keeps_playlist_entry(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            library = root / "previous"
            existing = library / "Moved" / "Renamed.wav"
            existing.parent.mkdir(parents=True)
            with wave.open(str(existing), "wb") as audio:
                audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\0" * 1600)
            audio = WAVE(existing)
            audio.add_tags()
            audio.tags.add(TIT2(encoding=3, text=["Café"]))
            audio.tags.add(TPE1(encoding=3, text=["Artist"]))
            audio.tags.add(TALB(encoding=3, text=["Original Album"]))
            audio.save()
            original = existing.read_bytes()
            (library / "Damaged.m4a").write_bytes(b"invalid audio")

            output = root / "new"
            playlist = output / "Playlists" / "Mix.m3u8"
            items = []
            for index, album in enumerate(["Other Album", "Original Album"], start=1):
                media = AppleMusicMedia(
                    str(index), partial=False,
                    media_metadata={"type": "songs", "attributes": {"name": "Cafe\u0301"}},
                    tags=MediaTags(title="Cafe\u0301", artist="Artist", album=album, title_id=index),
                    playlist_tags=PlaylistTags(track=index),
                )
                items.append(DownloadItem(
                    media, final_path=str(output / f"{index}.m4a"),
                    playlist_file_path=str(playlist),
                ))

            async def get_items(downloader, url):
                for item in items:
                    yield item

            async def download(downloader, item):
                await downloader._initial_processing(item)
                Path(item.final_path).write_bytes(b"new download")

            cookies = root / "cookies.txt"
            cookies.touch()
            request = DownloadRequest(
                urls=["playlist"], cookies_path=str(cookies),
                output_path=str(output), existing_library_path=str(library),
                save_playlist=True, overwrite=True,
            )
            manager = TaskManager()
            task = DownloadTask("incremental", request.model_dump())
            manager._tasks[task.id] = task
            with (
                patch.object(core_downloader.AppleMusicApi, "create_from_netscape_cookies",
                             return_value=SimpleNamespace(active_subscription=True)),
                patch.object(core_downloader.AppleMusicBaseInterface, "create"),
                patch.object(AppleMusicDownloader, "get_download_item_from_url", get_items),
                patch.object(AppleMusicDownloader, "download", download),
            ):
                await manager._execute_download(task.id)

            self.assertTrue((output / "1.m4a").is_file())
            self.assertFalse((output / "2.m4a").exists())
            self.assertEqual(existing.read_bytes(), original)
            self.assertEqual(task.progress, (2, 2))
            self.assertEqual(task.error_count, 0)
            entries = playlist.read_text(encoding="utf-8").splitlines()
            self.assertEqual([(playlist.parent / entry).resolve() for entry in entries],
                             [(output / "1.m4a").resolve(), existing.resolve()])

    async def test_missing_library_fails_before_initializing_apple_music(self):
        with TemporaryDirectory() as directory:
            logs = []
            errors = await core_downloader._download_urls_async(
                urls=["playlist"], existing_library_path=Path(directory) / "missing",
                log_callback=logs.append,
            )
            self.assertEqual(errors, 1)
            self.assertTrue(any("existing library" in line.lower() for line in logs))
