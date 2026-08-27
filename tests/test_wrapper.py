from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from amdl import core_downloader, server, task_manager as task_manager_module
from amdl.core_downloader import (
    _configure_gamdl_logging,
    _download_urls_async,
    _is_alac_codec,
    _normalize_playlist_tracks,
    _song_codec_priority,
)
from amdl.enums import SongCodec
from amdl.server import DownloadRequest, WrapperLoginRequest
from amdl.task_manager import DownloadTask, TaskManager


class WrapperRequestTests(unittest.IsolatedAsyncioTestCase):
    def test_auth_mode_and_local_address_validation(self) -> None:
        request = DownloadRequest(
            urls=["test"],
            use_wrapper=True,
            codec_song="alac",
            audio_format="mp3",
        )
        self.assertIsNone(request.cookies_path)
        self.assertIsNone(request.audio_format)

        with self.assertRaises(ValidationError):
            DownloadRequest(urls=["test"], use_wrapper=True, wrapper_url="http://192.168.1.2")
        with self.assertRaises(ValidationError):
            DownloadRequest(urls=["test"], use_wrapper=False, codec_song="alac")
        with self.assertRaises(ValidationError):
            DownloadRequest(urls=["test"], use_wrapper=True, audio_format="alac")

    def test_cookie_mode_remains_compatible(self) -> None:
        with tempfile.NamedTemporaryFile() as cookies:
            request = DownloadRequest(urls=["test"], cookies_path=cookies.name)
        self.assertFalse(request.use_wrapper)

    def test_wrapper_status_never_exposes_tokens(self) -> None:
        status = server._wrapper_status({
            "version": "0.0.2",
            "runtime": {"playback_ready": True},
            "auth": {
                "state": "authenticated",
                "dev_token": "secret-dev-token",
                "music_user_token": "secret-user-token",
            },
        })
        self.assertTrue(status["authenticated"])
        self.assertTrue(status["compatible"])
        self.assertNotIn("token", repr(status).lower())

    def test_wrapper_version_mismatch_is_reported(self) -> None:
        status = server._wrapper_status({"version": "999", "auth": {}, "runtime": {}})
        self.assertFalse(status["compatible"])

    async def test_login_reports_two_factor_without_echoing_credentials(self) -> None:
        request = WrapperLoginRequest(
            wrapper_url="http://127.0.0.1",
            apple_id="person@example.com",
            password="secret",
        )
        with patch.object(
            server,
            "_wrapper_request",
            AsyncMock(return_value=httpx.Response(202)),
        ) as wrapper_request:
            response = await server.wrapper_login(request)

        self.assertEqual(response.status_code, 202)
        self.assertNotIn("secret", response.body.decode())
        self.assertEqual(wrapper_request.await_args.kwargs["payload"]["password"], "secret")

    async def test_login_failure_does_not_forward_wrapper_body(self) -> None:
        request = WrapperLoginRequest(
            wrapper_url="http://127.0.0.1",
            apple_id="person@example.com",
            password="wrong-password",
        )
        with patch.object(
            server,
            "_wrapper_request",
            AsyncMock(return_value=httpx.Response(401, text="music_user_token=secret")),
        ):
            with self.assertRaises(HTTPException) as raised:
                await server.wrapper_login(request)

        self.assertEqual(raised.exception.status_code, 401)
        self.assertNotIn("token", raised.exception.detail.lower())

    async def test_two_factor_response_does_not_echo_code(self) -> None:
        request = server.WrapperTwoFactorRequest(code="123456")
        with patch.object(
            server,
            "_wrapper_request",
            AsyncMock(return_value=httpx.Response(200)),
        ):
            response = await server.wrapper_login_2fa(request)

        self.assertEqual(response, {"state": "authenticated"})
        self.assertNotIn("123456", repr(response))

    async def test_unreachable_wrapper_is_reported_as_service_unavailable(self) -> None:
        class FailingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def request(self, *_args, **_kwargs):
                request = httpx.Request("GET", "http://127.0.0.1/me")
                raise httpx.ConnectError("connection refused", request=request)

        with patch.object(server.httpx, "AsyncClient", return_value=FailingClient()):
            with self.assertRaises(HTTPException) as raised:
                await server._wrapper_request("GET", "http://127.0.0.1", "/me")

        self.assertEqual(raised.exception.status_code, 503)

    async def test_settings_reject_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_file = Path(tmp) / "settings.json"
            with patch.object(server, "SETTINGS_FILE", settings_file):
                with self.assertRaises(HTTPException):
                    await server.save_settings({"password": "secret"})
            self.assertFalse(settings_file.exists())

    async def test_old_alac_conversion_setting_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_file = Path(tmp) / "settings.json"
            with patch.object(server, "SETTINGS_FILE", settings_file):
                await server.save_settings({"codec_song": "alac", "audio_format": "mp3"})
                response = await server.get_settings()

        self.assertEqual(json.loads(response.body)["audio_format"], "")


