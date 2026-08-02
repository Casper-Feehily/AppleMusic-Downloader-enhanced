# 修复报告 — 2026-07-30

> 分支：`rebuild`
> 范围：Step 1 Bug 修复（对应 `rebuild.md` 问题清单 A 类 #1/#2/#3/#4/#5/#7 及症状根因 1/2）
> 原则：只修 Bug，不改架构、不动前端 UI。

---

## 1. 背景与症状

| 症状 | 平台 | 表现 |
|---|---|---|
| S1 | Windows | 服务能启动、任务能提交，但每首歌下载必失败，整体不可用 |
| S2 | macOS | 双击 .app 无反应；必须用终端打开 .app 内部二进制才能使用 |
| S3 | Linux | 未测试（理论上不受 S1/S2 影响） |

## 2. 根因分析

### S1 — Windows 不可用

`__main__.py` / `cli.py` 为绕过 httpx/anyio 兼容问题，强制设置了
`WindowsSelectorEventLoopPolicy`。但 Python 官方限制：Windows 的
**SelectorEventLoop 不支持 `asyncio.create_subprocess_exec`**，调用直接抛
`NotImplementedError`。下载管线恰有两处依赖它：

1. `core_downloader.py` 的 `download_stream` 覆盖 —
   `create_subprocess_exec(sys.executable, "-m", "yt_dlp", ...)`
2. gamdl 的 `_download_nm3u8dlre`（`gamdl/utils.py::async_subprocess`）

因此陷入两难：Proactor 循环（子进程可用）↔ httpx 挂；Selector 循环
（httpx 可用）↔ 子进程挂。历史提交"修改windows事件循环逻辑"等均在此打转。

另有潜伏问题：PyInstaller 冻结环境下 `sys.executable -m yt_dlp` 实际会
**重新启动 AMDL 自身**（frozen exe 不解析 `-m`），即使事件循环问题解决，
打包版 Windows 下载依然会坏。

### S2 — macOS 双击 .app 崩溃

工作区（rebuild 分支未提交改动）删除了 `server.py` 中
`_acquire_instance_lock` / `_release_instance_lock` / `_SUBPROCESS_FLAGS`
的**定义**，但**调用保留**：

- `run_desktop()` 第一行调用锁函数 → 双击 .app 走 `--desktop` → 立即
  `NameError` 崩溃，Finder 启动无终端，表现为"无反应"；
- 终端直跑内部二进制默认走 `run_server()`，不经过崩溃行，所以"能用"。

### 附带发现（同批修复）

| # | 位置 | 问题 |
|---|---|---|
| B1 | `server.py:288` | `_SUBPROCESS_FLAGS` 未定义，被 `try/except` 吞掉 → 依赖版本检测在**所有平台**恒失败（version 恒为 null） |
| B2 | `core_downloader.py` | 解析循环统计的 `errors` 随后被 `errors = 0` 清零 → URL 解析失败也显示"全部完成" |
| B3 | `server.py serve_static` | `FRONTEND_OUT / full_path` 未校验归一化路径 → 路径穿越可读任意文件（`--host 0.0.0.0` 时是实际漏洞） |
| B4 | `server.py` CORS | `allow_origins=["*"]` + `allow_credentials=True` 组合违反浏览器规范 |
| B5 | `task_manager.py` | 取消任务只置标志位，运行中的 `_future` 从未被 `cancel()`，下载实际继续 |
| B6 | `service.tsx` | `ApiInfoResponse` 前端类型 `{id, name}` 与后端实际返回 `{value, label}` 不一致 |

## 3. 修复内容

### 3.1 `src/amdl/core_downloader.py`

**核心策略：把下载路径上的 asyncio 子进程全部消灭，跳出 Selector/Proactor 二选一。**

- `download_stream` 覆盖重写：
  - **yt-dlp 分支**：改为进程内调用 `yt_dlp.YoutubeDL` + `HlsFD`/`HttpFD`，
    跑在 `asyncio.to_thread` 中。逻辑与 gamdl 上游 `_download_ytdlp_process`
    逐行对齐（同参数：`quiet/no_warnings/overwrites/allow_unplayable_formats/
    concurrent_fragment_downloads=8`）。一次规避三个坑：
    Selector 无子进程支持、frozen exe 无 `-m`、gamdl multiprocessing 在
    PyInstaller 下需 freeze_support。
  - **N_m3u8DL-RE 分支**：不再调 gamdl 的 `_download_nm3u8dlre`（内部是
    asyncio 子进程），改为自己 `subprocess.run`（参数与 gamdl 一致），
    跑在 `asyncio.to_thread` 中；Windows 下加 `CREATE_NO_WINDOW`。
