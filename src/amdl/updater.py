"""Verified, user-initiated updates for the packaged desktop application."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from amdl import __version__

RELEASE_REPO = "Casper-Feehily/AppleMusic-Downloader-enhanced"
RELEASE_API = f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"
MAX_PACKAGE_SIZE = 2 * 1024 ** 3
_DOWNLOAD_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str
    version: str


def _version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        raise UpdateError("Invalid release version")
    return tuple(map(int, value.lstrip("v").split(".")))


def select_asset(release: dict, system: str, machine: str, current: str) -> ReleaseAsset:
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        raise UpdateError("A stable release is required")
    tag = release.get("tag_name")
    if _version(tag) <= _version(current):
        raise UpdateError("There is no newer version to install")
    if system == "win32" and machine.lower() in {"amd64", "x86_64"}:
        name = "AppleMusicDownloader-Setup-windows-x64.exe"
    elif system == "darwin" and machine.lower() in {"arm64", "aarch64"}:
        name = "AppleMusicDownloader-macos-arm64.dmg"
    else:
        raise UpdateError("One-click updates support Windows x64 and Apple Silicon macOS")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("Release assets are missing")
    matches = [item for item in assets if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise UpdateError("This release does not have an installer for this system")
    item = matches[0]
    digest = item.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise UpdateError("GitHub SHA-256 is unavailable; use the manual download instead")
    size = item.get("size")
    if type(size) is not int or not 0 < size <= MAX_PACKAGE_SIZE:
        raise UpdateError("Invalid installer size")
    url = f"https://github.com/{RELEASE_REPO}/releases/download/{tag}/{name}"
    if item.get("browser_download_url") != url or item.get("state") != "uploaded":
        raise UpdateError("Unexpected installer download URL or state")
    return ReleaseAsset(name, url, size, digest.split(":", 1)[1].lower(), tag.lstrip("v"))


def download_asset(client: httpx.Client, asset: ReleaseAsset, destination: Path,
                   progress: Callable[[int, int], None]) -> None:
    partial = destination.with_suffix(destination.suffix + ".part")
    url = httpx.URL(asset.url)
    try:
        for _ in range(6):
            if (url.scheme != "https" or url.host not in _DOWNLOAD_HOSTS
                    or url.username or url.password or url.port not in (None, 443)):
                raise UpdateError("Unsafe installer redirect")
            with client.stream("GET", url, follow_redirects=False,
                               headers={"Accept-Encoding": "identity"}) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise UpdateError("Installer redirect has no destination")
                    url = url.join(location)
                    continue
                response.raise_for_status()
                done = 0
                checksum = hashlib.sha256()
                with partial.open("wb") as output:
                    for chunk in response.iter_bytes(256 * 1024):
                        done += len(chunk)
                        if done > asset.size:
                            raise UpdateError("Installer size does not match GitHub")
                        output.write(chunk)
                        checksum.update(chunk)
                        progress(done, asset.size)
                if done != asset.size:
                    raise UpdateError("Installer download is incomplete")
                if checksum.hexdigest() != asset.sha256:
                    raise UpdateError("Installer SHA-256 verification failed")
                partial.replace(destination)
                return
        raise UpdateError("Too many installer redirects")
    finally:
        partial.unlink(missing_ok=True)


def installation_target() -> tuple[str, Path]:
    if not getattr(sys, "frozen", False):
        raise UpdateError("One-click updates require an installed desktop application")
    executable = Path(sys.executable).resolve()
    if sys.platform == "win32" and platform.machine().lower() in {"amd64", "x86_64"}:
        target = executable
    elif sys.platform == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        target = next((p for p in executable.parents if p.suffix == ".app"), None)
        if target is None or str(target).startswith("/Volumes/"):
            raise UpdateError("Move the application out of the DMG before updating")
    else:
        raise UpdateError("One-click updates support Windows x64 and Apple Silicon macOS")
    if not os.access(target.parent, os.W_OK):
        raise UpdateError("The application folder is not writable; use the manual download")
    return sys.platform, target


class UpdateManager:
    def __init__(self, data_dir: Path, tasks, close_window: Callable[[], None]):
        self._data_dir = data_dir
        self._tasks = tasks
        self._close_window = close_window
        self._lock = threading.Lock()
        self._state = dict(phase="idle", downloaded=0, total=0, version="", error="")
        self._running = False

    def status(self) -> dict:
        with self._lock:
            result = dict(self._state)
        result["current_version"] = __version__
        try:
            installation_target()
            result.update(supported=True, reason="")
        except UpdateError as exc:
            result.update(supported=False, reason=str(exc))
        # External helpers leave an error file when installation fails after exit.
        error_file = self._data_dir / "updates" / "install-error.txt"
        if result["phase"] in {"idle", "installing"} and error_file.is_file():
            result.update(phase="error", error=error_file.read_text(encoding="utf-8-sig", errors="replace")[:2000])
            if self._running:
                self._tasks.release_update()
                with self._lock:
                    self._running = False
                    self._state.update(phase="error", error=result["error"])
        return result

    def _set(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def start(self) -> dict:
        installation_target()
        with self._lock:
            already_running = self._running
            if not already_running:
                self._running = True
                self._state.update(phase="checking", downloaded=0, total=0, version="", error="")
        if already_running:
            return self.status()
        try:
            threading.Thread(target=self._run, daemon=True, name="amdl-update").start()
        except Exception:
            with self._lock:
                self._running = False
            raise
        return self.status()

    def _run(self) -> None:
        from amdl.update_helpers import prepare_install, launch_helper

        reserved = False
        staged = None
        work = None
        try:
            system, target = installation_target()
            updates = self._data_dir / "updates"
            updates.mkdir(parents=True, exist_ok=True)
            (updates / "install-error.txt").unlink(missing_ok=True)
            work = Path(tempfile.mkdtemp(prefix="update-", dir=updates))
            with httpx.Client(timeout=httpx.Timeout(60, connect=15),
                              headers={"User-Agent": f"AppleMusicDownloader/{__version__}"}) as client:
                response = client.get(RELEASE_API)
                response.raise_for_status()
                asset = select_asset(response.json(), system, platform.machine(), __version__)
                self._set(phase="downloading", total=asset.size, version=asset.version)
                package = work / asset.name
                download_asset(client, asset, package,
                               lambda done, total: self._set(downloaded=done, total=total))
            self._set(phase="preparing")
            staged = prepare_install(system, package, target, work)
            self._set(phase="waiting")
            while not self._tasks.reserve_update():
                time.sleep(1)
            reserved = True
            # Create and start the helper before closing. A failed launch leaves
            # the current application fully usable and releases the task gate.
            launch_helper(system, staged, target, work, updates / "install-error.txt")
            self._set(phase="installing")
            self._close_window()
        except Exception as exc:
            self._set(phase="error", error=str(exc))
            if reserved:
                self._tasks.release_update()
            if work is not None:
                try:
                    (work / "abort").touch()
                except OSError:
                    pass
            if staged is not None and staged.suffix == ".app":
                shutil.rmtree(staged, ignore_errors=True)
        finally:
            with self._lock:
                self._running = self._state["phase"] == "installing"
