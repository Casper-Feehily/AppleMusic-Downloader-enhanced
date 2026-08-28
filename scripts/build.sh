#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# AppleMusic Downloader — PyInstaller build script
# Usage: bash scripts/build.sh <macos|windows|linux>
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

PLATFORM="${1:-}"
if [[ -z "$PLATFORM" ]]; then
  echo "Usage: $0 <macos|windows|linux>"
  exit 1
fi

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
cd "$ROOT_DIR/src/fronted"

# Copy next.config.ts temporarily without rewrites for export build
cp next.config.ts next.config.ts.bak

npm ci
npm run build

# Restore original next.config.ts (keeps rewrites for dev mode)
mv next.config.ts.bak next.config.ts

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
    FFMPEG_ASSET="ffmpeg-darwin-arm64"
    FFMPEG_SOURCE="$ROOT_DIR/bin/ffmpeg-darwin-arm64.unsigned"
    FFMPEG_DEST="$ROOT_DIR/bin/ffmpeg"
    FFMPEG_SHA="a90e3db6a3fd35f6074b013f948b1aa45b31c6375489d39e572bea3f18336584"
    LICENSE_ASSET="darwin-arm64.LICENSE"
    LICENSE_SHA="cb48bf09a11f5fb576cddb0431c8f5ed0a60157a9ec942adffc13907cbe083f2"
    README_ASSET="darwin-arm64.README"
    README_SHA="05ba4b92c96605434b1aaae3eedf5a2c280c9607bf78ffca9a5b536d9af2dc6a"
    ;;
  windows)
    FFMPEG_ASSET="ffmpeg-win32-x64"
    FFMPEG_DEST="$ROOT_DIR/bin/ffmpeg.exe"
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
  if [[ "$FFMPEG_SOURCE" != "$FFMPEG_DEST" ]]; then
    cp "$FFMPEG_SOURCE" "$FFMPEG_DEST"
  fi
  download_verified "$FFMPEG_BASE/$LICENSE_ASSET" "$THIRD_PARTY_DIR/LICENSE" "$LICENSE_SHA"
  download_verified "$FFMPEG_BASE/$README_ASSET" "$THIRD_PARTY_DIR/README" "$README_SHA"
  chmod +x "$FFMPEG_DEST"
  if [[ "$PLATFORM" == "macos" ]]; then
    codesign --force --sign - "$FFMPEG_DEST"
  fi
  "$FFMPEG_DEST" -version
fi

echo ">>> Downloading N_m3u8DL-RE for $PLATFORM..."
BINARY_FLAGS=()
if [[ -n "$FFMPEG_DEST" ]]; then
  BINARY_FLAGS+=("bin/${FFMPEG_DEST##*/}")
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
    DEST="$BIN_DIR/N_m3u8DL-RE"
    if [[ ! -f "$DEST" ]]; then
      # R2 only has Windows exe — macOS goes straight to GitHub
      GHLATEST=$(curl -sL https://api.github.com/repos/nilaoda/N_m3u8DL-RE/releases/latest)
      GHURL=$(echo "$GHLATEST" | grep browser_download_url | grep -E 'macos-(arm64|x64)\.tar\.gz' | head -1 | cut -d'"' -f4)
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

if [[ ${#BINARY_FLAGS[@]} -gt 1 ]] || [[ ${#BINARY_FLAGS[@]} -eq 1 && "${BINARY_FLAGS[0]}" == *N_m3u8DL* ]]; then
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
  --add-data "src/fronted/out${SEP}frontend_out"
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
    PYI_ARGS+=(--windowed --onedir --codesign-identity "-")
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
    DST_BUNDLE="$DIST_DIR/${APP_NAME}-macos-arm64.app"
    DMG_PATH="$DIST_DIR/${APP_NAME}-macos-arm64.dmg"
    if [[ -d "$SRC_BUNDLE" ]]; then
      rm -rf "$DST_BUNDLE"
      mv "$SRC_BUNDLE" "$DST_BUNDLE"
      # 设置 macOS 图标（.icns）
      if [[ -f "$ROOT_DIR/icon.icns" ]]; then
        cp "$ROOT_DIR/icon.icns" "$DST_BUNDLE/Contents/Resources/"
        plist="$DST_BUNDLE/Contents/Info.plist"
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
