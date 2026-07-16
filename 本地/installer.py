# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "咨询项目全流程需求管理系统"
APP_VERSION = "1.4.2"
APP_REGISTRY_ID = "ConsultingRequirementManager"
APP_EXE = "ConsultingRequirementManager.exe"
LAUNCHER_NAME = "启动咨询项目需求管理系统.bat"
LICENSE_FILENAME = "EULA_zh-CN.txt"
UNINSTALLER_EXE = "UninstallConsultingRequirementManager.exe"
INSTALL_MARKER_FILENAME = ".crm-install.json"
UNINSTALL_REGISTRY_KEY = (
    rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_REGISTRY_ID}"
)
UNINSTALL_WORKER_DIR_PATTERN = re.compile(r"CRM-Uninstall-[A-Za-z0-9_-]{6,80}")
DATA_DIRECTORIES = (
    Path("data"),
    Path("data/attachments"),
    Path("data/backups"),
    Path("data/exports"),
    Path("data/logs"),
)


class LicenseNotAcceptedError(PermissionError):
    """Raised when installation is attempted without explicit acceptance."""


class UninstallCancelledError(PermissionError):
    """Raised before any changes when uninstall confirmation is incomplete."""


@dataclass(frozen=True)
class UninstallResult:
    install_dir: Path
    data_path: Path
    data_preserved: bool
    install_dir_removed: bool


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def default_install_dir() -> Path:
    return Path.home() / "Documents" / "ConsultingRequirementManager"


def load_license_text(source_dir: str | os.PathLike[str] | None = None) -> str:
    base_dir = Path(source_dir) if source_dir is not None else resource_dir()
    license_path = base_dir / LICENSE_FILENAME
    try:
        content = license_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise FileNotFoundError(f"安装包缺少许可协议：{LICENSE_FILENAME}") from exc
    if not content:
        raise ValueError("安装包中的许可协议内容为空。")
    return content


def require_license_acceptance(accepted: bool) -> None:
    if accepted is not True:
        raise LicenseNotAcceptedError("必须阅读并接受《软件最终用户许可协议》后才能安装。")


def _is_reparse_point(path: Path) -> bool:
    try:
        path_stat = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(path_stat, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(path_stat.st_mode) or bool(attributes & reparse_attribute)


def _assert_no_reparse_points_in_path(path: Path) -> None:
    for candidate in [*reversed(path.parents), path]:
        if _is_reparse_point(candidate):
            raise ValueError(f"路径包含符号链接或重解析点，已拒绝操作：{candidate}")


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(left)) == os.path.normcase(os.fspath(right))


def _install_marker_payload(install_dir: Path) -> dict[str, str]:
    return {
        "application_id": APP_REGISTRY_ID,
        "application_version": APP_VERSION,
        "install_dir": str(install_dir),
    }


def _write_install_marker(install_dir: Path) -> Path:
    marker = install_dir / INSTALL_MARKER_FILENAME
    temp_file = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
    try:
        temp_file.write_text(
            json.dumps(_install_marker_payload(install_dir), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_file, marker)
    finally:
        temp_file.unlink(missing_ok=True)
    return marker


def _resolve_uninstaller_source(source_dir: Path) -> Path:
    bundled_uninstaller = source_dir / UNINSTALLER_EXE
    if bundled_uninstaller.is_file():
        return bundled_uninstaller
    if getattr(sys, "frozen", False):
        frozen_executable = Path(sys.executable)
        if frozen_executable.is_file():
            return frozen_executable
    raise FileNotFoundError(f"安装包缺少卸载程序：{UNINSTALLER_EXE}")


def uninstall_registry_values(install_dir: Path) -> dict[str, str | int]:
    target = install_dir.resolve(strict=False)
    uninstaller = target / UNINSTALLER_EXE
    uninstall_command = subprocess.list2cmdline(
        [str(uninstaller), "--uninstall-from", str(target)]
    )
    estimated_size = 0
    for filename in (APP_EXE, UNINSTALLER_EXE, LICENSE_FILENAME):
        candidate = target / filename
        if candidate.is_file():
            estimated_size += candidate.stat().st_size
    return {
        "DisplayName": APP_TITLE,
        "DisplayVersion": APP_VERSION,
        "DisplayIcon": f"{target / APP_EXE},0",
        "InstallLocation": str(target),
        "UninstallString": uninstall_command,
        "NoModify": 1,
        "NoRepair": 1,
        "EstimatedSize": max(1, (estimated_size + 1023) // 1024),
    }


def register_uninstall_entry(install_dir: Path) -> bool:
    if os.name != "nt":
        return False
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        UNINSTALL_REGISTRY_KEY,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        for name, value in uninstall_registry_values(install_dir).items():
            value_type = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, value_type, value)
    return True


def unregister_uninstall_entry() -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_KEY)
    except FileNotFoundError:
        return False
    return True


