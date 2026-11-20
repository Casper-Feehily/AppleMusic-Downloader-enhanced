"""Allocate filenames for downloads that share an output folder."""

from pathlib import Path
from typing import Literal
from unicodedata import normalize

OutputLayout = Literal["artist_album", "artist", "flat"]


def _name_key(name: str) -> str:
    return normalize("NFC", name).casefold()


class OutputNames:
    def __init__(self) -> None:
        self._used_by_folder: dict[Path, set[str]] = {}
        self._assigned: dict[tuple[Path, str], Path] = {}

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
