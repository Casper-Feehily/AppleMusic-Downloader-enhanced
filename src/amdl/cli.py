"""AMDL command-line interface."""

from __future__ import annotations

import sys

# Windows: use SelectorEventLoop + anyio asyncio backend.
if sys.platform == "win32":
    import os as _os
    _os.environ.setdefault("ANYIO_BACKEND", "asyncio")
    import asyncio as _asyncio
    try:
        _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

import click

from amdl import __version__


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="amdl")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """AppleMusic Downloader (amdl) — download songs, MVs, lyrics and more."""
    if ctx.invoked_subcommand is None and not ctx.args:
        click.echo(ctx.get_help())


@cli.command("server")
@click.option("--host", default="127.0.0.1", show_default=True, help="Listen address.")
@click.option("--port", default=8000, show_default=True, type=int, help="Listen port.")
@click.option(
    "--log-level",
    default="info",
    show_default=True,
    type=click.Choice(["debug", "info", "warning", "error"], case_sensitive=False),
    help="Log level.",
)
def server_cmd(host: str, port: int, log_level: str) -> None:
    """Start the API server."""
    from amdl.server import run_server
    run_server(host=host, port=port, log_level=log_level)


@cli.command("desktop")
def desktop_cmd() -> None:
    """Launch the desktop app."""
    from amdl.server import run_desktop
    run_desktop()


def main() -> None:
    """AMDL entry point.

    Routes to:
      - ``amdl server`` / ``amdl --server`` → API server
      - ``amdl desktop`` / ``amdl --desktop`` → desktop GUI
      - anything else → passthrough to gamdl CLI
    """
    args = sys.argv[1:]

    # ── backward-compat: --server / --desktop flags ──────────
    if args and args[0] == "--server":
        from amdl.server import run_server
        host, port, log_level = "127.0.0.1", 8000, "info"
        i = 1
        while i < len(args):
            if args[i] == "--host" and i + 1 < len(args):
                host = args[i + 1]; i += 2
            elif args[i] == "--port" and i + 1 < len(args):
                port = int(args[i + 1]); i += 2
            elif args[i] == "--log-level" and i + 1 < len(args):
                log_level = args[i + 1]; i += 2
            else:
                i += 1
        run_server(host=host, port=port, log_level=log_level)
        return

    if args and args[0] == "--desktop":
        from amdl.server import run_desktop
        run_desktop()
        return

    # ── new sub-command style ──────────────────────────────────
    if args and args[0] not in ("--help", "-h", "--version") and not args[0].startswith("-"):
        # Could be a sub-command (server/desktop) or gamdl passthrough
        if args[0] in ("server", "desktop"):
            cli(args=args, standalone_mode=False)
            return
        # Otherwise passthrough to gamdl
        _passthrough_to_gamdl(args)
        return

    # ── help / version / no args ─────────────────────────────
    if not args or args[0] in ("--help", "-h"):
        click.echo(_HELP)
        return

    cli(args=args, standalone_mode=False)


def _passthrough_to_gamdl(args: list[str]) -> None:
    """Forward arguments to gamdl CLI."""
    from gamdl.cli.cli import main as gamdl_main
    sys.argv = ["gamdl", *args]
    gamdl_main()


_HELP = f"""\
AppleMusic Downloader (amdl) v{__version__}

Usage:
  amdl server [--host HOST] [--port PORT]   Start API server
  amdl desktop                               Launch desktop app
  amdl <gamdl args...>                       Pass through to gamdl CLI

Server options:
  --host HOST        Listen address (default: 127.0.0.1)
  --port PORT        Listen port (default: 8000)
  --log-level LEVEL  Log level: debug, info, warning, error (default: info)

Examples:
  amdl server --host 0.0.0.0 --port 8000
  amdl desktop
  amdl -c /path/to/cookies.txt "https://music.apple.com/..."
  amdl --help
"""
from __future__ import annotations
import sys

# Windows: use SelectorEventLoop + anyio asyncio backend.
if sys.platform == "win32":
    import os as _os
    _os.environ.setdefault("ANYIO_BACKEND", "asyncio")
    import asyncio as _asyncio
    try:
        _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

_HELP = """\
AppleMusic Downloader (amdl) v2.4.6

Usage:
  amdl --server [options]     Start API server
  amdl --desktop              Launch desktop app
  amdl <gamdl args...>        Pass through to gamdl CLI

Server options:
  --host HOST        Listen address (default: 127.0.0.1)
  --port PORT        Listen port (default: 8000)
  --log-level LEVEL  Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)

Examples:
  amdl --server --host 0.0.0.0 --port 8000
  amdl --desktop
  amdl -c /path/to/cookies.txt "https://music.apple.com/..."
  amdl --help
"""


def main():
    """AMDL entry point.

    Usage:
        amdl --server [--host HOST] [--port PORT]   # 启动 API 服务
        amdl --desktop                                # 启动桌面应用
        amdl <gamdl args...>                          # 透传 gamdl 命令行
    """
    args = sys.argv[1:] if len(sys.argv) > 1 else []

    # ── 帮助信息 ──────────────────────────────────────────────
    if not args or args[0] in ("--help", "-h"):
        print(_HELP)
        return

    # ── API 服务模式 ──────────────────────────────────────────
    if args[0] == "--server":
        from amdl.server import run_server

        # 解析 --host / --port（如果有的话）
        host = "127.0.0.1"
        port = 8000
        log_level = "info"
        i = 1
        while i < len(args):
            if args[i] == "--host" and i + 1 < len(args):
                host = args[i + 1]
                i += 2
            elif args[i] == "--port" and i + 1 < len(args):
                port = int(args[i + 1])
                i += 2
            elif args[i] == "--log-level" and i + 1 < len(args):
                log_level = args[i + 1]
                i += 2
            else:
                i += 1
        run_server(host=host, port=port, log_level=log_level)
        return

    # ── 桌面模式 ──────────────────────────────────────────────
    if args[0] == "--desktop":
        from amdl.server import run_desktop

        run_desktop()
        return

    # ── 默认：透传给 gamdl ────────────────────────────────────
    from gamdl.cli.cli import main as gamdl_main

    sys.argv = ["gamdl", *args]
    gamdl_main()
    