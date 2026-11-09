from __future__ import annotations

import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from amdl import update_helpers


@unittest.skipUnless(sys.platform == "darwin", "Requires macOS native helpers")
class MacHelperTests(unittest.TestCase):
    def make_app(self, path: Path, marker: Path, text: str):
        executable = path / "Contents/MacOS/AppleMusicDownloader"
        executable.parent.mkdir(parents=True)
        # LaunchServices requires a native executable rather than a shell script.
        source = ('#include <stdio.h>\nint main(void) { FILE *f = fopen('
                  + json.dumps(str(marker), ensure_ascii=False)
                  + ', "w"); if (!f) return 1; fputs('
                  + json.dumps(text) + ', f); fclose(f); return 0; }')
        subprocess.run(["cc", "-x", "c", "-o", str(executable), "-"],
                       input=source.encode(), capture_output=True, check=True)
        (path / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleExecutable": "AppleMusicDownloader", "CFBundleIdentifier": "test.amdl.update",
            "CFBundlePackageType": "APPL", "CFBundleName": "AMDL Update Test",
        }))

    def test_installs_and_reopens_disposable_app_with_quoted_paths(self):
        with tempfile.TemporaryDirectory(prefix="amdl 空格 ' ") as temp:
            root = Path(temp)
            target = root / "Old App.app"
            staged = root / "New App.app"
            marker = root / "launched.txt"
            self.make_app(target, marker, "old")
            self.make_app(staged, marker, "new")
            work = root / "work"
            work.mkdir()
            script = work / "install.sh"
            script.write_text(update_helpers.MAC_SCRIPT)
            result = subprocess.run(["/bin/sh", str(script), "99999999", str(staged), str(target),
                                     str(work), str(root / "error.txt")], capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue((target / "Contents/MacOS/AppleMusicDownloader").exists())
            self.assertTrue((root / "Old App.app.previous-work").exists())
            import time
            for _ in range(30):
                if marker.exists():
                    break
                time.sleep(0.1)
            self.assertEqual(marker.read_text(), "new")

    def test_abort_keeps_original_application(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work = root / "work"
            work.mkdir()
            (work / "abort").touch()
            target, staged = root / "old.app", root / "new.app"
            target.mkdir()
            staged.mkdir()
            script = work / "install.sh"
            script.write_text(update_helpers.MAC_SCRIPT)
            subprocess.run(["/bin/sh", str(script), "99999999", str(staged), str(target),
                            str(work), str(root / "error.txt")], check=True, timeout=10)
            self.assertTrue(target.is_dir())
            self.assertTrue(staged.is_dir())

    def test_failed_reopen_restores_and_reopens_previous_app(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work = root / "work"
            work.mkdir()
            target, staged = root / "old.app", root / "broken.app"
            marker = root / "launched.txt"
            self.make_app(target, marker, "old")
            staged.mkdir()
            (root / "settings.json").write_text("preserve settings")
            script = work / "install.sh"
            script.write_text(update_helpers.MAC_SCRIPT)
            result = subprocess.run(["/bin/sh", str(script), "99999999", str(staged), str(target),
                                     str(work), str(root / "error.txt")], capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertTrue((target / "Contents/MacOS/AppleMusicDownloader").exists())
            self.assertIn("restored", (root / "error.txt").read_text())
            self.assertEqual((root / "settings.json").read_text(), "preserve settings")

    def test_prepares_verified_dmg_without_modifying_current_app(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contents = root / "contents"
            contents.mkdir()
            source = contents / "AppleMusicDownloader-macos-arm64.app"
            target = root / "Installed.app"
            self.make_app(source, root / "launched.txt", "new")
            self.make_app(target, root / "launched.txt", "old")
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(source)],
                           capture_output=True, check=True)
            package = root / "update.dmg"
            subprocess.run(["/usr/bin/hdiutil", "create", "-srcfolder", str(contents),
                            "-format", "UDZO", str(package)], capture_output=True, check=True)
            work = root / "work"
            work.mkdir()
            staged = update_helpers.prepare_install("darwin", package, target, work)
            self.assertTrue((staged / "Contents/MacOS/AppleMusicDownloader").is_file())
            self.assertTrue((target / "Contents/MacOS/AppleMusicDownloader").is_file())
            self.assertNotEqual(staged, target)


@unittest.skipUnless(sys.platform == "win32", "Requires Windows PowerShell")
class WindowsHelperTests(unittest.TestCase):
    def compile_exe(self, root: Path, name: str, source: str) -> Path:
        compiler = root / "compile.ps1"
        compiler.write_text('param($SourceFile, $OutputFile)\n$ErrorActionPreference="Stop"\n'
                            'Add-Type -TypeDefinition ([IO.File]::ReadAllText($SourceFile)) '
                            '-OutputAssembly $OutputFile -OutputType ConsoleApplication\n')
        source_path = root / (name + ".cs")
        source_path.write_text(source)
        output = root / (name + ".exe")
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(compiler),
                        "-SourceFile", str(source_path), "-OutputFile", str(output)],
                       capture_output=True, check=True, timeout=30)
        return output

    def run_helper(self, failed=False):
        with tempfile.TemporaryDirectory(prefix="amdl 空格 '") as temp:
            root = Path(temp)
            app = self.compile_exe(root, "Application", '''using System; using System.IO;
public class App { public static void Main() {
File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "restarted.txt"), "yes"); }}''')
            installer = self.compile_exe(root, "Installer", '''using System; using System.IO;
public class Installer { public static int Main(string[] args) {
foreach (string arg in args) { if (arg.StartsWith("/DIR=")) {
File.WriteAllText(Path.Combine(arg.Substring(5).Trim('"'), "installed.txt"), "yes"); }}
return ''' + ("1" if failed else "0") + "; }}")
            work = root / "work"
            work.mkdir()
            script = work / "install.ps1"
            script.write_text(update_helpers.WINDOWS_SCRIPT, encoding="utf-8-sig")
            error_file = root / "error.txt"
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                                     "-ParentProcessId", "2147483646", "-Package", str(installer), "-Target", str(app),
                                     "-Work", str(work), "-ErrorFile", str(error_file)],
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 1 if failed else 0, result.stderr.decode(errors="replace"))
            self.assertEqual((root / "installed.txt").read_text(), "yes")
            import time
            for _ in range(30):
                if (root / "restarted.txt").exists():
                    break
                time.sleep(0.1)
            self.assertTrue((root / "restarted.txt").exists())
            self.assertEqual(error_file.exists(), failed)

    def test_installs_and_restarts_with_quoted_paths(self):
        self.run_helper()

    def test_failed_install_records_error_and_reopens_app(self):
        self.run_helper(failed=True)


if __name__ == "__main__":
    unittest.main()
