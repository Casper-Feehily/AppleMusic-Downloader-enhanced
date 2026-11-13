from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from amdl.wrapper_patches import apply_wrapper_fixes


# Small independent excerpts from the pinned upstream revision. No Apple files.
FIXTURE = {
    "Dockerfile": "RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable\n",
    "src/daemon/apple/abi.hpp": "using fn_SEC_errorCode = int (*)(void* this_);\n",
    "src/daemon/apple/auth.cpp": "            code = s.SEC_errorCode(err->obj);\n",
    "src/daemon/apple/tokens.cpp": "        r.error_code = s.SEC_errorCode(err->obj);\n",
    "src/daemon/apple/playback.cpp": "                    int code = (s.SEC_errorCode != nullptr)\n                                 ? s.SEC_errorCode(err_sp->obj) : 0;\n",
}


class WrapperPatchTests(unittest.TestCase):
    def make_source(self, root: Path) -> None:
        for relative, text in FIXTURE.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def test_patched_abi_reads_reference_value_in_all_three_callers(self) -> None:
        compiler = shutil.which("c++") or shutil.which("g++")
        if not compiler:
            self.skipTest("Requires a C++ compiler")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_source(root)
            apply_wrapper_fixes(root)
            header = (root / "src/daemon/apple/abi.hpp").read_text()
            callers = [
                (root / f"src/daemon/apple/{name}.cpp").read_text()
                for name in ("auth", "tokens", "playback")
            ]
            # Reference-returning function has the native build1109 ABI.
            harness = '''#include <type_traits>
namespace abi {
''' + header + '''
}
int native_code = 8;
const int& native_error(void*) { return native_code; }
struct Error { void* obj = nullptr; };
struct Symbols { abi::fn_SEC_errorCode SEC_errorCode; };
int main() {
    static_assert(std::is_same<abi::fn_SEC_errorCode, const int& (*)(void*)>::value);
    Symbols s{&native_error};
    Error value; Error* err = &value; Error* err_sp = &value;
    struct { int error_code = 0; } r;
    int code = 0;
''' + callers[0] + callers[1] + '''
    if (code != 8 || r.error_code != 8) return 1;
    {
''' + callers[2] + '''
        if (code != 8) return 2;
    }
    s.SEC_errorCode = nullptr;
    {
''' + callers[2] + '''
        if (code != 0) return 3;
    }
    return 0;
}
'''
            source = root / "contract.cpp"
            source.write_text(harness)
            binary = root / "contract"
            compiled = subprocess.run([compiler, "-std=c++17", str(source), "-o", str(binary)], capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr.decode())
            subprocess.run([str(binary)], check=True, timeout=10)

    @unittest.skipIf(os.name == "nt", "Requires POSIX shell used by Docker")
    def test_failed_rust_download_never_runs_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_source(root)
            apply_wrapper_fixes(root)
            for name, body in (("curl", "exit 22"), ("sh", '/usr/bin/touch "$MARKER"; exit 0')):
                command = root / name
                command.write_text("#!/bin/sh\n" + body + "\n")
                command.chmod(0o755)
            marker = root / "installer-ran"
            command = (root / "Dockerfile").read_text().removeprefix("RUN ")
            result = subprocess.run(["/bin/sh", "-c", command], env={**os.environ, "PATH": str(root), "MARKER": str(marker)}, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

    def test_patch_is_idempotent_and_rejects_unknown_source_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_source(root)
            apply_wrapper_fixes(root)
            patched = {name: (root / name).read_bytes() for name in FIXTURE}
            self.assertNotEqual(patched["Dockerfile"], FIXTURE["Dockerfile"].encode())
            apply_wrapper_fixes(root)
            self.assertEqual(patched, {name: (root / name).read_bytes() for name in FIXTURE})
            self.make_source(root)
            (root / "src/daemon/apple/playback.cpp").write_text("unexpected upstream change")
            before = {name: (root / name).read_bytes() for name in FIXTURE}
            with self.assertRaises(ValueError):
                apply_wrapper_fixes(root)
            self.assertEqual(before, {name: (root / name).read_bytes() for name in FIXTURE})
