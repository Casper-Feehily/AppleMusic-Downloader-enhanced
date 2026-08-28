"""Audio / video format conversion via FFmpeg."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

LogFunc = Callable[[str], None]

# ── Audio codec presets ────────────────────────────────────────
# Each entry: (codec_args, quality_args)
_AUDIO_PRESETS: dict[str, tuple[list[str], list[str]]] = {
    "mp3":  (["-c:a", "libmp3lame"], ["-b:a", "320k", "-id3v2_version", "3", "-write_id3v1", "1"]),
    "flac": (["-c:a", "flac"],       ["-compression_level", "8"]),
    "wav":  (["-c:a", "pcm_s16le"],  []),
    "aac":  (["-c:a", "aac"],        ["-b:a", "256k"]),
    "m4a":  (["-c:a", "aac"],        ["-b:a", "256k"]),
    "ogg":  (["-c:a", "libvorbis"],  ["-q:a", "5"]),
    "wma":  (["-c:a", "wmav2"],      ["-b:a", "192k"]),
}

# ── Video codec presets ────────────────────────────────────────
_VIDEO_PRESETS: dict[str, list[str]] = {
    "mp4":  ["-c", "copy"],
    "mov":  ["-c", "copy"],
    "mkv":  ["-c", "copy"],
    "avi":  ["-c:v", "libx264", "-c:a", "aac"],
    "wmv":  ["-c:v", "wmv2", "-c:a", "wmav2"],
    "flv":  ["-c:v", "flv", "-c:a", "aac"],
    "webm": ["-c:v", "libvpx-vp9", "-c:a", "libopus"],
}


def _get_startupinfo() -> subprocess.STARTUPINFO | None:
    """Windows: hide console window for subprocess calls."""
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si
    return None


def resolve_ffmpeg_executable(
    preferred: str | None = None,
    fallback_paths: list[str] | None = None,
) -> str | None:
    """Resolve the ffmpeg executable path."""
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    else:
        candidates.append("ffmpeg")
    if fallback_paths:
        candidates.extend(fallback_paths)

    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                return candidate
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        if os.path.exists(candidate):
            return candidate
    from amdl.dependency_manager import find_bundled, find_system_binary

    bundled = find_bundled("ffmpeg")
    if bundled:
        return str(bundled)
    return find_system_binary("ffmpeg")


def _run_subprocess(cmd: list[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            startupinfo=_get_startupinfo(),
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as error:
        return 1, "", str(error)


def _build_ffmpeg_cmd(
    source_path: str,
    target_path: str,
    codec_args: list[str],
    quality_args: list[str],
    *,
    copy_video: bool = True,
) -> list[str]:
    """Build a standard ffmpeg command with common flags."""
    cmd = ["-y", "-i", source_path]
    cmd.extend(codec_args)
    cmd.extend(quality_args)
    if copy_video:
        cmd.extend(["-c:v", "copy", "-map", "0:0", "-map", "0:1?"])
    cmd.append(target_path)
    return cmd


def convert_audio_file(
    source_path: str,
    target_path: str,
    target_format: str,
    ffmpeg_exe: str | None,
    log: LogFunc,
) -> bool:
    """Convert an audio file to the target format."""
    if not os.path.exists(source_path):
        log(f"    Error: source file not found: {source_path}")
        return False
    if not ffmpeg_exe:
        log("    Error: FFmpeg not available")
        return False

    target_format = target_format.lower()
    preset = _AUDIO_PRESETS.get(target_format)
    if preset is None:
        log(f"    Error: unsupported audio format: {target_format}")
        return False

    codec_args, quality_args = preset
    cmd = [ffmpeg_exe] + _build_ffmpeg_cmd(source_path, target_path, codec_args, quality_args)

    log(f"    Converting: {os.path.basename(source_path)} → {target_format}")
    code, stdout, stderr = _run_subprocess(cmd)
    if code == 0:
        if stdout and stdout.strip():
            log(f"    FFmpeg: {stdout.strip()}")
        return True
    log(f"    FFmpeg error: {stderr[:500]}")
    return False


def convert_video_file(
    source_path: str,
    target_path: str,
    target_format: str,
    ffmpeg_exe: str | None,
    log: LogFunc,
) -> bool:
    """Convert a video file to the target format."""
    if not os.path.exists(source_path):
        log(f"    Error: source file not found: {source_path}")
        return False
    if not ffmpeg_exe:
        log("    Error: FFmpeg not available")
        return False

    target_format = target_format.lower()
    codec_args = _VIDEO_PRESETS.get(target_format, ["-c", "copy"])
    cmd = [ffmpeg_exe, "-y", "-i", source_path] + codec_args + [target_path]

    log(f"    Converting: {os.path.basename(source_path)} → {target_format}")
    code, stdout, stderr = _run_subprocess(cmd)
    if code == 0:
        if stdout and stdout.strip():
            log(f"    FFmpeg: {stdout.strip()}")
        return True
    log(f"    FFmpeg error: {stderr[:500]}")
    return False


def convert_downloaded_files(
    downloaded_files: list[str],
    audio_format: str,
    video_format: str,
    ffmpeg_exe: str | None,
    log: LogFunc,
) -> list[str]:
    """Batch-convert downloaded files by audio/video format."""
    result_files: list[str] = []
    if not ffmpeg_exe:
        log("    Error: FFmpeg not available, conversion skipped")
        return []

    try:
        log(f"Preparing to convert {len(downloaded_files)} file(s)")
        converted_count = 0

        if audio_format and audio_format != "keep original":
            for file_path in downloaded_files:
                if not file_path.endswith((".m4a", ".mp4")):
                    result_files.append(file_path)
                    continue
                converted_path = os.path.splitext(file_path)[0] + f".{audio_format}"
                if os.path.exists(converted_path):
                    log(f"    Skip {os.path.basename(file_path)} (target exists)")
                    result_files.append(converted_path)
                    continue
                if convert_audio_file(file_path, converted_path, audio_format, ffmpeg_exe, log):
                    converted_count += 1
                    log(f"    Converted {os.path.basename(file_path)} → {audio_format}")
                    result_files.append(converted_path)
                    try:
                        os.remove(file_path)
                    except Exception as error:
                        log(f"    Failed to delete original {os.path.basename(file_path)}: {error}")
                else:
                    result_files.append(file_path)

        if video_format and video_format != "keep original":
            for file_path in list(result_files):
                if not file_path.endswith((".mov", ".mp4")):
                    continue
                converted_path = os.path.splitext(file_path)[0] + f".{video_format}"
                if os.path.exists(converted_path):
                    log(f"    Skip {os.path.basename(file_path)} (target exists)")
                    if file_path in result_files:
                        result_files.remove(file_path)
                    result_files.append(converted_path)
                    continue
                if convert_video_file(file_path, converted_path, video_format, ffmpeg_exe, log):
                    converted_count += 1
                    log(f"    Converted {os.path.basename(file_path)} → {video_format}")
                    if file_path in result_files:
                        result_files.remove(file_path)
                    result_files.append(converted_path)
                    try:
                        os.remove(file_path)
                    except Exception as error:
                        log(f"    Failed to delete original {os.path.basename(file_path)}: {error}")
                else:
                    if file_path not in result_files:
                        result_files.append(file_path)

        log(f"Conversion done: {converted_count} file(s) converted")
    except Exception as error:
        log(f"Conversion error: {error}")
    return result_files


def convert_directory(
    output_dir: str,
    audio_format: str | None,
    video_format: str | None,
    ffmpeg_exe: str | None,
    log: LogFunc,
) -> list[str]:
    """Scan a directory and batch-convert all audio/video files."""
    if not ffmpeg_exe:
        log("    Error: FFmpeg not available")
        return []

    exts = (".m4a", ".mp4", ".mov", ".m4v")
    files = [str(p) for ext in exts for p in Path(output_dir).rglob(f"*{ext}")]
    files = list(dict.fromkeys(files))  # deduplicate preserving order

    if not files:
        log("    No convertible files found")
        return []

    return convert_downloaded_files(
        files,
        audio_format or "keep original",
        video_format or "keep original",
        ffmpeg_exe,
        log,
    )


def convert_file_list(
    files: list[Path],
    audio_format: str | None,
    video_format: str | None,
    ffmpeg_exe: str,
    log: LogFunc,
) -> list[str]:
    """Convert files from current download task."""
    if not ffmpeg_exe or not files:
        return []

    converted: list[str] = []
    audio_exts = (".m4a", ".mp4")
    video_exts = (".mp4", ".mov", ".m4v")

    for path in files:
        if not path.exists():
            continue
        ext = path.suffix.lower()
        if audio_format and ext in audio_exts:
            log(f"    Converting {path.name} → {audio_format}")
            target = str(path.with_suffix(f".{audio_format}"))
            if convert_audio_file(str(path), target, audio_format, ffmpeg_exe, log):
                converted.append(target)
        elif video_format and ext in video_exts:
            log(f"    Converting {path.name} → {video_format}")
            target = str(path.with_suffix(f".{video_format}"))
            if convert_video_file(str(path), target, video_format, ffmpeg_exe, log):
                converted.append(target)

    log(f"Conversion complete: {len(converted)} file(s)")
    return converted