def validate_uninstall_dir(install_dir: str | os.PathLike[str]) -> Path:
    raw_path = os.fspath(install_dir).strip()
    if not raw_path:
        raise ValueError("缺少卸载目录。")

    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        raise ValueError("卸载目录必须是绝对路径。")
    if target.parent == target:
        raise ValueError("不能从磁盘根目录执行卸载。")

    _assert_no_reparse_points_in_path(target)
    if not target.is_dir():
        raise ValueError("卸载目录不存在或不是目录。")
    target = target.resolve(strict=True)
    if target.parent == target:
        raise ValueError("不能从磁盘根目录执行卸载。")

    marker = target / INSTALL_MARKER_FILENAME
    if _is_reparse_point(marker) or not marker.is_file():
        raise ValueError("未找到可信的安装标记，已拒绝卸载。")
    try:
        marker_data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("安装标记损坏，已拒绝卸载。") from exc
    if marker_data.get("application_id") != APP_REGISTRY_ID:
        raise ValueError("安装标记不属于本程序，已拒绝卸载。")
    marker_install_dir = Path(str(marker_data.get("install_dir", ""))).expanduser()
    if not marker_install_dir.is_absolute() or not _same_path(
        marker_install_dir.resolve(strict=False), target
    ):
        raise ValueError("安装标记与卸载目录不一致，已拒绝卸载。")
    return target


def _validate_managed_install_files(target: Path) -> None:
    for filename in (
        APP_EXE,
        LAUNCHER_NAME,
        LICENSE_FILENAME,
        UNINSTALLER_EXE,
        INSTALL_MARKER_FILENAME,
    ):
        managed_file = target / filename
        if not managed_file.exists() and not _is_reparse_point(managed_file):
            continue
        if _is_reparse_point(managed_file) or not managed_file.is_file():
            raise ValueError(f"受管程序文件异常，已拒绝卸载：{filename}")


def _validate_data_tree(data_dir: Path) -> None:
    if not data_dir.exists() and not _is_reparse_point(data_dir):
        return
    if _is_reparse_point(data_dir) or not data_dir.is_dir():
        raise ValueError("数据目录是文件、符号链接或重解析点，已拒绝删除。")

    pending = [data_dir]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                entry_path = Path(entry.path)
                if _is_reparse_point(entry_path):
                    raise ValueError(f"数据目录包含符号链接或重解析点，已拒绝删除：{entry_path}")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry_path)
                elif not entry.is_file(follow_symlinks=False):
                    raise ValueError(f"数据目录包含不支持的文件类型，已拒绝删除：{entry_path}")


def _remove_data_tree(data_dir: Path) -> None:
    if not data_dir.exists():
        return
    with os.scandir(data_dir) as entries:
        children = [Path(entry.path) for entry in entries]
    for child in children:
        if _is_reparse_point(child):
            raise ValueError(f"删除过程中发现符号链接或重解析点，已中止：{child}")
        if child.is_dir():
            _remove_data_tree(child)
        elif child.is_file():
            child.unlink()
        else:
            raise ValueError(f"删除过程中发现不支持的文件类型，已中止：{child}")
    data_dir.rmdir()


def uninstall_application(
    install_dir: str | os.PathLike[str],
    *,
    confirmed: bool = False,
    delete_data: bool = False,
    data_deletion_confirmed: bool = False,
    unregister_system: bool = False,
) -> UninstallResult:
    if confirmed is not True:
        raise UninstallCancelledError("用户尚未确认卸载，未修改任何文件。")
    if delete_data and data_deletion_confirmed is not True:
        raise UninstallCancelledError("尚未二次确认删除业务数据，未修改任何文件。")

    target = validate_uninstall_dir(install_dir)
    _validate_managed_install_files(target)
    data_path = target / "data"
    if delete_data:
        _validate_data_tree(data_path)

    for filename in (
        APP_EXE,
        LAUNCHER_NAME,
        LICENSE_FILENAME,
        UNINSTALLER_EXE,
        INSTALL_MARKER_FILENAME,
    ):
        managed_file = target / filename
        if managed_file.exists():
            managed_file.unlink()

    if delete_data:
        _remove_data_tree(data_path)
    if unregister_system:
        unregister_uninstall_entry()

    try:
        target.rmdir()
        install_dir_removed = True
    except OSError:
        install_dir_removed = False

    return UninstallResult(
        install_dir=target,
        data_path=data_path,
        data_preserved=not delete_data and data_path.exists(),
        install_dir_removed=install_dir_removed,
    )


