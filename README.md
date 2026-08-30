# AppleMusic Downloader

![GitHub Downloads (all assets, all releases)](https://img.shields.io/github/downloads/Casper-Feehily/AppleMusic-Downloader-enhanced/total?style=social&logo=GitHub)

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![Platform](<https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey>)](https://github.com/Casper-Feehily/AppleMusic-Downloader-enhanced)
![GitHub License](https://img.shields.io/github/license/Casper-Feehily/AppleMusic-Downloader-enhanced?style=social)

- [English README](README_en.md)

---

AppleMusic Downloader 是一个 Apple Music 下载工具，支持歌曲、音乐视频、歌词和封面下载，也可通过本机 wrapper-v2 下载 Apple Music 提供的 ALAC 无损音频。

> 本仓库为独立维护的项目，基于 [wenfeng110402/AppleMusic-Downloader](https://github.com/wenfeng110402/AppleMusic-Downloader) 开发。

项目提供两种使用方式：

| 方式                 | 适用场景                                                 |
| -------------------- | -------------------------------------------------------- |
| **CLI 命令行** | 终端用户，通过`pip install applemusic-dl` 安装即可使用 |
| **桌面应用**   | 普通用户，下载打包好的安装程序直接使用                   |

---

## 致谢

本项目基于 [wenfeng110402/AppleMusic-Downloader](https://github.com/wenfeng110402/AppleMusic-Downloader) 开发，并使用了 [gamdl (Glomatico&#39;s Apple Music Downloader)](https://github.com/glomatico/gamdl) 和 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 的代码。感谢原项目及所有依赖项目的贡献者。

---

## 目录

- [安装方式](#安装方式)
  - [方式一：pip 安装（推荐）](#方式一pip-安装推荐)
  - [方式二：桌面安装包](#方式二桌面安装包)
  - [方式三：从源码运行](#方式三从源码运行)
- [下载 ALAC 无损：快速开始](#下载-alac-无损快速开始)
  - [准备正确的 Apple Music APK](#准备正确的-apple-music-apk)
  - [部署本机 wrapper-v2](#部署本机-wrapper-v2)
  - [在本项目中登录并下载](#在本项目中登录并下载)
- [CLI 命令行使用](#cli-命令行使用)
- [开发者 API](#开发者-api)
- [桌面应用](#桌面应用)
- [环境要求](#环境要求)
- [支持的链接类型](#支持的链接类型)
- [项目结构](#项目结构)
- [免责声明](#免责声明)

---

## 安装方式

### 方式一：pip 安装（推荐）

```bash
pip install applemusic-dl
```

安装后可直接使用 `amdl` 命令：

```bash
amdl --help
```

如果需要桌面 GUI 模式，请安装带桌面依赖的版本：

```bash
pip install "applemusic-dl[desktop]"
```

### 方式二：桌面安装包

从 [Releases](https://github.com/Casper-Feehily/AppleMusic-Downloader-enhanced/releases) 页面下载对应系统的安装包：

- Windows：运行 `AppleMusicDownloader-Setup-windows-x64.exe`
- Apple Silicon Mac：打开 `AppleMusicDownloader-macos-arm64.dmg`，将应用拖入“应用程序”

Windows 与 Apple Silicon macOS 安装包已内置 FFmpeg，无需首次启动下载。二进制来自固定的 [eugeneware/ffmpeg-static b6.1.1](https://github.com/eugeneware/ffmpeg-static/releases/tag/b6.1.1)；许可证与构建说明位于安装包的 `third_party/ffmpeg/`，源码与分发说明见 [FFmpeg Legal](https://ffmpeg.org/legal.html)。

### 方式三：从源码运行

```bash
git clone https://github.com/Casper-Feehily/AppleMusic-Downloader-enhanced.git
cd AppleMusic-Downloader
pip install -e ".[desktop]"
```

---

## 下载 ALAC 无损：快速开始

ALAC 不是“转换成 FLAC”得到的格式，而是 Apple Music 直接提供的无损源。它必须使用本机 [wrapper-v2](https://github.com/glomatico/wrapper-v2)；Cookies 模式只能下载 AAC。

需要准备：有效的 Apple Music 订阅、Docker、FFmpeg，以及一个本机 wrapper-v2。Windows/macOS/Linux 的桌面端都在下载页选择 **Wrapper v2** 和 **ALAC 无损** 即可；wrapper 的 HTTP 地址、解密地址只允许 `localhost`、`127.0.0.1` 或 `::1`。

### 准备正确的 Apple Music APK

wrapper-v2 上游当前验证的版本是 **Apple Music for Android 3.6.0-beta，build 1109**。请自行合法取得该版本的 `.apk` 或 `.apkm`；本项目和 wrapper-v2 都不提供、链接或分发 APK 与 Apple 原生库。

APK 内的架构必须和 wrapper 的构建目标一致，不能混用：Intel / AMD 64 位电脑用 `x86_64`，Apple Silicon、Linux ARM 或 Windows on ARM 用 `arm64-v8a`。请以 [wrapper-v2 的安装说明](https://github.com/glomatico/wrapper-v2#one-time-setup) 为准。

### 部署本机 wrapper-v2

Apple Silicon macOS 桌面版可在“设置 → 本机 Wrapper 配置”中一键完成：提前安装 Docker Desktop，并把兼容的 APKM 放入 `~/Downloads`。程序只在本机提取并校验 Apple 库，不上传或保留原始 APKM。Windows 第一版仍按下面的手动流程配置。

先克隆 wrapper-v2；下面两套命令只选与设备架构匹配的一套执行。

```bash
git clone https://github.com/glomatico/wrapper-v2.git
cd wrapper-v2
```

#### Apple Silicon / Linux ARM / Windows on ARM

```bash
bash tools/extract-libs.sh --bundle /绝对路径/apple-music.apkm --arch arm64-v8a
bash tools/stage-system.sh --arch arm64-v8a
TARGET_ARCH=arm64-v8a RUNTIME_PLATFORM=linux/arm64 docker compose up --build -d
```

#### Intel / AMD 64 位（含普通 Windows 电脑）

```bash
bash tools/extract-libs.sh --bundle /绝对路径/apple-music.apkm --arch x86_64
bash tools/stage-system.sh --arch x86_64
docker compose up --build -d
```

任一平台启动后都检查本机服务：

```bash
curl http://127.0.0.1/health
```

`/health` 返回的 `runtime.playback_ready` 必须为 `true`，才能下载 ALAC。请勿将 `/me` 的返回内容贴到公开位置。若构建、架构或 APK 版本不匹配，请回到 [wrapper-v2 文档](https://github.com/glomatico/wrapper-v2#local-build) 逐项核对，不要尝试用任意最新版 Apple Music APK 代替。

#### Windows 用户：在 WSL2 中执行上述脚本

`tools/*.sh` 是 Bash 脚本，不应直接在 PowerShell 中运行。请安装 Docker Desktop 并开启 **WSL Integration**，然后在 Windows PowerShell 中安装 Ubuntu：

```powershell
wsl --install -d Ubuntu
```

重启并打开 Ubuntu 后，将 APK/APKM 放在 Windows 目录，在 WSL 中以 `/mnt/c/...` 路径引用。例如普通 Intel/AMD Windows：

```bash
cd ~/wrapper-v2
bash tools/extract-libs.sh \
  --bundle /mnt/c/Users/你的用户名/Downloads/apple-music.apkm --arch x86_64
bash tools/stage-system.sh --arch x86_64
docker compose up --build -d
```

Windows 桌面版 AppleMusic Downloader 仍填写 `http://127.0.0.1`、解密主机 `127.0.0.1` 和端口 `10020`；Docker Desktop 会将端口提供给 Windows 本机。Windows CLI 用户也使用同样的 wrapper 地址和端口，前提是 WSL2 中的 wrapper-v2 已暴露到 Windows 本机。

### 在本项目中登录并下载

1. 启动桌面应用，进入 **Account Settings**，选择 **Wrapper v2**，填写默认地址 `http://127.0.0.1`、解密主机 `127.0.0.1` 和端口 `10020`。
2. 点击状态检测；服务已就绪后，在同一页完成 Apple ID 登录和 2FA。
3. 回到下载页，将“源音质”选为 **ALAC 无损**，提交 Apple Music 链接。

密码和验证码只用于一次登录请求，不会写入设置或任务日志。ALAC 曲目会保留 `.m4a` 容器且不二次转码；未提供 ALAC 的曲目会自动回退 AAC，并在任务日志中写明实际 codec。“下载后转换”里的 FLAC、MP3 等只是重新编码，不能把 AAC 变成真正无损。

用 `ffprobe` 确认结果：

```bash
ffprobe -v error -select_streams a:0 \
  -show_entries stream=codec_name -of default=nw=1 下载的文件.m4a
# 预期：codec_name=alac
```

CLI 也可使用 wrapper：

```bash
amdl --use-wrapper \
  --wrapper-url http://127.0.0.1 \
  --wrapper-decrypt-host 127.0.0.1 \
  --wrapper-decrypt-port 10020 \
  --song-codec-priority alac,aac \
  "https://music.apple.com/..."
```

---

## CLI 命令行使用

```bash
# 查看帮助
amdl --help

# 下载单曲（Cookies 模式下载 AAC）
amdl -c /path/to/cookies.txt "https://music.apple.com/cn/album/left-and-right/1630451412?i=1630451413"

# 下载整张专辑
amdl -c /path/to/cookies.txt "https://music.apple.com/cn/album/left-and-right/1630451412"

# 下载完整播放列表（自动展开并下载全部可用歌曲）
amdl -c /path/to/cookies.txt \
  "https://music.apple.com/cn/playlist/playlist-name/pl.1234567890abcdef1234567890abcdef"

# 指定输出目录
amdl -c /path/to/cookies.txt -o "./My Music" "https://music.apple.com/..."
```

---

## 开发者 API

README 保留面向下载使用者的说明；接口、部署和调用示例见 [docs/api.md](docs/api.md)。

---

## 桌面应用

在桌面模式下，后端服务和前端 Web UI 集成在同一个窗口中：

```bash
# 启动桌面应用
python -m amdl --desktop

# 或直接启动（自动检测）
python -m amdl
```

桌面应用基于 pywebview，在 Windows、macOS、Linux 上均可用。

> **🐧 Linux 用户请注意**：pywebview 在 Linux 上依赖 Qt WebEngine，启动桌面模式前需要先安装系统依赖：
>
> ```bash
> sudo apt update && sudo apt install -y python3-pyqt5 python3-pyqt5.qtwebengine libqt5webkit5-dev
> pip install pywebview[qt]
> ```

> **🍎 macOS 用户请注意**：从 Releases 下载的文件会被 macOS 添上隔离标记（quarantine flag），首次运行前需要解除：
>
> **.app 桌面应用**：
> ```bash
> sudo xattr -cr /Applications/AppleMusicDownloader.app
> ```
> 或者右键 → 打开（而非双击），在弹出的对话框中点击「打开」。
>
> **CLI 命令行二进制**：
> ```bash
> chmod +x ./AppleMusicDownloader
> sudo xattr -cr ./AppleMusicDownloader
> ```

> **🪟 Windows 用户请注意**：启动桌面应用后会同时弹出一个控制台窗口，用于显示运行日志。**请勿关闭该控制台窗口**，关闭后程序将无法正常工作。

---

## 环境要求

### 必需

- Python 3.10 或更高版本（推荐 Python 3.11+；Python 3.10 已被部分依赖标记为即将弃用）
- 有效的 Apple Music 订阅
- 本机 wrapper-v2，或 Netscape 格式的 Cookies 文件
- FFmpeg（Windows 与 Apple Silicon macOS 桌面安装包已内置；命令行或源码运行需自行安装）

**获取 Cookies 文件：**

- Firefox 用户：使用 [Export Cookies](https://addons.mozilla.org/firefox/addon/export-cookies-txt/) 扩展
- Chromium 用户：使用 [Open Cookies.txt](https://chromewebstore.google.com/detail/open-cookiestxt/gdocmgbfkjnnpapoeobnolbbkoibbcif) 扩展

**命令行或源码运行安装 FFmpeg：**

- macOS: `brew install ffmpeg`
- Linux: `apt install ffmpeg` / `pacman -S ffmpeg`
- Windows: 从 [ffmpeg.org](https://ffmpeg.org/) 下载

### 可选

- [MP4Box](https://gpac.io/downloads/gpac-nightly-builds/)：替代混流模式
- [N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE/releases/latest)：替代下载模式

---

## 支持的链接类型

- 单曲
- 专辑
- 播放列表
- 音乐视频
- 艺术家主页
- 帖子视频

> **💡 关于编码格式**：设置中选择 Atmos 或 AC3 编码时，如果该曲目没有对应的编码版本，下载器会自动降级到 AAC 立体声。并非所有歌曲都有 Atmos/AC3 版本（通常仅 2021 年后发行的部分歌曲支持），Apple Music 客户端中带有「Dolby Atmos」标志的歌曲才支持 Atmos 下载。

---

## 项目结构

```
AppleMusic-Downloader/
├── src/
│   ├── amdl/              # Python 后端包
│   │   ├── server.py      # FastAPI 服务入口
│   │   ├── cli.py         # CLI 命令行入口
│   │   ├── core_downloader.py  # 下载核心逻辑
│   │   ├── task_manager.py     # 任务队列管理
│   │   ├── converter.py        # 格式转换
│   │   └── ...
│   └── fronted/           # Next.js 前端
│       ├── app/
│       │   ├── components/  # 前端组件
│       │   ├── service.tsx  # API 调用封装
│       │   └── i18n.tsx     # 国际化
│       └── next.config.ts
├── docs/
│   └── api.md             # API 文档
├── pyproject.toml          # 包配置
├── requirements.txt
└── README.md
```

---

## 免责声明

本工具仅供学习与研究使用，严禁将其用于任何违反法律法规或侵犯他人权益的用途。

1. 本项目不直接提供或存储任何受版权保护的内容，用户需自行提供合法的凭证（如有效的 Apple Music 订阅和 Cookies 文件）以使用相关功能。
2. 本人不对用户如何使用本工具承担任何责任，因使用本工具产生的任何法律或版权争议，均由用户自行承担。
3. 本项目基于 [wenfeng110402/AppleMusic-Downloader](https://github.com/wenfeng110402/AppleMusic-Downloader) 开发，并使用 [gamdl](https://github.com/glomatico/gamdl) 和 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 的代码；与原项目及依赖项目的作者无直接关联。
4. 用户在使用本工具时，应自行确保符合当地相关法律法规。

By using this tool, you agree to comply with all applicable laws and assume full responsibility for your actions.
