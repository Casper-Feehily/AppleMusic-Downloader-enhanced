"""Read song identities from a previous download folder."""

from __future__ import annotations

import os
from pathlib import Path
from unicodedata import normalize

from gamdl.interface.types import AppleMusicMedia
from mutagen import MutagenError
from mutagen.asf import ASF
from mutagen.flac import FLAC
from mutagen.mp3 import EasyMP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE


_READERS = {
    ".m4a": (MP4, ("©nam", "©ART", "©alb")),
    ".mp3": (EasyMP3, ("title", "artist", "album")),
    ".flac": (FLAC, ("title", "artist", "album")),
    ".ogg": (OggVorbis, ("title", "artist", "album")),
    ".opus": (OggOpus, ("title", "artist", "album")),
    ".wav": (WAVE, ("TIT2", "TPE1", "TALB")),
    ".wma": (ASF, ("Title", "Author", "WM/AlbumTitle")),
}
_SONG_TYPES = {"songs", "library-songs"}
_SongTags = tuple[str, ...]


def _tag_key(*fields: str | None) -> _SongTags | None:
    key = tuple(normalize("NFC", field or "").strip().casefold() for field in fields)
    return key if all(key) else None


def _scan_error(error: OSError) -> None:
    raise error


class ExistingLibrary:
    def __init__(self, root: Path):
        self.by_id: dict[str, Path] = {}
        self.by_tags: dict[_SongTags, dict[str | None, Path]] = {}

        for folder, _, filenames in os.walk(root, onerror=_scan_error):
            for filename in filenames:
                path = Path(folder) / filename
                reader = _READERS.get(path.suffix.lower())
                if reader is None or not path.is_file():
                    continue
                read, keys = reader
                try:
                    audio = read(path)
                except (MutagenError, OSError):
                    continue
                if not audio.tags or not audio.info.length:
                    continue
                tags = audio.tags
                track_id = None
                if isinstance(audio, MP4):
                    if tags.get("stik", [1])[0] != 1:
                        continue
                    if ids := tags.get("cnID"):
                        if ids[0] > 0:
                            track_id = str(ids[0])
                            self.by_id.setdefault(track_id, path)

                key = _tag_key(*(str(tags[k][0]) if k in tags and tags[k] else None
                                 for k in keys))
                if key:
                    self.by_tags.setdefault(key, {}).setdefault(track_id, path)

    def find(self, media: AppleMusicMedia) -> Path | None:
        metadata = media.media_metadata or {}
        if metadata.get("type") not in _SONG_TYPES:
            return None

        attrs = metadata.get("attributes", {})
        tags = media.tags
        track_id = getattr(tags, "title_id", None) or attrs.get("playParams", {}).get("catalogId")
        if not track_id and not media.is_library:
            track_id = media.media_id
        if track_id and (path := self.by_id.get(str(track_id))) and path.is_file():
            return path

        for key in (
            _tag_key(tags.title, tags.artist, tags.album) if tags else None,
            _tag_key(attrs.get("name"), attrs.get("artistName"), attrs.get("albumName")),
        ):
            matches = self.by_tags.get(key) if key else None
            if not matches or len(matches) != 1:
                continue
            # Tags alone must never override a conflicting catalog ID.
            path = matches.get(None) if track_id else next(iter(matches.values()))
            if path and path.is_file():
                return path
        return None


def write_existing_playlist_entry(playlist: Path, track: int, audio: Path) -> None:
    try:
        entry = Path(os.path.relpath(audio, playlist.parent)).as_posix()
    except ValueError:  # Windows paths on different drives
        entry = audio.as_posix()
    try:
        lines = playlist.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    lines.extend([""] * (track - len(lines)))
    lines[track - 1] = entry
    playlist.parent.mkdir(parents=True, exist_ok=True)
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
