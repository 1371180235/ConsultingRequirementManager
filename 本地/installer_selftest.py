import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import installer


class LocalInstallerTest(unittest.TestCase):
    @staticmethod
    def make_bundle(root, executable=b"application", uninstaller=b"uninstaller"):
        bundle = root / "bundle"
        bundle.mkdir()
        if executable is not None:
            (bundle / installer.APP_EXE).write_bytes(executable)
        if uninstaller is not None:
            (bundle / installer.UNINSTALLER_EXE).write_bytes(uninstaller)
        (bundle / installer.LICENSE_FILENAME).write_text(
            "软件最终用户许可协议\n\n测试许可文本。继续安装即表示同意。",
            encoding="utf-8",
        )
        return bundle

    def install_fixture(self, root, executable=b"application"):
        bundle = self.make_bundle(root, executable)
        target = root / "installed" / "application"
        installer.install_application(target, bundle, license_accepted=True)
        return bundle, target

    def test_license_gate_accepts_only_explicit_true(self):
        self.assertIsNone(installer.require_license_acceptance(True))
        for rejected_value in (False, None, 0, 1, "yes"):
            with self.subTest(rejected_value=rejected_value):
                with self.assertRaisesRegex(installer.LicenseNotAcceptedError, "接受"):
                    installer.require_license_acceptance(rejected_value)

    def test_license_resource_and_packaging_spec_cover_required_terms(self):
        license_text = installer.load_license_text(Path(installer.__file__).resolve().parent)
        for required_term in (
            "授权范围",
            "本地数据保存与备份",
            "禁止行为",
            "免责声明与责任限制",
            "协议终止",
            "适用法律与争议解决",
            "继续安装",
        ):
            with self.subTest(required_term=required_term):
                self.assertIn(required_term, license_text)

        setup_spec = Path(installer.__file__).with_name(
            "ConsultingRequirementManagerSetup.spec"
        ).read_text(encoding="utf-8")
        self.assertIn(installer.LICENSE_FILENAME, setup_spec)

    def test_rejected_license_does_not_touch_install_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root)
            target = root / "installed" / "application"

            with self.assertRaises(installer.LicenseNotAcceptedError):
                installer.install_application(target, bundle, license_accepted=False)

            self.assertFalse(target.exists())

    def test_install_and_upgrade_preserve_existing_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root, b"version-one")
            target = root / "installed" / "application"

            preserved_file = target / "data" / "keep.db"
            preserved_file.parent.mkdir(parents=True)
            preserved_file.write_bytes(b"user-data")

            installed = installer.install_application(target, bundle, license_accepted=True)
            self.assertEqual(installed, target.resolve())
            self.assertEqual((target / installer.APP_EXE).read_bytes(), b"version-one")
            self.assertEqual(preserved_file.read_bytes(), b"user-data")
            self.assertTrue((target / "data" / "attachments").is_dir())
            self.assertTrue((target / "data" / "backups").is_dir())
            self.assertTrue((target / "data" / "exports").is_dir())
            self.assertTrue((target / "data" / "logs").is_dir())
            self.assertIn(
                "软件最终用户许可协议",
                (target / installer.LICENSE_FILENAME).read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (target / installer.UNINSTALLER_EXE).read_bytes(),
                b"uninstaller",
            )
            marker = json.loads(
                (target / installer.INSTALL_MARKER_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(marker["application_id"], installer.APP_REGISTRY_ID)
            self.assertEqual(Path(marker["install_dir"]), target.resolve())
            launcher = target / installer.LAUNCHER_NAME
            self.assertIn(installer.APP_EXE, launcher.read_text(encoding="ascii"))

            (bundle / installer.APP_EXE).write_bytes(b"version-two")
            (bundle / installer.UNINSTALLER_EXE).write_bytes(b"uninstaller-two")
            installer.install_application(target, bundle, license_accepted=True)
            self.assertEqual((target / installer.APP_EXE).read_bytes(), b"version-two")
            self.assertEqual(
                (target / installer.UNINSTALLER_EXE).read_bytes(),
                b"uninstaller-two",
            )
            self.assertEqual(preserved_file.read_bytes(), b"user-data")

    def test_uninstall_preserves_data_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _bundle, target = self.install_fixture(root)
            business_data = target / "data" / "attachments" / "contract.txt"
            business_data.write_text("business-data", encoding="utf-8")

            result = installer.uninstall_application(target, confirmed=True)

            self.assertTrue(result.data_preserved)
            self.assertFalse(result.install_dir_removed)
            self.assertEqual(business_data.read_text(encoding="utf-8"), "business-data")
            for filename in (
                installer.APP_EXE,
                installer.LAUNCHER_NAME,
                installer.LICENSE_FILENAME,
                installer.UNINSTALLER_EXE,
                installer.INSTALL_MARKER_FILENAME,
            ):
                self.assertFalse((target / filename).exists())

    def test_full_uninstall_requires_second_confirmation_and_removes_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _bundle, target = self.install_fixture(root)
            business_data = target / "data" / "attachments" / "nested" / "contract.txt"
            business_data.parent.mkdir()
            business_data.write_text("business-data", encoding="utf-8")

            with self.assertRaises(installer.UninstallCancelledError):
                installer.uninstall_application(
                    target,
                    confirmed=True,
                    delete_data=True,
                    data_deletion_confirmed=False,
                )
            self.assertTrue((target / installer.APP_EXE).is_file())
            self.assertTrue(business_data.is_file())

            result = installer.uninstall_application(
                target,
                confirmed=True,
                delete_data=True,
                data_deletion_confirmed=True,
            )
            self.assertFalse(result.data_preserved)
            self.assertTrue(result.install_dir_removed)
            self.assertFalse(target.exists())

    def test_cancelled_uninstall_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _bundle, target = self.install_fixture(root)
            business_data = target / "data" / "app.db"
            business_data.write_bytes(b"database")

            with self.assertRaises(installer.UninstallCancelledError):
                installer.uninstall_application(target, confirmed=False)

            self.assertTrue((target / installer.APP_EXE).is_file())
            self.assertTrue((target / installer.UNINSTALLER_EXE).is_file())
            self.assertEqual(business_data.read_bytes(), b"database")

    def test_uninstall_rejects_root_marker_mismatch_and_reparse_points(self):
        with self.assertRaisesRegex(ValueError, "根目录"):
            installer.validate_uninstall_dir(Path.cwd().anchor)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _bundle, target = self.install_fixture(root)
            marker_path = target / installer.INSTALL_MARKER_FILENAME
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["install_dir"] = str(root / "different-install")
            marker_path.write_text(json.dumps(marker), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "不一致"):
                installer.uninstall_application(target, confirmed=True)
            self.assertTrue((target / installer.APP_EXE).is_file())

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _bundle, target = self.install_fixture(root)
            original_reparse_check = installer._is_reparse_point

            def fake_reparse_check(path):
                return Path(path) == target or original_reparse_check(Path(path))

            with patch.object(installer, "_is_reparse_point", side_effect=fake_reparse_check):
                with self.assertRaisesRegex(ValueError, "重解析点"):
                    installer.uninstall_application(target, confirmed=True)
            self.assertTrue((target / installer.APP_EXE).is_file())

    def test_full_uninstall_rejects_reparse_inside_data_before_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _bundle, target = self.install_fixture(root)
            suspicious = target / "data" / "attachments" / "linked-data"
            suspicious.write_bytes(b"not-deleted")
            original_reparse_check = installer._is_reparse_point

            def fake_reparse_check(path):
                return Path(path) == suspicious or original_reparse_check(Path(path))

            with patch.object(installer, "_is_reparse_point", side_effect=fake_reparse_check):
                with self.assertRaisesRegex(ValueError, "重解析点"):
                    installer.uninstall_application(
                        target,
                        confirmed=True,
                        delete_data=True,
                        data_deletion_confirmed=True,
                    )

            self.assertTrue((target / installer.APP_EXE).is_file())
            self.assertEqual(suspicious.read_bytes(), b"not-deleted")

    def test_windows_uninstall_registry_values_are_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "installed" / "application"
            target.mkdir(parents=True)
            (target / installer.APP_EXE).write_bytes(b"application")
            (target / installer.UNINSTALLER_EXE).write_bytes(b"uninstaller")

            values = installer.uninstall_registry_values(target)

            self.assertEqual(values["DisplayName"], installer.APP_TITLE)
            self.assertEqual(values["DisplayVersion"], installer.APP_VERSION)
            self.assertEqual(values["InstallLocation"], str(target.resolve()))
            self.assertIn(installer.UNINSTALLER_EXE, values["UninstallString"])
            self.assertIn("--uninstall-from", values["UninstallString"])
            self.assertEqual(values["NoModify"], 1)
            self.assertEqual(values["NoRepair"], 1)
            self.assertGreater(values["EstimatedSize"], 0)

    def test_windows_uninstall_registry_is_written_and_removed(self):
        import winreg

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "installed" / "application"
            target.mkdir(parents=True)
            (target / installer.APP_EXE).write_bytes(b"application")
            (target / installer.UNINSTALLER_EXE).write_bytes(b"uninstaller")

            with (
                patch.object(winreg, "CreateKeyEx") as create_key,
                patch.object(winreg, "SetValueEx") as set_value,
                patch.object(winreg, "DeleteKey") as delete_key,
            ):
                registry_handle = object()
                create_key.return_value.__enter__.return_value = registry_handle

                self.assertTrue(installer.register_uninstall_entry(target))
                written_names = {call.args[1] for call in set_value.call_args_list}
                self.assertEqual(
                    written_names,
                    set(installer.uninstall_registry_values(target)),
                )
                self.assertTrue(installer.unregister_uninstall_entry())

                create_key.assert_called_once_with(
                    winreg.HKEY_CURRENT_USER,
                    installer.UNINSTALL_REGISTRY_KEY,
                    0,
                    winreg.KEY_SET_VALUE,
                )
                delete_key.assert_called_once_with(
                    winreg.HKEY_CURRENT_USER,
                    installer.UNINSTALL_REGISTRY_KEY,
                )

    def test_double_click_installed_uninstaller_starts_temporary_worker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "installed" / "application"
            executable = target / installer.UNINSTALLER_EXE
            target.mkdir(parents=True)
            executable.write_bytes(b"installed-uninstaller")
            sentinel = target / installer.APP_EXE
            sentinel.write_bytes(b"application-must-remain")
            with (
                patch.object(installer.sys, "frozen", True, create=True),
                patch.object(installer.sys, "executable", str(executable)),
                patch.object(installer, "start_uninstall_worker") as start_worker,
                patch.object(installer, "Uninstaller") as uninstaller_window,
            ):
                self.assertEqual(installer.main([]), 0)
                start_worker.assert_called_once_with(str(target))
                uninstaller_window.assert_not_called()

                start_worker.reset_mock()
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(
                        installer.main(
                            [
                                "--uninstall-from",
                                str(target),
                                "--uninstall-worker",
                            ]
                        ),
                        1,
                    )
                start_worker.assert_not_called()
                uninstaller_window.assert_not_called()

            self.assertEqual(sentinel.read_bytes(), b"application-must-remain")
            self.assertEqual(executable.read_bytes(), b"installed-uninstaller")

        with tempfile.TemporaryDirectory(
            prefix="CRM-Uninstall-",
            dir=tempfile.gettempdir(),
        ) as worker_dir:
            worker_executable = Path(worker_dir) / installer.UNINSTALLER_EXE
            worker_executable.write_bytes(b"temporary-worker")
            with (
                patch.object(installer.sys, "frozen", True, create=True),
                patch.object(installer.sys, "executable", str(worker_executable)),
            ):
                self.assertEqual(
                    installer.validate_uninstall_worker_context(),
                    Path(worker_dir).resolve(),
                )

    def test_rejects_unsafe_or_invalid_targets(self):
        filesystem_root = Path.cwd().anchor
        with self.assertRaisesRegex(ValueError, "根目录"):
            installer.validate_install_dir(filesystem_root)
        with self.assertRaisesRegex(ValueError, "绝对路径"):
            installer.validate_install_dir("relative/path")
        with self.assertRaisesRegex(ValueError, "选择安装路径"):
            installer.validate_install_dir("   ")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reparse_parent = root / "linked-parent"
            reparse_parent.mkdir()
            requested_target = reparse_parent / "application"
            original_reparse_check = installer._is_reparse_point

            def fake_reparse_check(path):
                return Path(path) == reparse_parent or original_reparse_check(Path(path))

            with patch.object(installer, "_is_reparse_point", side_effect=fake_reparse_check):
                with self.assertRaisesRegex(ValueError, "重解析点"):
                    installer.validate_install_dir(requested_target)
            self.assertFalse(requested_target.exists())

    def test_existing_file_target_is_rejected_without_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root, b"new-version")
            target = root / "application"
            target.write_bytes(b"occupied-target")

            with self.assertRaisesRegex(ValueError, "指向文件"):
                installer.install_application(target, bundle, license_accepted=True)
            self.assertEqual(target.read_bytes(), b"occupied-target")

    def test_missing_executable_does_not_create_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root, executable=None)
            target = root / "installed" / "application"
            with self.assertRaises(FileNotFoundError):
                installer.install_application(target, bundle, license_accepted=True)
            self.assertFalse(target.exists())

    def test_directory_conflict_is_detected_before_executable_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root, b"new-version")
            target = root / "installed" / "application"
            target.mkdir(parents=True)
            installed_exe = target / installer.APP_EXE
            installed_exe.write_bytes(b"old-version")
            data_conflict = target / "data"
            data_conflict.write_bytes(b"not-a-directory")

            with self.assertRaisesRegex(ValueError, "同名文件"):
                installer.install_application(target, bundle, license_accepted=True)
            self.assertEqual(installed_exe.read_bytes(), b"old-version")
            self.assertEqual(data_conflict.read_bytes(), b"not-a-directory")

    def test_launcher_directory_conflict_is_detected_before_any_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root, b"new-version")
            target = root / "installed" / "application"
            target.mkdir(parents=True)
            installed_exe = target / installer.APP_EXE
            installed_exe.write_bytes(b"old-version")
            (target / installer.LAUNCHER_NAME).mkdir()

            with self.assertRaisesRegex(ValueError, "同名目录"):
                installer.install_application(target, bundle, license_accepted=True)
            self.assertEqual(installed_exe.read_bytes(), b"old-version")
            self.assertFalse((target / "data").exists())

    def test_executable_directory_conflict_is_detected_before_any_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root, b"new-version")
            target = root / "installed" / "application"
            target.mkdir(parents=True)
            (target / installer.APP_EXE).mkdir()

            with self.assertRaisesRegex(ValueError, "同名目录"):
                installer.install_application(target, bundle, license_accepted=True)
            self.assertTrue((target / installer.APP_EXE).is_dir())
            self.assertFalse((target / installer.LAUNCHER_NAME).exists())
            self.assertFalse((target / "data").exists())

    def test_cli_install_to_is_noninteractive_and_returns_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self.make_bundle(root, b"cli-version")
            target = root / "installed" / "application"

            with (
                patch.object(installer, "resource_dir", return_value=bundle),
                patch.object(installer, "Installer", side_effect=AssertionError("GUI 不应启动")),
                patch.object(installer, "register_uninstall_entry", return_value=True) as register_entry,
            ):
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(installer.main(["--install-to", str(target)]), 1)
                self.assertFalse(target.exists())
                self.assertEqual(
                    installer.main(["--install-to", str(target), "--accept-license"]),
                    0,
                )
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(installer.main(["--install-to", "", "--accept-license"]), 1)

            self.assertEqual((target / installer.APP_EXE).read_bytes(), b"cli-version")
            register_entry.assert_called_once_with(target.resolve())


if __name__ == "__main__":
    unittest.main(verbosity=2)
