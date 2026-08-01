"""PyInstaller entry point for the desktop application bundle.

This script is the target for PyInstaller when building
standalone executables for Windows, macOS, and Linux.
"""
import os
import sys
import traceback


def _crash_log(msg: str) -> None:
    """Write crash info to a log file next to the exe / in DATA_DIR."""
    try:
        if getattr(sys, "frozen", False):
            log_dir = os.path.dirname(sys.executable)
        else:
            from amdl.dependency_manager import DATA_DIR
            log_dir = str(DATA_DIR)
        log_path = os.path.join(log_dir, "amdl_crash.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(msg)
        print(f"Crash log written to: {log_path}", file=sys.stderr)
    except Exception:
        pass


def main() -> None:
    try:
        from amdl.server import run_desktop
        run_desktop()
    except Exception:
        _crash_log(traceback.format_exc())
        raise
    except BaseException as e:
        _crash_log(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        raise


if __name__ == "__main__":
    main()
