"""Allocate filenames for downloads that share an output folder."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal
from unicodedata import normalize

if TYPE_CHECKING:
    from gamdl.downloader import AppleMusicBaseDownloader
    from gamdl.downloader.types import DownloadItem

OutputLayout = Literal["artist_album", "artist", "flat"]


def _name_key(name: str) -> str:
    return normalize("NFC", name).casefold()


class OutputNames:
    def __init__(self) -> None:
        self._used_by_folder: dict[Path, set[str]] = {}
        self._assigned: dict[tuple[Path, str], Path] = {}

    @classmethod
    def for_layout(cls, base: AppleMusicBaseDownloader, layout: OutputLayout) -> OutputNames | None:
        if layout == "artist_album":
            return None
        folder = "{artist}" if layout == "artist" else ""
        base.album_folder_template = folder
        base.compilation_folder_template = folder
        base.no_album_folder_template = folder
        base.single_disc_file_template = "{title}"
        base.multi_disc_file_template = "{title}"
        base.no_album_file_template = "{title}"
        base.playlist_folder_template = ""
        base.playlist_file_template = "{playlist_title}"
        return cls()

    def assign(self, item: DownloadItem, *, save_playlist: bool, include_media: bool = True) -> None:
        if save_playlist and item.playlist_file_path:
            playlist = item.media.playlist_tags
            playlist_identity = str(playlist.playlist_id or playlist.artist)
            item.playlist_file_path = str(self.reserve(Path(item.playlist_file_path), playlist_identity))
        if include_media:
            final_path = self.reserve(Path(item.final_path), item.media.media_id)
            item.final_path = str(final_path)
            if item.synced_lyrics_path:
                item.synced_lyrics_path = str(final_path.with_suffix(Path(item.synced_lyrics_path).suffix))
            if item.cover_path:
                item.cover_path = str(final_path.with_suffix(Path(item.cover_path).suffix))

    def reserve(self, path: Path, identity: str) -> Path:
        """Reserve one stem for the media file and its companion files."""
        key = (path, identity)
        assigned = self._assigned.get(key)
        if assigned is not None:
            return assigned

        used = self._used_by_folder.get(path.parent)
        if used is None:
            try:
                used = {_name_key(entry.stem) for entry in path.parent.iterdir()}
            except FileNotFoundError:
                used = set()
            self._used_by_folder[path.parent] = used

        stem = path.stem
        index = 0
        while _name_key(stem) in used:
            index += 1
            stem = f"{path.stem}（{index}）"
        used.add(_name_key(stem))
        assigned = path.with_name(stem + path.suffix)
        self._assigned[key] = assigned
        return assigned
