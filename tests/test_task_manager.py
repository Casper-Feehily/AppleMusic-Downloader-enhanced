from __future__ import annotations

import unittest
from unittest.mock import patch

from amdl import task_manager as task_manager_module
from amdl.task_manager import DownloadTask, TaskManager, TaskStatus


class UpdateReservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_reserved_update_blocks_submission_until_released(self):
        import asyncio
        manager = TaskManager()
        manager._queue = asyncio.Queue()
        self.assertTrue(manager.reserve_update())
        with self.assertRaises(RuntimeError):
            await manager.submit({"urls": ["test"]})
        self.assertEqual(manager.list_tasks(), [])
        manager.release_update()
        task_id = await manager.submit({"urls": ["test"]})
        self.assertEqual(manager.get_task(task_id).status, TaskStatus.PENDING)
        self.assertFalse(manager.reserve_update())

    async def test_cancelled_worker_must_unwind_before_reserving(self):
        manager = TaskManager()
        task = DownloadTask("busy", {})
        task.status = TaskStatus.CANCELLED
        manager._tasks[task.id] = task
        manager._active_task_id = task.id
        self.assertFalse(manager.reserve_update())
        manager._active_task_id = None
        self.assertTrue(manager.reserve_update())

    async def test_cancelled_thread_backed_download_still_blocks_update(self):
        import asyncio
        import threading
        manager = TaskManager()
        entered = threading.Event()
        finish = threading.Event()

        def blocking_download():
            entered.set()
            finish.wait(timeout=5)

        async def download(**kwargs):
            await asyncio.to_thread(blocking_download)
            await kwargs["progress_callback"](1, 1)
            return 0

        with patch.object(task_manager_module, "_download_urls_async", download):
            manager.start()
            try:
                task_id = await manager.submit({"urls": ["test"]})
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(entered.is_set())
                await manager.cancel_task(task_id)
                await asyncio.sleep(0.02)
                self.assertFalse(manager.reserve_update())
                finish.set()
                await asyncio.wait_for(manager._queue.join(), timeout=2)
                self.assertTrue(manager.reserve_update())
            finally:
                finish.set()
                await manager.stop()


class TaskResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_playlist_with_successes_and_errors_is_partial(self) -> None:
        manager = TaskManager()
        task = DownloadTask(
            "playlist-task",
            {
                "urls": ["https://music.apple.com/test-playlist"],
                "cookies_path": "cookies.txt",
            },
        )
        manager._tasks[task.id] = task

        async def partial_download(**kwargs) -> int:
            await kwargs["progress_callback"](9, 10)
            return 1

        with patch.object(task_manager_module, "_download_urls_async", partial_download):
            await manager._execute_download(task.id)

        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.message, "部分完成（1 个错误）")


if __name__ == "__main__":
    unittest.main()
