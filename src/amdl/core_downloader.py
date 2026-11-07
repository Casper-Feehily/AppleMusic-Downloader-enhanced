"""Core download API — thin wrapper around gamdl's embedding API."""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Callable
from xml.dom import Node, minidom

import structlog

# ── Python version guard ────────────────────────────────
_MIN_PYTHON = (3, 10)
if sys.version_info[:2] < _MIN_PYTHON:
    raise RuntimeError(f"Python {sys.version_info[0]}.{sys.version_info[1]} too old")

if sys.platform == "win32":
    os.environ.setdefault("ANYIO_BACKEND", "asyncio")

# ── gamdl imports ───────────────────────────────────────
from gamdl.api import AppleMusicApi, WrapperApi
from gamdl.downloader import (
    AppleMusicBaseDownloader,
    AppleMusicDownloader,
    AppleMusicMusicVideoDownloader,
    AppleMusicSongDownloader,
    AppleMusicUploadedVideoDownloader,
    DownloadMode,
)
from gamdl.downloader.exceptions import (
    GamdlDownloaderMediaFileExistsError,
    GamdlDownloaderSyncedLyricsOnlyError,
)
from gamdl.interface import (
    AppleMusicBaseInterface,
    AppleMusicInterface,
    AppleMusicMedia,
    AppleMusicMusicVideoInterface,
    AppleMusicSongInterface,
    AppleMusicUploadedVideoInterface,
    CoverFormat,
    MediaRating,
    MediaTags,
    MediaType,
    MusicVideoCodec,
    SongCodec,
    SyncedLyricsFormat,
    UploadedVideoQuality,
)
from gamdl.interface.exceptions import (
    GamdlInterfaceDecryptionNotAvailableError,
    GamdlInterfaceMediaNotStreamableError,
)
from gamdl.interface.types import Cover, DecryptionKeyAv, StreamInfoAv

from amdl.existing_library import ExistingLibrary, write_existing_playlist_entry

LogCallback = Callable[[str], None]
ParseProgressCallback = Callable[[int, int | None, str], object]
_PUBLICATION_LOCK = threading.Lock()


async def _parse_items_with_progress(dl, url: str, log, callback=None):
    """Report metadata resolution separately from completed file downloads."""
    completed = 0
    total = None
    collection_types = {"playlists", "library-playlists", "albums", "library-albums", "artists"}
    # Retain objects so streaming CLI consumption cannot reuse a resolved id.
    resolved_media: dict[int, object] = {}

    async def report(title: str = ""):
        if callback:
            result = callback(completed, total, title)
            if asyncio.iscoroutine(result):
                await result

    await report()
    async for item in dl.get_download_item_from_url(url):
        media = item.media
        metadata = media.media_metadata if isinstance(media.media_metadata, dict) else {}
        media_type = metadata.get("type")
        if media_type in collection_types:
            tracks = metadata.get("relationships", {}).get("tracks", {}).get("data")
            if tracks is not None:
                completed = 0
                resolved_media.clear()
                total = len(tracks)
                log.info(f"Found {total} tracks; resolving metadata")
                await report()
        else:
            if total is None:
                total = getattr(media, "total", None) or None
            title = metadata.get("attributes", {}).get("name", "?")
            if media.partial:
                log.info(f'Resolving track {completed + 1}/{total if total is not None else "?"}: "{title}"')
                await report(title)
            elif id(media) not in resolved_media:
                resolved_media[id(media)] = media
                completed += 1
                suffix = " (unavailable)" if media.error else ""
                log.info(f'Parsed {completed}/{total if total is not None else "?"}: "{title}"{suffix}')
                await report(title)
        yield item


def _song_codec_priority(codec: SongCodec) -> list[SongCodec]:
    return [SongCodec.ALAC, SongCodec.AAC] if codec == SongCodec.ALAC else [codec]


def _is_alac_codec(codec: str | None) -> bool:
    return bool(codec and codec.lower().startswith("alac"))


def _configure_gamdl_logging(level: str) -> None:
    numeric_level = max(getattr(logging, level.upper(), logging.INFO), logging.INFO)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
    )