def validate_install_dir(install_dir: str | os.PathLike[str]) -> Path:
    raw_path = os.fspath(install_dir).strip()
    if not raw_path:
        raise ValueError("请选择安装路径。")

    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        raise ValueError("安装路径必须是绝对路径。")
    if target.parent == target:
        raise ValueError("不能直接安装到磁盘根目录，请选择一个子目录。")

    _assert_no_reparse_points_in_path(target)
    target = target.resolve(strict=False)
    _assert_no_reparse_points_in_path(target)
    if target.parent == target:
        raise ValueError("不能直接安装到磁盘根目录，请选择一个子目录。")
    if target.exists() and not target.is_dir():
        raise ValueError("安装路径指向文件，请选择一个目录。")
    return target


def _atomic_copy(source: Path, destination: Path) -> None:
    temp_file = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(source, temp_file)
        os.replace(temp_file, destination)
    finally:
        temp_file.unlink(missing_ok=True)


def _validate_file_paths(target: Path, relative_files: tuple[Path, ...]) -> None:
    for relative_file in relative_files:
        destination = target / relative_file
        if destination.exists() and not destination.is_file():
            raise ValueError(f"无法更新文件，路径已被同名目录占用：{relative_file}")


def _prepare_directories(target: Path, relative_dirs: tuple[Path, ...]) -> None:
    for relative_dir in relative_dirs:
        directory = target / relative_dir
        try:
            directory.resolve(strict=False).relative_to(target)
        except ValueError as exc:
            raise ValueError(f"安装目录包含不安全的目录链接：{relative_dir}") from exc
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"无法创建目录，已存在同名文件：{relative_dir}")

    target.mkdir(parents=True, exist_ok=True)
    for relative_dir in relative_dirs:
        (target / relative_dir).mkdir(parents=True, exist_ok=True)


def create_launcher(install_dir: Path) -> Path:
    launcher = install_dir / LAUNCHER_NAME
    temp_file = launcher.with_name(f".{launcher.name}.{os.getpid()}.tmp")
    launcher_content = (
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        f'start "" "%~dp0{APP_EXE}"\r\n'
    )
    try:
        temp_file.write_text(launcher_content, encoding="ascii", newline="")
        os.replace(temp_file, launcher)
    finally:
        temp_file.unlink(missing_ok=True)
    return launcher


def install_application(
    install_dir: str | os.PathLike[str],
    bundled_dir: str | os.PathLike[str] | None = None,
    *,
    license_accepted: bool = False,
    register_system: bool = False,
) -> Path:
    """Install or upgrade the application without opening any GUI dialogs."""
    require_license_acceptance(license_accepted)
    target = validate_install_dir(install_dir)
    source_dir = Path(bundled_dir) if bundled_dir is not None else resource_dir()
    bundled_exe = source_dir / APP_EXE
    if not bundled_exe.is_file():
        raise FileNotFoundError(f"安装包缺少主程序：{APP_EXE}")
    load_license_text(source_dir)
    uninstaller_source = _resolve_uninstaller_source(source_dir)

    _validate_file_paths(
        target,
        (
            Path(APP_EXE),
            Path(LICENSE_FILENAME),
            Path(UNINSTALLER_EXE),
            Path(INSTALL_MARKER_FILENAME),
            Path(LAUNCHER_NAME),
        ),
    )
    _prepare_directories(target, DATA_DIRECTORIES)
    _atomic_copy(bundled_exe, target / APP_EXE)
    _atomic_copy(source_dir / LICENSE_FILENAME, target / LICENSE_FILENAME)
    _atomic_copy(uninstaller_source, target / UNINSTALLER_EXE)
    create_launcher(target)
    _write_install_marker(target)
    if register_system:
        register_uninstall_entry(target)
    return target


