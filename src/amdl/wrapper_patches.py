"""Validated local source fixes for wrapper-v2 commit 100e0a8 (APK build1109).

Can also be run standalone: python wrapper_patches.py /path/to/wrapper-v2.
This only edits build sources, never Apple libraries or account/session files.
"""
from __future__ import annotations

import argparse
from pathlib import Path


# Fail closed if the pinned upstream source changes. Check every edit before
# writing any file, and accept already-patched sources for safe repeated use.
_EDITS = {
    "Dockerfile": (
        "RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable",
        "RUN curl --retry 5 --retry-delay 2 --retry-all-errors -fsSL "
        "https://sh.rustup.rs -o /tmp/rustup-init.sh && \\\n"
        "    sh /tmp/rustup-init.sh -y --profile minimal --default-toolchain stable && \\\n"
        "    rm /tmp/rustup-init.sh",
    ),
    "src/daemon/apple/abi.hpp": (
        "using fn_SEC_errorCode = int (*)(void* this_);",
        "// Match build1109 errorCode()'s native reference-returning signature.\n"
        "using fn_SEC_errorCode = const int& (*)(void* this_);\n"
        "inline int store_error_code(fn_SEC_errorCode fn, void* error) {\n"
        "    return fn ? fn(error) : 0;\n"
        "}",
    ),
    "src/daemon/apple/auth.cpp": (
        "code = s.SEC_errorCode(err->obj);",
        "code = abi::store_error_code(s.SEC_errorCode, err->obj);",
    ),
    "src/daemon/apple/tokens.cpp": (
        "r.error_code = s.SEC_errorCode(err->obj);",
        "r.error_code = abi::store_error_code(s.SEC_errorCode, err->obj);",
    ),
    "src/daemon/apple/playback.cpp": (
        "int code = (s.SEC_errorCode != nullptr)\n"
        "                                 ? s.SEC_errorCode(err_sp->obj) : 0;",
        "int code = abi::store_error_code(s.SEC_errorCode, err_sp->obj);",
    ),
}


def apply_wrapper_fixes(source: Path) -> None:
    pending: list[tuple[Path, str]] = []
    for relative, (before, after) in _EDITS.items():
        path = source / relative
        text = path.read_text(encoding="utf-8")
        if text.count(after) == 1 and before not in text:
            continue
        if text.count(before) != 1 or after in text:
            raise ValueError(f"Pinned Wrapper source is not recognized: {relative}")
        pending.append((path, text.replace(before, after, 1)))
    for path, text in pending:
        path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="wrapper-v2 source directory at commit 100e0a8")
    args = parser.parse_args()
    apply_wrapper_fixes(args.source)
    print("Wrapper source fixes applied (or already present). Rebuild the image to use them.")
