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

    # Root logger → file + stderr
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    fh = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # Redirect stdout/stderr to log file as fallback
    # This catches any print() or uncaught exceptions that bypass logging
    class _LogStream:
        """Write to both the original stream and the log file."""
        def __init__(self, original, log_file, label):
            self._original = original
            self._log_file = log_file
            self._label = label
        def write(self, s):
            if s.strip():
                try:
                    self._log_file.write(f"[{self._label}] {s}")
                    self._log_file.flush()
                except Exception:
                    pass
            if self._original:
                try:
                    self._original.write(s)
                except Exception:
                    pass
        def flush(self):
            try:
                self._log_file.flush()
            except Exception:
                pass
            if self._original:
                try:
                    self._original.flush()
                except Exception:
                    pass
        def isatty(self):
            return False
        @property
        def encoding(self):
            return "utf-8"

    try:
        log_f = open(log_path, "a", encoding="utf-8")
        sys.stdout = _LogStream(sys.stdout, log_f, "stdout")
        sys.stderr = _LogStream(sys.stderr, log_f, "stderr")
    except Exception:
        pass  # If we can't redirect, just continue

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