class Installer(tk.Tk):
    def __init__(self):
        license_text = load_license_text()
        super().__init__()
        self.title(f"{APP_TITLE} 安装向导")
        self.geometry("760x610")
        self.resizable(False, False)
        self.license_text = license_text
        self.license_accepted = tk.BooleanVar(value=False)
        self.install_path = tk.StringVar(value=str(default_install_dir()))
        self.open_after_install = tk.BooleanVar(value=False)
        self.build()

    def build(self):
        self.page_host = ttk.Frame(self, padding=(24, 18, 24, 18))
        self.page_host.pack(fill=tk.BOTH, expand=True)
        self.build_license_page()
        self.build_install_page()
        self.show_license_page()

    def build_license_page(self):
        self.license_page = ttk.Frame(self.page_host)

        ttk.Label(
            self.license_page,
            text="软件最终用户许可协议",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            self.license_page,
            text="请阅读以下条款。只有明确接受协议后，才能选择路径并继续安装。",
            wraplength=700,
        ).pack(anchor="w", pady=(6, 12))

        text_frame = ttk.Frame(self.license_page)
        text_frame.pack(fill=tk.BOTH, expand=True)
        license_view = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=("Microsoft YaHei UI", 9),
            padx=12,
            pady=10,
            relief=tk.SOLID,
            borderwidth=1,
            background="#FFFFFF",
            foreground="#1F2937",
            selectbackground="#BFDBFE",
        )
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=license_view.yview)
        license_view.configure(yscrollcommand=scrollbar.set)
        license_view.insert("1.0", self.license_text)
        license_view.configure(state=tk.DISABLED)
        license_view.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Checkbutton(
            self.license_page,
            text="我已阅读并接受《软件最终用户许可协议》",
            variable=self.license_accepted,
            command=self.update_license_buttons,
        ).pack(anchor="w", pady=(14, 8))

        buttons = ttk.Frame(self.license_page)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="拒绝并退出", command=self.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        self.next_button = ttk.Button(buttons, text="下一步", command=self.show_install_page)
        self.next_button.pack(side=tk.RIGHT)

    def build_install_page(self):
        self.install_page = ttk.Frame(self.page_host)

        ttk.Label(self.install_page, text=APP_TITLE, font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            self.install_page,
            text=(
                "请选择安装路径。程序、SQLite 数据库、附件、导出、备份和日志默认保存在该路径下；"
                "覆盖升级会更新程序文件并保留已有 data 业务数据。"
            ),
            wraplength=700,
        ).pack(anchor="w", pady=(8, 20))

        ttk.Label(self.install_page, text="安装路径").pack(anchor="w")
        path_row = ttk.Frame(self.install_page)
        path_row.pack(fill=tk.X, pady=(6, 14))
        ttk.Entry(path_row, textvariable=self.install_path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(path_row, text="浏览...", command=self.choose_dir).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Checkbutton(
            self.install_page,
            text="安装完成后立即启动",
            variable=self.open_after_install,
        ).pack(anchor="w")
        self.status = ttk.Label(self.install_page, text="")
        self.status.pack(anchor="w", pady=(18, 0))

        buttons = ttk.Frame(self.install_page)
        buttons.pack(side=tk.BOTTOM, anchor="e")
        ttk.Button(buttons, text="退出", command=self.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="安装", command=self.install).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="上一步", command=self.show_license_page).pack(side=tk.RIGHT, padx=(0, 8))

    def update_license_buttons(self):
        self.next_button.configure(state=tk.NORMAL if self.license_accepted.get() else tk.DISABLED)

    def show_license_page(self):
        self.install_page.pack_forget()
        self.license_page.pack(fill=tk.BOTH, expand=True)
        self.update_license_buttons()

    def show_install_page(self):
        try:
            require_license_acceptance(self.license_accepted.get())
        except LicenseNotAcceptedError as exc:
            messagebox.showwarning("请先接受协议", str(exc))
            return
        self.license_page.pack_forget()
        self.install_page.pack(fill=tk.BOTH, expand=True)

    def choose_dir(self):
        current_path = Path(self.install_path.get()).expanduser()
        initial_dir = current_path.parent if current_path.is_absolute() else Path.home()
        chosen = filedialog.askdirectory(initialdir=str(initial_dir), title="选择安装目录")
        if chosen:
            self.install_path.set(chosen)

    def install(self):
        try:
            target = install_application(
                self.install_path.get(),
                license_accepted=self.license_accepted.get(),
                register_system=True,
            )
        except LicenseNotAcceptedError as exc:
            messagebox.showwarning("请先接受协议", str(exc))
            self.show_license_page()
            return
        except Exception as exc:
            messagebox.showerror("安装失败", str(exc))
            return

        self.status.config(text=f"安装完成：{target}")
        messagebox.showinfo(
            "安装完成",
            f"已安装到：\n{target}\n\n可双击 {APP_EXE} 或中文启动器运行。",
        )
        if self.open_after_install.get():
            try:
                subprocess.Popen([str(target / APP_EXE)], cwd=str(target))
            except Exception as exc:
                messagebox.showwarning("启动失败", str(exc))
        self.destroy()


