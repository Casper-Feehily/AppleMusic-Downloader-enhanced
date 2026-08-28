from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from amdl.wrapper_setup import (
    APPLE_BUILD,
    APPLE_PACKAGE,
    APPLE_VERSION,
    ARCH,
    SetupBusy,
    SetupError,
    WrapperSetupManager,
    _extract_source,
    extract_libraries,
    find_bundles,
    inspect_bundle,
)


def _write_apkm(path: Path, libs: dict[str, bytes], *, version: str = APPLE_VERSION, arches=None) -> None:
    apk_data = io.BytesIO()
    with zipfile.ZipFile(apk_data, "w") as apk:
        for name, data in libs.items():
            apk.writestr(f"lib/{ARCH}/{name}", data)
    info = {
        "pname": APPLE_PACKAGE,
        "release_version": version,
        "versioncode": APPLE_BUILD,
        "arches": arches if arches is not None else [ARCH],
    }
    with zipfile.ZipFile(path, "w") as apkm:
        apkm.writestr("info.json", json.dumps(info))
        apkm.writestr("split_config.arm64_v8a.apk", apk_data.getvalue())


class BundleTests(unittest.TestCase):
    def test_matching_apkm_is_found_and_libraries_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "music.apkm"
            libs = {"libandroidappmusic.so": b"apple", "libCoreADI.so": b"core"}
            _write_apkm(package, libs)
            manifest = root / "LIBS_VERSION.json"
            manifest.write_text(json.dumps({"libs": {ARCH: {
                name: hashlib.sha256(data).hexdigest() for name, data in libs.items()
            }}}))

            self.assertEqual(find_bundles(root), [package])
            extract_libraries(package, manifest, root / "rootfs")
            self.assertEqual((root / "rootfs/system/lib64/libCoreADI.so").read_bytes(), b"core")

    def test_wrong_version_and_missing_arm64_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wrong = root / "wrong.apkm"
            _write_apkm(wrong, {"libandroidappmusic.so": b"x"}, version="4.0")
            with self.assertRaisesRegex(SetupError, "3.6.0-beta"):
                inspect_bundle(wrong)
            no_arm = root / "no-arm.apkm"
            _write_apkm(no_arm, {"libandroidappmusic.so": b"x"}, arches=["x86_64"])
            with self.assertRaises(SetupError):
                inspect_bundle(no_arm)

    def test_multiple_candidates_require_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("one.apkm", "two.apkm"):
                _write_apkm(root / name, {"libandroidappmusic.so": b"x"})
            self.assertEqual(len(find_bundles(root)), 2)

    def test_hash_mismatch_and_malicious_zip_path_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "music.apkm"
            _write_apkm(package, {"libandroidappmusic.so": b"wrong"})
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"libs": {ARCH: {
                "libandroidappmusic.so": hashlib.sha256(b"right").hexdigest()
            }}}))
            with self.assertRaisesRegex(SetupError, "hash mismatch"):
                extract_libraries(package, manifest, root / "rootfs")

            malicious = root / "malicious.apkm"
            with zipfile.ZipFile(malicious, "w") as archive:
                archive.writestr("../outside", b"no")
                archive.writestr("info.json", "{}")
            with self.assertRaisesRegex(SetupError, "unsafe path"):
                inspect_bundle(malicious)

    def test_source_tar_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as tf:
                data = b"bad"
                member = tarfile.TarInfo("wrapper/../../outside")
                member.size = len(data)
                tf.addfile(member, io.BytesIO(data))
            with self.assertRaisesRegex(SetupError, "unsafe path"):
                _extract_source(archive, root / "out")


class ManagerTests(unittest.TestCase):
    def test_concurrent_start_is_rejected(self) -> None:
        manager = WrapperSetupManager()
        manager._running = True
        with self.assertRaises(SetupBusy):
            manager.start()

    def test_retry_clears_previous_confirmation(self) -> None:
        manager = WrapperSetupManager()
        manager._state["requires_confirmation"] = True
        with patch("amdl.wrapper_setup.threading.Thread"):
            state = manager.start()
        self.assertFalse(state["requires_confirmation"])

    def test_existing_compatible_wrapper_is_reused(self) -> None:
        manager = WrapperSetupManager()
        probe = {"version": "0.0.2", "compatible": True, "playback_ready": True}
        with (
            patch("amdl.wrapper_setup.platform.system", return_value="Darwin"),
            patch("amdl.wrapper_setup.platform.machine", return_value="arm64"),
            patch.object(manager, "_ensure_docker", return_value=Path("/docker")),
            patch.object(manager, "_select_bundle", return_value=Path("music.apkm")),
            patch.object(manager, "_probe", return_value=probe),
            patch.object(manager, "_log"),
        ):
            manager._running = True
            manager._setup(None, False)
        self.assertEqual(manager._state["phase"], "ready")

    def test_incompatible_container_requires_confirmation(self) -> None:
        manager = WrapperSetupManager()
        with (
            patch("amdl.wrapper_setup.platform.system", return_value="Darwin"),
            patch("amdl.wrapper_setup.platform.machine", return_value="arm64"),
            patch.object(manager, "_ensure_docker", return_value=Path("/docker")),
            patch.object(manager, "_select_bundle", return_value=Path("music.apkm")),
            patch.object(manager, "_probe", return_value=None),
            patch.object(manager, "_containers", return_value=[{"name": "wrapper-v2", "status": "Up", "ports": "0.0.0.0:80->80/tcp"}]),
            patch.object(manager, "_log"),
        ):
            manager._running = True
            manager._setup(None, False)
        self.assertEqual(manager._state["phase"], "error")
        self.assertTrue(manager._state["requires_confirmation"])
        self.assertNotIn("music.apkm", manager._state["error"])

    def test_missing_docker_error_is_safe(self) -> None:
        manager = WrapperSetupManager()
        with (
            patch("amdl.wrapper_setup.platform.system", return_value="Darwin"),
            patch("amdl.wrapper_setup.platform.machine", return_value="arm64"),
            patch.object(manager, "_ensure_docker", side_effect=SetupError("Docker Desktop is not installed")),
            patch.object(manager, "_log"),
        ):
            manager._running = True
            manager._setup(None, False)
        self.assertEqual(manager._state["phase"], "error")
        self.assertEqual(manager._state["error"], "Docker Desktop is not installed")

    def test_non_wrapper_port_conflict_is_reported(self) -> None:
        manager = WrapperSetupManager()
        with (
            patch("amdl.wrapper_setup.platform.system", return_value="Darwin"),
            patch("amdl.wrapper_setup.platform.machine", return_value="arm64"),
            patch("amdl.wrapper_setup._port_available", return_value=False),
            patch.object(manager, "_ensure_docker", return_value=Path("/docker")),
            patch.object(manager, "_select_bundle", return_value=Path("music.apkm")),
            patch.object(manager, "_probe", return_value=None),
            patch.object(manager, "_containers", return_value=[]),
            patch.object(manager, "_log"),
        ):
            manager._running = True
            manager._setup(None, False)
        self.assertEqual(manager._state["error"], "Port 80 or 10020 is already in use by a non-Wrapper process")


if __name__ == "__main__":
    unittest.main()
