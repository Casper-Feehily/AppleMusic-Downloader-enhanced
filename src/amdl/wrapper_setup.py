"""Local-only Apple Silicon wrapper-v2 setup.

The APK/APKM and extracted Apple libraries never leave the user's Mac.  This
module deliberately uses only fixed subprocess argument lists and stdlib ZIP
handling at that trust boundary.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import platform
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import httpx

from amdl.dependency_manager import _SUBPROCESS_FLAGS

logger = logging.getLogger("amdl.wrapper_setup")

WRAPPER_COMMIT = "100e0a864e883e03a3ac450a780dd9563fff5271"
WRAPPER_REV = "100e0a8"
WRAPPER_API_VERSION = "0.0.2"
SOURCE_URL = f"https://codeload.github.com/glomatico/wrapper-v2/tar.gz/{WRAPPER_COMMIT}"
WRAPPER_DIR = Path.home() / "Library" / "Application Support" / "AppleMusicDownloader" / "wrapper"
SOURCE_DIR = WRAPPER_DIR / WRAPPER_REV
PERSISTENT_DATA_DIR = WRAPPER_DIR / "data"
LOG_FILE = WRAPPER_DIR / "setup.log"
IMAGE_NAME = f"amdl-wrapper-v2:{WRAPPER_REV}"
CONTAINER_NAME = "amdl-wrapper-v2"
APPLE_PACKAGE = "com.apple.android.music"
APPLE_VERSION = "3.6.0-beta"
APPLE_BUILD = "1109"
ARCH = "arm64-v8a"
_MAX_ARCHIVE_SIZE = 1_000_000_000


class SetupError(RuntimeError):
    pass


class SetupBusy(SetupError):
    pass


class ReplaceConfirmationRequired(SetupError):
    pass


def _safe_zip(zf: zipfile.ZipFile) -> None:
    total = 0
    for item in zf.infolist():
        name = item.filename
        path = PurePosixPath(name)
        if "\\" in name or path.is_absolute() or ".." in path.parts:
            raise SetupError("The APK archive contains an unsafe path")
        total += item.file_size
        if total > _MAX_ARCHIVE_SIZE:
            raise SetupError("The APK archive is unexpectedly large")


def inspect_bundle(path: Path, *, require_metadata: bool = True) -> dict:
    """Validate an APKM's metadata, or an APK's ARM64 library layout."""
    if not path.is_file() or path.suffix.lower() not in (".apk", ".apkm"):
        raise SetupError("Select an Apple Music .apk or .apkm file")
    try:
        with zipfile.ZipFile(path) as outer:
            _safe_zip(outer)
            if path.suffix.lower() == ".apk":
                names = set(outer.namelist())
                if not any(n in names for n in (
                    "lib/arm64-v8a/libandroidappmusic.so",
                    "lib/arm64/libandroidappmusic.so",
                )):
                    raise SetupError("The APK does not contain ARM64 Apple Music libraries")
                return {"filename": path.name, "kind": "apk", "compatible": True}

            try:
                info = json.loads(outer.read("info.json"))
            except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise SetupError("The APKM has no valid info.json") from exc
            valid = (
                info.get("pname") == APPLE_PACKAGE
                and info.get("release_version") == APPLE_VERSION
                and str(info.get("versioncode")) == APPLE_BUILD
                and ARCH in info.get("arches", [])
            )
            if require_metadata and not valid:
                raise SetupError(
                    f"Apple Music {APPLE_VERSION} build {APPLE_BUILD} with arm64-v8a is required"
                )
            return {
                "filename": path.name,
                "kind": "apkm",
                "compatible": valid,
                "version": info.get("release_version"),
                "build": str(info.get("versioncode", "")),
            }
    except zipfile.BadZipFile as exc:
        raise SetupError("The selected APK/APKM is not a valid ZIP archive") from exc


def find_bundles(downloads: Path | None = None) -> list[Path]:
    downloads = downloads or Path.home() / "Downloads"
    matches: list[Path] = []
    if not downloads.is_dir():
        return matches
    for path in sorted((*downloads.glob("*.apkm"), *downloads.glob("*.APKM"))):
        try:
            if inspect_bundle(path)["compatible"]:
                matches.append(path)
        except SetupError:
            pass
    return matches


def _open_library_apk(bundle: Path) -> tuple[zipfile.ZipFile, io.BytesIO | None]:
    if bundle.suffix.lower() == ".apk":
        apk = zipfile.ZipFile(bundle)
        _safe_zip(apk)
        return apk, None
    with zipfile.ZipFile(bundle) as outer:
        _safe_zip(outer)
        for member in outer.namelist():
            if not member.lower().endswith(".apk"):
                continue
            data = outer.read(member)
            holder = io.BytesIO(data)
            apk = zipfile.ZipFile(holder)
            _safe_zip(apk)
            names = set(apk.namelist())
            if any(n in names for n in (
                "lib/arm64-v8a/libandroidappmusic.so",
                "lib/arm64/libandroidappmusic.so",
            )):
                return apk, holder
            apk.close()
    raise SetupError("The APKM has no ARM64 Apple Music library split")


def extract_libraries(bundle: Path, manifest_path: Path, rootfs: Path) -> None:
    """Extract and hash-check the complete pinned Apple library set."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected: dict[str, str] = manifest["libs"][ARCH]
    apk, holder = _open_library_apk(bundle)
    del holder  # keeps the BytesIO alive until apk is closed
    try:
        names = set(apk.namelist())
        prefix = next(
            (p for p in ("lib/arm64-v8a", "lib/arm64") if f"{p}/libandroidappmusic.so" in names),
            None,
        )
        if not prefix:
            raise SetupError("The selected APK has no ARM64 Apple Music libraries")
        verified: dict[str, bytes] = {}
        for name, digest in expected.items():
            member = f"{prefix}/{name}"
            if member not in names:
                raise SetupError(f"The Apple Music package is missing {name}")
            data = apk.read(member)
            if hashlib.sha256(data).hexdigest() != digest:
                raise SetupError(f"Apple library hash mismatch: {name}")
            verified[name] = data
    finally:
        apk.close()

    target = rootfs / "system" / "lib64"
    target.mkdir(parents=True, exist_ok=True)
    for name, data in verified.items():
        (target / name).write_bytes(data)