- 删除 `errors = 0` 清零行（B2），解析错误正确计入最终错误数。
- Windows 的 Selector 事件循环策略**保留不动**——httpx 兼容 hack 不受影响。

### 3.2 `src/amdl/server.py`

- 恢复单实例锁 `_acquire_instance_lock` / `_release_instance_lock` 定义（S2）。
- `_SUBPROCESS_FLAGS` 改为从 `dependency_manager` 导入（单一来源，修 B1）。
- `serve_static`：`(FRONTEND_OUT / full_path).resolve()` 后校验
  `is_relative_to(FRONTEND_OUT.resolve())`，越界回落 index / 404（修 B3）。
- CORS `allow_credentials=False`（本应用无 cookie 鉴权，修 B4）。

### 3.3 `src/amdl/task_manager.py`

- `DownloadTask.__init__` 显式声明 `self._future: asyncio.Task | None = None`。
- `cancel_task()` 对未完成的 `_future` 调用 `.cancel()`，使 CancelledError
  立即传播到下载协程（修 B5）。
  - 已知残留：正在 `to_thread` 中执行的单个文件下载线程无法被杀，会在
    后台自然结束，但任务状态与队列即刻终止。彻底的子进程级取消列入
    rebuild.md Step 5。

### 3.4 `src/fronted/app/service.tsx`（仅数据层，UI 零改动）

- `ApiInfoResponse` 四个数组字段类型改为 `{value, label}`（修 B6，
  该接口当前无消费者，属预防性修复）。

### 3.5 `rebuild.md`

- 新增"一.五、症状 → 根因对照"章节，记录 S1/S2/S3 的完整因果链与修法。

## 4. 验证结果（macOS 本机）

| 验证项 | 方法 | 结果 |
|---|---|---|
| 模块完整性 | `py_compile` + import `server/task_manager/core_downloader`，并显式导入 `_acquire_instance_lock`、`_SUBPROCESS_FLAGS` | 通过 |
| yt-dlp 新调用路径 | 导入 `YoutubeDL` / `HlsFD` / `HttpFD` | 通过 |
| 服务启动 | `uvicorn amdl.server:app`（端口 8766） | 正常启动 |
| 健康检查 | `GET /api/health` | `{"status":"ok","version":"2.0.0"}` |
| 依赖版本检测（B1） | `GET /api/dependencies` | ffmpeg / MP4Box / N_m3u8DL-RE **均返回版本号**（修复前恒为 null） |
| 路径穿越（B3） | `curl --path-as-is /..%2f..%2f..%2fetc%2fpasswd` 及 `/../../../../etc/passwd` | 返回 index.html，未泄漏系统文件 |
| 前端类型 | `npx tsc --noEmit` | 退出码 0 |

## 5. 未覆盖 / 待办

- **Windows 实机回归**：本机为 macOS，S1 的修复基于根因推导 + 与 gamdl
  上游逻辑对齐，发布前需在 Windows 跑一次完整下载（yt-dlp 模式 +
  N_m3u8DL-RE 模式各一次）。
- macOS .app 需按当前代码**重新打包**后验证双击启动。
- 取消任务的子进程级终止、日志/任务内存上界 → rebuild.md Step 5。
- 死代码清理、参数模型统一等 → rebuild.md Step 2–6，未在本次范围。

## 6. 变更文件清单

```
rebuild.md                       新增症状→根因章节
docs/fix-report-2026-07-30.md    本报告
src/amdl/server.py               恢复实例锁；导入 SUBPROCESS_FLAGS；路径穿越；CORS
src/amdl/core_downloader.py      下载改线程化（yt-dlp 进程内 / RE subprocess.run）；errors 清零
src/amdl/task_manager.py         _future 声明 + 取消时 cancel()
src/fronted/app/service.tsx      ApiInfoResponse 类型对齐
```