def _normalize_playlist_tracks(items: list) -> None:
    if not any(
        getattr(getattr(item.media, "playlist_tags", None), "track", None) == 0
        for item in items
    ):
        return

    seen_media: set[int] = set()
    for item in items:
        media_key = id(item.media)
        tags = getattr(item.media, "playlist_tags", None)
        if tags is not None and media_key not in seen_media:
            tags.track += 1
            seen_media.add(media_key)


# ── logger ──────────────────────────────────────────────
class _CallbackHandler(logging.Handler):
    def __init__(self, cb: LogCallback) -> None:
        super().__init__(logging.DEBUG)
        self.cb = cb
    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.cb(self.format(record))
        except Exception:
            self.handleError(record)

def _get_logger(name: str, level: str, cb: LogCallback | None) -> logging.Logger:
    log = logging.getLogger(name)
    log.setLevel(level)
    log.handlers.clear()
    h = _CallbackHandler(cb) if cb else logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(message)s" if cb else "[%(levelname)s] %(message)s"))
    log.addHandler(h)
    return log


# ── binary resolver ─────────────────────────────────────
def _format_ttml(raw_ttml: str) -> str:
    """Indent structural XML while preserving mixed content in lyric lines."""
    document = minidom.parseString(raw_ttml)

    def format_node(node, indent: str) -> str:
        if (
            node.nodeType != Node.ELEMENT_NODE
            or node.localName in {"p", "text"}
            or node.getAttribute("xml:space") == "preserve"
            or any(
                child.nodeType == Node.CDATA_SECTION_NODE
                or (child.nodeType == Node.TEXT_NODE and child.data.strip())
                for child in node.childNodes
            )
        ):
            return indent + node.toxml()
        children = [
            child for child in node.childNodes
            if child.nodeType != Node.TEXT_NODE
        ]
        if not children:
            return indent + node.toxml()

        # Write just the opening tag, retaining the original namespace prefixes.
        opening = node.cloneNode(deep=False).toxml()[:-2] + ">"
        return "\n".join([
            indent + opening,
            *(format_node(child, indent + "  ") for child in children),
            indent + f"</{node.tagName}>",
        ])

    return '<?xml version="1.0" encoding="utf-8"?>\n' + "\n".join(
        format_node(node, "") for node in document.childNodes
    ) + "\n"


def _first_playback_song(playback: dict | None) -> dict:
    songs = playback.get("songList") if isinstance(playback, dict) else None
    if isinstance(songs, list) and songs and isinstance(songs[0], dict):
        return songs[0]
    return {}


