"""PyInstaller entry point for the desktop application bundle.

This script is the target for PyInstaller when building
standalone executables for Windows, macOS, and Linux.
"""
import logging
import sys
import traceback


def _alloc_console() -> None:
    """On Windows, allocate a visible console window for real-time log output.

    PyInstaller's --windowed mode sets sys.stdout/stderr to None, which
    causes async libraries (httpx, asyncio) to hang or crash. Allocating
    a console provides valid stdout/stderr streams.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.AllocConsole()
            sys.stdout = open("CONOUT$", "w", encoding="utf-8")
            sys.stderr = open("CONOUT$", "w", encoding="utf-8")
        except Exception:
            pass


def main() -> None:
    _alloc_console()

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("amdl").info("=== AMDL starting ===")

    def _excepthook(exc_type, exc_value, exc_tb):
        logging.getLogger("amdl").critical(
            "Unhandled exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
    sys.excepthook = _excepthook

    try:
        from amdl.server import run_desktop
        run_desktop()
    except Exception:
        logging.getLogger("amdl").critical("Fatal:\n%s", traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