class Uninstaller(tk.Tk):
    def __init__(self, install_dir: str | os.PathLike[str]):
        self.install_dir = validate_uninstall_dir(install_dir)
        super().__init__()
        self.title(f"卸载 {APP_TITLE}")
        self.geometry("680x430")
        self.resizable(False, False)
        self.delete_data = tk.BooleanVar(value=False)
        self.build()

    def build(self):
        root = ttk.Frame(self, padding=(28, 24, 28, 22))
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            root,
            text=f"卸载 {APP_TITLE}",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            root,
            text=(
                "卸载将移除主程序、启动器、许可协议、卸载器和 Windows 已安装应用记录。"
                "默认保留全部本地业务数据，方便以后重装或迁移。"
            ),
            wraplength=620,
        ).pack(anchor="w", pady=(10, 18))

        location = ttk.LabelFrame(root, text="本地数据保留位置", padding=(14, 10))
        location.pack(fill=tk.X)
        ttk.Label(
            location,
            text=str(self.install_dir / "data"),
            wraplength=590,
        ).pack(anchor="w")

        delete_option = ttk.Checkbutton(
            root,
            text="同时删除全部本地业务数据（不可恢复）",
            variable=self.delete_data,
        )
        delete_option.pack(anchor="w", pady=(22, 6))
        ttk.Label(
            root,
            text="业务数据包括 SQLite 数据库、附件、导出、备份和日志。建议先复制 data 目录或创建独立备份。",
            wraplength=620,
            foreground="#B91C1C",
        ).pack(anchor="w")

        buttons = ttk.Frame(root)
        buttons.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="卸载", command=self.uninstall).pack(side=tk.RIGHT)

    def uninstall(self):
        delete_data = self.delete_data.get()
        if not messagebox.askyesno(
            "确认卸载",
            "确定卸载本程序吗？" + ("\n\n下一步还会要求确认删除全部业务数据。" if delete_data else "\n\n业务数据将保留在原位置。"),
            parent=self,
        ):
            return

        data_deletion_confirmed = False
        if delete_data:
            data_deletion_confirmed = messagebox.askyesno(
                "最终确认：删除全部业务数据",
                (
                    "此操作将永久删除 SQLite 数据库、附件、导出、备份和日志，且无法通过本程序恢复。\n\n"
                    f"删除位置：\n{self.install_dir / 'data'}\n\n确定继续吗？"
                ),
                icon="warning",
                parent=self,
            )
            if not data_deletion_confirmed:
                return

        try:
            result = uninstall_application(
                self.install_dir,
                confirmed=True,
                delete_data=delete_data,
                data_deletion_confirmed=data_deletion_confirmed,
                unregister_system=True,
            )
        except Exception as exc:
            messagebox.showerror("卸载失败", str(exc), parent=self)
            return

        if result.data_preserved:
            completion_message = (
                "程序已卸载，业务数据已保留。\n\n"
                f"保留位置：\n{result.data_path}"
            )
        else:
            completion_message = "程序及本地业务数据已卸载。"
        messagebox.showinfo("卸载完成", completion_message, parent=self)
        self.destroy()


def _is_installed_uninstaller() -> bool:
    return getattr(sys, "frozen", False) and (
        Path(sys.executable).name.casefold() == UNINSTALLER_EXE.casefold()
    )


def validate_uninstall_worker_context() -> Path:
    if not getattr(sys, "frozen", False):
        raise ValueError("临时卸载模式只能由已打包的卸载程序启动。")

    worker_executable = Path(sys.executable)
    if not worker_executable.is_absolute():
        raise ValueError("临时卸载程序路径必须是绝对路径。")
    if worker_executable.name.casefold() != UNINSTALLER_EXE.casefold():
        raise ValueError("临时卸载程序名称无效。")
    if _is_reparse_point(worker_executable) or not worker_executable.is_file():
        raise ValueError("临时卸载程序不存在或是重解析点。")

    worker_dir = worker_executable.parent
    _assert_no_reparse_points_in_path(worker_dir)
    worker_dir = worker_dir.resolve(strict=True)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if worker_dir.parent != temp_root:
        raise ValueError("临时卸载程序不在系统临时目录的直接子目录中。")
    if UNINSTALL_WORKER_DIR_PATTERN.fullmatch(worker_dir.name) is None:
        raise ValueError("临时卸载目录名称无效。")
    return worker_dir


