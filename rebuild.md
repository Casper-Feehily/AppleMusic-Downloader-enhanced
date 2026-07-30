# rebuild.md — 重构分析与实施方案

> 范围约定：**不改动前端 UI 的视觉与交互**（组件结构、样式、页面布局全部保留），
> 只允许修改前端的数据层（`service.tsx`）和后端全部代码。

---

## 一、现状架构

```
前端 (Next.js 静态导出 src/fronted/out)
   │  HTTP 轮询 /api/tasks（每 3s）
   ▼
FastAPI server.py ──► TaskManager (task_manager.py, 内存队列, 串行执行)
   │                        │
   │                        ▼
   │                 core_downloader.py（包装 gamdl 的下载管线）
   │                        │
   │                        ▼
   │                 converter.py（ffmpeg 格式转换）
   ▼
dependency_manager.py（自动下载 ffmpeg / N_m3u8DL-RE）
桌面模式：pywebview 包一层 (run_desktop)
```

---

## 一.五、症状 → 根因对照（实测问题的直接解释）

### 症状 1：Windows 上完全无法使用

**根因**：`__main__.py` / `cli.py` 强制设置了 `WindowsSelectorEventLoopPolicy`
（为绕过 httpx/anyio 的兼容问题），但 Windows 的 **SelectorEventLoop 不支持
`asyncio.create_subprocess_exec`**（Python 官方限制，直接抛 `NotImplementedError`）。
而下载管线里恰好有两处依赖它：

- `core_downloader.py` 的 `download_stream` 覆盖 → `asyncio.create_subprocess_exec(sys.executable, "-m", "yt_dlp", ...)`
- gamdl 的 `_download_nm3u8dlre`（`gamdl/utils.py::async_subprocess`）→ 同样是 `create_subprocess_exec`

结果：服务器能起、任务能提交，但**每一首歌下载时必抛 NotImplementedError**。
最近几次提交（"修改windows事件循环逻辑"、"放弃事件循环强行兼容"）都在
Selector（httpx 正常但子进程挂）和 Proactor（子进程正常但 httpx 挂）之间打转。

**修法（本次已实施）**：跳出二选一——把下载路径上的 asyncio 子进程全部消灭：

- yt-dlp 改为**进程内调用**（`yt_dlp.YoutubeDL` + `HlsFD`/`HttpFD`，跑在
  `asyncio.to_thread` 里）。这与 gamdl 上游 `_download_ytdlp_process` 的逻辑
  逐行对齐，同时避开了三个坑：Selector 循环无子进程支持、PyInstaller 下
  `sys.executable -m yt_dlp` 实际会启动 AMDL 自己（frozen exe 没有 `-m`）、
  gamdl 用 multiprocessing 在 frozen 环境需要 freeze_support。
- N_m3u8DL-RE 分支不再调 gamdl 的 `_download_nm3u8dlre`，改为自己用
  `subprocess.run`（同参数）跑在 `asyncio.to_thread` 里。

之后 Selector 策略可以安心保留，httpx 兼容 hack 不受影响。

### 症状 2：macOS 双击 .app 无反应，必须终端打开内部二进制

**根因**：当前工作区（rebuild 分支未提交的改动）把 `server.py` 里
`_acquire_instance_lock` / `_release_instance_lock` 和 `_SUBPROCESS_FLAGS`
的**定义删了，但调用还留着**（`run_desktop()` 第一行就调用锁函数）。
用这份代码打包后：

- 双击 .app → 走 `--desktop` → `run_desktop()` 立刻 `NameError` 崩溃，
  Finder 启动没有终端，看不到任何报错，表现为"无反应"。
- 终端直接跑内部二进制（不带 `--desktop`）→ 默认走 `run_server()`，
  不经过崩溃行，所以"能用"。

**修法（本次已实施）**：恢复单实例锁定义；`_SUBPROCESS_FLAGS` 从
`dependency_manager` 导入（单一来源）。叠加因素（Finder 启动 PATH 极简、
`time.sleep(2)` 等待）已有部分补救，彻底收敛见 Step 4。

### 症状 3：Linux 未测试

Linux 不受上述两个平台特判影响（无 Selector 强制、无 .app 包装），
理论上是三个平台里问题最少的。

---

## 二、主要问题清单

### A. 明确的 Bug（必须修）

