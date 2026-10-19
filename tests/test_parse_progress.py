from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from amdl import core_downloader, task_manager
from amdl.server import TaskInfoResponse
from amdl.task_manager import DownloadTask, TaskManager, TaskStatus


class ParseProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_artist_subalbums_each_have_their_own_progress(self):
        updates = []

        class Downloader:
            async def get_download_item_from_url(self, url):
                for album in range(2):
                    yield SimpleNamespace(media=SimpleNamespace(
                        partial=True, error=None, media_metadata={
                            "type": "albums", "relationships": {"tracks": {"data": [{}, {}]}},
                        },
                    ))
                    for index in range(2):
                        yield SimpleNamespace(media=SimpleNamespace(
                            partial=False, error=None, media_metadata={
                                "type": "songs", "attributes": {"name": f"{album}/{index}"},
                            },
                        ))

        async for _ in core_downloader._parse_items_with_progress(
            Downloader(), "artist", logging.getLogger("test.parse"),
            lambda *update: updates.append(update),
        ):
            pass
        self.assertEqual(updates, [(0, None, ""), (0, 2, ""), (1, 2, "0/0"),
                                  (2, 2, "0/1"), (0, 2, ""), (1, 2, "1/0"), (2, 2, "1/1")])

    async def test_streaming_cli_counts_every_track_without_retaining_items(self):
        from gamdl.interface.types import AppleMusicMedia
        from gamdl.downloader.types import DownloadItem

        updates = []

        class Downloader:
            async def get_download_item_from_url(self, url):
                for index in range(100):
                    media = AppleMusicMedia(str(index), index=index, total=100, media_metadata={
                        "type": "songs", "attributes": {"name": f"Song {index}"},
                    })
                    yield DownloadItem(media)
                    media.partial = False
                    yield DownloadItem(media)

        async for _ in core_downloader._parse_items_with_progress(
            Downloader(), "playlist", logging.getLogger("test.parse"),
            lambda *update: updates.append(update),
        ):
            pass
        self.assertEqual(updates[-1], (100, 100, "Song 99"))

    async def test_playlist_reports_total_and_current_song_before_slow_resolution(self):
        updates = []
        first = SimpleNamespace(
            partial=True, error=None,
            media_metadata={"type": "songs", "attributes": {"name": "First"}},
        )
        failed = SimpleNamespace(
            partial=False, error=RuntimeError("unavailable"),
            media_metadata={"type": "songs", "attributes": {"name": "Unavailable"}},
        )
        playlist = SimpleNamespace(
            partial=True, error=None,
            media_metadata={"type": "playlists", "relationships": {
                "tracks": {"data": [{"id": "1"}, {"id": "2"}]},
            }},
        )

        async def report(completed, total, title):
            updates.append((completed, total, title))

        class Downloader:
            async def get_download_item_from_url(self, url):
                yield SimpleNamespace(media=playlist)
                yield SimpleNamespace(media=first)
                # This runs before the next item is yielded, as real network work does.
                self_test.assertEqual(updates[-1], (0, 2, "First"))
                first.partial = False
                yield SimpleNamespace(media=first)
                yield SimpleNamespace(media=failed)

        self_test = self
        items = [item async for item in core_downloader._parse_items_with_progress(
            Downloader(), "playlist", logging.getLogger("test.parse"), report,
        )]
        self.assertEqual(len(items), 4)
        self.assertEqual(updates[0], (0, None, ""))
        self.assertIn((0, 2, ""), updates)
        self.assertIn((1, 2, "First"), updates)
        self.assertEqual(updates[-1], (2, 2, "Unavailable"))

    async def test_unknown_total_and_new_url_reset(self):
        updates = []

        class Downloader:
            async def get_download_item_from_url(self, url):
                yield SimpleNamespace(media=SimpleNamespace(
                    partial=False, error=None,
                    media_metadata={"type": "songs", "attributes": {"name": url}},
                ))

        for url in ("one", "two"):
            async for _ in core_downloader._parse_items_with_progress(
                Downloader(), url, logging.getLogger("test.parse"),
                lambda *update: updates.append(update),
            ):
                pass
        self.assertEqual(updates, [(0, None, ""), (1, None, "one"),
                                  (0, None, ""), (1, None, "two")])

    async def test_parse_failure_does_not_count_as_download_success(self):
        manager = TaskManager()
        task = DownloadTask("parse-error", {"urls": ["playlist"]})
        task.status = TaskStatus.RUNNING
        manager._tasks[task.id] = task
        snapshots = []

        async def download(**kwargs):
            await kwargs["parse_progress_callback"](3, 10, "Third song")
            snapshots.append(TaskInfoResponse(**task.to_dict()))
            return 3

        with patch.object(task_manager, "_download_urls_async", download):
            await manager._execute_download(task.id)

        snapshot = snapshots[0]
        self.assertEqual(snapshot.phase, "parsing")
        self.assertEqual(snapshot.parsing, {"completed": 3, "total": 10, "current_title": "Third song"})
        self.assertEqual(snapshot.progress["completed"], 0)
        self.assertEqual(task.status, TaskStatus.FAILED)

    async def test_download_phase_and_websocket_subscription_include_parse_state(self):
        manager = TaskManager()
        task = DownloadTask("phases", {"urls": ["playlist"]})
        task.status = TaskStatus.RUNNING
        manager._tasks[task.id] = task
        socket = SimpleNamespace(send_json=AsyncMock())

        async def download(**kwargs):
            await kwargs["parse_progress_callback"](1, 2, "Second")
            await manager.subscribe(task.id, socket)
            snapshot = socket.send_json.call_args.args[0]
            self.assertEqual(snapshot["phase"], "parsing")
            self.assertEqual(snapshot["parsing"]["completed"], 1)
            await kwargs["progress_callback"](0, 2)
            self.assertEqual(task.phase, "downloading")
            await kwargs["progress_callback"](2, 2)
            return 0

        with patch.object(task_manager, "_download_urls_async", download):
            await manager._execute_download(task.id)
        self.assertEqual(task.status, TaskStatus.COMPLETED)

    async def test_cancelled_parse_cannot_update_progress(self):
        manager = TaskManager()
        task = DownloadTask("cancelled", {"urls": ["playlist"]})
        manager._tasks[task.id] = task

        async def download(**kwargs):
            task.cancelled = True
            await kwargs["parse_progress_callback"](1, 2, "Song")

        with patch.object(task_manager, "_download_urls_async", download):
            with self.assertRaises(asyncio.CancelledError):
                await manager._execute_download(task.id)


if __name__ == "__main__":
    unittest.main()