class CoreWrapperTests(unittest.IsolatedAsyncioTestCase):
    def test_gamdl_debug_logs_are_capped_at_info(self) -> None:
        sentinel = object()
        with (
            patch.object(core_downloader.structlog, "make_filtering_bound_logger", return_value=sentinel) as make_logger,
            patch.object(core_downloader.structlog, "configure") as configure,
        ):
            _configure_gamdl_logging("DEBUG")

        make_logger.assert_called_once_with(logging.INFO)
        configure.assert_called_once_with(wrapper_class=sentinel)

    def test_alac_priority_and_detection(self) -> None:
        self.assertEqual(
            _song_codec_priority(SongCodec.ALAC),
            [SongCodec.ALAC, SongCodec.AAC],
        )
        self.assertTrue(_is_alac_codec("alac"))
        self.assertFalse(_is_alac_codec("mp4a.40.2"))

    def test_zero_based_playlist_tracks_are_normalized_once(self) -> None:
        first = SimpleNamespace(playlist_tags=SimpleNamespace(track=0))
        second = SimpleNamespace(playlist_tags=SimpleNamespace(track=1))
        items = [
            SimpleNamespace(media=first),
            SimpleNamespace(media=first),
            SimpleNamespace(media=second),
        ]

        _normalize_playlist_tracks(items)

        self.assertEqual(first.playlist_tags.track, 1)
        self.assertEqual(second.playlist_tags.track, 2)

    async def test_wrapper_api_is_passed_to_base_interface(self) -> None:
        wrapper_api = object()
        apple_music_api = SimpleNamespace(active_subscription=True)
        with (
            patch.object(core_downloader.WrapperApi, "create", AsyncMock(return_value=wrapper_api)),
            patch.object(
                core_downloader.AppleMusicApi,
                "create_from_wrapper",
                AsyncMock(return_value=apple_music_api),
            ),
            patch.object(
                core_downloader.AppleMusicBaseInterface,
                "create",
                AsyncMock(side_effect=RuntimeError("stop after interface construction")),
            ) as create_interface,
        ):
            with self.assertRaisesRegex(RuntimeError, "stop after interface"):
                await _download_urls_async(urls=[], use_wrapper=True)

        self.assertIs(create_interface.await_args.kwargs["wrapper_api"], wrapper_api)

    async def test_alac_fallback_logs_track_and_actual_codec(self) -> None:
        media = SimpleNamespace(
            error=None,
            partial=False,
            final_path=Path("/tmp/Fallback Song.m4a"),
            media_metadata={"attributes": {"name": "Fallback Song"}},
            stream_info=SimpleNamespace(
                audio_track=SimpleNamespace(codec="mp4a.40.2"),
            ),
        )

        class FakeDownloader:
            async def get_download_item_from_url(self, _url):
                yield SimpleNamespace(media=media, final_path=media.final_path)

            async def download(self, _item):
                return None

        logs: list[str] = []
        base_downloader = SimpleNamespace(
            full_nm3u8dlre_path=None,
            full_ffmpeg_path=None,
            download_stream=None,
        )
        with (
            patch.object(core_downloader.WrapperApi, "create", AsyncMock(return_value=object())),
            patch.object(
                core_downloader.AppleMusicApi,
                "create_from_wrapper",
                AsyncMock(return_value=SimpleNamespace(active_subscription=True)),
            ),
            patch.object(core_downloader.AppleMusicBaseInterface, "create", AsyncMock(return_value=object())),
            patch.object(core_downloader, "AppleMusicInterface", return_value=object()),
            patch.object(core_downloader, "AppleMusicSongInterface", return_value=object()),
            patch.object(core_downloader, "AppleMusicMusicVideoInterface", return_value=object()),
            patch.object(core_downloader, "AppleMusicUploadedVideoInterface", return_value=object()),
            patch.object(core_downloader, "AppleMusicBaseDownloader", return_value=base_downloader),
            patch.object(core_downloader, "AppleMusicSongDownloader", return_value=object()),
            patch.object(core_downloader, "AppleMusicMusicVideoDownloader", return_value=object()),
            patch.object(core_downloader, "AppleMusicUploadedVideoDownloader", return_value=object()),
            patch.object(core_downloader, "AppleMusicDownloader", return_value=FakeDownloader()),
        ):
            errors = await _download_urls_async(
                urls=["https://music.apple.com/test"],
                use_wrapper=True,
                codec_song=SongCodec.ALAC,
                log_callback=logs.append,
            )

        self.assertEqual(errors, 0)
        warning = next(line for line in logs if "ALAC unavailable" in line)
        self.assertIn("Fallback Song", warning)
        self.assertIn("mp4a.40.2", warning)

    async def test_shared_failed_media_is_logged_once(self) -> None:
        media = SimpleNamespace(
            error=RuntimeError("blocked"),
            partial=False,
            media_metadata={"attributes": {"name": "Explicit Song"}},
        )

        class FakeDownloader:
            async def get_download_item_from_url(self, _url):
                item = SimpleNamespace(media=media, final_path=None)
                yield item
                yield item

        logs: list[str] = []
        base_downloader = SimpleNamespace(
            full_nm3u8dlre_path=None,
            full_ffmpeg_path=None,
            download_stream=None,
        )
        with (
            patch.object(core_downloader.WrapperApi, "create", AsyncMock(return_value=object())),
            patch.object(
                core_downloader.AppleMusicApi,
                "create_from_wrapper",
                AsyncMock(return_value=SimpleNamespace(active_subscription=True)),
            ),
            patch.object(core_downloader.AppleMusicBaseInterface, "create", AsyncMock(return_value=object())),
            patch.object(core_downloader, "AppleMusicInterface", return_value=object()),
            patch.object(core_downloader, "AppleMusicSongInterface", return_value=object()),
            patch.object(core_downloader, "AppleMusicMusicVideoInterface", return_value=object()),
            patch.object(core_downloader, "AppleMusicUploadedVideoInterface", return_value=object()),
            patch.object(core_downloader, "AppleMusicBaseDownloader", return_value=base_downloader),
            patch.object(core_downloader, "AppleMusicSongDownloader", return_value=object()),
            patch.object(core_downloader, "AppleMusicMusicVideoDownloader", return_value=object()),
            patch.object(core_downloader, "AppleMusicUploadedVideoDownloader", return_value=object()),
            patch.object(core_downloader, "AppleMusicDownloader", return_value=FakeDownloader()),
        ):
            errors = await _download_urls_async(
                urls=["https://music.apple.com/test"],
                use_wrapper=True,
                codec_song=SongCodec.ALAC,
                log_callback=logs.append,
            )

        self.assertEqual(errors, 1)
        self.assertEqual(sum('Skip "Explicit Song"' in line for line in logs), 1)
        self.assertNotIn("NoneType: None", "\n".join(logs))

    async def test_task_manager_forwards_wrapper_options(self) -> None:
        manager = TaskManager()
        task = DownloadTask("wrapper-task", {
            "urls": ["https://music.apple.com/test"],
            "use_wrapper": True,
            "wrapper_url": "http://127.0.0.1:8080",
            "wrapper_decrypt_host": "localhost",
            "wrapper_decrypt_port": 11020,
        })
        manager._tasks[task.id] = task
        captured: dict = {}

        async def fake_download(**kwargs) -> int:
            captured.update(kwargs)
            return 0

        with patch.object(task_manager_module, "_download_urls_async", fake_download):
            await manager._execute_download(task.id)

        self.assertTrue(captured["use_wrapper"])
        self.assertEqual(captured["wrapper_url"], "http://127.0.0.1:8080")
        self.assertEqual(captured["wrapper_decrypt_port"], 11020)


if __name__ == "__main__":
    unittest.main()