def start_uninstall_worker(install_dir: str | os.PathLike[str]) -> None:
    target = validate_uninstall_dir(install_dir)
    if not getattr(sys, "frozen", False):
        raise RuntimeError("只有已打包的卸载程序才能启动临时卸载进程。")

    worker_dir = Path(tempfile.mkdtemp(prefix="CRM-Uninstall-"))
    worker_executable = worker_dir / UNINSTALLER_EXE
    try:
        shutil.copy2(Path(sys.executable), worker_executable)
        subprocess.Popen(
            [
                str(worker_executable),
                "--uninstall-from",
                str(target),
                "--uninstall-worker",
            ],
            cwd=str(worker_dir),
            close_fds=True,
        )
    except Exception:
        shutil.rmtree(worker_dir, ignore_errors=True)
        raise


def schedule_uninstall_worker_cleanup() -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        worker_dir = validate_uninstall_worker_context()
    except ValueError:
        return
    worker_executable = Path(sys.executable).resolve(strict=True)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=False)

    cleanup_script = temp_root / f"CRM-Uninstall-Cleanup-{uuid.uuid4().hex}.cmd"
    cleanup_script.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "for /l %%I in (1,1,30) do (\r\n"
        "  del /f /q \"%CRM_UNINSTALL_WORKER%\" >nul 2>&1\r\n"
        "  if not exist \"%CRM_UNINSTALL_WORKER%\" goto cleaned\r\n"
        "  ping 127.0.0.1 -n 2 >nul\r\n"
        ")\r\n"
        ":cleaned\r\n"
        "rmdir \"%CRM_UNINSTALL_WORKER_DIR%\" >nul 2>&1\r\n"
        "del /f /q \"%~f0\" >nul 2>&1\r\n",
        encoding="ascii",
        newline="",
    )
    cleanup_environment = os.environ.copy()
    cleanup_environment["CRM_UNINSTALL_WORKER"] = str(worker_executable)
    cleanup_environment["CRM_UNINSTALL_WORKER_DIR"] = str(worker_dir)
    subprocess.Popen(
        [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", str(cleanup_script)],
        env=cleanup_environment,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_TITLE} 安装程序")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--install-to",
        metavar="ABSOLUTE_PATH",
        help="不打开图形界面，直接安装到指定绝对路径",
    )
    mode.add_argument(
        "--uninstall-from",
        metavar="ABSOLUTE_PATH",
        help="从指定绝对路径卸载程序",
    )
    parser.add_argument(
        "--accept-license",
        action="store_true",
        help="明确接受软件最终用户许可协议（仅用于无人值守安装）",
    )
    parser.add_argument(
        "--uninstall-worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    if args.uninstall_from is None and args.install_to is None and _is_installed_uninstaller():
        args.uninstall_from = str(Path(sys.executable).parent)

    if args.uninstall_worker:
        if args.uninstall_from is None:
            print("卸载程序拒绝启动：临时卸载模式缺少卸载目录。", file=sys.stderr)
            return 1
        try:
            validate_uninstall_worker_context()
        except ValueError as exc:
            print(f"卸载程序拒绝启动：{exc}", file=sys.stderr)
            return 1

    if args.install_to is not None:
        try:
            install_application(
                args.install_to,
                license_accepted=args.accept_license,
                register_system=True,
            )
        except Exception as exc:
            print(f"安装失败：{exc}", file=sys.stderr)
            return 1
        return 0

    if args.uninstall_from is not None:
        if getattr(sys, "frozen", False) and not args.uninstall_worker:
            try:
                start_uninstall_worker(args.uninstall_from)
            except Exception as exc:
                error_root = tk.Tk()
                error_root.withdraw()
                messagebox.showerror("卸载程序无法启动", str(exc))
                error_root.destroy()
                return 1
            return 0
        try:
            Uninstaller(args.uninstall_from).mainloop()
        except Exception as exc:
            error_root = tk.Tk()
            error_root.withdraw()
            messagebox.showerror("卸载程序无法启动", str(exc))
            error_root.destroy()
            return 1
        finally:
            if args.uninstall_worker:
                schedule_uninstall_worker_cleanup()
        return 0

    try:
        Installer().mainloop()
    except Exception as exc:
        error_root = tk.Tk()
        error_root.withdraw()
        messagebox.showerror("安装程序无法启动", str(exc))
        error_root.destroy()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