class _SongInterface(AppleMusicSongInterface):
    def __init__(
        self, *, language: str, no_synced_lyrics: bool, log: logging.Logger,
        save_cover: bool = False, **kwargs,
    ):
        super().__init__(**kwargs)
        self.language = language
        self.no_synced_lyrics = no_synced_lyrics
        self.save_cover = save_cover
        self.log = log

    def _get_lyrics(self, lyrics_ttml: str):
        lyrics = super()._get_lyrics(lyrics_ttml)
        if lyrics.synced and self.synced_lyrics_format == SyncedLyricsFormat.TTML:
            lyrics.synced = _format_ttml(lyrics_ttml)
        return lyrics

    async def get_lyrics(self, song_metadata: dict):
        attrs = song_metadata.get("attributes", {})
        if self.no_synced_lyrics or not attrs.get("hasLyrics"):
            return None
        song_id = song_metadata.get("id")
        if (
            self.synced_lyrics_format == SyncedLyricsFormat.TTML
            and song_id
            and not (attrs.get("playParams") or {}).get("isLibrary")
        ):
            api = self.base.apple_music_api
            try:
                result = await api._amp_request(
                    f"/v1/catalog/{api.storefront}/songs/{song_id}/syllable-lyrics",
                    params={"l": self.language, "extend": "ttmlLocalizations"},
                )
                for entry in result.get("data", []):
                    lyrics_attrs = entry.get("attributes", {})
                    localized = lyrics_attrs.get("ttmlLocalizations")
                    if isinstance(localized, dict):
                        language = self.language.casefold()
                        localized = next(
                            (value for key, value in localized.items() if key.casefold() == language),
                            localized.get(language.split("-")[0]) or localized.get("zh-Hans")
                            or next(iter(localized.values()), None),
                        )
                    for ttml in (localized, lyrics_attrs.get("ttml")):
                        if isinstance(ttml, str) and ttml:
                            lyrics = self._get_lyrics(ttml)
                            if lyrics.synced:
                                self.log.info("Retrieved syllable/translated TTML for song %s", song_id)
                                return lyrics
            except Exception as error:
                self.log.debug("Syllable lyrics unavailable for %s: %s", song_id, error)
        # Catalog entries can offer lyrics even when no media stream is available.
        if not attrs.get("playParams"):
            song_metadata = {**song_metadata, "attributes": {**attrs, "playParams": {}}}
        return await super().get_lyrics(song_metadata)

    async def _get_web_stream_info(self, webplayback: dict | None, codec: SongCodec):
        stream = await super()._get_web_stream_info(webplayback, codec)
        if stream and not stream.audio_track.codec:
            stream.audio_track.codec = codec.value
        return stream

    async def _get_song_decryption_key(
        self, stream: StreamInfoAv, media_id: str,
    ) -> DecryptionKeyAv | None:
        audio = stream.audio_track
        if audio.drm_free:
            return None
        if audio.widevine_pssh:
            return DecryptionKeyAv(
                audio_track=await self.base.get_decryption_key(audio.widevine_pssh, media_id),
            )
        if not self.base.wrapper_api or not audio.fairplay_key:
            raise GamdlInterfaceDecryptionNotAvailableError(media_id=media_id)
        return None

    async def _resolve_song_stream(
        self, media: AppleMusicMedia, webplayback: dict | None, playback: dict | None,
    ) -> tuple[StreamInfoAv, DecryptionKeyAv | None]:
        # Let gamdl resolve native codecs first. Probe web codecs separately so
        # a native manifest or license failure cannot hide a usable web stream.
        fallback_codecs = (
            [codec for codec in self.codec_priority if codec.is_web]
            if webplayback and not media.is_library and not self.codec_priority[0].is_web
            else []
        )
        try:
            stream = await self.get_stream_info(
                media.media_id, media.is_library, None if fallback_codecs else webplayback, playback,
            )
            return stream, await self._get_song_decryption_key(stream, media.media_id)
        except Exception:
            for codec in fallback_codecs:
                try:
                    stream = await self._get_web_stream_info(webplayback, codec)
                    if stream:
                        return stream, await self._get_song_decryption_key(stream, media.media_id)
                except Exception as error:
                    self.log.debug("Web fallback %s unavailable for %s: %s", codec.value, media.media_id, error)
            raise

    async def _get_song_tags(
        self, media: AppleMusicMedia, webplayback: dict | None, playback: dict | None,
    ) -> MediaTags:
        unsynced = media.lyrics.unsynced if media.lyrics else None
        for source, payload in (("webplayback", webplayback), ("Wrapper", playback)):
            try:
                asset = _first_playback_song(payload)["assets"][0]["metadata"]
                tags = await self.base.get_tags_from_asset_info(asset, unsynced, self.use_album_date)
            except Exception as error:
                self.log.debug("Could not extract tags from %s for %s: %s", source, media.media_id, error)
            else:
                if source == "Wrapper":
                    self.log.warning("Localized metadata unavailable; using Wrapper metadata")
                return tags

        attrs = media.media_metadata.get("attributes", {})
        release_date = attrs.get("releaseDate")
        try:
            date = self.base.parse_date(release_date) if release_date else None
        except ValueError:
            date = release_date
        return MediaTags(
            album=attrs.get("albumName"),
            album_artist=attrs.get("albumArtistName") or attrs.get("artistName"),
            artist=attrs.get("artistName"),
            composer=attrs.get("composerName"),
            copyright=attrs.get("copyright"),
            date=date,
            disc=attrs.get("discNumber") or 1,
            disc_total=attrs.get("discCount"),
            genre=(attrs.get("genreNames") or ["Unknown"])[0],
            lyrics=unsynced,
            media_type=MediaType.SONG,
            rating={"explicit": MediaRating.EXPLICIT, "clean": MediaRating.CLEAN}.get(
                attrs.get("contentRating"),
            ),
            title=attrs.get("name"),
            title_id=int(media.media_id) if str(media.media_id).isdecimal() else None,
            track=attrs.get("trackNumber") or 1,
            track_total=attrs.get("trackCount"),
            xid=attrs.get("isrc"),
        )

    async def get_media(self, media: AppleMusicMedia):
        if not media.media_metadata:
            api = self.base.apple_music_api
            get_song = api.get_library_song if media.is_library else api.get_song
            media.media_metadata = (await get_song(media.media_id))["data"][0]

        if (media.media_metadata.get("attributes", {}).get("playParams") or {}).get("isLibrary"):
            catalog = self.base.get_catalog_metadata_from_library(media.media_metadata)
            if catalog:
                media.media_id = catalog["id"]
                media.is_library = False
                media.media_metadata = catalog

        yield media

        if not self.skip_stream_info and not self.base.is_media_streamable(media.media_metadata):
            raise GamdlInterfaceMediaNotStreamableError(media_id=media.media_id)

        if media.playlist_metadata:
            media.playlist_tags = self.base.get_playlist_tags(media.playlist_metadata, media.index)

        async def fetch_cover():
            if not self.skip_stream_info:
                return await self.base.get_cover(media.media_metadata)
            if self.save_cover:
                try:
                    cover = await self.base.get_cover(media.media_metadata)
                    if cover and cover.file_extension:
                        return cover
                except Exception as error:
                    self.log.debug("Cover unavailable for %s: %s", media.media_id, error)
            # gamdl builds a cover path even when it will only save lyrics.
            return Cover(file_extension=".jpg")

        async def fetch_lyrics():
            try:
                return await self.get_lyrics(media.media_metadata)
            except Exception as error:
                self.log.debug("Lyrics unavailable for %s: %s", media.media_id, error)
                if self.skip_stream_info:
                    raise

        async def fetch_webplayback():
            try:
                return await self.base.apple_music_api.get_webplayback(media.media_id, media.is_library)
            except Exception as error:
                self.log.debug("Webplayback unavailable for %s: %s", media.media_id, error)

        async def fetch_wrapper_playback():
            if self.base.wrapper_api and not media.is_library:
                try:
                    return await self.base.wrapper_api.get_playback(media.media_id)
                except Exception as error:
                    self.log.debug("Wrapper playback unavailable for %s: %s", media.media_id, error)

        media.cover, media.lyrics, webplayback, playback = await asyncio.gather(
            fetch_cover(),
            fetch_lyrics(),
            fetch_webplayback(),
            fetch_wrapper_playback(),
        )
        media.tags = await self._get_song_tags(media, webplayback, playback)

        if not self.skip_stream_info:
            web_song = _first_playback_song(webplayback)
            assets = web_song.get("assets")
            # gamdl's stream helpers require these keys even when playback has
            # enough information for tags. An unusable response permits fallback.
            if not web_song.get("songId") or not isinstance(assets, list):
                webplayback = None
            else:
                assets = [asset for asset in assets if isinstance(asset, dict) and asset.get("URL")]
                if not media.is_library:
                    assets = [asset for asset in assets if asset.get("flavor")]
                webplayback = {"songList": [{**web_song, "assets": assets}]} if assets else None

            media.stream_info, media.decryption_key = await self._resolve_song_stream(
                media, webplayback, playback if _first_playback_song(playback) else None,
            )

        media.partial = False
        yield media


