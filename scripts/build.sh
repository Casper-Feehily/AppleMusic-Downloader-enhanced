#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# AppleMusic Downloader — PyInstaller build script
# Usage: bash scripts/build.sh <macos|macos-arm64|macos-x64|windows|linux>
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

PLATFORM="${1:-}"
if [[ -z "$PLATFORM" ]]; then
  echo "Usage: $0 <macos|macos-arm64|macos-x64|windows|linux>"
  exit 1
fi

MAC_ARCH=""
case "$PLATFORM" in
  macos|macos-arm64|macos-x64)
    case "$(uname -m)" in
      arm64) MAC_ARCH="arm64" ;;
      x86_64) MAC_ARCH="x64" ;;
      *) echo "Unsupported macOS architecture: $(uname -m)" >&2; exit 1 ;;
    esac
    if [[ "$PLATFORM" != "macos" && "$PLATFORM" != "macos-$MAC_ARCH" ]]; then
      echo "$PLATFORM requires a matching native host (got macos-$MAC_ARCH)" >&2
      exit 1
    fi
    PLATFORM="macos"
    ;;
  windows|linux) ;;
  *) echo "Unsupported platform: $PLATFORM" >&2; exit 1 ;;
esac

# On Windows (Git Bash), pwd returns MSYS path like /d/a/... which
# PyInstaller (Python) cannot resolve.  Use pwd -W for native paths.
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
  ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd -W)"
else
  ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fi
APP_NAME="AppleMusicDownloader"

echo "═══ Building $APP_NAME for $PLATFORM ═══"

# ── Step 1: Build frontend ─────────────────────────────────────
echo ">>> Building Next.js frontend..."
cd "$ROOT_DIR/src/frontend"

npm ci
npm run build

echo ">>> Frontend built:"
ls -la out/

# ── Step 2: Download bundled binaries ─────────────────────────
FFMPEG_VERSION="b6.1.1"
FFMPEG_BASE="https://github.com/eugeneware/ffmpeg-static/releases/download/$FFMPEG_VERSION"
THIRD_PARTY_DIR="$ROOT_DIR/third_party/ffmpeg"
mkdir -p "$THIRD_PARTY_DIR"

verify_sha256() {
  python -c 'import hashlib,sys; p=sys.argv[1]; expected=sys.argv[2]; actual=hashlib.sha256(open(p,"rb").read()).hexdigest(); sys.exit(0 if actual == expected else f"SHA-256 mismatch for {p}: {actual}")' "$1" "$2"
}

download_verified() {
  local url="$1" dest="$2" sha="$3"
  if [[ ! -f "$dest" ]] || ! verify_sha256 "$dest" "$sha"; then
    curl --fail --location --retry 3 --retry-all-errors "$url" -o "$dest"
  fi
  verify_sha256 "$dest" "$sha"
}

echo ">>> Downloading pinned FFmpeg $FFMPEG_VERSION for $PLATFORM..."
case "$PLATFORM" in
  macos)
    FFMPEG_ASSET="ffmpeg-darwin-$MAC_ARCH"
    FFMPEG_SOURCE="$ROOT_DIR/bin/$FFMPEG_ASSET.unsigned"
    FFMPEG_DEST="$ROOT_DIR/bin/ffmpeg"
    FFPROBE_SOURCE="$ROOT_DIR/bin/ffprobe-darwin-$MAC_ARCH.unsigned"
    FFPROBE_DEST="$ROOT_DIR/bin/ffprobe"
    LICENSE_ASSET="darwin-$MAC_ARCH.LICENSE"
    README_ASSET="darwin-$MAC_ARCH.README"
    case "$MAC_ARCH" in
      arm64)
        FFMPEG_SHA="a90e3db6a3fd35f6074b013f948b1aa45b31c6375489d39e572bea3f18336584"
        FFPROBE_SHA="bb2db6f5d8cef919da12fbf592119a987202a8c060a886f3cab091f9cab90b64"
        LICENSE_SHA="cb48bf09a11f5fb576cddb0431c8f5ed0a60157a9ec942adffc13907cbe083f2"
        README_SHA="05ba4b92c96605434b1aaae3eedf5a2c280c9607bf78ffca9a5b536d9af2dc6a"
        ;;
      x64)
        FFMPEG_SHA="ebdddc936f61e14049a2d4b549a412b8a40deeff6540e58a9f2a2da9e6b18894"
        FFPROBE_SHA="fa3add0ce901f7241abe0dfc0155d958fc834aca3f8ce61f87cc712ae669c1e0"
        LICENSE_SHA="2e1d16c72fd74e12063776371da757322f8b77589386532f4fd8634bde7de1af"
        README_SHA="e88a0325f8e5b75210355e37341824f074d3cd82def2125be54c914b62848a36"
        ;;
    esac
    ;;
  windows)
    FFMPEG_ASSET="ffmpeg-win32-x64"
    FFMPEG_DEST="$ROOT_DIR/bin/ffmpeg.exe"
    FFPROBE_SOURCE="$ROOT_DIR/bin/ffprobe.exe"
    FFPROBE_DEST="$FFPROBE_SOURCE"
    FFPROBE_SHA="3a7e2dc003dc2cd1472827e4c7c4f056ae1ae0ae7c5bbc580c99b49827351ba4"
    FFMPEG_SOURCE="$FFMPEG_DEST"
    FFMPEG_SHA="04e1307997530f9cf2fe35cba2ca7e8875ca91da02f89d6c7243df819c94ad00"
    LICENSE_ASSET="win32-x64.LICENSE"
    LICENSE_SHA="8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"
    README_ASSET="win32-x64.README"
    README_SHA="a636a7183c58006351acbaf35303c0ed85c6e1320fd4e80de453ba6157de6311"
    ;;
  *) FFMPEG_DEST=""; FFMPEG_SOURCE="" ;;
