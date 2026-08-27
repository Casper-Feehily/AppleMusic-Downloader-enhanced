from __future__ import annotations

import json
import logging
import os
import socket
import sys
import shutil
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

# ── Windows: ensure anyio uses asyncio backend ──────────────
if sys.platform == "win32":
    os.environ.setdefault("ANYIO_BACKEND", "asyncio")

import httpx
from gamdl.api.wrapper import TARGET_WRAPPER_API_VERSION
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from amdl.enums import (
    CoverFormat,
    DownloadMode,
    MusicVideoCodec,
    SongCodec,
    SyncedLyricsFormat,
    UploadedVideoQuality,
)
from amdl import __version__
from amdl.task_manager import get_task_manager
from amdl.dependency_manager import BIN_DIR, DATA_DIR, _SUBPROCESS_FLAGS
from amdl.dependency_manager import ensure_dependencies_async

logger = logging.getLogger("amdl.server")

# ── Path resolution（兼容 PyInstaller 打包） ───────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]  (只读)
    FRONTEND_OUT = BASE_DIR / "frontend_out"
else:
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    FRONTEND_OUT = BASE_DIR / "src" / "fronted" / "out"

# DATA_DIR / TEMP_DIR / SETTINGS_FILE — 统一从 dependency_manager 获取
TEMP_DIR = DATA_DIR / "temp"
SETTINGS_FILE = DATA_DIR / "settings.json"


def _add_bin_to_path() -> None:
    """Add BIN_DIR to PATH so shutil.which can find bundled binaries."""
    bin_str = str(BIN_DIR)
    current = os.environ.get("PATH", "")
    if bin_str not in current:
        os.environ["PATH"] = f"{bin_str}{os.pathsep}{current}"


_add_bin_to_path()

# ── 图标：根据平台自动选择 ────────────────────────────────
import platform as _platform
if _platform.system() == "Darwin":
    ICON_FILE = BASE_DIR / "icon.icns"
elif _platform.system() == "Windows":
    ICON_FILE = BASE_DIR / "icon.ico"
else:
    ICON_FILE = BASE_DIR / "icon.png"


# ═══════════════════════════════════════════════════════════════
# Lifespan
# ═══════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    tm = get_task_manager()
    tm.start()
    # Auto-download missing dependencies in background
    ensure_dependencies_async()
    logger.info("AMDL server started")
    yield
    await tm.stop()
    logger.info("AMDL server stopped")