def _move_download_to_final_path(staged_path: str, final_path: str, overwrite: bool) -> None:
    """Publish a finished file atomically, including across filesystems."""
    staged = Path(staged_path)
    final = Path(final_path)
    if not staged.is_file() or staged.stat().st_size == 0:
        raise RuntimeError(f"Download produced no complete file: {staged}")
    final.parent.mkdir(parents=True, exist_ok=True)

    def publish(source: Path) -> None:
        with _PUBLICATION_LOCK:
            if final.exists() and not overwrite:
                raise GamdlDownloaderMediaFileExistsError(final_path)
            os.replace(source, final)

    try:
        publish(staged)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        with tempfile.NamedTemporaryFile(dir=final.parent, delete=False) as target:
            pending = Path(target.name)
        try:
            shutil.copy2(staged, pending)
            publish(pending)
        finally:
            pending.unlink(missing_ok=True)


# ── sync entry point ────────────────────────────────────
def download_urls(**kwargs) -> int:
    """Synchronous entry point for download_urls.

    Creates a new event loop appropriate for the platform.
    On Windows, ProactorEventLoop is used (supports subprocesses & async IO).
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_download_urls_async(**kwargs))
    finally:
        loop.close()


# ══════════════════════════════════════════════════════════
# async entry point
# ══════════════════════════════════════════════════════════
async def _download_urls_async(
    *,
    urls: list[str],
    cookies_path: Path | None = None,
    use_wrapper: bool = False,
    wrapper_url: str = "http://127.0.0.1",
    wrapper_decrypt_host: str = "127.0.0.1",
    wrapper_decrypt_port: int = 10020,
    output_path: Path = Path("./Apple Music"),
    existing_library_path: Path | None = None,
    temp_path: Path = Path("./temp"),
    wvd_path: Path | None = None,
    nm3u8dlre_path: str = "N_m3u8DL-RE",
    ffmpeg_path: str = "ffmpeg",
    download_mode: DownloadMode = DownloadMode.YTDLP,
    codec_song: SongCodec = SongCodec.AAC_WEB,
    codec_music_video: MusicVideoCodec = MusicVideoCodec.H264,
    quality_post: UploadedVideoQuality = UploadedVideoQuality.BEST,
    synced_lyrics_format: SyncedLyricsFormat = SyncedLyricsFormat.LRC,
    cover_format: CoverFormat = CoverFormat.JPG,
    cover_size: int = 1200,
    truncate: int | None = None,
    audio_format: str | None = None,
    video_format: str | None = None,
    template_folder_album: str = "{album_artist}/{album}",
    template_folder_compilation: str = "Compilations/{album}",
    template_file_single_disc: str = "{track:02d} {title}",
    template_file_multi_disc: str = "{disc}-{track:02d} {title}",
    template_folder_no_album: str = "{artist}/Unknown Album",
    template_file_no_album: str = "{title}",
    template_file_playlist: str = "Playlists/{playlist_artist}/{playlist_title}",
    template_date: str = "%Y-%m-%dT%H:%M:%SZ",
    exclude_tags: str | None = None,
    overwrite: bool = False,
    save_cover: bool = False,
    save_playlist: bool = False,
    synced_lyrics_only: bool = False,
    no_synced_lyrics: bool = False,
    disable_music_video_skip: bool = False,
    read_urls_as_txt: bool = False,
    no_exceptions: bool = True,
    language: str = "en-US",
    log_callback: LogCallback | None = None,
    log_level: str = "INFO",
    progress_callback: Callable[[int, int], None] | None = None,
    parse_progress_callback: ParseProgressCallback | None = None,
) -> int:
    log = _get_logger("amdl.core", log_level, log_callback)
    _configure_gamdl_logging(log_level)
    log.info("System: Python %s.%s.%s | Platform: %s | EventLoop: %s",
             sys.version_info.major, sys.version_info.minor, sys.version_info.micro,
             sys.platform, type(asyncio.get_running_loop()).__name__)

    # ── PATH: Finder/Explorer launch has minimal env ────
    for d in ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local/bin")]:
        if d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{d}{os.pathsep}{os.environ.get('PATH', '')}"

    # ── absolute paths ──────────────────────────────────
    temp_path = temp_path.resolve()
    output_path = output_path.resolve()
    if cookies_path:
        cookies_path = cookies_path.resolve()
    if wvd_path:
        wvd_path = wvd_path.resolve()

    existing_library = None
    if existing_library_path is not None and not synced_lyrics_only:
        try:
            library_path = existing_library_path.expanduser().resolve()
            log.info("Scanning existing library: %s", library_path)
            existing_library = await asyncio.to_thread(ExistingLibrary, library_path)
        except (OSError, RuntimeError) as e:
            log.error("Could not scan existing library: %s", e)
            return 1

    # ── txt expansion ───────────────────────────────────
    if read_urls_as_txt:
        _u: list[str] = []
        for u in urls:
            p = Path(u)
            _u.extend(p.read_text("utf-8").splitlines() if p.exists() else [u])
        urls = _u

    # ── deduplicate urls while preserving order ────────
    seen_urls: set[str] = set()
    deduped_urls: list[str] = []
    for u in urls:
        clean_u = u.strip()
        if clean_u and clean_u not in seen_urls:
            seen_urls.add(clean_u)
            deduped_urls.append(clean_u)
    urls = deduped_urls

    exclude_list = [t.strip().lower() for t in (exclude_tags or "").split(",") if t.strip()]

    # ── API client ──────────────────────────────────────
    wrapper_api = None
    try:
        if use_wrapper:
            wrapper_api = await WrapperApi.create(
                base_url=wrapper_url,
                decrypt_host=wrapper_decrypt_host,
                decrypt_port=wrapper_decrypt_port,
            )
            api = await AppleMusicApi.create_from_wrapper(
                wrapper_api=wrapper_api,
                language=language,
            )
        else:
            if cookies_path is None:
                raise ValueError("cookies_path is required in cookies mode")
            api = await AppleMusicApi.create_from_netscape_cookies(
                str(cookies_path),
                language=language,
            )
    except Exception as e:
        log.critical(f"API init failed: {e}"); return 1

    if not api.active_subscription:
        log.critical("No active subscription"); return 1

    # ── gamdl interface ─────────────────────────────────
    log.info("Creating AppleMusicBaseInterface...")
    base_iface = await AppleMusicBaseInterface.create(
        apple_music_api=api, cover_format=cover_format, cover_size=cover_size,
        wvd_path=str(wvd_path) if wvd_path else None,
        wrapper_api=wrapper_api,
    )
    log.info("AppleMusicBaseInterface created, building AppleMusicInterface...")
    song_priority = list(dict.fromkeys([
        *_song_codec_priority(codec_song), SongCodec.AAC_WEB, SongCodec.AAC_HE_WEB,
    ]))
    mv_priority = list(dict.fromkeys([codec_music_video, MusicVideoCodec.H265, MusicVideoCodec.H264]))
    no_synced_lyrics = no_synced_lyrics and not synced_lyrics_only
    song_iface = _SongInterface(
        base=base_iface,
        synced_lyrics_format=synced_lyrics_format,
        codec_priority=song_priority,
        skip_stream_info=synced_lyrics_only,
        no_synced_lyrics=no_synced_lyrics,
        save_cover=save_cover,
        language=language,
        log=log,
    )
    iface = AppleMusicInterface(
        song=song_iface,
        music_video=AppleMusicMusicVideoInterface(base=base_iface, codec_priority=mv_priority),
        uploaded_video=AppleMusicUploadedVideoInterface(base=base_iface, quality=quality_post),
    )
    log.info("AppleMusicInterface ready")

    # ── gamdl downloader ────────────────────────────────
    from amdl.dependency_manager import find_binary

    _ff_path = find_binary("ffmpeg", ffmpeg_path) or ffmpeg_path
    _nm_path = find_binary("N_m3u8DL-RE", nm3u8dlre_path) or nm3u8dlre_path

    base_dl = AppleMusicBaseDownloader(
        interface=iface, output_path=str(output_path), temp_path=str(temp_path),
        nm3u8dlre_path=_nm_path, ffmpeg_path=_ff_path, download_mode=download_mode,
        album_folder_template=template_folder_album,
        compilation_folder_template=template_folder_compilation,
        no_album_folder_template=template_folder_no_album,
        playlist_folder_template=template_file_playlist,
        single_disc_file_template=template_file_single_disc,
        multi_disc_file_template=template_file_multi_disc,
        no_album_file_template=template_file_no_album,
        playlist_file_template="{playlist_title}",
        date_tag_template=template_date, exclude_tags=exclude_list,
        **({"truncate": truncate} if truncate is not None else {}),
    )

    # ── override download: run everything in threads, never via asyncio
    # subprocess — Windows SelectorEventLoop (forced for httpx compat)
    # raises NotImplementedError on create_subprocess_exec.
    _use_nm3u8 = download_mode == DownloadMode.NM3U8DLRE

    def _ytdlp_download_sync(stream_url: str, dl_path: str) -> None:
        # Mirrors gamdl's _download_ytdlp_process, but in-process:
        # avoids multiprocessing (broken under PyInstaller without
        # freeze_support) and `sys.executable -m yt_dlp` (frozen exe
        # would relaunch AMDL itself).
        from yt_dlp import YoutubeDL
        from yt_dlp.downloader.hls import HlsFD
        from yt_dlp.downloader.http import HttpFD

        with YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "overwrites": True,
                "noprogress": True,
                "allow_unplayable_formats": True,
                # Match gamdl's exact params — do NOT add http_chunk_size:
                # range-chunking on CENC HLS causes "Conflicting range"
                # errors that exhaust retries and leave an empty file.
                "concurrent_fragment_downloads": 8,
            }
        ) as ydl:
            if stream_url.split("?")[0].endswith(".m3u8"):
                success, _ = HlsFD(ydl, ydl.params).download(
                    dl_path, {"url": stream_url, "ext": "mp4", "protocol": "m3u8"}
                )
                if not success:
                    raise RuntimeError("yt-dlp HLS download failed")
            else:
                success, _ = HttpFD(ydl, ydl.params).download(dl_path, {"url": stream_url})
                if not success:
                    raise RuntimeError("yt-dlp HTTP download failed")

    def _nm3u8_download_sync(stream_url: str, dl_path: str) -> None:
        p = Path(dl_path)
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
        result = subprocess.run(
            [
                base_dl.full_nm3u8dlre_path or _nm_path,
                stream_url,
                "--binary-merge", "--no-log", "--log-level", "off",
                "--ffmpeg-binary-path", base_dl.full_ffmpeg_path or _ff_path,
                "--save-name", p.stem,
                "--save-dir", str(p.parent),
                "--tmp-dir", str(p.parent),
            ],
            capture_output=True, text=True, creationflags=creationflags,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"N_m3u8DL-RE failed ({result.returncode}): {(result.stderr or result.stdout or '')[:200]}"
            )

    async def _download(stream_url: str, dl_path: str) -> None:
        path = Path(dl_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # HlsFD can report success for an existing file, even if it is empty.
        path.unlink(missing_ok=True)
        if _use_nm3u8 and stream_url.split("?")[0].endswith(".m3u8"):
            await asyncio.to_thread(_nm3u8_download_sync, stream_url, dl_path)
        else:
            await asyncio.to_thread(_ytdlp_download_sync, stream_url, dl_path)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Download produced no complete file: {dl_path}")

    base_dl.download_stream = _download  # type: ignore[assignment]

    log.info("Creating AppleMusicDownloader...")
    dl = AppleMusicDownloader(
        song=AppleMusicSongDownloader(base=base_dl),
        music_video=AppleMusicMusicVideoDownloader(base=base_dl),
        uploaded_video=AppleMusicUploadedVideoDownloader(base=base_dl),
        overwrite=overwrite, save_cover=save_cover, save_playlist=save_playlist and not synced_lyrics_only,
        no_synced_lyrics=no_synced_lyrics, synced_lyrics_only=synced_lyrics_only,
    )
    async def _final_processing(item):
        # gamdl silently returns when staging failed to create a file.
        if not item.staged_path:
            raise RuntimeError("Download produced no staged path")
        _move_download_to_final_path(item.staged_path, item.final_path, overwrite)

    dl._final_processing = _final_processing
    log.info("AppleMusicDownloader ready, starting parse...")

    # ── parse → download ────────────────────────────────
    items: list = []
    errors = 0
    for url in urls:
        log.info('Parsing "%s"', url)
        try:
            async for item in _parse_items_with_progress(dl, url, log, parse_progress_callback):
                items.append(item)
        except Exception as e:
            errors += 1
            log.error('Failed to parse "%s": %s', url, e, exc_info=not no_exceptions)

    total = sum(
        1 for item in items
        if not item.media.error and not item.media.partial and item.final_path
    )
    completed = 0
    library_skipped = 0
    done_files: list[str] = []
    seen_error_media: set[int] = set()
    _normalize_playlist_tracks(items)

    if progress_callback:
        r = progress_callback(0, total)
        if asyncio.iscoroutine(r):
            await r

    for item in items:
        if item.media.error:
            # gamdl yields partial and failed items sharing the same media object.
            media_key = id(item.media)
            if media_key in seen_error_media:
                continue
            seen_error_media.add(media_key)
            errors += 1
            m = item.media.media_metadata
            n = m.get("attributes", {}).get("name", "?") if isinstance(m, dict) else "?"
            log.error('Skip "%s": %s', n, item.media.error)
            continue

        if item.media.partial or not item.final_path:
            continue

        title = item.media.media_metadata.get("attributes", {}).get("name", "?") if isinstance(item.media.media_metadata, dict) else "?"
        existing_file = existing_library.find(item.media) if existing_library else None
        if existing_file:
            try:
                if save_playlist and item.playlist_file_path:
                    write_existing_playlist_entry(
                        Path(item.playlist_file_path), item.media.playlist_tags.track, existing_file,
                    )
            except Exception as error:
                errors += 1
                log.error('Failed "%s": %s', title, error, exc_info=not no_exceptions)
                continue
            library_skipped += 1
            log.info('Skipped "%s": already in existing library (%s)', title, existing_file)
        else:
            actual_codec = getattr(
                getattr(getattr(item.media, "stream_info", None), "audio_track", None),
                "codec",
                None,
            )
            if codec_song == SongCodec.ALAC and actual_codec and not _is_alac_codec(actual_codec):
                log.warning(
                    'WARNING: ALAC unavailable for "%s"; falling back to AAC (%s)',
                    title,
                    actual_codec,
                )

            log.info('Downloading "%s"', title)
            max_attempts = 3
            download_succeeded = False
            for attempt in range(1, max_attempts + 1):
                try:
                    await dl.download(item)
                except GamdlDownloaderSyncedLyricsOnlyError:
                    lyrics_path = Path(item.synced_lyrics_path) if item.synced_lyrics_path else None
                    if not lyrics_path or not lyrics_path.is_file() or lyrics_path.stat().st_size == 0:
                        errors += 1
                        log.error('No synced lyrics available for "%s"', title)
                        break
                    log.info('Synced lyrics saved for "%s"', title)
                except GamdlDownloaderMediaFileExistsError:
                    log.info('Skipped "%s": exists', title)
                except Exception as error:
                    if attempt < max_attempts:
                        log.warning(
                            'Retrying "%s" (attempt %d/%d) after error: %s',
                            title, attempt, max_attempts, error,
                        )
                        await asyncio.sleep(1.5)
                        continue
                    errors += 1
                    log.error('Failed "%s": %s\n%s', title, error, traceback.format_exc())
                    break
                else:
                    done_files.append(str(item.final_path))
                download_succeeded = True
                break
            if not download_succeeded:
                continue

        completed += 1
        if progress_callback:
            r = progress_callback(completed, total)
            if asyncio.iscoroutine(r):
                await r

    # ── format conversion ───────────────────────────────
    effective_audio_format = None if codec_song == SongCodec.ALAC else audio_format
    if (effective_audio_format or video_format) and done_files and not synced_lyrics_only:
        try:
            from amdl.converter import convert_file_list, resolve_ffmpeg_executable
            exe = resolve_ffmpeg_executable(ffmpeg_path)
            if exe:
                log.info("Converting…")
                convert_file_list([Path(p) for p in done_files], effective_audio_format, video_format, exe,
                                  log_callback or (lambda m: None))
        except Exception as e:
            log.error("Conversion failed: %s", e, exc_info=not no_exceptions)

    if existing_library is not None:
        log.info("Skipped %d song(s) already in the existing library", library_skipped)
    log.info("Done (%d error(s))", errors)
    return errors
