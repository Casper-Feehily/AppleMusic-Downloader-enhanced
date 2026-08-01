"""PyInstaller entry point for the desktop application bundle.

This script is the target for PyInstaller when building
standalone executables for Windows, macOS, and Linux.
"""
import logging
import os
import sys
import traceback


def _get_log_dir() -> str:
    """Determine the log directory (same as exe or DATA_DIR)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    try:
        from amdl.dependency_manager import DATA_DIR
        return str(DATA_DIR)
    except Exception:
        return os.getcwd()


def _setup_logging() -> str:
    """Set up file logging BEFORE anything else. Returns log path."""
    log_dir = _get_log_dir()
    log_path = os.path.join(log_dir, "amdl.log")

    # Root logger → file only (no stderr handler to avoid conflicts)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    fh = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return log_path


def main() -> None:
    log_path = _setup_logging()
    logging.getLogger("amdl").info("=== AMDL starting === log: %s", log_path)

    # Catch ALL unhandled exceptions (including those from threads)
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
