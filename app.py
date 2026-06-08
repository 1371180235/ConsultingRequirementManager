import csv
import os
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_NAME = "咨询项目全流程需求管理系统"
STATUS_FLOW = ["草稿", "规划中", "已排期", "研发中", "待验收", "已上线运维", "已关闭"]
EXTRA_STATUSES = ["已驳回", "已挂起", "已取消", "变更中", "退回修改"]
ROLES = ["管理员", "咨询负责人", "客户", "销售", "项目经理", "研发人员", "运营人员"]
SENSITIVE_ROLES = {"客户", "研发人员", "运营人员"}


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def app_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class Database:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.data_dir = base_dir / "data"
        self.attachments_dir = self.data_dir / "attachments"
        self.backups_dir = self.data_dir / "backups"
        self.exports_dir = self.data_dir / "exports"
        for folder in [self.data_dir, self.attachments_dir, self.backups_dir, self.exports_dir]:
            folder.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "app.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()
        self.seed_defaults()

    def execute(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def query(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def init_schema(self):
        schema = """
        CREATE TABLE IF NOT EXISTS planning_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_code TEXT NOT NULL UNIQUE,
            project_name TEXT NOT NULL,
            customer_name TEXT,
            project_background TEXT,
            total_budget REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS annual_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            plan_year INTEGER NOT NULL,
            plan_name TEXT NOT NULL,
            annual_budget REAL DEFAULT 0,
            business_pain_points TEXT,
            plan_description TEXT,
            status TEXT DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS implementation_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            annual_plan_id INTEGER NOT NULL,
            version_code TEXT NOT NULL,
            version_name TEXT NOT NULL,
            version_goal TEXT,
            version_scope TEXT,
            version_budget REAL DEFAULT 0,
            status TEXT DEFAULT 'planning',
            is_frozen INTEGER DEFAULT 0,
            planned_start_date TEXT,
            planned_end_date TEXT,
            actual_start_date TEXT,
            actual_end_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requirement_code TEXT NOT NULL UNIQUE,
            requirement_name TEXT NOT NULL,
            requirement_description TEXT NOT NULL,
            source_role TEXT NOT NULL,
            proposer_name TEXT,
            owner_name TEXT,
            project_id INTEGER NOT NULL,
            annual_plan_id INTEGER,
            version_id INTEGER,
            requirement_type TEXT,
            tags TEXT,
            priority TEXT DEFAULT 'P1',
            status TEXT DEFAULT '草稿',
            estimated_budget REAL DEFAULT 0,
            allocated_budget REAL DEFAULT 0,
            actual_cost REAL DEFAULT 0,
            planned_finish_date TEXT,
            actual_finish_date TEXT,
            remark TEXT,
            is_deleted INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS budget_flows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flow_code TEXT NOT NULL UNIQUE,
            project_id INTEGER NOT NULL,
            annual_plan_id INTEGER,
            version_id INTEGER,
            requirement_id INTEGER,
            flow_type TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            operator_name TEXT,
            occurred_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_code TEXT NOT NULL UNIQUE,
            artifact_name TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_ext TEXT,
            file_size INTEGER,
            related_object_type TEXT NOT NULL,
            related_object_id INTEGER NOT NULL,
            version_no TEXT,
            description TEXT,
            uploaded_by TEXT,
            uploaded_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            password_hash TEXT,
            role_name TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_name TEXT,
            operation_time TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_id INTEGER,
            operation_type TEXT NOT NULL,
            before_value TEXT,
            after_value TEXT,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS change_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id INTEGER NOT NULL,
            requirement_id INTEGER,
            change_title TEXT NOT NULL,
            change_reason TEXT,
            impact_scope TEXT,
            approval_status TEXT DEFAULT 'pending',
            requested_by TEXT,
            requested_at TEXT NOT NULL,
            approved_by TEXT,
            approved_at TEXT
        );
        """
        self.conn.executescript(schema)
        self.conn.commit()

    def seed_defaults(self):
        if not self.one("SELECT id FROM users WHERE username='admin'"):
            self.execute(
                "INSERT INTO users(username, display_name, password_hash, role_name, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                ("admin", "默认管理员", "", "管理员", now_text(), now_text()),
            )
        if not self.one("SELECT id FROM planning_projects"):
            t = now_text()
            self.execute(
                "INSERT INTO planning_projects(project_code, project_name, customer_name, project_background, total_budget, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                ("PRJ-DEMO", "示例咨询项目", "示例客户", "用于演示规划、年度、版本、需求、资金和成果物的完整链路。", 1000000, t, t),
            )
            project_id = self.one("SELECT id FROM planning_projects WHERE project_code='PRJ-DEMO'")["id"]
            self.execute(
                "INSERT INTO annual_plans(project_id, plan_year, plan_name, annual_budget, plan_description, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                (project_id, datetime.now().year, "示例年度计划", 600000, "年度任务拆分与预算安排。", t, t),
            )
            plan_id = self.one("SELECT id FROM annual_plans WHERE project_id=?", (project_id,))["id"]
            self.execute(
                "INSERT INTO implementation_versions(project_id, annual_plan_id, version_code, version_name, version_goal, version_budget, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (project_id, plan_id, "V1.0", "示例落地版本", "完成首批核心需求建设。", 300000, t, t),
            )
            version_id = self.one("SELECT id FROM implementation_versions WHERE project_id=?", (project_id,))["id"]
            self.execute(
                """INSERT INTO requirements(requirement_code, requirement_name, requirement_description, source_role, proposer_name, owner_name,
                   project_id, annual_plan_id, version_id, requirement_type, tags, priority, status, estimated_budget, allocated_budget, actual_cost, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("REQ-DEMO-001", "建立统一需求池", "将客户、销售、研发、运营等来源的需求统一登记并跟踪状态。", "咨询负责人", "咨询负责人", "默认管理员",
                 project_id, plan_id, version_id, "功能优化", "版本必做,待确认", "P0", "规划中", 80000, 60000, 12000, t, t),
            )
        self.log("系统", "system", None, "init", "", "", "初始化数据库和默认数据")

    def log(self, operator, object_type, object_id, operation_type, before_value, after_value, description):
        self.execute(
            "INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id, operation_type, before_value, after_value, description) VALUES(?,?,?,?,?,?,?,?)",
            (operator, now_text(), object_type, object_id, operation_type, str(before_value or ""), str(after_value or ""), description),
        )


class FieldDialog(tk.Toplevel):
    def __init__(self, parent, title, fields, initial=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(True, True)
        self.result = None
        self.vars = {}
        initial = initial or {}
        body = ttk.Frame(self, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        for row, field in enumerate(fields):
            key, label, kind, options = field
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
            value = initial.get(key, "")
            if kind == "text":
                var = tk.StringVar(value=value)
                widget = ttk.Entry(body, textvariable=var, width=42)
            elif kind == "combo":
                var = tk.StringVar(value=value or (options[0] if options else ""))
                widget = ttk.Combobox(body, textvariable=var, values=options, state="readonly", width=40)
            else:
                var = tk.StringVar(value=value)
                widget = tk.Text(body, width=42, height=5)
                widget.insert("1.0", value)
            widget.grid(row=row, column=1, sticky="ew", pady=5)
            self.vars[key] = (var, widget, kind)
        buttons = ttk.Frame(body)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="保存", command=self.save).pack(side=tk.RIGHT)
        body.columnconfigure(1, weight=1)
        self.grab_set()
        self.transient(parent)
        self.wait_visibility()
        self.focus()

    def save(self):
        values = {}
        for key, (var, widget, kind) in self.vars.items():
            values[key] = widget.get("1.0", tk.END).strip() if kind == "memo" else var.get().strip()
        self.result = values
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.base_dir = app_base_dir()
        self.db = Database(self.base_dir)
        self.title(APP_NAME)
        self.geometry("1280x760")
        self.minsize(1100, 680)
        self.current_user = "默认管理员"
        self.current_role = tk.StringVar(value="管理员")
        self.selected_project = tk.StringVar()
        self.selected_plan = tk.StringVar()
        self.selected_version = tk.StringVar()
        self.search_var = tk.StringVar()
        self.content = None
        self.current_page = "首页工作台"
        self.configure_style()
        self.build_layout()
        self.refresh_contexts()
        self.show_dashboard()

    def configure_style(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f6f7f9")
        style.configure("Side.TFrame", background="#243142")
        style.configure("Side.TButton", background="#243142", foreground="#ffffff", anchor="w", padding=(14, 10))
        style.map("Side.TButton", background=[("active", "#31445b")])
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"), background="#f6f7f9")
        style.configure("Metric.TLabel", font=("Microsoft YaHei UI", 18, "bold"), background="#ffffff")
        style.configure("Card.TFrame", background="#ffffff", relief="solid", borderwidth=1)

    def build_layout(self):
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill=tk.X)
        ttk.Label(top, text="项目").pack(side=tk.LEFT)
        self.project_box = ttk.Combobox(top, textvariable=self.selected_project, state="readonly", width=24)
        self.project_box.pack(side=tk.LEFT, padx=(6, 12))
        self.project_box.bind("<<ComboboxSelected>>", lambda e: self.on_project_change())
        ttk.Label(top, text="年度").pack(side=tk.LEFT)
        self.plan_box = ttk.Combobox(top, textvariable=self.selected_plan, state="readonly", width=20)
        self.plan_box.pack(side=tk.LEFT, padx=(6, 12))
        self.plan_box.bind("<<ComboboxSelected>>", lambda e: self.on_plan_change())
        ttk.Label(top, text="版本").pack(side=tk.LEFT)
        self.version_box = ttk.Combobox(top, textvariable=self.selected_version, state="readonly", width=20)
        self.version_box.pack(side=tk.LEFT, padx=(6, 12))
        self.version_box.bind("<<ComboboxSelected>>", lambda e: self.reload_page())
        ttk.Entry(top, textvariable=self.search_var, width=28).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Button(top, text="搜索", command=self.show_search).pack(side=tk.LEFT)
        ttk.Label(top, text="角色").pack(side=tk.RIGHT, padx=(12, 4))
        role_box = ttk.Combobox(top, textvariable=self.current_role, values=ROLES, state="readonly", width=12)
        role_box.pack(side=tk.RIGHT)
        role_box.bind("<<ComboboxSelected>>", lambda e: self.reload_page())

        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True)
        side = ttk.Frame(main, style="Side.TFrame", width=180)
        side.pack(side=tk.LEFT, fill=tk.Y)
        side.pack_propagate(False)
        ttk.Label(side, text=APP_NAME, background="#243142", foreground="#ffffff", font=("Microsoft YaHei UI", 11, "bold"), wraplength=150).pack(anchor="w", padx=14, pady=(16, 18))
        for name, cmd in [
            ("首页工作台", self.show_dashboard), ("项目管理", self.show_projects), ("年度计划", self.show_plans),
            ("版本管理", self.show_versions), ("需求管理", self.show_requirements), ("资金管理", self.show_budget),
            ("成果物管理", self.show_artifacts), ("搜索中心", self.show_search), ("报表导出", self.show_exports),
            ("系统设置", self.show_settings),
        ]:
            ttk.Button(side, text=name, style="Side.TButton", command=cmd).pack(fill=tk.X, padx=8, pady=2)
        self.content = ttk.Frame(main, padding=14)
        self.content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bottom = ttk.Frame(self, padding=(10, 5))
        bottom.pack(fill=tk.X)
        ttk.Label(bottom, text=f"数据库: {self.db.db_path}").pack(side=tk.LEFT)
        ttk.Label(bottom, text="版本: MVP 1.0").pack(side=tk.RIGHT)

    def clear(self, title):
        for child in self.content.winfo_children():
            child.destroy()
        ttk.Label(self.content, text=title, style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        self.current_page = title

    def refresh_contexts(self):
        projects = self.db.query("SELECT id, project_name FROM planning_projects ORDER BY id")
        self.projects = {f"{r['id']} - {r['project_name']}": r["id"] for r in projects}
        self.project_box["values"] = list(self.projects.keys())
        if not self.selected_project.get() and self.projects:
            self.selected_project.set(next(iter(self.projects)))
        self.on_project_change(refresh_only=True)

    def on_project_change(self, refresh_only=False):
        project_id = self.current_project_id()
        plans = self.db.query("SELECT id, plan_year, plan_name FROM annual_plans WHERE project_id=? ORDER BY plan_year DESC, id", (project_id,)) if project_id else []
        self.plans = {f"{r['id']} - {r['plan_year']} {r['plan_name']}": r["id"] for r in plans}
        self.plan_box["values"] = list(self.plans.keys())
        if self.plans and self.selected_plan.get() not in self.plans:
            self.selected_plan.set(next(iter(self.plans)))
        elif not self.plans:
            self.selected_plan.set("")
        self.on_plan_change(refresh_only=True)
        if not refresh_only:
            self.reload_page()

    def on_plan_change(self, refresh_only=False):
        project_id = self.current_project_id()
        plan_id = self.current_plan_id()
        versions = self.db.query("SELECT id, version_code, version_name FROM implementation_versions WHERE project_id=? AND annual_plan_id=? ORDER BY id", (project_id, plan_id)) if project_id and plan_id else []
        self.versions = {f"{r['id']} - {r['version_code']} {r['version_name']}": r["id"] for r in versions}
        self.version_box["values"] = list(self.versions.keys())
        if self.versions and self.selected_version.get() not in self.versions:
            self.selected_version.set(next(iter(self.versions)))
        elif not self.versions:
            self.selected_version.set("")
        if not refresh_only:
            self.reload_page()

    def current_project_id(self):
        return self.projects.get(self.selected_project.get())

    def current_plan_id(self):
        return self.plans.get(self.selected_plan.get())

    def current_version_id(self):
        return self.versions.get(self.selected_version.get())

    def reload_page(self):
        getattr(self, {
            "首页工作台": "show_dashboard", "项目管理": "show_projects", "年度计划": "show_plans", "版本管理": "show_versions",
            "需求管理": "show_requirements", "资金管理": "show_budget", "成果物管理": "show_artifacts", "搜索中心": "show_search",
            "报表导出": "show_exports", "系统设置": "show_settings",
        }.get(self.current_page, "show_dashboard"))()

    def add_table(self, parent, columns, rows, height=16):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height)
        ybar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        xbar = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="w")
        for row in rows:
            tree.insert("", tk.END, values=[row.get(c[0], "") if isinstance(row, dict) else row[c[0]] for c in columns])
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        xbar.pack(side=tk.BOTTOM, fill=tk.X)
        return tree

    def metric_card(self, parent, title, value):
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10), pady=(0, 10))
        ttk.Label(card, text=title, background="#ffffff").pack(anchor="w")
        ttk.Label(card, text=str(value), style="Metric.TLabel").pack(anchor="w", pady=(8, 0))

    def show_dashboard(self):
        self.clear("首页工作台")
        project_id = self.current_project_id()
        version_id = self.current_version_id()
        counts = {
            "规划项目": self.db.one("SELECT COUNT(*) c FROM planning_projects")["c"],
            "年度计划": self.db.one("SELECT COUNT(*) c FROM annual_plans WHERE project_id=?", (project_id,))["c"] if project_id else 0,
            "落地版本": self.db.one("SELECT COUNT(*) c FROM implementation_versions WHERE project_id=?", (project_id,))["c"] if project_id else 0,
            "版本需求": self.db.one("SELECT COUNT(*) c FROM requirements WHERE is_deleted=0 AND version_id=?", (version_id,))["c"] if version_id else 0,
        }
        row = ttk.Frame(self.content)
        row.pack(fill=tk.X)
        for k, v in counts.items():
            self.metric_card(row, k, v)
        role = self.current_role.get()
        msg = {
            "客户": "关注项目整体进度、需求处理状态、版本规划和待确认事项。",
            "销售": "关注资金申报进度、项目进展、投入汇总和可导出材料。",
            "项目经理": "关注版本交付进度、验收准备、实际投入和问题风险。",
            "研发人员": "关注待办任务、需求优先级、研发状态和工期评估。",
            "运营人员": "关注上线版本、线上问题池、运营推广记录和问题闭环。",
        }.get(role, "可查看全部模块，维护基础数据、备份恢复和操作日志。")
        ttk.Label(self.content, text=f"当前视角：{role}。{msg}", wraplength=920).pack(anchor="w", pady=(2, 12))
        rows = self.db.query("""SELECT requirement_code, requirement_name, source_role, priority, status, updated_at
                                FROM requirements WHERE is_deleted=0 AND (? IS NULL OR version_id=?)
                                ORDER BY updated_at DESC LIMIT 12""", (version_id, version_id))
        self.add_table(self.content, [
            ("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 260), ("source_role", "来源", 90),
            ("priority", "优先级", 70), ("status", "状态", 110), ("updated_at", "最近更新", 150),
        ], rows, 12)

    def show_projects(self):
        self.clear("项目管理")
        bar = ttk.Frame(self.content)
        bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(bar, text="新建项目", command=self.add_project).pack(side=tk.LEFT)
        rows = self.db.query("SELECT id, project_code, project_name, customer_name, total_budget, status, updated_at FROM planning_projects ORDER BY id DESC")
        self.add_table(self.content, [
            ("id", "ID", 50), ("project_code", "项目编号", 120), ("project_name", "项目名称", 240), ("customer_name", "客户", 160),
            ("total_budget", "总预算", 100), ("status", "状态", 90), ("updated_at", "更新时间", 150),
        ], rows)

    def add_project(self):
        d = FieldDialog(self, "新建项目", [
            ("project_code", "项目编号", "text", None), ("project_name", "项目名称", "text", None),
            ("customer_name", "客户名称", "text", None), ("total_budget", "总预算", "text", None),
            ("project_background", "项目背景", "memo", None),
        ])
        if d.result:
            t = now_text()
            self.db.execute("INSERT INTO planning_projects(project_code, project_name, customer_name, project_background, total_budget, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                            (d.result["project_code"], d.result["project_name"], d.result["customer_name"], d.result["project_background"], float(d.result["total_budget"] or 0), t, t))
            self.db.log(self.current_user, "planning_project", None, "create", "", d.result, "新建规划项目")
            self.refresh_contexts()
            self.show_projects()

    def show_plans(self):
        self.clear("年度计划")
        ttk.Button(self.content, text="新建年度计划", command=self.add_plan).pack(anchor="w", pady=(0, 8))
        rows = self.db.query("SELECT id, plan_year, plan_name, annual_budget, status, updated_at FROM annual_plans WHERE project_id=? ORDER BY plan_year DESC, id DESC", (self.current_project_id(),))
        self.add_table(self.content, [("id", "ID", 50), ("plan_year", "年度", 80), ("plan_name", "计划名称", 260), ("annual_budget", "年度预算", 110), ("status", "状态", 90), ("updated_at", "更新时间", 150)], rows)

    def add_plan(self):
        if not self.current_project_id():
            messagebox.showwarning("提示", "请先新建项目")
            return
        d = FieldDialog(self, "新建年度计划", [
            ("plan_year", "年度", "text", None), ("plan_name", "计划名称", "text", None), ("annual_budget", "年度预算", "text", None),
            ("business_pain_points", "业务痛点", "memo", None), ("plan_description", "计划说明", "memo", None),
        ])
        if d.result:
            t = now_text()
            self.db.execute("INSERT INTO annual_plans(project_id, plan_year, plan_name, annual_budget, business_pain_points, plan_description, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                            (self.current_project_id(), int(d.result["plan_year"]), d.result["plan_name"], float(d.result["annual_budget"] or 0), d.result["business_pain_points"], d.result["plan_description"], t, t))
            self.db.log(self.current_user, "annual_plan", None, "create", "", d.result, "新建年度计划")
            self.refresh_contexts()
            self.show_plans()

    def show_versions(self):
        self.clear("版本管理")
        bar = ttk.Frame(self.content)
        bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(bar, text="新建版本", command=self.add_version).pack(side=tk.LEFT)
        ttk.Button(bar, text="冻结当前版本", command=self.freeze_version).pack(side=tk.LEFT, padx=(8, 0))
        rows = self.db.query("SELECT id, version_code, version_name, version_budget, status, is_frozen, planned_start_date, planned_end_date FROM implementation_versions WHERE project_id=? AND annual_plan_id=? ORDER BY id DESC", (self.current_project_id(), self.current_plan_id()))
        self.add_table(self.content, [("id", "ID", 50), ("version_code", "版本编号", 100), ("version_name", "版本名称", 220), ("version_budget", "版本预算", 100), ("status", "状态", 90), ("is_frozen", "已冻结", 70), ("planned_start_date", "计划开始", 110), ("planned_end_date", "计划结束", 110)], rows)

    def add_version(self):
        if not self.current_plan_id():
            messagebox.showwarning("提示", "请先新建年度计划")
            return
        d = FieldDialog(self, "新建落地版本", [
            ("version_code", "版本编号", "text", None), ("version_name", "版本名称", "text", None), ("version_budget", "版本预算", "text", None),
            ("planned_start_date", "计划开始日期", "text", None), ("planned_end_date", "计划结束日期", "text", None), ("version_goal", "版本目标", "memo", None), ("version_scope", "版本范围", "memo", None),
        ])
        if d.result:
            t = now_text()
            self.db.execute("""INSERT INTO implementation_versions(project_id, annual_plan_id, version_code, version_name, version_goal, version_scope, version_budget, planned_start_date, planned_end_date, created_at, updated_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (self.current_project_id(), self.current_plan_id(), d.result["version_code"], d.result["version_name"], d.result["version_goal"], d.result["version_scope"], float(d.result["version_budget"] or 0), d.result["planned_start_date"], d.result["planned_end_date"], t, t))
            self.db.log(self.current_user, "implementation_version", None, "create", "", d.result, "新建落地版本")
            self.refresh_contexts()
            self.show_versions()

    def freeze_version(self):
        version_id = self.current_version_id()
        if not version_id:
            return
        self.db.execute("UPDATE implementation_versions SET is_frozen=1, status='frozen', updated_at=? WHERE id=?", (now_text(), version_id))
        self.db.log(self.current_user, "implementation_version", version_id, "freeze", "", "", "冻结版本，核心字段转入变更管理")
        self.show_versions()

    def show_requirements(self):
        self.clear("需求管理")
        bar = ttk.Frame(self.content)
        bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(bar, text="新建需求", command=self.add_requirement).pack(side=tk.LEFT)
        ttk.Button(bar, text="状态流转", command=self.advance_requirement_status).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bar, text="导出需求清单", command=self.export_requirements).pack(side=tk.LEFT, padx=(8, 0))
        role = self.current_role.get()
        cols = [("id", "ID", 50), ("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 230), ("source_role", "来源", 90), ("requirement_type", "类型", 100), ("tags", "标签", 160), ("priority", "优先级", 70), ("status", "状态", 110)]
        if role not in SENSITIVE_ROLES:
            cols += [("allocated_budget", "分配预算", 100), ("actual_cost", "实际消耗", 100)]
        cols += [("updated_at", "更新时间", 150)]
        rows = self.db.query("SELECT * FROM requirements WHERE is_deleted=0 AND version_id=? ORDER BY id DESC", (self.current_version_id(),))
        self.req_tree = self.add_table(self.content, cols, rows)

    def add_requirement(self):
        if not self.current_project_id():
            messagebox.showwarning("提示", "请先选择项目")
            return
        code = "REQ-" + datetime.now().strftime("%Y%m%d%H%M%S")
        d = FieldDialog(self, "新建需求", [
            ("requirement_code", "需求编号", "text", None), ("requirement_name", "需求名称", "text", None), ("source_role", "来源角色", "combo", ["客户", "销售", "项目经理", "研发", "运营", "咨询负责人"]),
            ("proposer_name", "提出人", "text", None), ("owner_name", "对接人", "text", None), ("requirement_type", "需求类型", "combo", ["业务痛点", "功能优化", "运维 Bug", "招投标要求", "验收整改", "客户新增"]),
            ("tags", "标签", "text", None), ("priority", "优先级", "combo", ["P0", "P1", "P2", "高", "中", "低"]), ("status", "状态", "combo", STATUS_FLOW + EXTRA_STATUSES),
            ("estimated_budget", "预估预算", "text", None), ("allocated_budget", "分配预算", "text", None), ("actual_cost", "实际消耗", "text", None),
            ("planned_finish_date", "预计完成时间", "text", None), ("requirement_description", "需求描述", "memo", None), ("remark", "备注", "memo", None),
        ], {"requirement_code": code, "priority": "P1", "status": "草稿"})
        if d.result:
            t = now_text()
            self.db.execute("""INSERT INTO requirements(requirement_code, requirement_name, requirement_description, source_role, proposer_name, owner_name, project_id, annual_plan_id, version_id,
                               requirement_type, tags, priority, status, estimated_budget, allocated_budget, actual_cost, planned_finish_date, remark, created_at, updated_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (d.result["requirement_code"], d.result["requirement_name"], d.result["requirement_description"], d.result["source_role"], d.result["proposer_name"], d.result["owner_name"],
                             self.current_project_id(), self.current_plan_id(), self.current_version_id(), d.result["requirement_type"], d.result["tags"], d.result["priority"], d.result["status"],
                             float(d.result["estimated_budget"] or 0), float(d.result["allocated_budget"] or 0), float(d.result["actual_cost"] or 0), d.result["planned_finish_date"], d.result["remark"], t, t))
            self.db.log(self.current_user, "requirement", None, "create", "", d.result, "新建需求任务")
            self.show_requirements()

    def selected_requirement_id(self):
        sel = getattr(self, "req_tree", None).selection() if hasattr(self, "req_tree") else ()
        if not sel:
            messagebox.showwarning("提示", "请先在需求表中选择一行")
            return None
        return self.req_tree.item(sel[0])["values"][0]

    def advance_requirement_status(self):
        req_id = self.selected_requirement_id()
        if not req_id:
            return
        req = self.db.one("SELECT * FROM requirements WHERE id=?", (req_id,))
        d = FieldDialog(self, "状态流转", [("status", "目标状态", "combo", STATUS_FLOW + EXTRA_STATUSES), ("remark", "流转说明", "memo", None)], {"status": req["status"]})
        if d.result:
            before = req["status"]
            after = d.result["status"]
            self.db.execute("UPDATE requirements SET status=?, remark=?, updated_at=?, actual_finish_date=CASE WHEN ?='已关闭' THEN ? ELSE actual_finish_date END WHERE id=?",
                            (after, d.result["remark"], now_text(), after, now_text()[:10], req_id))
            self.db.log(self.current_user, "requirement", req_id, "status_change", before, after, f"需求状态流转：{d.result['remark']}")
            self.show_requirements()

    def show_budget(self):
        self.clear("资金管理")
        bar = ttk.Frame(self.content)
        bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(bar, text="登记资金流水", command=self.add_budget_flow).pack(side=tk.LEFT)
        ttk.Button(bar, text="导出资金明细", command=self.export_budget).pack(side=tk.LEFT, padx=(8, 0))
        project_id = self.current_project_id()
        version_id = self.current_version_id()
        p = self.db.one("SELECT total_budget FROM planning_projects WHERE id=?", (project_id,)) if project_id else {"total_budget": 0}
        a = self.db.one("SELECT annual_budget FROM annual_plans WHERE id=?", (self.current_plan_id(),)) if self.current_plan_id() else {"annual_budget": 0}
        v = self.db.one("SELECT version_budget FROM implementation_versions WHERE id=?", (version_id,)) if version_id else {"version_budget": 0}
        r = self.db.one("SELECT SUM(allocated_budget) allocated, SUM(actual_cost) cost FROM requirements WHERE version_id=? AND is_deleted=0", (version_id,)) if version_id else {"allocated": 0, "cost": 0}
        row = ttk.Frame(self.content)
        row.pack(fill=tk.X)
        for title, value in [("项目总预算", p["total_budget"] or 0), ("年度预算", a["annual_budget"] or 0), ("版本预算", v["version_budget"] or 0), ("需求已分配", r["allocated"] or 0), ("实际消耗", r["cost"] or 0)]:
            self.metric_card(row, title, f"{value:.2f}")
        rows = self.db.query("SELECT flow_code, flow_type, amount, description, operator_name, occurred_at FROM budget_flows WHERE project_id=? ORDER BY occurred_at DESC", (project_id,))
        self.add_table(self.content, [("flow_code", "流水编号", 140), ("flow_type", "类型", 100), ("amount", "金额", 100), ("description", "说明", 300), ("operator_name", "操作人", 100), ("occurred_at", "发生时间", 150)], rows, 12)

    def add_budget_flow(self):
        d = FieldDialog(self, "登记资金流水", [
            ("flow_type", "资金类型", "combo", ["计划预算", "已分配预算", "实际消耗", "调整金额", "冻结金额"]),
            ("amount", "金额", "text", None), ("description", "说明", "memo", None),
        ])
        if d.result:
            t = now_text()
            code = "BF-" + datetime.now().strftime("%Y%m%d%H%M%S")
            self.db.execute("INSERT INTO budget_flows(flow_code, project_id, annual_plan_id, version_id, flow_type, amount, description, operator_name, occurred_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (code, self.current_project_id(), self.current_plan_id(), self.current_version_id(), d.result["flow_type"], float(d.result["amount"] or 0), d.result["description"], self.current_user, t, t))
            self.db.log(self.current_user, "budget_flow", None, "create", "", d.result, "登记资金流水")
            self.show_budget()

    def show_artifacts(self):
        self.clear("成果物管理")
        ttk.Button(self.content, text="挂载本地文件", command=self.add_artifact).pack(anchor="w", pady=(0, 8))
        rows = self.db.query("SELECT artifact_code, artifact_name, artifact_type, related_object_type, related_object_id, file_path, uploaded_by, uploaded_at FROM artifacts ORDER BY id DESC")
        self.add_table(self.content, [("artifact_code", "成果物编号", 130), ("artifact_name", "名称", 180), ("artifact_type", "类型", 110), ("related_object_type", "挂载对象", 100), ("related_object_id", "对象ID", 70), ("file_path", "文件路径", 360), ("uploaded_by", "上传人", 90), ("uploaded_at", "上传时间", 150)], rows)

    def add_artifact(self):
        source = filedialog.askopenfilename(title="选择成果物文件")
        if not source:
            return
        d = FieldDialog(self, "成果物信息", [
            ("artifact_type", "成果物类型", "combo", ["可研报告", "分年任务申报书", "任务书方案", "招标文件", "应标文件", "验收报告", "项目总结", "运维反馈", "运营反馈", "其他"]),
            ("related_object_type", "挂载对象", "combo", ["项目", "年度", "版本", "需求"]),
            ("related_object_id", "对象ID", "text", None), ("description", "说明", "memo", None),
        ], {"related_object_type": "版本", "related_object_id": str(self.current_version_id() or self.current_project_id() or "")})
        if d.result:
            src = Path(source)
            code = "ART-" + datetime.now().strftime("%Y%m%d%H%M%S")
            dest = self.db.attachments_dir / f"{code}{src.suffix}"
            shutil.copy2(src, dest)
            t = now_text()
            self.db.execute("""INSERT INTO artifacts(artifact_code, artifact_name, artifact_type, file_path, file_ext, file_size, related_object_type, related_object_id, description, uploaded_by, uploaded_at, created_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (code, src.name, d.result["artifact_type"], str(dest), src.suffix, dest.stat().st_size, d.result["related_object_type"], int(d.result["related_object_id"] or 0), d.result["description"], self.current_user, t, t))
            self.db.log(self.current_user, "artifact", None, "create", source, dest, "挂载成果物文件")
            self.show_artifacts()

    def show_search(self):
        self.clear("搜索中心")
        keyword = self.search_var.get().strip()
        bar = ttk.Frame(self.content)
        bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(bar, text="关键词").pack(side=tk.LEFT)
        ttk.Entry(bar, textvariable=self.search_var, width=36).pack(side=tk.LEFT, padx=8)
        ttk.Button(bar, text="查询", command=self.show_search).pack(side=tk.LEFT)
        like = f"%{keyword}%"
        rows = self.db.query("""SELECT requirement_code, requirement_name, source_role, tags, priority, status, updated_at
                                FROM requirements
                                WHERE is_deleted=0 AND (requirement_code LIKE ? OR requirement_name LIKE ? OR requirement_description LIKE ? OR tags LIKE ?)
                                ORDER BY updated_at DESC""", (like, like, like, like)) if keyword else []
        self.add_table(self.content, [("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 260), ("source_role", "来源", 90), ("tags", "标签", 180), ("priority", "优先级", 70), ("status", "状态", 110), ("updated_at", "更新时间", 150)], rows)

    def show_exports(self):
        self.clear("报表导出")
        ttk.Button(self.content, text="导出需求清单 CSV", command=self.export_requirements).pack(anchor="w", pady=5)
        ttk.Button(self.content, text="导出资金明细 CSV", command=self.export_budget).pack(anchor="w", pady=5)
        ttk.Button(self.content, text="导出成果物目录 CSV", command=self.export_artifacts).pack(anchor="w", pady=5)
        ttk.Button(self.content, text="创建本地备份 ZIP", command=self.create_backup).pack(anchor="w", pady=5)
        ttk.Button(self.content, text="从备份 ZIP 恢复", command=self.restore_backup).pack(anchor="w", pady=5)
        ttk.Label(self.content, text=f"导出目录：{self.db.exports_dir}\n备份目录：{self.db.backups_dir}", wraplength=900).pack(anchor="w", pady=(12, 0))

    def export_csv(self, name, rows):
        path = self.db.exports_dir / f"{name}_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
        if not rows:
            messagebox.showinfo("提示", "没有可导出的数据")
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows([dict(r) for r in rows])
        messagebox.showinfo("导出完成", str(path))

    def export_requirements(self):
        self.export_csv("requirements", self.db.query("SELECT * FROM requirements WHERE is_deleted=0 ORDER BY id DESC"))

    def export_budget(self):
        self.export_csv("budget_flows", self.db.query("SELECT * FROM budget_flows ORDER BY id DESC"))

    def export_artifacts(self):
        self.export_csv("artifacts", self.db.query("SELECT * FROM artifacts ORDER BY id DESC"))

    def create_backup(self):
        backup = self.db.backups_dir / f"backup_{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
        with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(self.db.db_path, "app.db")
            for file in self.db.attachments_dir.rglob("*"):
                if file.is_file():
                    z.write(file, f"attachments/{file.name}")
        self.db.log(self.current_user, "backup", None, "create", "", backup, "创建本地备份")
        messagebox.showinfo("备份完成", str(backup))

    def restore_backup(self):
        source = filedialog.askopenfilename(title="选择备份 ZIP", filetypes=[("ZIP", "*.zip")])
        if not source:
            return
        if not messagebox.askyesno("确认恢复", "恢复会覆盖当前数据库和附件，请确认已另行备份。是否继续？"):
            return
        self.db.conn.close()
        with zipfile.ZipFile(source, "r") as z:
            z.extractall(self.db.data_dir)
        messagebox.showinfo("恢复完成", "已恢复备份，请重新启动程序。")
        self.destroy()

    def show_settings(self):
        self.clear("系统设置")
        ttk.Label(self.content, text="本机账号：默认管理员。首版支持通过右上角角色选择器查看不同角色视图。").pack(anchor="w", pady=(0, 8))
        rows = self.db.query("SELECT operator_name, operation_time, object_type, object_id, operation_type, description FROM operation_logs ORDER BY id DESC LIMIT 80")
        self.add_table(self.content, [("operator_name", "操作人", 100), ("operation_time", "时间", 150), ("object_type", "对象", 120), ("object_id", "对象ID", 70), ("operation_type", "操作", 120), ("description", "说明", 360)], rows)


if __name__ == "__main__":
    App().mainloop()