1. **`server.py:288` 使用了未定义的 `_SUBPROCESS_FLAGS`**
   该名字只定义在 `dependency_manager.py` 里，`server.py` 从未导入。
   由于外层 `try/except Exception` 吞掉了 `NameError`，后果是
   `/api/dependencies` 的版本号检测**在所有平台上永远失败**（version 恒为 null），
   而且没有任何报错。

2. **`core_downloader.py:274` 解析错误计数被清零**
   ```python
   for url in urls:            # 解析阶段 errors += 1
       ...
   total = len(items) or 1
   errors = 0                  # ← 把解析阶段的错误全部抹掉
   ```
   URL 解析失败的任务最终会显示"全部完成"。

3. **静态文件路径穿越**（`server.py:459` `serve_static`）
   `FRONTEND_OUT / full_path` 未做归一化校验，`GET /../../etc/passwd`
   （URL 编码后）可读取任意文件。虽然默认只绑 127.0.0.1，但
   `--server --host 0.0.0.0` 是文档支持的用法，必须修。

4. **CORS 配置非法**：`allow_origins=["*"]` 与 `allow_credentials=True`
   同时开启（浏览器规范不允许，FastAPI 实际会静默降级），应显式收紧。

5. **`ApiInfoResponse` 前后端字段不一致**：后端返回 `{value, label}`，
   前端类型声明是 `{id, name}`（`service.tsx:67-75`）。目前碰巧没炸是因为
   没人真正消费这个接口，属于"沉睡的 bug"。

6. **`disable_music_video_skip` 参数被接收但从未使用**（core_downloader）。

7. **任务取消不彻底**：`cancel_task` 只置了标志位，取消只在
   `progress_callback`（每完成一首歌）时才被检查；正在跑的
   yt-dlp / N_m3u8DL-RE 子进程不会被杀掉，`task._future` 也从未被
   `cancel()`。取消一个大专辑任务后，下载实际仍在后台继续。

### B. 架构性问题（重构核心）

8. **同一份"下载参数"在 4 处重复定义、手工同步**：
   - `server.py` 的 `DownloadRequest`（pydantic，40+ 字段）
   - `core_downloader._download_urls_async` 的 40+ 个关键字参数
   - `task_manager._execute_download` 里 **逐个手抄** 的 35 行 `kwargs.get(...)`
   - 前端 `service.tsx` 的 `FormState`

   加一个参数要改 4 个地方，这是本项目最大的维护成本来源。

9. **`_download_urls_async` 是一个 220 行的"上帝函数"**：
   cookies 解析、API 初始化（含 Windows 特判分支）、gamdl 接口组装、
   `download_stream` 猴子补丁、解析循环、下载循环、格式转换全部内联。
   无法单测，Windows 分支和 macOS 分支的行为差异只能靠真机验证。

10. **Windows 事件循环 hack 散落 4 个文件**：
    `ANYIO_BACKEND` 环境变量在 `server.py` / `core_downloader.py` /
    `cli.py` / `__main__.py` 各设置一遍，事件循环策略在 2 处设置。
    应收敛为一个 `_bootstrap.py`，所有入口 import 它。

11. **后端有完整的 WebSocket 推送（`/api/ws/{task_id}` + 广播逻辑约 120 行），
    但前端从未使用**，实际用的是每 3 秒轮询 `/api/tasks`，且**每次轮询都
    全量返回所有任务的全部日志**——日志越多请求越肥，等于最差的两头：
    维护着 WS 代码却承担轮询的开销。

12. **任务与日志在内存中无上界**：`task.logs` 无限增长、`_tasks` 字典
    永不清理，长时间运行必然内存膨胀；重启后任务记录全部丢失。

13. **版本号三处不一致**：pyproject `dynamic version`、server.py 硬编码
    `"2.0.0"`（3 处）、cli.py 帮助文本 `v2.4.6`。

### C. 死代码 / 冗余（直接删）

14. `amdl/utils.py`（`resource_path`、`prepend_tools_to_path`）——全项目无引用。
15. `amdl/i18n.py`、`amdl/desktop_entry.py` ——无引用（i18n 已由前端 `i18n.tsx` 承担）。
16. `converter.py` 的 `convert_directory` / `convert_downloaded_files` ——
    只有 `convert_file_list` 被调用；且 8 个格式分支是同一模板的复制粘贴，
    应改为**格式→ffmpeg 参数表**驱动，451 行可压到 ~120 行。