# ═══════════════════════════════════════════════════════════════
# FastAPI app
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="AMDL API",
    description="Apple Music Downloader API",
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Allow overriding CORS origins via environment variable for production deployments.
# Defaults to ["*"] for local/desktop use; set AMDL_CORS_ORIGINS for stricter control.
_CORS_ORIGINS = os.environ.get("AMDL_CORS_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


_LOCAL_WRAPPER_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SECRET_SETTING_KEYS = {"apple_id", "username", "password", "code", "two_factor_code"}


def _local_wrapper_host(value: str) -> str:
    host = value.strip().lower().strip("[]")
    if host not in _LOCAL_WRAPPER_HOSTS:
        raise ValueError("Wrapper host must be localhost, 127.0.0.1, or ::1")
    return host


def _local_wrapper_url(value: str) -> str:
    raw = value.strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid wrapper URL") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in _LOCAL_WRAPPER_HOSTS
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("Wrapper URL must be a local HTTP URL without a path")
    return raw.rstrip("/")


async def _wrapper_request(
    method: str,
    wrapper_url: str,
    path: str,
    *,
    payload: dict | None = None,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            return await client.request(method, f"{wrapper_url}{path}", json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Wrapper is not reachable") from exc


def _wrapper_status(me: dict) -> dict:
    auth = me.get("auth") if isinstance(me.get("auth"), dict) else {}
    runtime = me.get("runtime") if isinstance(me.get("runtime"), dict) else {}
    auth_state = auth.get("state", "unknown")
    return {
        "reachable": True,
        "version": me.get("version"),
        "compatible": me.get("version") == TARGET_WRAPPER_API_VERSION,
        "authenticated": auth_state == "authenticated",
        "auth_state": auth_state,
        "playback_ready": bool(runtime.get("playback_ready")),
    }


# ═══════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════

class DownloadRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1)
    cookies_path: str | None = Field(default=None)
    use_wrapper: bool = Field(default=False)
    wrapper_url: str = Field(default="http://127.0.0.1")
    wrapper_decrypt_host: str = Field(default="127.0.0.1")
    wrapper_decrypt_port: int = Field(default=10020, ge=1, le=65535)
    output_path: str = Field(default="./Apple Music")
    temp_path: str = Field(default="./temp")
    wvd_path: str | None = Field(default=None)
    nm3u8dlre_path: str = Field(default="N_m3u8DL-RE")
    ffmpeg_path: str = Field(default="ffmpeg")
    download_mode: DownloadMode = Field(default=DownloadMode.YTDLP)
    codec_song: SongCodec = Field(default=SongCodec.AAC_WEB)
    codec_music_video: MusicVideoCodec = Field(default=MusicVideoCodec.H264)
    quality_post: UploadedVideoQuality = Field(default=UploadedVideoQuality.BEST)
    synced_lyrics_format: SyncedLyricsFormat = Field(default=SyncedLyricsFormat.LRC)
    cover_format: CoverFormat = Field(default=CoverFormat.JPG)
    cover_size: int = Field(default=1200, ge=50, le=5000)
    truncate: int | None = Field(default=None, ge=0)
    audio_format: str | None = Field(default=None)
    video_format: str | None = Field(default=None)
    template_folder_album: str = Field(default="{album_artist}/{album}")
    template_folder_compilation: str = Field(default="Compilations/{album}")
    template_file_single_disc: str = Field(default="{track:02d} {title}")
    template_file_multi_disc: str = Field(default="{disc}-{track:02d} {title}")
    template_folder_no_album: str = Field(default="{artist}/Unknown Album")
    template_file_no_album: str = Field(default="{title}")
    template_file_playlist: str = Field(default="Playlists/{playlist_artist}/{playlist_title}")
    template_date: str = Field(default="%Y-%m-%dT%H:%M:%SZ")
    exclude_tags: str | None = Field(default=None)
    overwrite: bool = Field(default=False)
    save_cover: bool = Field(default=False)
    save_playlist: bool = Field(default=False)
    synced_lyrics_only: bool = Field(default=False)
    no_synced_lyrics: bool = Field(default=False)
    disable_music_video_skip: bool = Field(default=False)
    read_urls_as_txt: bool = Field(default=False)
    language: str = Field(default="en-US")
    log_level: str = Field(default="INFO")

    @field_validator("wrapper_url")
    @classmethod
    def _validate_wrapper_url(cls, v: str) -> str:
        return _local_wrapper_url(v)

    @field_validator("wrapper_decrypt_host")
    @classmethod
    def _validate_wrapper_decrypt_host(cls, v: str) -> str:
        return _local_wrapper_host(v)

    @model_validator(mode="after")
    def _validate_auth_mode(self):
        if self.use_wrapper:
            if self.codec_song == SongCodec.ALAC:
                self.audio_format = None
            return self
        if self.codec_song == SongCodec.ALAC:
            raise ValueError("ALAC requires wrapper mode")
        if not self.cookies_path or not self.cookies_path.strip():
            raise ValueError("cookies_path must not be empty in cookies mode")
        self.cookies_path = self.cookies_path.strip()
        path = Path(self.cookies_path)
        if not path.exists():
            raise ValueError(f"Cookies file not found: {self.cookies_path}")
        if not path.is_file():
            raise ValueError(f"Not a file: {self.cookies_path}")
        return self

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(f"Invalid log level: {v}, options: {', '.join(sorted(allowed))}")
        return v.upper()

    @field_validator("audio_format")
    @classmethod
    def _validate_audio_fmt(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v.lower() not in {"mp3", "flac", "wav", "aac", "m4a", "ogg", "wma"}:
            raise ValueError(f"Unsupported format: {v}")
        return v.lower()

    @field_validator("video_format")
    @classmethod
    def _validate_video_fmt(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v.lower() not in {"mp4", "mov", "mkv", "avi", "wmv", "flv", "webm"}:
            raise ValueError(f"Unsupported format: {v}")
        return v.lower()


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = __version__


class DependencyCheckItem(BaseModel):
    name: str
    found: bool
    path: str | None = None
    version: str | None = None


class DependencyCheckResponse(BaseModel):
    all_ok: bool
    dependencies: list[DependencyCheckItem]


class WrapperConnection(BaseModel):
    wrapper_url: str = Field(default="http://127.0.0.1")

    @field_validator("wrapper_url")
    @classmethod
    def _validate_wrapper_url(cls, v: str) -> str:
        return _local_wrapper_url(v)


class WrapperLoginRequest(WrapperConnection):
    apple_id: str = Field(min_length=1)
    password: SecretStr = Field(min_length=1)

    @field_validator("apple_id")
    @classmethod
    def _validate_apple_id(cls, v: str) -> str:
        value = v.strip()
        if not value:
            raise ValueError("Apple ID must not be empty")
        return value


class WrapperTwoFactorRequest(WrapperConnection):
    code: SecretStr

    @model_validator(mode="after")
    def _validate_code(self):
        code = self.code.get_secret_value().strip()
        if len(code) != 6 or not code.isdigit():
            raise ValueError("2FA code must contain exactly 6 digits")
        self.code = SecretStr(code)
        return self


class TaskSubmitResponse(BaseModel):
    task_id: str
    status: str
    message: str


class TaskInfoResponse(BaseModel):
    id: str
    status: str
    progress: dict
    error_count: int
    message: str
    logs: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    urls: list[str]


class TaskListResponse(BaseModel):
    tasks: list[TaskInfoResponse]
    total: int


class ApiInfoResponse(BaseModel):
    api_version: str
    supported_codecs_song: list[dict[str, str]]
    supported_codecs_music_video: list[dict[str, str]]
    supported_cover_formats: list[dict[str, str]]
    supported_download_modes: list[dict[str, str]]
    supported_audio_conversion_formats: list[str]
    supported_video_conversion_formats: list[str]


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

# ── well-known fallback paths per platform ──────────────────
_HOMEBREW_PATHS: list[str] = [
    "/opt/homebrew/bin",   # Apple Silicon
    "/usr/local/bin",       # Intel Mac
    "/home/linuxbrew/.linuxbrew/bin",
]

_KNOWN_EXTENSIONS: dict[str, str | None] = {
    "N_m3u8DL-RE": ".exe" if sys.platform == "win32" else None,
    "MP4Box": ".exe" if sys.platform == "win32" else None,
    "ffmpeg": ".exe" if sys.platform == "win32" else None,
}


def _find_executable(name: str, custom_path: str | None = None) -> DependencyCheckItem:
    """Find an executable, searching PATH → BIN_DIR → Homebrew paths."""
    target = custom_path or name

    # 1) shutil.which — respects PATH (including BIN_DIR added above)
    found_path = shutil.which(target)

    # 2) BIN_DIR direct lookup (for cases where BIN_DIR isn't in PATH yet)
    if not found_path:
        ext = _KNOWN_EXTENSIONS.get(name, None) or ""
        candidate = BIN_DIR / f"{target}{ext}"
        if candidate.exists():
            found_path = str(candidate)

    # 3) Homebrew fallback (macOS / Linux)
    if not found_path and sys.platform != "win32":
        for brew_dir in _HOMEBREW_PATHS:
            candidate = Path(brew_dir) / target
            if candidate.exists():
                found_path = str(candidate)
                break

    if found_path and Path(found_path).exists():
        resolved = Path(found_path).resolve()
        try:
            result = subprocess.run(
                [str(resolved), "-version"] if name in ("ffmpeg", "MP4Box") else [str(resolved), "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=_SUBPROCESS_FLAGS,
            )
            version_line = (result.stdout or result.stderr).split("\n")[0]
        except Exception:
            version_line = None
        return DependencyCheckItem(
            name=name,
            found=True,
            path=str(resolved),
            version=version_line,
        )

    return DependencyCheckItem(name=name, found=False)


# ═══════════════════════════════════════════════════════════════
# API — System
# ═══════════════════════════════════════════════════════════════

@app.get("/api/health", response_model=HealthResponse, tags=["system"])
async def health_check():
    return HealthResponse()

@app.get("/api/settings", tags=["system"])
async def get_settings():
    if SETTINGS_FILE.exists():
        try:
            settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if settings.get("audio_format") == "alac" or settings.get("codec_song") == "alac":
                settings["audio_format"] = ""
            return JSONResponse(content=settings)
        except Exception:
            pass
    return JSONResponse(content={})


@app.post("/api/settings", tags=["system"])
async def save_settings(payload: dict):
    secret_keys = {
        key for key in payload
        if key.lower() in _SECRET_SETTING_KEYS
        or key.lower().endswith(("_password", "_2fa", "_2fa_code", "_apple_id", "_username"))
    }
    if secret_keys:
        raise HTTPException(status_code=422, detail="Credentials must not be stored in settings")
    if payload.get("audio_format") == "alac":
        payload["audio_format"] = ""
    # 读取已有设置，做合并而非覆盖
    existing: dict = {}
    if SETTINGS_FILE.exists():
        try:
            existing = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.update(payload)
    if existing.get("codec_song") == "alac":
        existing["audio_format"] = ""
    SETTINGS_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse(content={"status": "ok"})



@app.get("/api/info", response_model=ApiInfoResponse, tags=["system"])
async def get_api_info():
    return ApiInfoResponse(
        api_version=__version__,
        supported_codecs_song=[{"value": c.value, "label": c.name} for c in SongCodec],
        supported_codecs_music_video=[{"value": c.value, "label": c.name} for c in MusicVideoCodec],
        supported_cover_formats=[{"value": c.value, "label": c.name} for c in CoverFormat],
        supported_download_modes=[{"value": c.value, "label": c.name} for c in DownloadMode],
        supported_audio_conversion_formats=["mp3", "flac", "wav", "aac", "m4a", "ogg"],
        supported_video_conversion_formats=["mp4", "mov", "mkv", "avi", "webm"],
    )


@app.get("/api/dependencies", response_model=DependencyCheckResponse, tags=["system"])
async def check_dependencies(ffmpeg_path: str = "", nm3u8dlre_path: str = "", mp4box_path: str = ""):
    deps = [
        _find_executable("ffmpeg", ffmpeg_path or None),
        _find_executable("MP4Box", mp4box_path or None),
        _find_executable("N_m3u8DL-RE", nm3u8dlre_path or None),
    ]
    return DependencyCheckResponse(all_ok=all(d.found for d in deps), dependencies=deps)


@app.get("/api/dependencies/download-progress", tags=["system"])
async def dep_download_progress():
    """Get the progress of auto-downloading missing dependencies."""
    from amdl.dependency_manager import get_progress as _get_progress
    return {"dependencies": _get_progress()}


# ═══════════════════════════════════════════════════════════════
# API — Wrapper v2
# ═══════════════════════════════════════════════════════════════

@app.get("/api/wrapper/status", tags=["wrapper"])
async def wrapper_status(
    wrapper_url: str = Query(default="http://127.0.0.1"),
):
    try:
        wrapper_url = _local_wrapper_url(wrapper_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response = await _wrapper_request("GET", wrapper_url, "/me")
    if response.is_error:
        raise HTTPException(status_code=502, detail="Wrapper returned an error")
    try:
        me = response.json()
        if not isinstance(me, dict):
            raise ValueError("Expected an object")
        return _wrapper_status(me)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Wrapper returned invalid JSON") from exc


@app.post("/api/wrapper/login", tags=["wrapper"])
async def wrapper_login(request: WrapperLoginRequest):
    response = await _wrapper_request(
        "POST",
        request.wrapper_url,
        "/login",
        payload={
            "username": request.apple_id.strip(),
            "password": request.password.get_secret_value(),
        },
    )
    if response.status_code == 200:
        return {"state": "authenticated"}
    if response.status_code == 202:
        return JSONResponse(status_code=202, content={"state": "requires_2fa"})
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Wrapper login failed")
    raise HTTPException(status_code=502, detail="Wrapper returned an error")


@app.post("/api/wrapper/login/2fa", tags=["wrapper"])
async def wrapper_login_2fa(request: WrapperTwoFactorRequest):
    response = await _wrapper_request(
        "POST",
        request.wrapper_url,
        "/login/2fa",
        payload={"code": request.code.get_secret_value()},
    )
    if response.is_success:
        return {"state": "authenticated"}
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Wrapper 2FA login failed")
    raise HTTPException(status_code=502, detail="Wrapper returned an error")


@app.delete("/api/wrapper/login", tags=["wrapper"])
async def wrapper_logout(
    wrapper_url: str = Query(default="http://127.0.0.1"),
):
    try:
        wrapper_url = _local_wrapper_url(wrapper_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response = await _wrapper_request("DELETE", wrapper_url, "/login")
    if response.is_error:
        raise HTTPException(status_code=502, detail="Wrapper returned an error")
    return {"state": "logged_out"}


@app.delete("/api/temp", tags=["system"])
async def clean_temp():
    count = 0
    if TEMP_DIR.exists():
        for item in TEMP_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            count += 1
    return {"message": f"Cleaned {count} items from temp directory"}


# ═══════════════════════════════════════════════════════════════
# API — Tasks
# ═══════════════════════════════════════════════════════════════

@app.post("/api/tasks", response_model=TaskSubmitResponse, tags=["tasks"])
async def submit_task(request: DownloadRequest):
    tm = get_task_manager()
    task_id = await tm.submit(request.model_dump())
    return TaskSubmitResponse(task_id=task_id, status="pending", message="Task submitted")


@app.get("/api/tasks", response_model=TaskListResponse, tags=["tasks"])
async def list_tasks():
    tm = get_task_manager()
    tasks = tm.list_tasks()
    return TaskListResponse(
        tasks=[TaskInfoResponse(**t.to_dict()) for t in tasks],
        total=len(tasks),
    )


@app.get("/api/tasks/{task_id}", response_model=TaskInfoResponse, tags=["tasks"])
async def get_task(task_id: str):
    tm = get_task_manager()
    task = tm.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return TaskInfoResponse(**task.to_dict())


@app.delete("/api/tasks/{task_id}", tags=["tasks"])
async def cancel_task(task_id: str):
    tm = get_task_manager()
    ok = await tm.cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Task not found or finished: {task_id}")
    return {"message": "Task cancelled", "task_id": task_id}


# ═══════════════════════════════════════════════════════════════
# WebSocket
# ═══════════════════════════════════════════════════════════════

@app.websocket("/api/ws/{task_id}")
async def task_progress_ws(websocket: WebSocket, task_id: str):
    tm = get_task_manager()
    await websocket.accept()

    ok = await tm.subscribe(task_id, websocket)
    if not ok:
        await websocket.send_json({"type": "error", "message": f"Task not found: {task_id}"})
        await websocket.close(code=1008)
        return

    try:
        while True:
            data = await websocket.receive_text()
            if data == '{"type":"ping"}':
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await tm.unsubscribe(task_id, websocket)


# ═══════════════════════════════════════════════════════════════
# Static files (Next.js build output for pywebview)
# ═══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index = FRONTEND_OUT / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse(
        content="<html><body>Frontend not built. Run: cd src/fronted && npm run build</body></html>",
        status_code=200,
    )


@app.get("/{full_path:path}", response_class=FileResponse)
async def serve_static(full_path: str):
    file_path = (FRONTEND_OUT / full_path).resolve()
    root = FRONTEND_OUT.resolve()
    if file_path.is_relative_to(root) and file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    index = FRONTEND_OUT / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"error": "Not found"}, status_code=404)


# ═══════════════════════════════════════════════════════════════
# pywebview desktop API
# ═══════════════════════════════════════════════════════════════

class PywebviewApi:
    def __init__(self, window_ref: list):
        self._window_ref = window_ref

    def open_file(self, **kwargs) -> str | None:
        import webview
        from webview import FileDialog

        result = self._window_ref[0].create_file_dialog(
            FileDialog.OPEN,
            file_types=("Text files (*.txt)", "All files (*.*)"),
        )
        return result[0] if result else None

    def open_folder(self, **kwargs) -> str | None:
        import webview
        from webview import FileDialog

        result = self._window_ref[0].create_file_dialog(
            FileDialog.FOLDER,
        )
        return result[0] if result else None

    def save_file(self, **kwargs) -> str | None:
        import webview
        from webview import FileDialog

        result = self._window_ref[0].create_file_dialog(
            FileDialog.SAVE,
            file_types=("All files (*.*)",),
        )
        return result[0] if result else None


# ═══════════════════════════════════════════════════════════════
# Entry points
# ═══════════════════════════════════════════════════════════════

# ── single-instance lock ────────────────────────────────────
# Prevent multiple `run_desktop()` calls from spawning duplicate
# uvicorn processes and windows.
# Uses BOTH a threading lock (thread-safe) and a TCP port bind (process-safe).
_LOCK_PORT = 51_999
_LOCK_SOCKET: list[socket.socket | None] = [None]
_LOCK_THREAD = threading.Lock()


def _acquire_instance_lock() -> bool:
    with _LOCK_THREAD:
        if _LOCK_SOCKET[0] is not None:
            return False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", _LOCK_PORT))
            s.listen(1)
            _LOCK_SOCKET[0] = s
            return True
        except OSError:
            return False


def _release_instance_lock() -> None:
    with _LOCK_THREAD:
        if _LOCK_SOCKET[0] is not None:
            try:
                _LOCK_SOCKET[0].close()
            except Exception:
                pass
            _LOCK_SOCKET[0] = None


def _find_free_port(start: int = 8000, max_attempts: int = 20) -> int:
    """Find the first available port starting from *start*."""
    for port in range(start, start + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start  # give up, let uvicorn fail with the original port


def run_server(host: str = "127.0.0.1", port: int = 8000, log_level: str = "info"):
    import uvicorn

    original_port = port
    port = _find_free_port(port)
    if port != original_port:
        logger.info("Port %d in use — using port %d instead", original_port, port)

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    uvicorn.run(app, host=host, port=int(port), log_level=log_level)


def run_desktop():
    # Single-instance guard: only one desktop window allowed.
    # Acquires the lock BEFORE starting the server thread, so that
    # any concurrent call to run_desktop() (e.g. from a forked process
    # or re-import) will bail immediately.
    if not _acquire_instance_lock():
        logger.warning("Another AMDL desktop instance is already running — skipping")
        return

    import webview

    host = "127.0.0.1"
    port = _find_free_port(8000)

    server_thread = threading.Thread(target=run_server, args=(host, port), daemon=True)
    server_thread.start()
    time.sleep(2)

    url = f"http://{host}:{port}"
    window_ref: list = []
    api = PywebviewApi(window_ref)

    kwargs: dict = dict(
        title="Apple Music Downloader",
        url=url,
        width=1200,
        height=800,
        min_size=(800, 600),
        resizable=True,
        js_api=api,
    )

    # 设置窗口图标（pywebview >= 6.0 才支持 icon 参数）
    if ICON_FILE.exists():
        try:
            _v = tuple(int(x) for x in getattr(webview, "__version__", "0").split(".")[:2])
        except Exception:
            _v = (0, 0)
        if _v >= (6, 0):
            kwargs["icon"] = str(ICON_FILE)

    window = webview.create_window(**kwargs)
    window_ref.append(window)
    webview.start(debug=False)


if __name__ == "__main__":
    import sys

    if "--desktop" in sys.argv:
        run_desktop()
    else:
        run_server()