esac

BIN_DIR="$ROOT_DIR/bin"
mkdir -p "$BIN_DIR"
if [[ -n "$FFMPEG_DEST" ]]; then
  download_verified "$FFMPEG_BASE/$FFMPEG_ASSET" "$FFMPEG_SOURCE" "$FFMPEG_SHA"
  download_verified "$FFMPEG_BASE/${FFMPEG_ASSET/ffmpeg/ffprobe}" "$FFPROBE_SOURCE" "$FFPROBE_SHA"
  if [[ "$FFMPEG_SOURCE" != "$FFMPEG_DEST" ]]; then
    cp "$FFMPEG_SOURCE" "$FFMPEG_DEST"
    cp "$FFPROBE_SOURCE" "$FFPROBE_DEST"
  fi
  download_verified "$FFMPEG_BASE/$LICENSE_ASSET" "$THIRD_PARTY_DIR/LICENSE" "$LICENSE_SHA"
  download_verified "$FFMPEG_BASE/$README_ASSET" "$THIRD_PARTY_DIR/README" "$README_SHA"
  chmod +x "$FFMPEG_DEST" "$FFPROBE_DEST"
  if [[ "$PLATFORM" == "macos" ]]; then
    codesign --force --sign - "$FFMPEG_DEST"
    codesign --force --sign - "$FFPROBE_DEST"
  fi
  "$FFMPEG_DEST" -version
  "$FFPROBE_DEST" -version
fi

echo ">>> Downloading N_m3u8DL-RE for $PLATFORM..."
BINARY_FLAGS=()
if [[ -n "$FFMPEG_DEST" ]]; then
  BINARY_FLAGS+=("bin/${FFMPEG_DEST##*/}" "bin/${FFPROBE_DEST##*/}")
fi

# Temporarily disable set -e so a failed download doesn't kill the build.
# The binary will be auto-downloaded at runtime via dependency_manager.py.
set +e
set +o pipefail