17. `enums.py` 的 `PostQuality` legacy 别名——无引用。
18. `server.py` 的 `PywebviewApi.open_file/open_folder/save_file` 里
    `import webview` 后未使用变量、重复导入。

### D. 工程 / 仓库卫生

19. `src/fronted`（frontend 拼写错误）目录下 **`out/`、`.next/`、
    `node_modules/` 构建产物疑似进了 git**——仓库体积与 diff 噪音。
20. `requirements.txt` 与 `pyproject.toml` 依赖不同步
    （`httpx-retries`、`certifi`、`pywebview` 只在 requirements 里）。
21. `cli.py` 手写 while 循环解析参数，而 `click` 已是声明的依赖。
22. `dependency_manager` 把整个压缩包（ffmpeg zip 可达 100MB+）读进
    `BytesIO` 内存再解压；下载的二进制**无任何校验**（无 sha256）。
23. `run_desktop` 用 `time.sleep(2)` 等服务器启动，慢机器上会打开白屏窗口；
    应轮询 `/api/health`。

---

## 三、重构方案（分六步，每步独立可验证、可提交）

### Step 1 — Bug 修复（不改结构，先让现状正确）

| 文件 | 修改 |
|---|---|
| `server.py` | `from amdl.dependency_manager import _SUBPROCESS_FLAGS`（顺手改成公开名 `SUBPROCESS_FLAGS`） |
| `core_downloader.py` | 删除 `errors = 0` 重置行，解析错误并入总错误数 |
| `server.py serve_static` | `resolved = (FRONTEND_OUT / full_path).resolve()`，再 `resolved.is_relative_to(FRONTEND_OUT.resolve())` 校验，否则 404 |
| `server.py` CORS | `allow_credentials=False`（本应用不用 cookie 鉴权），保留 `allow_origins=["*"]` 供 dev 前端使用 |
| `service.tsx` | `ApiInfoResponse` 字段改为 `{value, label}` |
| `task_manager.py` | `cancel_task` 对 RUNNING 任务调用 `task._future.cancel()`；`DownloadTask.__init__` 声明 `self._future = None` |

### Step 2 — 删除死代码 + 仓库卫生

- 删除：`utils.py`、`i18n.py`、`desktop_entry.py`、
  `converter.py` 中 `convert_directory` / `convert_downloaded_files`、
  `enums.py` 的 `PostQuality`。
- `converter.py` 改为参数表驱动：
  ```python
  AUDIO_ARGS = {"mp3": ["-c:a","libmp3lame","-b:a","320k",...], "flac": [...], ...}
  ```
- `.gitignore` 增加 `src/fronted/out/`、`src/fronted/.next/`、`node_modules/`，
  并 `git rm -r --cached` 移出已跟踪的构建产物
  （PyInstaller 打包时由 `scripts/build.sh` 现场 `npm run build`，不依赖仓库内产物；
  **此项涉及构建流程，执行前单独确认**）。
- `pyproject.toml` 补齐 `httpx-retries`、`certifi`；`requirements.txt`
  改为 `-e .` 或保留但由 pyproject 生成，二选一。
- 目录改名 `fronted → frontend` **暂不做**（牵动 build.sh、PyInstaller spec、
  文档，收益低，列入"不做"）。

### Step 3 — 统一参数模型（消灭 4 处重复，核心一步）

新建 `amdl/options.py`：

```python
class DownloadOptions(BaseModel):
    """唯一的下载参数定义（原 server.DownloadRequest 全部字段 + 校验器搬到这里）"""
    urls: list[str]
    cookies_path: str
    ...  # 现 DownloadRequest 的全部字段与 validator
```

- `server.py`：`DownloadRequest = DownloadOptions`（API schema 不变，前端零感知）。
- `task_manager.submit()` 直接收 `DownloadOptions` 实例存进 task；
  `_execute_download` 里 35 行手抄映射替换为一行：
  `await run_download(task.options, log_callback=..., progress_callback=...)`。
- `core_downloader` 入口签名改为
  `async def run_download(opts: DownloadOptions, *, log_callback, progress_callback) -> int`。
- 前端 `FormState` 不动（它只是 UI 表单子集，本来就不要求全量对齐）。

### Step 4 — 拆解 core_downloader（可测试化）

`core_downloader.py` 拆为职责清晰的私有函数，同文件即可，不必多建模块：

