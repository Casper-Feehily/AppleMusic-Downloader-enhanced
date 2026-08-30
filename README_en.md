# AppleMusic Downloader

![GitHub Downloads (all assets, all releases)](https://img.shields.io/github/downloads/Casper-Feehily/AppleMusic-Downloader-enhanced/total?style=social&logo=GitHub)

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](https://github.com/Casper-Feehily/AppleMusic-Downloader-enhanced)
![GitHub License](https://img.shields.io/github/license/Casper-Feehily/AppleMusic-Downloader-enhanced?style=social)

- [Chinese README](README.md)

---

AppleMusic Downloader downloads Apple Music songs, music videos, lyrics, and cover art. It can also download Apple Music's ALAC lossless source through a local wrapper-v2.

> This independently maintained project is based on [wenfeng110402/AppleMusic-Downloader](https://github.com/wenfeng110402/AppleMusic-Downloader).

The project offers two usage modes:

| Mode | Use Case |
|------|----------|
| **CLI** | Terminal users, install via `pip install applemusic-dl` |
| **Desktop App** | General users, download the packaged installer and run |

---

## Acknowledgments

This project is based on [wenfeng110402/AppleMusic-Downloader](https://github.com/wenfeng110402/AppleMusic-Downloader) and uses code from [gamdl (Glomatico's Apple Music Downloader)](https://github.com/glomatico/gamdl) and [yt-dlp](https://github.com/yt-dlp/yt-dlp). Thanks to the upstream project and all dependency contributors.

---

## Table of Contents

- [Installation](#installation)
  - [Method 1: pip install (recommended)](#method-1-pip-install-recommended)
  - [Method 2: Desktop installers](#method-2-desktop-installers)
  - [Method 3: From source](#method-3-from-source)
- [Download ALAC Lossless: Quick Start](#download-alac-lossless-quick-start)
  - [Get the Correct Apple Music APK](#get-the-correct-apple-music-apk)
  - [Deploy a Local wrapper-v2](#deploy-a-local-wrapper-v2)
  - [Sign In and Download](#sign-in-and-download)
- [CLI Usage](#cli-usage)
- [Developer API](#developer-api)
- [Desktop App](#desktop-app)
- [Requirements](#requirements)
- [Supported Link Types](#supported-link-types)
- [Project Structure](#project-structure)
- [Disclaimer](#disclaimer)

---

## Installation

### Method 1: pip install (recommended)

```bash
pip install applemusic-dl
```

Use the `amdl` command directly after installation:

```bash
amdl --help
```

For desktop GUI mode, install with desktop dependencies:

```bash
pip install "applemusic-dl[desktop]"
```

### Method 2: Desktop installers

Download the installer for your system from the [Releases](https://github.com/Casper-Feehily/AppleMusic-Downloader-enhanced/releases) page:

- Windows: run `AppleMusicDownloader-Setup-windows-x64.exe`
- Apple Silicon Mac: open `AppleMusicDownloader-macos-arm64.dmg` and drag the app into Applications

Windows and Apple Silicon macOS installers bundle FFmpeg, so no first-launch download is required. The binary comes from pinned [eugeneware/ffmpeg-static b6.1.1](https://github.com/eugeneware/ffmpeg-static/releases/tag/b6.1.1); license and build information are included under `third_party/ffmpeg/`. See [FFmpeg Legal](https://ffmpeg.org/legal.html) for source and distribution details.

### Method 3: From source

```bash
git clone https://github.com/Casper-Feehily/AppleMusic-Downloader-enhanced.git
cd AppleMusic-Downloader
pip install -e ".[desktop]"
```

---

## Download ALAC Lossless: Quick Start

ALAC is a lossless source codec served by Apple Music; it is not created by converting AAC to FLAC. It requires a local [wrapper-v2](https://github.com/glomatico/wrapper-v2); Cookies mode downloads AAC only.

You need an active Apple Music subscription, Docker, FFmpeg, and a local wrapper-v2. In the desktop app, select **Wrapper v2** and **ALAC Lossless**. Wrapper HTTP and decrypt addresses are restricted to `localhost`, `127.0.0.1`, or `::1`.

### Get the Correct Apple Music APK

wrapper-v2 upstream currently validates **Apple Music for Android 3.6.0-beta, build 1109**. Obtain this `.apk` or `.apkm` legally yourself; neither this project nor wrapper-v2 provides, links to, or distributes APKs or Apple native libraries.

The APK architecture must match the wrapper build target: use `x86_64` for Intel / AMD 64-bit computers, and `arm64-v8a` for Apple Silicon, Linux ARM, or Windows on ARM. Follow the [wrapper-v2 setup guide](https://github.com/glomatico/wrapper-v2#one-time-setup).

### Deploy a Local wrapper-v2

The Apple Silicon macOS desktop app can do this from **Settings → Local Wrapper Setup**. Install Docker Desktop first and place a compatible APKM in `~/Downloads`. The app extracts and verifies Apple libraries locally and never uploads or retains the original APKM. The first Windows release still uses the manual steps below.

Clone wrapper-v2, then run only the command set matching your architecture.

```bash
git clone https://github.com/glomatico/wrapper-v2.git
cd wrapper-v2
```

#### Apple Silicon / Linux ARM / Windows on ARM

```bash
bash tools/extract-libs.sh --bundle /absolute/path/apple-music.apkm --arch arm64-v8a
bash tools/stage-system.sh --arch arm64-v8a
TARGET_ARCH=arm64-v8a RUNTIME_PLATFORM=linux/arm64 docker compose up --build -d
```

#### Intel / AMD 64-bit (including typical Windows PCs)

```bash
bash tools/extract-libs.sh --bundle /absolute/path/apple-music.apkm --arch x86_64
bash tools/stage-system.sh --arch x86_64
docker compose up --build -d
```

On any platform, check the local service:

```bash
curl http://127.0.0.1/health
```

`runtime.playback_ready` in the `/health` response must be `true` before ALAC downloads work. Do not post `/me` output publicly. If the build, architecture, or APK version does not match, follow the [wrapper-v2 local-build guide](https://github.com/glomatico/wrapper-v2#local-build); do not substitute an arbitrary current Apple Music APK.

#### Windows: run the scripts in WSL2

`tools/*.sh` are Bash scripts and should not be run directly in PowerShell. Install Docker Desktop and enable **WSL Integration**, then install Ubuntu from Windows PowerShell:

```powershell
wsl --install -d Ubuntu
```

After restarting, open Ubuntu. Keep the APK/APKM in a Windows folder and reference it in WSL using `/mnt/c/...`. For a typical Intel/AMD Windows computer:

```bash
cd ~/wrapper-v2
bash tools/extract-libs.sh \
  --bundle /mnt/c/Users/your-name/Downloads/apple-music.apkm --arch x86_64
bash tools/stage-system.sh --arch x86_64
docker compose up --build -d
```

In the Windows AppleMusic Downloader desktop app, use `http://127.0.0.1`, decrypt host `127.0.0.1`, and port `10020`; Docker Desktop exposes the wrapper port to Windows. Windows CLI users use the same wrapper address and port, as long as the wrapper-v2 service in WSL2 is exposed to Windows.

### Sign In and Download

1. Start the desktop app. In **Account Settings**, select **Wrapper v2** and use `http://127.0.0.1`, decrypt host `127.0.0.1`, and port `10020`.
2. Check status, then sign in with your Apple ID and complete 2FA on the same page.
3. On the download page, choose **ALAC Lossless** as the source quality and submit an Apple Music link.

Passwords and 2FA codes are used for a single login request only, and are not written to settings or task logs. Successful ALAC tracks keep the `.m4a` container without re-encoding; unavailable ALAC falls back to AAC and reports the actual codec in the task log. Post-download FLAC or MP3 options only re-encode audio and cannot turn AAC into true lossless audio.

Verify a completed download with `ffprobe`:

```bash
ffprobe -v error -select_streams a:0 \
  -show_entries stream=codec_name -of default=nw=1 downloaded-file.m4a
# Expected: codec_name=alac
```

The CLI can use wrapper too:

```bash
amdl --use-wrapper \
  --wrapper-url http://127.0.0.1 \
  --wrapper-decrypt-host 127.0.0.1 \
  --wrapper-decrypt-port 10020 \
  --song-codec-priority alac,aac \
  "https://music.apple.com/..."
```

---

## CLI Usage

```bash
# View help
amdl --help

# Download a single track (Cookies mode downloads AAC)
amdl -c /path/to/cookies.txt "https://music.apple.com/us/album/left-and-right/1630451412?i=1630451413"

# Download an entire album
amdl -c /path/to/cookies.txt "https://music.apple.com/us/album/left-and-right/1630451412"

# Download a complete playlist
amdl -c /path/to/cookies.txt \
  "https://music.apple.com/us/playlist/playlist-name/pl.1234567890abcdef1234567890abcdef"

# Specify output directory
amdl -c /path/to/cookies.txt -o "./My Music" "https://music.apple.com/..."
```

---

## Developer API

This README is for download users. API endpoints, deployment, and examples are in [docs/api.md](docs/api.md).

---

## Desktop App

In desktop mode, the backend service and frontend Web UI are integrated in a single window:

```bash
# Launch desktop app
python -m amdl --desktop

# Or simply launch (auto-detect)
python -m amdl
```

The desktop app is built on pywebview and works on Windows, macOS, and Linux.

> **🐧 Linux users**: pywebview on Linux requires Qt WebEngine. Before launching desktop mode, install the system dependencies:
> ```bash
> sudo apt update && sudo apt install -y python3-pyqt5 python3-pyqt5.qtwebengine libqt5webkit5-dev
> pip install pywebview[qt]
> ```














> **🍎 macOS users**: Files downloaded from Releases are flagged with a quarantine attribute by macOS. Remove it before first launch:
>
> **.app (Desktop app)**:
> ```bash
> sudo xattr -cr /Applications/AppleMusicDownloader.app
> ```
> Or right-click → Open (instead of double-clicking), then click "Open" in the dialog.
>
> **CLI binary**:
> ```bash
> chmod +x ./AppleMusicDownloader
> sudo xattr -cr ./AppleMusicDownloader
> ```

> **🪟 Windows users**: A console window will appear alongside the desktop app to display runtime logs. **Do not close this console window**, or the application will stop working.

## Requirements

### Required

- Python 3.10 or higher (Python 3.11+ recommended; Python 3.10 is deprecated by some dependencies)
- A valid Apple Music subscription
- A local wrapper-v2 or a Netscape-format cookies file
- FFmpeg (bundled in the Windows and Apple Silicon macOS desktop installers; install it yourself for CLI or source use)

**Obtaining a cookies file:**

- Firefox users: Use the [Export Cookies](https://addons.mozilla.org/firefox/addon/export-cookies-txt/) extension
- Chromium users: Use the [Open Cookies.txt](https://chromewebstore.google.com/detail/open-cookiestxt/gdocmgbfkjnnpapoeobnolbbkoibbcif) extension

**Installing FFmpeg for CLI or source use:**

- macOS: `brew install ffmpeg`
- Linux: `apt install ffmpeg` / `pacman -S ffmpeg`
- Windows: Download from [ffmpeg.org](https://ffmpeg.org/)

### Optional

- [MP4Box](https://gpac.io/downloads/gpac-nightly-builds/): Alternative remux mode
- [N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE/releases/latest): Alternative download mode

---

## Supported Link Types

- Singles
- Albums
- Playlists
- Music Videos
- Artist Profiles
- Post Videos

> **💡 About codec selection**: When selecting Atmos or AC3 in the settings, the downloader will automatically fall back to AAC stereo if the track is not available in the selected format. Not all songs have Atmos/AC3 versions (typically only songs released after 2021). Only tracks marked with "Dolby Atmos" in Apple Music support Atmos downloads.

---

## Project Structure

```
AppleMusic-Downloader/
├── src/
│   ├── amdl/              # Python backend package
│   │   ├── server.py      # FastAPI server entry point
│   │   ├── cli.py         # CLI entry point
│   │   ├── core_downloader.py  # Core download logic
│   │   ├── task_manager.py     # Task queue management
│   │   ├── converter.py        # Format conversion
│   │   └── ...
│   └── fronted/           # Next.js frontend
│       ├── app/
│       │   ├── components/  # Frontend components
│       │   ├── service.tsx  # API client wrapper
│       │   └── i18n.tsx     # Internationalization
│       └── next.config.ts
├── docs/
│   └── api.md             # API documentation
├── pyproject.toml          # Package configuration
├── requirements.txt
└── README.md
```

---

## Disclaimer

This tool is for educational and research purposes only. Any use that violates laws or infringes on the rights of others is strictly prohibited.

1. This project does not directly provide or store any copyrighted content. Users must independently provide valid credentials (e.g., a valid Apple Music subscription and cookies file) to use its features.
2. The development team assumes no responsibility for how users use this tool. Any legal or copyright disputes arising from its use are the sole responsibility of the user.
3. This project is implemented based on code from [gamdl](https://github.com/glomatico/gamdl) and [yt-dlp](https://github.com/yt-dlp/yt-dlp) and is not directly affiliated with the original projects' authors. If there are any objections, please contact us for assistance.
4. Users must ensure compliance with local laws and regulations when using this tool.

By using this tool, you agree to comply with all applicable laws and assume full responsibility for your actions.
