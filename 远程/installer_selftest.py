import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import installer


class RemoteInstallerTest(unittest.TestCase):
    def make_bundle(self, root: Path) -> Path:
        bundle = root / "bundle"
        config_dir = bundle / "config"
        config_dir.mkdir(parents=True)
        (bundle / installer.APP_EXE).write_bytes(b"remote-version-one")
        (config_dir / installer.CONFIG_EXAMPLE).write_text(
            '{"host": "example.invalid"}', encoding="utf-8"
        )
        return bundle

    def test_install_preserves_data_and_real_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root)
            target = root / "installed" / "application"
            active_config = target / "config" / "mysql_config.json"
            preserved_data = target / "data" / "keep.db"
            active_config.parent.mkdir(parents=True)
            active_config.write_text('{"host": "production"}', encoding="utf-8")
            preserved_data.parent.mkdir(parents=True)
            preserved_data.write_bytes(b"user-data")

            installer.install_application(target, bundle)

            example = target / "config" / installer.CONFIG_EXAMPLE
            self.assertEqual((target / installer.APP_EXE).read_bytes(), b"remote-version-one")
            self.assertEqual(active_config.read_text(encoding="utf-8"), '{"host": "production"}')
            self.assertEqual(preserved_data.read_bytes(), b"user-data")
            self.assertEqual(example.read_text(encoding="utf-8"), '{"host": "example.invalid"}')
            self.assertTrue((target / "data" / "attachments").is_dir())
            self.assertTrue((target / installer.LAUNCHER_NAME).is_file())

            example.write_text("user-edited-example", encoding="utf-8")
            active_config.write_text("user-edited-config", encoding="utf-8")
            (bundle / installer.APP_EXE).write_bytes(b"remote-version-two")
            installer.install_application(target, bundle)
            self.assertEqual((target / installer.APP_EXE).read_bytes(), b"remote-version-two")
            self.assertEqual(example.read_text(encoding="utf-8"), "user-edited-example")
            self.assertEqual(active_config.read_text(encoding="utf-8"), "user-edited-config")
            self.assertEqual(preserved_data.read_bytes(), b"user-data")

    def test_fresh_install_only_creates_example_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root)
            target = root / "installed" / "application"
            installer.install_application(target, bundle)
            self.assertTrue((target / "config" / installer.CONFIG_EXAMPLE).is_file())
            self.assertFalse((target / "config" / "mysql_config.json").exists())

    def test_rejects_root_and_missing_template_before_install(self):
        with self.assertRaisesRegex(ValueError, "根目录"):
            installer.validate_install_dir(Path.cwd().anchor)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            target = root / "installed" / "application"
            bundle.mkdir()
            (bundle / installer.APP_EXE).write_bytes(b"remote")
            with self.assertRaisesRegex(FileNotFoundError, "配置模板"):
                installer.install_application(target, bundle)
            self.assertFalse(target.exists())

    def test_existing_file_target_is_rejected_without_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root)
            target = root / "application"
            target.write_bytes(b"occupied-target")

            with self.assertRaisesRegex(ValueError, "指向文件"):
                installer.install_application(target, bundle)
            self.assertEqual(target.read_bytes(), b"occupied-target")

    def test_config_directory_conflict_is_detected_before_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root)
            target = root / "installed" / "application"
            target.mkdir(parents=True)
            installed_exe = target / installer.APP_EXE
            installed_exe.write_bytes(b"old-version")
            config_conflict = target / "config"
            config_conflict.write_bytes(b"not-a-directory")

            with self.assertRaisesRegex(ValueError, "同名文件"):
                installer.install_application(target, bundle)
            self.assertEqual(installed_exe.read_bytes(), b"old-version")
            self.assertEqual(config_conflict.read_bytes(), b"not-a-directory")
            self.assertFalse((target / "data").exists())

    def test_config_file_directory_conflicts_are_detected_before_any_update(self):
        for conflict_name in (installer.CONFIG_EXAMPLE, "mysql_config.json"):
            with self.subTest(conflict_name=conflict_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                bundle = self.make_bundle(root)
                target = root / "installed" / "application"
                config_dir = target / "config"
                config_dir.mkdir(parents=True)
                installed_exe = target / installer.APP_EXE
                installed_exe.write_bytes(b"old-version")
                (config_dir / conflict_name).mkdir()

                with self.assertRaisesRegex(ValueError, "同名目录"):
                    installer.install_application(target, bundle)
                self.assertEqual(installed_exe.read_bytes(), b"old-version")
                self.assertFalse((target / "data").exists())

    def test_launcher_directory_conflict_is_detected_before_any_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root)
            target = root / "installed" / "application"
            target.mkdir(parents=True)
            installed_exe = target / installer.APP_EXE
            installed_exe.write_bytes(b"old-version")
            (target / installer.LAUNCHER_NAME).mkdir()

            with self.assertRaisesRegex(ValueError, "同名目录"):
                installer.install_application(target, bundle)
            self.assertEqual(installed_exe.read_bytes(), b"old-version")
            self.assertFalse((target / "config").exists())
            self.assertFalse((target / "data").exists())

    def test_cli_install_to_is_noninteractive_and_returns_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root)
            target = root / "installed" / "application"

            with (
                patch.object(installer, "resource_dir", return_value=bundle),
                patch.object(installer, "Installer", side_effect=AssertionError("GUI 不应启动")),
            ):
                self.assertEqual(installer.main(["--install-to", str(target)]), 0)
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(installer.main(["--install-to", ""]), 1)

            self.assertEqual((target / installer.APP_EXE).read_bytes(), b"remote-version-one")


if __name__ == "__main__":
    unittest.main(verbosity=2)