case "$PLATFORM" in
  windows)
    DEST="$BIN_DIR/N_m3u8DL-RE.exe"
    if [[ ! -f "$DEST" ]]; then
      curl -#fL "https://pub-e4955324bbd043d79465a5231bec51f6.r2.dev/N_m3u8DL-RE.exe" -o "$DEST" 2>&1
    fi
    if [[ -f "$DEST" ]]; then
      BINARY_FLAGS+=("bin/N_m3u8DL-RE.exe")
    fi
    ;;
  macos)
    DEST="$BIN_DIR/N_m3u8DL-RE-$MAC_ARCH"
    if [[ ! -f "$DEST" ]]; then
      N_M3U8_TMP=$(mktemp -d)
      if [[ -d "$N_M3U8_TMP" ]]; then
        GHLATEST=$(curl -fsSL https://api.github.com/repos/nilaoda/N_m3u8DL-RE/releases/latest)
        GHURL=$(echo "$GHLATEST" | grep browser_download_url | grep -F "macos-$MAC_ARCH.tar.gz" | head -1 | cut -d'"' -f4)
        if [[ -n "$GHURL" ]] && curl -#fL "$GHURL" -o "$N_M3U8_TMP/download.tar.gz"; then
          if tar xzf "$N_M3U8_TMP/download.tar.gz" -C "$N_M3U8_TMP"; then
            N_M3U8_BINARY=$(find "$N_M3U8_TMP" -type f -name N_m3u8DL-RE -print -quit)
            if [[ -n "$N_M3U8_BINARY" ]]; then
              mv "$N_M3U8_BINARY" "$DEST"
            fi
          fi
        fi
        rm -rf "$N_M3U8_TMP"
      fi
    fi
    if [[ -f "$DEST" ]] && cp "$DEST" "$BIN_DIR/N_m3u8DL-RE"; then
      chmod +x "$BIN_DIR/N_m3u8DL-RE"
      BINARY_FLAGS+=("bin/N_m3u8DL-RE")
    fi
    ;;
  linux)
    DEST="$BIN_DIR/N_m3u8DL-RE"
    if [[ ! -f "$DEST" ]]; then
      GHLATEST=$(curl -sL https://api.github.com/repos/nilaoda/N_m3u8DL-RE/releases/latest)
      GHURL=$(echo "$GHLATEST" | grep browser_download_url | grep 'linux-x64.tar.gz' | head -1 | cut -d'"' -f4)
      if [[ -n "$GHURL" ]]; then
        curl -#fL "$GHURL" -o "/tmp/N_m3u8DL-RE.tar.gz" 2>&1
      fi
      if [[ -f "/tmp/N_m3u8DL-RE.tar.gz" ]]; then
        tar xzf "/tmp/N_m3u8DL-RE.tar.gz" -C "$BIN_DIR" 2>/dev/null
        rm -f "/tmp/N_m3u8DL-RE.tar.gz"
        # GitHub tar is N_m3u8DL-RE/N_m3u8DL-RE (subdir) — move file up
        if [[ -f "$BIN_DIR/N_m3u8DL-RE/N_m3u8DL-RE" ]]; then
          mv "$BIN_DIR/N_m3u8DL-RE/N_m3u8DL-RE" "$DEST"
          rmdir "$BIN_DIR/N_m3u8DL-RE" 2>/dev/null || true
        fi
      fi
    fi
    if [[ -f "$DEST" ]]; then
      chmod +x "$DEST"
      BINARY_FLAGS+=("bin/N_m3u8DL-RE")
    fi
    ;;
esac

set -o pipefail
set -e

if [[ " ${BINARY_FLAGS[*]} " == *" bin/N_m3u8DL-RE"* ]]; then
  echo ">>> N_m3u8DL-RE bundled: $(ls -lh "$BIN_DIR"/N_m3u8DL-RE* 2>/dev/null | awk '{print $5, $NF}')"
else
  echo ">>> N_m3u8DL-RE skipped (will be auto-downloaded at runtime)"
fi

# ── Step 3: Install Python deps + PyInstaller ──────────────────
echo ">>> Installing Python dependencies..."
cd "$ROOT_DIR"
python -m pip install --upgrade pip
python -m pip install -e ".[desktop]"
python -m pip install pyinstaller

# ── Step 4: Run PyInstaller ────────────────────────────────────
echo ">>> Running PyInstaller..."

# PyInstaller --add-data / --add-binary separator is platform-dependent:
#   Unix: ":"   Windows: ";"
if [[ "$PLATFORM" == "windows" ]]; then
  SEP=";"
else
  SEP=":"
fi

PYI_ARGS=(
  --name "$APP_NAME"
  --add-data "src/frontend/out${SEP}frontend_out"
  --add-data "LICENSE${SEP}."
  --add-data "icon.ico${SEP}."
  --add-data "icon.png${SEP}."
  --add-data "icon.icns${SEP}."
  --add-data "third_party/ffmpeg${SEP}third_party/ffmpeg"
  --collect-all gamdl
  --collect-binaries gamdl
  --collect-all yt_dlp
  --collect-all pywebview
  --hidden-import amdl.dependency_manager
  --hidden-import amdl.wrapper_setup
  --hidden-import gamdl._ammuxer
  --hidden-import gamdl.api.apple_music
  --hidden-import gamdl.downloader.amdecrypt
  --hidden-import gamdl.downloader.song
  --clean
  --noconfirm
)

for binary in "${BINARY_FLAGS[@]}"; do
  PYI_ARGS+=(--add-binary "${binary}${SEP}bin")
done

# Platform-specific flags
case "$PLATFORM" in
  macos)
    # Leaving the identity unset uses ad-hoc signing without hardened runtime.
    # An explicit '-' enables hardened runtime and prevents Python from loading.
    PYI_ARGS+=(--windowed --onedir)
    # macOS code signing identity (optional, set via env)
    if [[ -n "${APPLE_SIGN_IDENTITY:-}" ]]; then
      PYI_ARGS+=(--codesign-identity "$APPLE_SIGN_IDENTITY")
    fi
    # macOS notarization (optional)
    if [[ -n "${APPLE_NOTARIZATION_TEAM:-}" ]]; then
      PYI_ARGS+=(--osx-notarization-team-id "$APPLE_NOTARIZATION_TEAM")
    fi
    ;;
  windows)
    PYI_ARGS+=(--windowed --onedir --contents-directory .)
    if [[ -f "icon.ico" ]]; then
      PYI_ARGS+=(--icon "icon.ico")
    fi
    ;;
  linux)
    PYI_ARGS+=(--onefile)
    if [[ -f "icon.png" ]]; then
      PYI_ARGS+=(--icon "icon.png")
    fi
    ;;