```
run_download(opts)
 ├─ _create_api(opts)          # cookies → AppleMusicApi；Windows 特判收敛在此一处
 ├─ _build_downloader(api, opts)  # interface + downloader 组装、download_stream 覆盖
 ├─ _collect_items(dl, urls)   # 解析循环 → (items, parse_errors)
 ├─ _download_items(dl, items, progress_cb)  # 下载循环 → (done_files, dl_errors)
 └─ _post_convert(done_files, opts)          # 调 converter
```

- `_bootstrap.py`：集中 `ANYIO_BACKEND` + `WindowsSelectorEventLoopPolicy` 设置，
  `cli.py` / `__main__.py` / `server.py` 只 `import amdl._bootstrap`，
  删除 4 处重复 hack。
- `download_stream` 猴子补丁保留（这是绕过 PyInstaller 下 PATH 问题的现实方案），
  但加一行注释说明其针对的 gamdl 版本，并在 `pyproject.toml` 把 gamdl
  锁到兼容上界（`gamdl>=3.8.3,<4`），防止上游改内部 API 静默炸裂。
- 落实 `disable_music_video_skip`（透传给 gamdl）或从参数中彻底移除——
  以 gamdl 实际支持为准。

### Step 5 — TaskManager 加固

- **取消**：`_download_items` 每个 item 前检查取消标志；`cancel_task`
  同时 `future.cancel()`，确保 CancelledError 传播、子进程随协程终止。
- **日志上界**：`task.logs` 改为 `collections.deque(maxlen=2000)`。
- **任务清理**：完成/失败任务保留最近 50 条，超出的从 `_tasks` 移除
  （前端队列页展示的就是最近任务，行为无感知）。
- **锁统一**：全部跑在 uvicorn 事件循环里，`threading.Lock` 换成
  `asyncio.Lock` 或直接去掉（单循环无竞争）。
- 持久化（重启恢复任务记录）**不做**——桌面工具场景收益低。

### Step 6 — 前端数据层接线（不动 UI）

只改 `service.tsx` 与后端接口，组件文件零改动：

- `GET /api/tasks` 增加 `?include_logs=false`（默认 false），列表轮询不再
  携带全量日志；新增 `GET /api/tasks/{id}` 已存在，展开某个任务时按需拉日志。
  `queue.tsx` 不改结构——`useTasks` 内部在 expanded 时补拉详情即可
  （若必须在组件里加一行 hook 调用，控制在最小 diff）。
- WebSocket 二选一决策：**保留轮询、删除后端 WS**（`/api/ws` 端点 +
  task_manager 里约 120 行广播/订阅代码）。理由：轮询已满足 3 秒粒度的
  UI 需求，WS 是无人使用的维护负担；先删，未来真需要实时进度条再加回。
- 版本号统一：`amdl/__init__.py` 定义 `__version__`，server / cli 引用它，
  pyproject 用 flit 的 dynamic version 读同一处。

---

## 四、明确不做的事

- 不改前端组件的 JSX 结构、样式、交互（约定前提）。
- 不做任务持久化 / 数据库。
- 不改 `fronted` 目录名。
- 不引入新框架、不换 HTTP 客户端。
- `dependency_manager` 的流式下载与 sha256 校验列为**后续可选项**
  （涉及维护每个平台的校验和清单，成本较高；本轮只加"下载失败给出明确
  UI 提示"即可）。

## 五、执行顺序与验证

每个 Step 单独提交，验证方式：

1. **Step 1-2**：`python -m amdl --server` 启动，手测
   `/api/health`、`/api/dependencies`（version 应有值）、
   `/api/tasks` 提交一个 URL 走通全流程；路径穿越用
   `curl --path-as-is http://127.0.0.1:8000/..%2f..%2fetc/passwd` 验证 404。
2. **Step 3-4**：同上回归 + 为 `_collect_items`、converter 参数表、
   `DownloadOptions` 校验器补最小单测（新增 `tests/`）。
3. **Step 5**：提交大任务→立即取消→确认子进程退出（`ps` 观察）。
4. **Step 6**：`npm run build` 后桌面模式启动，队列页展开/收起/取消/
   复制日志全部手测；确认轮询请求体积明显变小。

风险点：Windows 分支（httpx 直连 + SelectorEventLoop）无法在 macOS 本机
验证，Step 4 收敛该分支时保持逻辑逐行等价搬移，不做行为改动，
发布前需在 Windows 实机跑一次完整下载。