def stage_android_system(source: Path, manifest_path: Path, rootfs: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected: dict[str, str] = manifest["android_system"][ARCH]
    vendor = source / "vendor" / "android-system" / ARCH
    for relative, digest in expected.items():
        path = vendor / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SetupError(f"Pinned Android system file failed verification: {relative}")
    for relative in expected:
        src = vendor / relative
        dest = rootfs / "system" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _patch_health_endpoint(source: Path) -> None:
    """Make pinned wrapper /health expose the worker runtime required by the UI."""
    path = source / "src" / "rust" / "main.rs"
    text = path.read_text(encoding="utf-8")
    start = text.find('        ("GET", "/health") => {')
    end = text.find('        ("GET", "/me") =>', start)
    if start < 0 or end < 0:
        raise SetupError("Pinned wrapper health handler is not recognized")
    replacement = '''        ("GET", "/health") => proxy_json(\n            &mut stream,\n            worker.health(),\n        )?,\n'''
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


def _extract_source(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        top: str | None = None
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise SetupError("The wrapper source archive contains an unsafe path")
            if path.parts:
                top = top or path.parts[0]
                if path.parts[0] != top:
                    raise SetupError("The wrapper source archive has an unexpected layout")
        if not top:
            raise SetupError("The wrapper source archive is empty")
        destination.mkdir(parents=True)
        for member in members:
            parts = PurePosixPath(member.name).parts[1:]
            if not parts:
                continue
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source_file = tf.extractfile(member)
                if source_file is None:
                    raise SetupError("The wrapper source archive is incomplete")
                with target.open("wb") as output:
                    shutil.copyfileobj(source_file, output)
                target.chmod(member.mode & 0o777)


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class WrapperSetupManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._state = self._base_state()

    @staticmethod
    def _base_state() -> dict:
        supported = platform.system() == "Darwin" and platform.machine().lower() in ("arm64", "aarch64")
        return {
            "phase": "idle",
            "progress": 0,
            "message": "",
            "supported": supported,
            "docker": {"installed": False, "running": False},
            "apkm": {"filename": None, "matches": 0, "needs_picker": False},
            "container": {"name": None, "status": "missing", "compatible": False, "playback_ready": False},
            "requires_confirmation": False,
            "error": None,
        }

    def _set(self, phase: str, progress: int, message: str = "", **extra) -> None:
        with self._lock:
            self._state.update({"phase": phase, "progress": progress, "message": message, **extra})
        self._log(f"{phase}: {message}")

    @staticmethod
    def _log(message: str) -> None:
        WRAPPER_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")

    @staticmethod
    def _docker() -> Path | None:
        found = shutil.which("docker")
        candidates = (
            Path(found) if found else None,
            Path("/usr/local/bin/docker"),
            Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
        )
        return next((p for p in candidates if p and p.is_file()), None)

    @staticmethod
    def _run_docker(docker: Path, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [str(docker), *args], capture_output=True, text=True, timeout=timeout,
            creationflags=_SUBPROCESS_FLAGS,
        )
        if result.returncode:
            raise SetupError(f"Docker command failed ({args[0]}, exit {result.returncode})")
        return result

    def _docker_running(self, docker: Path) -> bool:
        try:
            self._run_docker(docker, ["info", "--format", "{{.ServerVersion}}"], 15)
            return True
        except (SetupError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _docker_socket_running() -> bool:
        for path in (Path.home() / ".docker" / "run" / "docker.sock", Path("/var/run/docker.sock")):
            if not path.exists():
                continue
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(0.5)
            try:
                client.connect(str(path))
                return True
            except OSError:
                pass
            finally:
                client.close()
        return False

    def _ensure_docker(self) -> Path:
        docker = self._docker()
        if not docker:
            raise SetupError("Docker Desktop is not installed. Install it from docker.com/products/docker-desktop")
        if self._docker_running(docker):
            return docker
        docker_app = Path("/Applications/Docker.app")
        if not docker_app.exists():
            raise SetupError("Docker Desktop is installed but its daemon is unavailable")
        subprocess.run(["/usr/bin/open", "-a", "Docker"], check=False, creationflags=_SUBPROCESS_FLAGS)
        for _ in range(60):
            if self._docker_running(docker):
                return docker
            time.sleep(2)
        raise SetupError("Docker Desktop did not become ready within two minutes")

    @staticmethod
    def _probe() -> dict | None:
        try:
            response = httpx.get("http://127.0.0.1/health", timeout=3.0)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                return None
            runtime = body.get("runtime") if isinstance(body.get("runtime"), dict) else {}
            if not runtime:
                # Older compatible wrappers expose runtime readiness only on
                # /me.  Read it locally, retain only the non-secret fields.
                me_response = httpx.get("http://127.0.0.1/me", timeout=3.0)
                me_response.raise_for_status()
                me = me_response.json()
                if isinstance(me, dict):
                    runtime = me.get("runtime") if isinstance(me.get("runtime"), dict) else {}
            return {
                "version": body.get("version"),
                "compatible": body.get("version") == WRAPPER_API_VERSION,
                "playback_ready": bool(runtime.get("playback_ready")),
            }
        except (httpx.HTTPError, ValueError):
            return None

    def _containers(self, docker: Path) -> list[dict]:
        result = self._run_docker(
            docker, ["ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"], 15
        )
        containers = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 2)
            containers.append({
                "name": parts[0],
                "status": parts[1] if len(parts) > 1 else "",
                "ports": parts[2] if len(parts) > 2 else "",
            })
        return containers

    def inspect(self) -> dict:
        with self._lock:
            if self._running or self._state["phase"] != "idle":
                return dict(self._state)
        state = self._base_state()
        matches = find_bundles()
        state["apkm"] = {
            "filename": matches[0].name if len(matches) == 1 else None,
            "matches": len(matches),
            "needs_picker": len(matches) != 1,
        }
        docker = self._docker()
        state["docker"] = {"installed": bool(docker), "running": bool(docker and self._docker_socket_running())}
        probe = self._probe()
        if probe:
            state["container"] = {"name": None, "status": "running", **probe}
            state["requires_confirmation"] = not (probe["compatible"] and probe["playback_ready"])
        with self._lock:
            if not self._running:
                self._state = state
            return dict(self._state)

    def status(self) -> dict:
        return self.inspect()

    def start(self, apkm_path: str | None = None, replace_existing: bool = False) -> dict:
        with self._lock:
            if self._running:
                raise SetupBusy("Wrapper setup is already running")
            self._running = True
            self._state.update({
                "phase": "checking",
                "progress": 1,
                "error": None,
                "requires_confirmation": False,
            })
        thread = threading.Thread(
            target=self._setup, args=(Path(apkm_path).expanduser() if apkm_path else None, replace_existing), daemon=True
        )
        thread.start()
        return dict(self._state)

    def _download_source(self) -> None:
        marker = SOURCE_DIR / ".amdl-wrapper-commit"
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == WRAPPER_COMMIT:
            return
        self._set("downloading", 20, "Downloading pinned wrapper-v2 source")
        WRAPPER_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=WRAPPER_DIR) as temp:
            archive = Path(temp) / "source.tar.gz"
            try:
                with httpx.stream("GET", SOURCE_URL, follow_redirects=True, timeout=120.0) as response:
                    response.raise_for_status()
                    with archive.open("wb") as file:
                        for chunk in response.iter_bytes(128 * 1024):
                            file.write(chunk)
            except httpx.HTTPError as exc:
                raise SetupError("Could not download the pinned wrapper-v2 source") from exc
            extracted = Path(temp) / "source"
            _extract_source(archive, extracted)
            _patch_health_endpoint(extracted)
            (extracted / ".amdl-wrapper-commit").write_text(WRAPPER_COMMIT, encoding="utf-8")
            if SOURCE_DIR.exists():
                backup = WRAPPER_DIR / f"{WRAPPER_REV}-invalid-{datetime.now():%Y%m%d-%H%M%S}"
                SOURCE_DIR.rename(backup)
            shutil.move(str(extracted), str(SOURCE_DIR))

    def _select_bundle(self, requested: Path | None) -> Path:
        if requested:
            inspect_bundle(requested)
            return requested
        matches = find_bundles()
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise SetupError("No compatible APKM was found in Downloads; choose one manually")
        raise SetupError("Multiple compatible APKM files were found; choose one manually")

    def _backup_existing(self, docker: Path, container: dict) -> None:
        name = container["name"]
        if name not in ("wrapper-v2", CONTAINER_NAME):
            raise SetupError("Ports 80 or 10020 are used by another Docker container")
        if container["status"].lower().startswith("up"):
            self._run_docker(docker, ["stop", name], 60)
        backup = f"wrapper-v2-backup-{datetime.now():%Y%m%d-%H%M%S}"
        self._run_docker(docker, ["rename", name, backup])

    def _setup(self, requested: Path | None, replace_existing: bool) -> None:
        try:
            if platform.system() != "Darwin" or platform.machine().lower() not in ("arm64", "aarch64"):
                raise SetupError("One-click Wrapper setup currently supports Apple Silicon macOS only")
            self._set("checking", 5, "Checking Docker, APKM, and existing Wrapper")
            docker = self._ensure_docker()
            bundle = self._select_bundle(requested)
            probe = self._probe()
            if probe and probe["compatible"] and probe["playback_ready"]:
                self._set("ready", 100, "Existing compatible Wrapper is ready")
                return
            containers = self._containers(docker)
            occupying = next((c for c in containers if c["name"] in ("wrapper-v2", CONTAINER_NAME)), None)
            if not occupying:
                occupying = next((c for c in containers if "0.0.0.0:80->" in c["ports"] or ":10020->" in c["ports"]), None)
            if occupying and not replace_existing:
                raise ReplaceConfirmationRequired("An existing Wrapper must be backed up before setup")
            if occupying:
                self._backup_existing(docker, occupying)
            elif not _port_available(80) or not _port_available(10020):
                raise SetupError("Port 80 or 10020 is already in use by a non-Wrapper process")

            self._download_source()
            manifest = SOURCE_DIR / "LIBS_VERSION.json"
            rootfs = SOURCE_DIR / "rootfs"
            self._set("extracting", 42, "Verifying Apple Music libraries")
            extract_libraries(bundle, manifest, rootfs)
            stage_android_system(SOURCE_DIR, manifest, rootfs)

            image_exists = subprocess.run(
                [str(docker), "image", "inspect", IMAGE_NAME], capture_output=True,
                timeout=15,
                creationflags=_SUBPROCESS_FLAGS,
            ).returncode == 0
            if not image_exists:
                self._set("building", 55, "Building the local Wrapper image")
                self._run_docker(docker, [
                    "build", "--platform", "linux/arm64",
                    "--build-arg", f"TARGET_ARCH={ARCH}",
                    "--build-arg", "BUILD_PLATFORM=linux/amd64",
                    "--build-arg", "RUNTIME_PLATFORM=linux/arm64",
                    "-t", IMAGE_NAME, str(SOURCE_DIR),
                ], 3600)

            self._set("starting", 88, "Starting Wrapper and checking playback runtime")
            PERSISTENT_DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._run_docker(docker, [
                "run", "-d", "--name", CONTAINER_NAME, "--restart", "unless-stopped",
                "--cap-add", "SYS_ADMIN", "--cap-add", "SYS_CHROOT", "--cap-add", "SYS_PTRACE",
                "--security-opt", "apparmor=unconfined", "-p", "80:80", "-p", "10020:10020",
                "-v", f"{PERSISTENT_DATA_DIR}:/app/rootfs/data/data/com.apple.android.music/files",
                IMAGE_NAME,
            ], 60)
            for _ in range(30):
                probe = self._probe()
                if probe and probe["compatible"] and probe["playback_ready"]:
                    self._set("ready", 100, "Wrapper is ready for Apple Music login")
                    return
                time.sleep(2)
            raise SetupError("Wrapper started, but its Apple playback runtime is not ready")
        except ReplaceConfirmationRequired as exc:
            self._set("error", 5, str(exc), error=str(exc), requires_confirmation=True)
        except (SetupError, OSError, KeyError, json.JSONDecodeError, tarfile.TarError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
            message = str(exc) if isinstance(exc, SetupError) else "Wrapper setup failed; see setup.log"
            self._set("error", 0, message, error=message, requires_confirmation=False)
            logger.warning("Wrapper setup failed: %s", type(exc).__name__)
        finally:
            with self._lock:
                self._running = False


_manager = WrapperSetupManager()


def get_wrapper_setup_manager() -> WrapperSetupManager:
    return _manager