esac

# On Windows (Git Bash/MSYS), disable automatic POSIX→Windows path conversion
# for arguments passed to native Windows programs (pyinstaller.exe).
# Without this, MSYS mangles paths like D:/a/... into \d\a\... (invalid).
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
  export MSYS_NO_PATHCONV=1
fi

pyinstaller "${PYI_ARGS[@]}" src/amdl/desktop_entry.py

# ── Step 5: Collect output ─────────────────────────────────────
echo ">>> Build complete! Output:"
DIST_DIR="$ROOT_DIR/dist"
mkdir -p "$DIST_DIR"

case "$PLATFORM" in
  macos)
    # onedir --windowed 产出 dist/AppleMusicDownloader.app
    SRC_BUNDLE="$ROOT_DIR/dist/$APP_NAME.app"
    DST_BUNDLE="$DIST_DIR/${APP_NAME}-macos-${MAC_ARCH}.app"
    DMG_PATH="$DIST_DIR/${APP_NAME}-macos-${MAC_ARCH}.dmg"
    if [[ -d "$SRC_BUNDLE" ]]; then
      rm -rf "$DST_BUNDLE"
      mv "$SRC_BUNDLE" "$DST_BUNDLE"
      plist="$DST_BUNDLE/Contents/Info.plist"
      APP_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT_DIR/src/amdl/__init__.py")"
      /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $APP_VERSION" "$plist" 2>/dev/null ||
        /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $APP_VERSION" "$plist"
      /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $APP_VERSION" "$plist" 2>/dev/null ||
        /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $APP_VERSION" "$plist"
      # 设置 macOS 图标（.icns）
      if [[ -f "$ROOT_DIR/icon.icns" ]]; then
        cp "$ROOT_DIR/icon.icns" "$DST_BUNDLE/Contents/Resources/"
        /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile icon.icns" "$plist" 2>/dev/null || true
      fi
      # 清除框架签名冲突（Anaconda Python 自带 Team ID 跟 ad-hoc 冲突）
      if [[ -d "$DST_BUNDLE/Contents/Frameworks/Python.framework" ]]; then
        codesign --remove-signature "$DST_BUNDLE/Contents/Frameworks/Python.framework" 2>/dev/null || true
      fi
      # 统一 ad-hoc 重签整个 .app
      codesign --deep --force --sign - "$DST_BUNDLE" 2>/dev/null || true
    fi
    # Create DMG for distribution
    if command -v hdiutil &>/dev/null && [[ -d "$DST_BUNDLE" ]]; then
      hdiutil create -volname "$APP_NAME" -srcfolder "$DST_BUNDLE" -ov -format UDZO "$DMG_PATH"
      echo "DMG created: $DMG_PATH"
    fi
    ;;
  windows)
    APP_DIR="$ROOT_DIR/dist/$APP_NAME"
    if [[ -d "$APP_DIR" ]]; then
      mv "$APP_DIR" "$DIST_DIR/${APP_NAME}-windows-x64"
    fi
    ;;
  linux)
    BIN_PATH="$ROOT_DIR/dist/$APP_NAME"
    if [[ -f "$BIN_PATH" ]]; then
      mv "$BIN_PATH" "$DIST_DIR/${APP_NAME}-linux-x64"
    fi
    ;;
esac

echo "═══ Done! Artifacts in $DIST_DIR ═══"
ls -lh "$DIST_DIR"
