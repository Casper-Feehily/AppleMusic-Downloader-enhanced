"""AMDL command-line interface."""

from __future__ import annotations

import sys
from typing import get_args

import click

from amdl import __version__
from amdl.output_layout import OutputLayout, OutputNames


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
      - ``amdl --gamdl-help`` → gamdl CLI full help
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

    if args and args[0] in ("server", "desktop", "--version"):
        cli(args=args, standalone_mode=False)
        return

    if not args or args[0] in ("--help", "-h"):
        click.echo(_HELP)
        return

    if args[0] == "-V":
        click.echo(f"amdl {__version__}")
        return

    if args[0] == "--gamdl-help":
        _passthrough_to_gamdl(["--help"])
        return

    _passthrough_to_gamdl(args)


def _remember_output_layout(ctx: click.Context, _option: click.Option, value: OutputLayout) -> None:
    ctx.meta["output_layout"] = value


class _DownloadCommand(click.Command):
    def get_params(self, ctx: click.Context) -> list[click.Parameter]:
        # gamdl reads command.params directly to populate its config dataclass.
        # Add the AMDL option only to Click's parsing and help.
        return [*super().get_params(ctx), click.Option(
            ["--output-layout"],
            type=click.Choice(get_args(OutputLayout)),
            default="artist_album",
            show_default=True,
            expose_value=False,
            callback=_remember_output_layout,
            help="Save by artist and album, by artist only, or directly in the output directory.",
        )]


def _passthrough_to_gamdl(args: list[str]) -> None:
    """Keep gamdl's CLI options and add AMDL download behavior."""
    from types import SimpleNamespace

    from gamdl.cli import cli as gamdl_cli
    from amdl.core_downloader import _parse_items_with_progress

    original_downloader = gamdl_cli.AppleMusicDownloader
    original_argv = sys.argv
    upstream_command = gamdl_cli.main
    command = _DownloadCommand(
        name=upstream_command.name,
        callback=upstream_command.callback,
        params=upstream_command.params,
        context_settings=upstream_command.context_settings,
        help=upstream_command.help,
    )

    class ProgressDownloader(original_downloader):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            layout = click.get_current_context().meta["output_layout"]
            self.output_names = OutputNames.for_layout(self.base, layout)

        async def get_download_item_from_url(self, url):
            source = SimpleNamespace(get_download_item_from_url=super().get_download_item_from_url)
            log = gamdl_cli.logger.bind(action="Parsing")
            log.info("Fetching song or playlist information…")
            async for item in _parse_items_with_progress(source, url, log):
                yield item

        async def download(self, item):
            if self.output_names is not None and item.final_path:
                self.output_names.assign(item, save_playlist=self.save_playlist)
            await super().download(item)

    try:
        gamdl_cli.AppleMusicDownloader = ProgressDownloader
        sys.argv = ["gamdl", *args]
        command(prog_name="amdl")
    finally:
        gamdl_cli.AppleMusicDownloader = original_downloader
        sys.argv = original_argv


_HELP = f"""\
AppleMusic Downloader (amdl) v{__version__}

Usage:
  amdl server [--host HOST] [--port PORT]   Start API server
  amdl desktop                               Launch desktop app
  amdl <gamdl args...>                       Download songs, albums or playlists

Download options:
  -c, --cookies-path PATH          Path to cookies.txt for cookie-based downloads
  -o, --output-path PATH           Output directory
  --output-layout LAYOUT           artist_album (default), artist, or flat
  --use-wrapper                    Use Wrapper for account, playback and decryption
  --song-codec-priority CSV        Comma-separated codec priority (e.g. alac,aac-web)
  --synced-lyrics-format FORMAT    Synced lyrics format (lrc, srt, ttml)
  --synced-lyrics-only             Download synced lyrics only
  --no-synced-lyrics               Skip synced lyrics
  --save-cover                     Save cover art as a separate file
  --download-mode MODE             Download engine (ytdlp, nm3u8dlre)
  --overwrite                      Overwrite existing files

Server options:
  --host HOST        Listen address (default: 127.0.0.1)
  --port PORT        Listen port (default: 8000)
  --log-level LEVEL  Log level: debug, info, warning, error (default: info)

Examples:
  amdl server --host 0.0.0.0 --port 8000
  amdl desktop
  amdl -c /path/to/cookies.txt "https://music.apple.com/..."
  amdl -c /path/to/cookies.txt "https://music.apple.com/.../playlist/.../pl..."
  amdl -c /path/to/cookies.txt -o "./Music" --output-layout flat "https://music.apple.com/..."
  amdl --gamdl-help                          Show all gamdl CLI options
"""
