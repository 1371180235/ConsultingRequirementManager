import os
import shutil
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "咨询项目全流程需求管理系统"
APP_EXE = "ConsultingRequirementManager.exe"


def resource_dir():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def default_install_dir():
    return Path.home() / "Documents" / "ConsultingRequirementManager"


def create_launcher(install_dir: Path):
    launcher = install_dir / "启动咨询项目需求管理系统.bat"
    launcher.write_text(f'@echo off\r\nstart "" "%~dp0{APP_EXE}"\r\n', encoding="utf-8")
    return launcher


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} 安装向导")
        self.geometry("620x300")
        self.resizable(False, False)
        self.install_path = tk.StringVar(value=str(default_install_dir()))
        self.open_after_install = tk.BooleanVar(value=True)
        self.build()

    def build(self):
        root = ttk.Frame(self, padding=22)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text=APP_TITLE, font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        ttk.Label(root, text="请选择安装路径。安装后，程序数据会保存在安装目录下的 data 文件夹中。").pack(anchor="w", pady=(8, 20))

        row = ttk.Frame(root)
        row.pack(fill=tk.X)
        ttk.Label(row, text="安装路径").pack(anchor="w")
        path_row = ttk.Frame(root)
        path_row.pack(fill=tk.X, pady=(6, 14))
        ttk.Entry(path_row, textvariable=self.install_path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(path_row, text="浏览...", command=self.choose_dir).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Checkbutton(root, text="安装完成后立即启动", variable=self.open_after_install).pack(anchor="w")
        self.status = ttk.Label(root, text="")
        self.status.pack(anchor="w", pady=(18, 0))

        buttons = ttk.Frame(root)
        buttons.pack(side=tk.BOTTOM, anchor="e")
        ttk.Button(buttons, text="退出", command=self.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="安装", command=self.install).pack(side=tk.RIGHT)

    def choose_dir(self):
        chosen = filedialog.askdirectory(initialdir=str(Path(self.install_path.get()).parent), title="选择安装目录")
        if chosen:
            self.install_path.set(chosen)

    def install(self):
        target = Path(self.install_path.get()).expanduser()
        if not str(target).strip():
            messagebox.showwarning("提示", "请选择安装路径")
            return

        bundled_exe = resource_dir() / APP_EXE
        if not bundled_exe.exists():
            messagebox.showerror("安装失败", f"安装包缺少主程序：{APP_EXE}")
            return

        try:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled_exe, target / APP_EXE)
            create_launcher(target)
            for folder in ["data", "data\\attachments", "data\\backups", "data\\exports"]:
                (target / folder).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("安装失败", str(exc))
            return

        self.status.config(text=f"安装完成：{target}")
        messagebox.showinfo("安装完成", f"已安装到：\n{target}\n\n双击 {APP_EXE} 即可运行。")
        if self.open_after_install.get():
            try:
                subprocess.Popen([str(target / APP_EXE)], cwd=str(target))
            except Exception as exc:
                messagebox.showwarning("启动失败", str(exc))
        self.destroy()


if __name__ == "__main__":
    Installer().mainloop()
