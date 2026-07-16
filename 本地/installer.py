# -*- coding: utf-8 -*-

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "咨询项目全流程需求管理系统"
APP_EXE = "ConsultingRequirementManager.exe"
LAUNCHER_NAME = "启动咨询项目需求管理系统.bat"
DATA_DIRECTORIES = (
    Path("data"),
    Path("data/attachments"),
    Path("data/backups"),
    Path("data/exports"),
    Path("data/logs"),
)


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def default_install_dir() -> Path:
    return Path.home() / "Documents" / "ConsultingRequirementManager"


def validate_install_dir(install_dir: str | os.PathLike[str]) -> Path:
    raw_path = os.fspath(install_dir).strip()
    if not raw_path:
        raise ValueError("请选择安装路径。")

    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        raise ValueError("安装路径必须是绝对路径。")

    target = target.resolve(strict=False)
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
) -> Path:
    """Install or upgrade the application without opening any GUI dialogs."""
    target = validate_install_dir(install_dir)
    source_dir = Path(bundled_dir) if bundled_dir is not None else resource_dir()
    bundled_exe = source_dir / APP_EXE
    if not bundled_exe.is_file():
        raise FileNotFoundError(f"安装包缺少主程序：{APP_EXE}")

    _validate_file_paths(target, (Path(APP_EXE), Path(LAUNCHER_NAME)))
    _prepare_directories(target, DATA_DIRECTORIES)
    _atomic_copy(bundled_exe, target / APP_EXE)
    create_launcher(target)
    return target


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} 安装向导")
        self.geometry("680x330")
        self.resizable(False, False)
        self.install_path = tk.StringVar(value=str(default_install_dir()))
        self.open_after_install = tk.BooleanVar(value=False)
        self.build()

    def build(self):
        root = ttk.Frame(self, padding=24)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text=APP_TITLE, font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text="请选择安装路径。升级时会更新主程序，但不会覆盖已有业务数据。",
            wraplength=620,
        ).pack(anchor="w", pady=(8, 20))

        ttk.Label(root, text="安装路径").pack(anchor="w")
        path_row = ttk.Frame(root)
        path_row.pack(fill=tk.X, pady=(6, 14))
        ttk.Entry(path_row, textvariable=self.install_path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(path_row, text="浏览...", command=self.choose_dir).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Checkbutton(
            root,
            text="安装完成后立即启动",
            variable=self.open_after_install,
        ).pack(anchor="w")
        self.status = ttk.Label(root, text="")
        self.status.pack(anchor="w", pady=(18, 0))

        buttons = ttk.Frame(root)
        buttons.pack(side=tk.BOTTOM, anchor="e")
        ttk.Button(buttons, text="退出", command=self.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="安装", command=self.install).pack(side=tk.RIGHT)

    def choose_dir(self):
        current_path = Path(self.install_path.get()).expanduser()
        initial_dir = current_path.parent if current_path.is_absolute() else Path.home()
        chosen = filedialog.askdirectory(initialdir=str(initial_dir), title="选择安装目录")
        if chosen:
            self.install_path.set(chosen)

    def install(self):
        try:
            target = install_application(self.install_path.get())
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_TITLE} 安装程序")
    parser.add_argument(
        "--install-to",
        metavar="ABSOLUTE_PATH",
        help="不打开图形界面，直接安装到指定绝对路径",
    )
    args = parser.parse_args(argv)
    if args.install_to is not None:
        try:
            install_application(args.install_to)
        except Exception as exc:
            print(f"安装失败：{exc}", file=sys.stderr)
            return 1
        return 0

    Installer().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
