"""Native install helpers, kept outside the application being replaced."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import time
from pathlib import Path

from amdl.updater import UpdateError


MAC_SCRIPT = r'''#!/bin/sh
parent_pid=$1
staged=$2
target=$3
work=$4
error_file=$5
backup="$target.previous-${work##*/}"
replaced=0
fail() {
    printf '%s\n' "$1" > "$error_file"
    if [ "$replaced" = 1 ] && [ -d "$backup" ]; then
        # Keep the failed new bundle for diagnosis, restore the old bundle.
        mv "$target" "$work/failed.app" 2>/dev/null || true
        mv "$backup" "$target" || exit 1
    fi
    /usr/bin/open -n "$target" 2>/dev/null || true
    exit 1
}
: > "$work/ready"
attempt=0
while kill -0 "$parent_pid" 2>/dev/null; do
    [ -f "$work/abort" ] && exit 0
    attempt=$((attempt + 1))
    if [ "$attempt" -gt 120 ]; then
        printf '%s\n' 'The application did not close; update cancelled.' > "$error_file"
        exit 1
    fi
    sleep 1
done
[ -f "$work/abort" ] && exit 0
[ -d "$staged" ] || fail 'Prepared application is missing.'
[ ! -e "$backup" ] || fail 'The application backup path is already occupied.'
mv "$target" "$backup" || fail 'Could not back up the application.'
replaced=1
mv "$staged" "$target" || fail 'Could not install the new application.'
/usr/bin/open -n "$target" || fail 'Could not reopen the application; restored the previous version.'
rm -f "$error_file"
rm -f "$work/"*.dmg
exit 0
'''


WINDOWS_SCRIPT = r'''param(
    [int]$ParentProcessId,
    [string]$Package,
    [string]$Target,
    [string]$Work,
    [string]$ErrorFile
)
$ErrorActionPreference = 'Stop'
try {
    Set-Content -LiteralPath (Join-Path $Work 'ready') -Value ''
    $deadline = (Get-Date).AddSeconds(120)
    while (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue) {
        if (Test-Path -LiteralPath (Join-Path $Work 'abort')) { exit 0 }
        if ((Get-Date) -gt $deadline) { throw 'The application did not close; update cancelled.' }
        Start-Sleep -Seconds 1
    }
    if (Test-Path -LiteralPath (Join-Path $Work 'abort')) { exit 0 }
    $installDir = Split-Path -Parent $Target
    $log = Join-Path $Work 'installer.log'
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/SP-', '/NORESTART',
                   '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS',
                   '/RESTARTEXITCODE=3010', ('/DIR="' + $installDir + '"'), ('/LOG="' + $log + '"'))
    $installer = Start-Process -FilePath $Package -ArgumentList $arguments -Wait -PassThru
    if ($installer.ExitCode -ne 0) { throw "Installer exited with code $($installer.ExitCode). See $log" }
    Remove-Item -LiteralPath $ErrorFile -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $Target -WorkingDirectory $installDir
    Remove-Item -LiteralPath $Package -Force -ErrorAction SilentlyContinue
} catch {
    $_.Exception.Message | Set-Content -LiteralPath $ErrorFile -Encoding UTF8
    # Do not start a second instance when the original failed to close.
    if (-not (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) {
        if (Test-Path -LiteralPath $Target) {
            Start-Process -FilePath $Target -WorkingDirectory (Split-Path -Parent $Target)
        }
    }
    exit 1
}
'''


def _run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        raise UpdateError(result.stderr.decode(errors="replace")[:1000] or "Could not prepare installer")
    return result


def prepare_install(system: str, package: Path, target: Path, work: Path) -> Path:
    if system == "win32":
        # Detect an HTML/error document even if release metadata was incorrect.
        with package.open("rb") as source:
            if source.read(2) != b"MZ":
                raise UpdateError("The Windows installer is not an executable")
        return package

    mount = work / "mount"
    mount.mkdir()
    staged = target.parent / f".amdl-{work.name}.app"
    attached = False
    try:
        _run(["/usr/bin/hdiutil", "attach", str(package), "-readonly", "-nobrowse", "-mountpoint", str(mount)])
        attached = True
        source = mount / "AppleMusicDownloader-macos-arm64.app"
        if not source.is_dir() or source.is_symlink():
            raise UpdateError("The macOS installer does not contain the expected application")
        _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(source)])
        info = plistlib.loads((source / "Contents/Info.plist").read_bytes())
        if info.get("CFBundleExecutable") != "AppleMusicDownloader":
            raise UpdateError("Unexpected macOS application executable")
        _run(["/usr/bin/ditto", str(source), str(staged)])
        _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged)])
        return staged
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    finally:
        if attached:
            # Eject before installation so no disk image is left mounted.
            _run(["/usr/bin/hdiutil", "detach", str(mount)])


def launch_helper(system: str, staged: Path, target: Path, work: Path, error_file: Path) -> None:
    if system == "win32":
        script = work / "install.ps1"
        script.write_text(WINDOWS_SCRIPT, encoding="utf-8-sig")
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                   "-ParentProcessId", str(os.getpid()), "-Package", str(staged), "-Target", str(target),
                   "-Work", str(work), "-ErrorFile", str(error_file)]
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        script = work / "install.sh"
        script.write_text(MAC_SCRIPT, encoding="utf-8")
        command = ["/bin/sh", str(script), str(os.getpid()), str(staged), str(target), str(work), str(error_file)]
        flags = 0
    with (work / "helper.log").open("ab") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   cwd=work, creationflags=flags, start_new_session=system != "win32")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if (work / "ready").exists():
            return
        if process.poll() is not None:
            break
        time.sleep(0.1)
    (work / "abort").touch()
    raise UpdateError("The installation helper could not start; see helper.log")
