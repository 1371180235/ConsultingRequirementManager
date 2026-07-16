import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import installer


class LocalInstallerTest(unittest.TestCase):
    def test_install_and_upgrade_preserve_existing_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            target = root / "installed" / "application"
            bundle.mkdir()
            (bundle / installer.APP_EXE).write_bytes(b"version-one")

            preserved_file = target / "data" / "keep.db"
            preserved_file.parent.mkdir(parents=True)
            preserved_file.write_bytes(b"user-data")

            installed = installer.install_application(target, bundle)
            self.assertEqual(installed, target.resolve())
            self.assertEqual((target / installer.APP_EXE).read_bytes(), b"version-one")
            self.assertEqual(preserved_file.read_bytes(), b"user-data")
            self.assertTrue((target / "data" / "attachments").is_dir())
            self.assertTrue((target / "data" / "backups").is_dir())
            self.assertTrue((target / "data" / "exports").is_dir())
            self.assertTrue((target / "data" / "logs").is_dir())
            launcher = target / installer.LAUNCHER_NAME
            self.assertIn(installer.APP_EXE, launcher.read_text(encoding="ascii"))

            (bundle / installer.APP_EXE).write_bytes(b"version-two")
            installer.install_application(target, bundle)
            self.assertEqual((target / installer.APP_EXE).read_bytes(), b"version-two")
            self.assertEqual(preserved_file.read_bytes(), b"user-data")

    def test_rejects_unsafe_or_invalid_targets(self):
        filesystem_root = Path.cwd().anchor
        with self.assertRaisesRegex(ValueError, "根目录"):
            installer.validate_install_dir(filesystem_root)
        with self.assertRaisesRegex(ValueError, "绝对路径"):
            installer.validate_install_dir("relative/path")
        with self.assertRaisesRegex(ValueError, "选择安装路径"):
            installer.validate_install_dir("   ")

    def test_existing_file_target_is_rejected_without_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            target = root / "application"
            bundle.mkdir()
            (bundle / installer.APP_EXE).write_bytes(b"new-version")
            target.write_bytes(b"occupied-target")

            with self.assertRaisesRegex(ValueError, "指向文件"):
                installer.install_application(target, bundle)
            self.assertEqual(target.read_bytes(), b"occupied-target")

    def test_missing_executable_does_not_create_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "empty-bundle"
            target = root / "installed" / "application"
            bundle.mkdir()
            with self.assertRaises(FileNotFoundError):
                installer.install_application(target, bundle)
            self.assertFalse(target.exists())

    def test_directory_conflict_is_detected_before_executable_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            target = root / "installed" / "application"
            bundle.mkdir()
            target.mkdir(parents=True)
            (bundle / installer.APP_EXE).write_bytes(b"new-version")
            installed_exe = target / installer.APP_EXE
            installed_exe.write_bytes(b"old-version")
            data_conflict = target / "data"
            data_conflict.write_bytes(b"not-a-directory")

            with self.assertRaisesRegex(ValueError, "同名文件"):
                installer.install_application(target, bundle)
            self.assertEqual(installed_exe.read_bytes(), b"old-version")
            self.assertEqual(data_conflict.read_bytes(), b"not-a-directory")

    def test_launcher_directory_conflict_is_detected_before_any_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            target = root / "installed" / "application"
            bundle.mkdir()
            target.mkdir(parents=True)
            (bundle / installer.APP_EXE).write_bytes(b"new-version")
            installed_exe = target / installer.APP_EXE
            installed_exe.write_bytes(b"old-version")
            (target / installer.LAUNCHER_NAME).mkdir()

            with self.assertRaisesRegex(ValueError, "同名目录"):
                installer.install_application(target, bundle)
            self.assertEqual(installed_exe.read_bytes(), b"old-version")
            self.assertFalse((target / "data").exists())

    def test_executable_directory_conflict_is_detected_before_any_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            target = root / "installed" / "application"
            bundle.mkdir()
            target.mkdir(parents=True)
            (bundle / installer.APP_EXE).write_bytes(b"new-version")
            (target / installer.APP_EXE).mkdir()

            with self.assertRaisesRegex(ValueError, "同名目录"):
                installer.install_application(target, bundle)
            self.assertTrue((target / installer.APP_EXE).is_dir())
            self.assertFalse((target / installer.LAUNCHER_NAME).exists())
            self.assertFalse((target / "data").exists())

    def test_cli_install_to_is_noninteractive_and_returns_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            target = root / "installed" / "application"
            bundle.mkdir()
            (bundle / installer.APP_EXE).write_bytes(b"cli-version")

            with (
                patch.object(installer, "resource_dir", return_value=bundle),
                patch.object(installer, "Installer", side_effect=AssertionError("GUI 不应启动")),
            ):
                self.assertEqual(installer.main(["--install-to", str(target)]), 0)
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(installer.main(["--install-to", ""]), 1)

            self.assertEqual((target / installer.APP_EXE).read_bytes(), b"cli-version")


if __name__ == "__main__":
    unittest.main(verbosity=2)
