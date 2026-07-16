import csv
import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk


APP_NAME = "咨询项目全流程需求管理系统"
APP_VERSION = "1.6.3-mysql"
APP_VARIANT = "MySQL 8.x LTS 远程版"
APP_RELEASE_LABEL = f"应用 v{APP_VERSION.removesuffix('-mysql')}"
HOST_NAME = socket.gethostname()
MYSQL_CONFIG_FILE = "mysql_config.json"
SESSION_HEARTBEAT_MS = 10_000
USER_DISPLAY_NAME_MAX_LENGTH = 40
TOPBAR_DISPLAY_NAME_MAX_LENGTH = 10
STATUS_FLOW = ["草稿", "规划中", "已排期", "研发中", "待验收", "已上线运维", "已关闭"]
EXTRA_STATUSES = ["已驳回", "已挂起", "已取消", "变更中", "退回修改"]
ROLES = ["管理员", "咨询负责人", "客户", "销售", "项目经理", "研发人员", "运营人员"]
SENSITIVE_ROLES = {"客户", "研发人员", "运营人员"}
STATUS_TRANSITIONS = {
    "草稿": ["规划中", "已取消"],
    "规划中": ["已排期", "已驳回", "已挂起", "退回修改"],
    "已排期": ["研发中", "已挂起", "已取消", "退回修改"],
    "研发中": ["待验收", "已挂起", "退回修改"],
    "待验收": ["已上线运维", "退回修改"],
    "已上线运维": ["已关闭", "变更中"],
    "已关闭": ["变更中"],
    "已驳回": ["草稿", "已取消"],
    "已挂起": ["规划中", "已排期", "研发中", "已取消"],
    "已取消": ["草稿"],
    "变更中": ["规划中", "已排期", "研发中", "待验收"],
    "退回修改": ["规划中", "已排期", "研发中", "已取消"],
}
ROLE_ACTIONS = {
    "管理员": {"*"},
    "咨询负责人": {"project", "plan", "version", "requirement_create", "requirement_edit", "requirement_delete", "requirement_assign", "status", "budget", "artifact", "approve", "export", "effort", "funding_review", "operation_record"},
    "客户": {"requirement_create"},
    "销售": {"requirement_create", "artifact", "export", "funding_create", "funding_submit"},
    "项目经理": {"requirement_create", "requirement_edit", "status", "budget", "artifact", "export", "effort"},
    "研发人员": {"requirement_create", "requirement_edit", "status", "artifact", "task_claim", "effort", "export"},
    "运营人员": {"requirement_create", "requirement_edit", "status", "artifact", "export", "operation_record"},
}
ROLE_DESCRIPTIONS = {
    "客户": "关注项目整体进度、需求处理状态、版本规划和待确认事项。",
    "销售": "关注资金申报进度、项目进展、投入汇总和可导出材料。",
    "项目经理": "关注版本交付进度、验收准备、实际投入和问题风险。",
    "研发人员": "关注待办任务、需求优先级、研发状态和工期评估。",
    "运营人员": "关注上线版本、线上问题池、运营推广记录和问题闭环。",
    "咨询负责人": "统筹项目规划、版本落地、资金拆分、需求响应和跨角色同步。",
    "管理员": "可查看全部模块，维护基础数据、备份恢复和操作日志。",
}
MONEY_COLUMNS = {
    "total_budget",
    "annual_budget",
    "version_budget",
    "estimated_budget",
    "allocated_budget",
    "actual_cost",
    "amount",
    "left_budget",
    "right_budget",
}
ROLE_PANEL_TITLES = {
    "客户": "客户视角待跟进",
    "销售": "销售视角资金同步",
    "项目经理": "项目经理交付关注",
    "研发人员": "研发任务队列",
    "运营人员": "运营问题与上线反馈",
    "咨询负责人": "咨询负责人统筹视图",
    "管理员": "管理员全局巡检",
}
DASHBOARD_SECTIONS = [
    ("role_panel", "角色工作台"),
    ("status", "需求状态分布"),
    ("trend", "需求推进趋势"),
    ("recent", "最近需求"),
]
DEFAULT_THEME = "云岚蓝"
THEME_PALETTES = {
    "云岚蓝": {
        "bg": "#F4F6F8", "surface": "#FFFFFF", "surface_alt": "#F8FAFC",
        "side": "#F8FAFC", "side_hover": "#EEF2F6", "side_active": "#E3ECFC",
        "side_text": "#475467", "side_active_text": "#1D4ED8", "brand_text": "#18212B",
        "text": "#18212B", "muted": "#667085",
        "line": "#DDE3EA", "primary": "#2563EB", "primary_active": "#1D4ED8",
        "focus": "#3B82F6", "control_hover": "#EEF3F8", "selection": "#DCE9FF",
        "selection_text": "#163A70", "status_bg": "#EDF1F4", "status_text": "#526172",
        "heading_bg": "#EEF2F6", "heading_hover": "#E3E9F0", "heading_text": "#344054",
        "role_bg": "#EAF2FF", "role_text": "#2457A6",
        "success": "#15803D", "warning": "#A15C00", "danger": "#B42318",
    },
    "松石青": {
        "bg": "#F3F7F6", "surface": "#FFFFFF", "surface_alt": "#F7FAF9",
        "side": "#1D2D2C", "side_hover": "#29403E", "side_active": "#0F766E",
        "side_text": "#E4EFED", "side_active_text": "#FFFFFF", "brand_text": "#FFFFFF",
        "text": "#172522", "muted": "#62716D",
        "line": "#D8E3E0", "primary": "#0F766E", "primary_active": "#0B5F59",
        "focus": "#14877E", "control_hover": "#EAF2F0", "selection": "#D8F0EC",
        "selection_text": "#124E49", "status_bg": "#EAF0EE", "status_text": "#50625D",
        "heading_bg": "#EAF1EF", "heading_hover": "#DFE9E6", "heading_text": "#304540",
        "role_bg": "#E3F3EF", "role_text": "#17645C",
        "success": "#15803D", "warning": "#A15C00", "danger": "#B42318",
    },
    "靛夜紫": {
        "bg": "#F6F5F8", "surface": "#FFFFFF", "surface_alt": "#FAF9FC",
        "side": "#282735", "side_hover": "#383648", "side_active": "#4F46E5",
        "side_text": "#ECEAF5", "side_active_text": "#FFFFFF", "brand_text": "#FFFFFF",
        "text": "#211F2C", "muted": "#6C687A",
        "line": "#E1DFE8", "primary": "#4F46E5", "primary_active": "#4338CA",
        "focus": "#6366F1", "control_hover": "#F0EFF5", "selection": "#E6E5FF",
        "selection_text": "#3730A3", "status_bg": "#EFEDF3", "status_text": "#5D596A",
        "heading_bg": "#F0EEF4", "heading_hover": "#E6E3EC", "heading_text": "#3F3B4D",
        "role_bg": "#EEECFF", "role_text": "#4338CA",
        "success": "#15803D", "warning": "#A15C00", "danger": "#B42318",
    },
    "石墨灰": {
        "bg": "#F4F5F6", "surface": "#FFFFFF", "surface_alt": "#F8F9FA",
        "side": "#222529", "side_hover": "#31353A", "side_active": "#475467",
        "side_text": "#ECEFF2", "side_active_text": "#FFFFFF", "brand_text": "#FFFFFF",
        "text": "#202327", "muted": "#66707A",
        "line": "#DDE1E5", "primary": "#344054", "primary_active": "#252F3F",
        "focus": "#52677F", "control_hover": "#EEF0F2", "selection": "#E4E8ED",
        "selection_text": "#273444", "status_bg": "#ECEFF1", "status_text": "#515B66",
        "heading_bg": "#EEF0F2", "heading_hover": "#E3E6E9", "heading_text": "#3A424B",
        "role_bg": "#E9EEF3", "role_text": "#3D5268",
        "success": "#15803D", "warning": "#A15C00", "danger": "#B42318",
    },
}
STATUS_COLORS = {
    "草稿": "#F1F3F5", "规划中": "#EAF2FF", "已排期": "#E7F6F1",
    "研发中": "#FFF3D6", "待验收": "#FFF0E5", "已上线运维": "#E6F5EF",
    "已关闭": "#E9EDF1", "已驳回": "#FDE9E7", "已挂起": "#F0ECFA",
    "已取消": "#ECEFF1", "变更中": "#F6EAF4", "退回修改": "#FBE9E8",
    "已完成": "#E5F3EA", "当前阶段": "#FFF2D8", "未开始": "#F1F3F5",
    "已提交": "#EAF2FF", "审批中": "#FFF2D8", "已批复": "#E5F3EA",
    "已拨付": "#E6F5EF", "待处理": "#FFF0E5", "处理中": "#FFF2D8",
}
STATUS_ACCENTS = {
    "草稿": "#667085", "规划中": "#2563EB", "已排期": "#0F766E",
    "研发中": "#A15C00", "待验收": "#C2410C", "已上线运维": "#15803D",
    "已关闭": "#475467", "已驳回": "#B42318", "已挂起": "#6941C6",
    "已取消": "#667085", "变更中": "#9E3D86", "退回修改": "#B42318",
    "已完成": "#15803D", "当前阶段": "#A15C00", "未开始": "#667085",
    "已提交": "#2563EB", "审批中": "#A15C00", "已批复": "#15803D",
    "已拨付": "#0F766E", "待处理": "#C2410C", "处理中": "#A15C00",
}
DIFF_COLORS = {"新增": "#E5F3EA", "移除": "#FBE9E8", "变更": "#FFF2D8"}
ARTIFACT_STAGE_HINTS = {
    "可研报告": "宏观规划",
    "分年任务申报书": "规划细化",
    "任务书方案": "建设落地",
    "需求任务表": "建设落地",
    "任务清单": "建设落地",
    "招标文件": "招投标",
    "应标文件": "招投标",
    "验收报告": "项目交付验收",
    "项目总结": "项目交付验收",
    "运维反馈": "运维运营",
    "运营反馈": "运维运营",
}
PROJECT_STAGES = ["宏观规划", "规划细化", "建设落地", "招投标", "项目交付验收", "运维运营"]
FUNDING_STATUSES = ["草稿", "已提交", "审批中", "已批复", "已驳回", "已拨付"]
FUNDING_TRANSITIONS = {
    "草稿": ["已提交"],
    "已提交": ["审批中"],
    "审批中": ["已批复", "已驳回"],
    "已批复": ["已拨付"],
    "已驳回": [],
    "已拨付": [],
}
OPERATION_TYPES = ["推广活动", "线上问题", "维护记录", "问题解答"]
OPERATION_STATUSES = ["待处理", "处理中", "已完成", "已关闭"]
ARTIFACT_TARGET_TYPES = {
    "可研报告": {"项目"},
    "分年任务申报书": {"年度"},
    "任务书方案": {"版本"},
    "需求任务表": {"版本"},
    "任务清单": {"版本"},
    "招标文件": {"版本"},
    "应标文件": {"版本"},
    "验收报告": {"版本"},
    "项目总结": {"版本"},
    "运维反馈": {"需求"},
    "运营反馈": {"需求"},
    "其他": {"项目", "年度", "版本", "需求"},
}
DANGEROUS_ARTIFACT_EXTENSIONS = {
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".msi", ".msp", ".scr", ".dll",
    ".jar", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".reg",
}
PASSWORD_ITERATIONS = 240000
LOGGER = logging.getLogger("consulting_requirement.runtime")
AUDIT_LOGGER = logging.getLogger("consulting_requirement.audit")


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_date(value, field_name):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field_name}必须使用 YYYY-MM-DD 格式并且是有效日期。") from exc


def money_text(value):
    return f"{float(value or 0):,.2f}"


def percent_text(part, total):
    total = float(total or 0)
    if total <= 0:
        return "0%"
    return f"{float(part or 0) / total * 100:.1f}%"


BUDGET_EPSILON = 1e-9


def budget_limit_exceeded(projected, limit):
    return float(projected or 0) > float(limit or 0) + BUDGET_EPSILON


def validate_cumulative_budget(new_budget, existing_budget, parent_budget, child_label, parent_label):
    values = [float(new_budget or 0), float(existing_budget or 0), float(parent_budget or 0)]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("预算必须是有限数值。")
    new_value, existing_value, parent_value = values
    if new_value < 0:
        raise ValueError(f"{child_label}不能小于 0。")
    projected = existing_value + new_value
    if budget_limit_exceeded(projected, parent_value):
        raise ValueError(
            f"{child_label}累计预算 {money_text(projected)} 不能超过{parent_label} {money_text(parent_value)}。"
        )
    return projected


def csv_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def business_key_text(value):
    return " ".join(str(value or "").strip().casefold().split())


def requirement_business_key(row):
    if row is None:
        return ""
    if not isinstance(row, dict):
        row = dict(row)
    return business_key_text(row.get("business_key")) or business_key_text(row.get("requirement_name"))


def requirement_business_key_conflicts(rows):
    seen = {}
    conflicts = []
    for raw_row in rows:
        row = raw_row if isinstance(raw_row, dict) else dict(raw_row)
        key = requirement_business_key(row)
        scope = (row.get("project_id"), row.get("version_id"), key)
        if key and scope in seen:
            conflicts.append((seen[scope], row.get("id"), key, scope[0], scope[1]))
        elif key:
            seen[scope] = row.get("id")
    return conflicts


REQUIREMENT_COMPARE_FIELDS = (
    ("requirement_code", "需求编号"),
    ("requirement_name", "需求名称"),
    ("requirement_description", "需求描述"),
    ("source_role", "来源角色"),
    ("proposer_name", "提出人"),
    ("owner_name", "负责人"),
    ("requirement_type", "需求类型"),
    ("tags", "标签"),
    ("priority", "优先级"),
    ("parent_requirement_business_key", "原需求关联"),
    ("planned_finish_date", "预计完成日期"),
    ("actual_finish_date", "实际完成日期"),
    ("status", "状态"),
    ("remark", "备注"),
)
REQUIREMENT_COMPARE_MONEY_FIELDS = (
    ("estimated_budget", "预估预算"),
    ("allocated_budget", "分配预算"),
    ("actual_cost", "实际成本"),
)
REQUIREMENT_COMPARE_EFFORT_FIELDS = (
    ("estimated_hours", "预估工时"),
    ("actual_hours", "实际工时"),
)


def _requirement_compare_value(row, field):
    if row is None:
        return None
    try:
        value = row[field]
    except (KeyError, IndexError, TypeError):
        value = None
    if field == "tags":
        return tuple(sorted(item.strip() for item in str(value or "").replace("，", ",").split(",") if item.strip()))
    if field in {name for name, _label in REQUIREMENT_COMPARE_MONEY_FIELDS + REQUIREMENT_COMPARE_EFFORT_FIELDS}:
        return round(float(value or 0), 8)
    if isinstance(value, str):
        return value.strip()
    return value


def requirement_compare_changes(left, right, include_money=False, include_effort=False):
    fields = list(REQUIREMENT_COMPARE_FIELDS)
    if include_money:
        fields.extend(REQUIREMENT_COMPARE_MONEY_FIELDS)
    if include_effort:
        fields.extend(REQUIREMENT_COMPARE_EFFORT_FIELDS)
    return [
        label for field, label in fields
        if _requirement_compare_value(left, field) != _requirement_compare_value(right, field)
    ]


def config_bool(value, name):
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise RuntimeError(f"{name} 必须使用 JSON 布尔值 true 或 false。")


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except (AttributeError, ValueError):
        return False


def app_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def env_int(name, default, minimum, maximum):
    try:
        return min(maximum, max(minimum, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} 必须是 1/0、true/false、yes/no 或 on/off。")


def configure_logging(logs_dir, variant):
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = env_int("CRM_LOG_MAX_BYTES", 10 * 1024 * 1024, 1024 * 1024, 1024 * 1024 * 1024)
    backup_count = env_int("CRM_LOG_BACKUP_COUNT", 30, 1, 365)
    level_name = os.environ.get("CRM_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    for logger in (LOGGER, AUDIT_LOGGER):
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        logger.propagate = False

    runtime_handler = RotatingFileHandler(logs_dir / "runtime.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    runtime_handler.setLevel(level)
    runtime_handler.setFormatter(logging.Formatter(f"%(asctime)s | %(levelname)s | %(process)d | {HOST_NAME} | %(message)s"))
    error_handler = RotatingFileHandler(logs_dir / "error.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter(f"%(asctime)s | %(levelname)s | %(process)d | {HOST_NAME} | %(message)s"))
    # Handler levels control routing. Keep the logger permissive so a CRITICAL
    # runtime setting cannot suppress ordinary ERROR records in error.log.
    LOGGER.setLevel(logging.DEBUG)
    LOGGER.addHandler(runtime_handler)
    LOGGER.addHandler(error_handler)

    audit_handler = RotatingFileHandler(logs_dir / "audit.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    audit_handler.setLevel(logging.INFO)
    audit_handler.setFormatter(logging.Formatter("%(message)s"))
    AUDIT_LOGGER.setLevel(logging.INFO)
    AUDIT_LOGGER.addHandler(audit_handler)
    if os.name != "nt":
        try:
            logs_dir.chmod(0o700)
            for path in [logs_dir / "runtime.log", logs_dir / "error.log", logs_dir / "audit.log"]:
                path.chmod(0o600)
        except OSError as exc:
            LOGGER.warning("log_permission_hardening_failed path=%s reason=%s", logs_dir, exc)
    LOGGER.info("logging_initialized variant=%s level=%s max_bytes=%s backups=%s", variant, level_name, max_bytes, backup_count)
    return logs_dir


def close_logging():
    for logger in (LOGGER, AUDIT_LOGGER):
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.flush()
            handler.close()


def new_event_id():
    return uuid.uuid4().hex


def new_session_token():
    return secrets.token_urlsafe(32)


def validated_display_name(value):
    display_name = str(value or "").strip()
    if not display_name:
        raise ValueError("显示名称不能为空。")
    if any(character in display_name for character in "\r\n\t"):
        raise ValueError("显示名称不能包含换行或制表符。")
    if len(display_name) > USER_DISPLAY_NAME_MAX_LENGTH:
        raise ValueError(f"显示名称不能超过 {USER_DISPLAY_NAME_MAX_LENGTH} 个字符。")
    return display_name


def topbar_identity_text(display_name, role_name):
    compact_name = " ".join(str(display_name or "未命名用户").split()) or "未命名用户"
    if len(compact_name) > TOPBAR_DISPLAY_NAME_MAX_LENGTH:
        compact_name = compact_name[:TOPBAR_DISPLAY_NAME_MAX_LENGTH - 1] + "…"
    return f"{compact_name} · {role_name}"


def audit_event(operator, object_type, object_id, operation_type, description, result="success", event_id=None):
    event_id = event_id or new_event_id()
    payload = {
        "timestamp": now_text(),
        "event_id": event_id,
        "host": HOST_NAME,
        "process_id": os.getpid(),
        "app_version": APP_VERSION,
        "variant": APP_VARIANT,
        "operator": str(operator or ""),
        "object_type": str(object_type or ""),
        "object_id": object_id,
        "operation_type": str(operation_type or ""),
        "description": str(description or ""),
        "result": result,
    }
    AUDIT_LOGGER.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return event_id


def sql_summary(sql):
    return " ".join(str(sql).split())[:160]


def log_transaction_exception(name, exc):
    if isinstance(exc, ValueError):
        LOGGER.warning("transaction_rejected name=%s reason=%s", name, exc)
    else:
        LOGGER.error("transaction_failed name=%s", name, exc_info=True)


def verify_directory_writable(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="crm-health-", dir=folder, delete=False) as handle:
        path = Path(handle.name)
        handle.write(b"healthcheck")
    path.unlink()


def validate_restore_archive(archive, required_names=(), required_prefix=None):
    configured_limit = env_int("CRM_RESTORE_MAX_BYTES", 20 * 1024**3, 1024**2, 1024**4)
    free_limit = max(0, shutil.disk_usage(tempfile.gettempdir()).free // 2)
    limit = min(configured_limit, free_limit)
    total_size = 0
    names = []
    for info in archive.infolist():
        name = info.filename
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("备份包含不安全路径。")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("备份包含不允许恢复的符号链接。")
        total_size += info.file_size
        if total_size > limit:
            raise ValueError(f"备份解压后大小超过恢复上限：{limit} 字节。")
        if info.file_size > 10 * 1024**2 and (info.compress_size == 0 or info.file_size / info.compress_size > 200):
            raise ValueError("备份包含异常压缩比文件，已拒绝恢复。")
        names.append(name)
    if any(name not in names for name in required_names):
        raise ValueError("备份缺少必要文件。")
    if required_prefix and not any(name.startswith(required_prefix) for name in names):
        raise ValueError(f"备份中没有 {required_prefix} 目录。")
    return names


class ExecutionResult:
    def __init__(self, lastrowid=None, rowcount=0):
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def close(self):
        pass


class Database:
    def __init__(self, base_dir: Path, data_dir=None, config_dir=None):
        self.base_dir = base_dir
        configured_data_dir = data_dir or os.environ.get("CRM_DATA_DIR", "")
        configured_config_dir = config_dir or os.environ.get("CRM_CONFIG_DIR", "")
        self.data_dir = Path(configured_data_dir).expanduser().resolve() if configured_data_dir else base_dir / "data"
        self.config_dir = Path(configured_config_dir).expanduser().resolve() if configured_config_dir else base_dir / "config"
        if self.data_dir.resolve() == Path(self.data_dir.resolve().anchor):
            raise RuntimeError("CRM_DATA_DIR 不能配置为磁盘根目录。")
        if self.config_dir.resolve() == Path(self.config_dir.resolve().anchor):
            raise RuntimeError("CRM_CONFIG_DIR 不能配置为磁盘根目录。")
        self.attachments_dir = self.data_dir / "attachments"
        self.backups_dir = self.data_dir / "backups"
        self.exports_dir = self.data_dir / "exports"
        self.logs_dir = self.data_dir / "logs"
        for folder in [self.data_dir, self.config_dir, self.attachments_dir, self.backups_dir, self.exports_dir, self.logs_dir]:
            folder.mkdir(parents=True, exist_ok=True)
        configure_logging(self.logs_dir, "MySQL remote")
        LOGGER.info("database_initializing engine=mysql config=%s", self.config_dir / MYSQL_CONFIG_FILE)
        self.config_path = self.config_dir / MYSQL_CONFIG_FILE
        self.config = self.load_config()
        self.attachment_storage = self.config["attachment_storage"]
        self._oss_bucket = None
        self._oss_credentials_fingerprint = None
        if self.attachment_storage == "server" and self.config["attachments_dir"]:
            self.attachments_dir = Path(self.config["attachments_dir"]).expanduser()
            resolved_attachments = self.attachments_dir.resolve()
            unsafe_roots = {Path(resolved_attachments.anchor), self.base_dir.resolve(), self.data_dir.resolve()}
            if resolved_attachments in unsafe_roots:
                raise RuntimeError("attachments_dir 必须是独立附件子目录，不能配置为磁盘根、共享根、应用目录或 data 根目录。")
            if resolved_attachments.parent == Path(resolved_attachments.anchor) or resolved_attachments == Path.home().resolve():
                raise RuntimeError("attachments_dir 范围过宽，请配置专用的两级附件子目录。")
            for protected in [self.base_dir.resolve(), self.data_dir.resolve(), self.config_dir.resolve()]:
                try:
                    protected.relative_to(resolved_attachments)
                    raise RuntimeError("attachments_dir 不能包含应用、数据或配置目录。")
                except ValueError:
                    pass
            self.attachments_dir.mkdir(parents=True, exist_ok=True)
        self.storage_name = "MySQL"
        self.db_label = f"mysql://{self.config['user']}@{self.config['host']}:{self.config['port']}/{self.config['database']}"
        self.db_path = self.config_path
        self.conn = self.connect()
        self.init_schema()
        self.seed_defaults()

    def load_config(self):
        example = {
            "host": "127.0.0.1",
            "port": 3306,
            "user": "crm_user",
            "password": "",
            "password_env": "CRM_DB_PASSWORD",
            "database": "consulting_requirement_manager",
            "create_database": False,
            "seed_demo_data": False,
            "connect_timeout": 10,
            "read_timeout": 30,
            "write_timeout": 30,
            "ssl_ca": "",
            "attachment_storage": "server",
            "attachments_dir": "",
            "oss_endpoint": "",
            "oss_bucket": "",
            "oss_prefix": "consulting-requirement-manager",
        }
        if not self.config_path.exists():
            self.config_path.write_text(json.dumps(example, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(
                f"已生成 MySQL 配置文件：{self.config_path}\n"
                "请先修改数据库连接，并配置共享 attachments_dir 或 OSS 后重新启动。"
            )
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        for key in ["host", "port", "user", "database"]:
            if key not in config or config[key] in ("", None):
                raise RuntimeError(f"MySQL 配置缺少 {key}：{self.config_path}")
        if not re.fullmatch(r"[A-Za-z0-9_]+", str(config["database"])):
            raise RuntimeError("database 只能包含字母、数字和下划线。")
        config["port"] = int(config["port"])
        config["create_database"] = config_bool(config.get("create_database", False), "create_database")
        config["seed_demo_data"] = config_bool(config.get("seed_demo_data", False), "seed_demo_data")
        config["connect_timeout"] = max(3, int(config.get("connect_timeout", 10)))
        config["read_timeout"] = max(3, int(config.get("read_timeout", 30)))
        config["write_timeout"] = max(3, int(config.get("write_timeout", 30)))
        config["ssl_ca"] = str(config.get("ssl_ca", "")).strip()
        config["password_env"] = str(config.get("password_env", "CRM_DB_PASSWORD")).strip()
        env_password = os.environ.get(config["password_env"], "") if config["password_env"] else ""
        config["password"] = env_password or str(config.get("password", ""))
        if not config["password"]:
            raise RuntimeError(
                f"MySQL 密码未配置。请设置环境变量 {config['password_env'] or 'CRM_DB_PASSWORD'}，"
                "或仅在受控环境中填写配置文件 password。"
            )
        config["attachment_storage"] = str(config.get("attachment_storage", "server")).strip().lower()
        if config["attachment_storage"] not in {"server", "oss"}:
            raise RuntimeError("attachment_storage 只能是 server 或 oss。")
        config["attachments_dir"] = str(config.get("attachments_dir", "")).strip()
        config["oss_endpoint"] = str(config.get("oss_endpoint", "")).strip()
        config["oss_bucket"] = str(config.get("oss_bucket", "")).strip()
        config["oss_prefix"] = str(config.get("oss_prefix", "consulting-requirement-manager")).strip(" /")
        if config["attachment_storage"] == "oss" and (not config["oss_endpoint"] or not config["oss_bucket"]):
            raise RuntimeError("OSS 模式必须配置 oss_endpoint 和 oss_bucket。")
        if config["attachment_storage"] == "server" and not config["attachments_dir"]:
            raise RuntimeError("多人远程版的 server 附件模式必须配置所有客户端可访问的共享 attachments_dir。")
        if config["attachment_storage"] == "oss":
            if not config["oss_endpoint"].lower().startswith("https://"):
                raise RuntimeError("正式 OSS 连接必须使用 https:// endpoint。")
            if not config["oss_prefix"] or ".." in config["oss_prefix"].split("/"):
                raise RuntimeError("oss_prefix 必须是非空且不包含 .. 的业务专用前缀。")
        return config

    def get_oss_bucket(self):
        try:
            import oss2
        except ImportError as exc:
            raise RuntimeError("OSS 模式缺少 oss2 依赖，请运行 pip install -r requirements.txt") from exc
        access_key_id = os.environ.get("OSS_ACCESS_KEY_ID", "")
        access_key_secret = os.environ.get("OSS_ACCESS_KEY_SECRET", "")
        security_token = os.environ.get("OSS_SECURITY_TOKEN", "")
        if not access_key_id or not access_key_secret:
            raise RuntimeError("OSS 凭据必须通过 OSS_ACCESS_KEY_ID 和 OSS_ACCESS_KEY_SECRET 环境变量提供。")
        fingerprint = hashlib.sha256(
            f"{access_key_id}\0{access_key_secret}\0{security_token}".encode("utf-8")
        ).hexdigest()
        if self._oss_bucket is not None and self._oss_credentials_fingerprint == fingerprint:
            return self._oss_bucket
        auth = oss2.StsAuth(access_key_id, access_key_secret, security_token) if security_token else oss2.Auth(access_key_id, access_key_secret)
        self._oss_bucket = oss2.Bucket(auth, self.config["oss_endpoint"], self.config["oss_bucket"])
        self._oss_credentials_fingerprint = fingerprint
        return self._oss_bucket

    def oss_key_from_path(self, stored_path):
        prefix = f"oss://{self.config['oss_bucket']}/"
        if not str(stored_path).startswith(prefix):
            raise RuntimeError("附件 OSS 路径与当前 Bucket 不一致。")
        key = str(stored_path)[len(prefix):]
        required_prefix = self.config["oss_prefix"].rstrip("/") + "/"
        if not key.startswith(required_prefix):
            raise RuntimeError("附件 OSS 路径不属于当前系统前缀。")
        return key

    def attachment_maintenance_lock_name(self):
        database_hash = hashlib.sha256(self.config["database"].encode("utf-8")).hexdigest()[:40]
        return f"crm_attachment_{database_hash}"

    def acquire_attachment_maintenance_lock(self, timeout=30):
        if self.attachment_storage != "server":
            return None
        connection = self.connect()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT GET_LOCK(%s, %s)", (self.attachment_maintenance_lock_name(), int(timeout)))
            result = cursor.fetchone()
            if not result or result[0] != 1:
                raise RuntimeError("等待附件维护锁超时，请确认没有其他客户端正在上传、备份或恢复附件。")
            return connection
        except Exception:
            connection.close()
            raise
        finally:
            cursor.close()

    def release_attachment_maintenance_lock(self, connection):
        if connection is None:
            return
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT RELEASE_LOCK(%s)", (self.attachment_maintenance_lock_name(),))
            cursor.fetchone()
        except Exception:
            LOGGER.exception("attachment_maintenance_lock_release_failed")
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

    def store_attachment(self, source, code, lock_held=False):
        source = Path(source)
        filename = f"{code}{source.suffix}"
        if self.attachment_storage == "oss":
            key = "/".join(part for part in [self.config["oss_prefix"], datetime.now().strftime("%Y/%m"), filename] if part)
            self.get_oss_bucket().put_object_from_file(key, str(source))
            return f"oss://{self.config['oss_bucket']}/{key}"
        maintenance_connection = None if lock_held else self.acquire_attachment_maintenance_lock()
        try:
            destination = self.attachments_dir / filename
            shutil.copy2(source, destination)
            return filename
        finally:
            self.release_attachment_maintenance_lock(maintenance_connection)

    def delete_attachment(self, stored_path, lock_held=False):
        if not stored_path:
            return
        if stored_path.startswith("oss://"):
            self.get_oss_bucket().delete_object(self.oss_key_from_path(stored_path))
            return
        path = Path(stored_path)
        if not path.is_absolute():
            path = self.attachments_dir / path
        try:
            path.resolve().relative_to(self.attachments_dir.resolve())
        except ValueError as exc:
            raise RuntimeError("拒绝删除附件根目录之外的文件。") from exc
        maintenance_connection = None if lock_held else self.acquire_attachment_maintenance_lock()
        try:
            if path.exists() and path.is_file():
                path.unlink()
        finally:
            self.release_attachment_maintenance_lock(maintenance_connection)

    def download_attachment(self, stored_path, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if stored_path.startswith("oss://"):
            self.get_oss_bucket().get_object_to_file(self.oss_key_from_path(stored_path), str(destination))
            return destination
        source = Path(stored_path)
        if not source.is_absolute():
            source = self.attachments_dir / source
        try:
            source.resolve().relative_to(self.attachments_dir.resolve())
        except ValueError as exc:
            raise RuntimeError("附件路径不在当前服务器附件目录内。") from exc
        if not source.is_file():
            raise FileNotFoundError(f"附件不存在：{source}")
        shutil.copy2(source, destination)
        return destination

    def connect(self):
        try:
            import mysql.connector
        except ImportError as exc:
            raise RuntimeError("缺少依赖 mysql-connector-python，请先运行：pip install -r requirements.txt") from exc
        connect_options = {
            "host": self.config["host"],
            "port": self.config["port"],
            "user": self.config["user"],
            "password": self.config["password"],
            "charset": "utf8mb4",
            "use_unicode": True,
            "connection_timeout": self.config["connect_timeout"],
            "read_timeout": self.config["read_timeout"],
            "write_timeout": self.config["write_timeout"],
            "autocommit": True,
        }
        if self.config["ssl_ca"]:
            connect_options.update({
                "ssl_ca": self.config["ssl_ca"],
                "ssl_verify_cert": True,
                "ssl_verify_identity": True,
            })
        if self.config["create_database"]:
            server_conn = mysql.connector.connect(**connect_options)
            server_cur = server_conn.cursor()
            server_cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{self.config['database']}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            server_conn.commit()
            server_cur.close()
            server_conn.close()
        return mysql.connector.connect(database=self.config["database"], **connect_options)

    def ensure_connection(self):
        try:
            self.conn.ping(reconnect=True, attempts=2, delay=1)
        except Exception as exc:
            LOGGER.exception("mysql_connection_unavailable target=%s", self.db_label)
            raise RuntimeError(f"MySQL 连接不可用：{exc}") from exc

    def begin_transaction(self):
        self.ensure_connection()
        self.conn.autocommit = False
        try:
            return self.conn.cursor(dictionary=True)
        except Exception:
            self.conn.autocommit = True
            raise

    def end_transaction(self, cursor):
        try:
            if cursor is not None:
                cursor.close()
        except Exception:
            LOGGER.exception("mysql_transaction_cursor_close_failed")
        finally:
            try:
                self.conn.autocommit = True
            except Exception:
                LOGGER.exception("mysql_autocommit_restore_failed")

    def mysql_sql(self, sql):
        sql = sql.replace("?", "%s")
        sql = sql.replace("v.version_code || ' ' || v.version_name", "CONCAT(v.version_code, ' ', v.version_name)")
        return sql

    def execute(self, sql, params=()):
        self.ensure_connection()
        cur = self.conn.cursor(dictionary=True)
        try:
            cur.execute(self.mysql_sql(sql), params)
            return ExecutionResult(cur.lastrowid, cur.rowcount)
        except Exception:
            LOGGER.exception("mysql_execute_failed sql=%s", sql_summary(sql))
            raise
        finally:
            cur.close()

    def query(self, sql, params=()):
        self.ensure_connection()
        cur = self.conn.cursor(dictionary=True)
        try:
            cur.execute(self.mysql_sql(sql), params)
            return cur.fetchall()
        except Exception:
            LOGGER.exception("mysql_query_failed sql=%s", sql_summary(sql))
            raise
        finally:
            cur.close()

    def one(self, sql, params=()):
        self.ensure_connection()
        cur = self.conn.cursor(dictionary=True, buffered=True)
        try:
            cur.execute(self.mysql_sql(sql), params)
            return cur.fetchone()
        except Exception:
            LOGGER.exception("mysql_query_one_failed sql=%s", sql_summary(sql))
            raise
        finally:
            cur.close()

    def close(self):
        try:
            self.conn.close()
            LOGGER.info("database_connection_closed engine=mysql")
        except Exception:
            LOGGER.exception("database_close_failed engine=mysql")

    def claim_user_session(self, user_id, session_token, started_at, expected_password_hash, expected_role):
        result = self.execute(
            """UPDATE users SET session_token=?, session_started_at=?
               WHERE id=? AND is_active=1
                 AND BINARY COALESCE(password_hash,'')=BINARY ? AND role_name=?""",
            (session_token, started_at, user_id, str(expected_password_hash or ""), expected_role),
        )
        return result.rowcount == 1

    def release_user_session(self, user_id, session_token):
        if not user_id or not session_token:
            return False
        result = self.execute(
            """UPDATE users SET session_token=NULL, session_started_at=NULL
               WHERE id=? AND session_token=?""",
            (user_id, session_token),
        )
        return result.rowcount == 1

    def update_user_role(self, user_id, new_role, operator_name, occurred_at):
        if new_role not in ROLES:
            raise ValueError("无效的用户角色。")
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT username, role_name FROM users WHERE id=%s FOR UPDATE", (user_id,))
            user = cur.fetchone()
            if not user:
                raise ValueError("用户不存在。")
            if user["role_name"] == new_role:
                self.conn.rollback()
                return user["role_name"]
            cur.execute(
                """UPDATE users SET role_name=%s, session_token=NULL, session_started_at=NULL, updated_at=%s
                   WHERE id=%s""",
                (new_role, occurred_at, user_id),
            )
            cur.execute("DELETE FROM user_project_access WHERE user_id=%s", (user_id,))
            event_id = new_event_id()
            cur.execute(
                """INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                  operation_type, before_value, after_value, description,
                                                  event_id, result)
                   VALUES(%s,%s,'user',%s,'change_role',%s,%s,%s,%s,'success')""",
                (operator_name, occurred_at, user_id, user["role_name"], new_role,
                 f"修改用户 {user['username']} 角色并清空项目授权", event_id),
            )
            self.conn.commit()
            audit_event(operator_name, "user", user_id, "change_role",
                        f"用户 {user['username']} 角色由 {user['role_name']} 调整为 {new_role}", event_id=event_id)
            return user["role_name"]
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("update_user_role", exc)
            raise
        finally:
            self.end_transaction(cur)

    def healthcheck(self):
        server = self.one("""SELECT VERSION() mysql_version,
                                    @@character_set_database database_charset,
                                    @@collation_database database_collation,
                                    @@session.time_zone session_time_zone,
                                    @@session.sql_mode sql_mode,
                                    @@session.transaction_isolation transaction_isolation""")
        version_match = re.match(r"(\d+)", str(server["mysql_version"]))
        if "mariadb" in str(server["mysql_version"]).lower() or not version_match or int(version_match.group(1)) != 8:
            raise RuntimeError(f"远程版要求 MySQL 8.x，当前版本：{server['mysql_version']}")
        if str(server["database_charset"]).lower() != "utf8mb4":
            raise RuntimeError(f"数据库字符集必须为 utf8mb4，当前：{server['database_charset']}")
        if env_bool("CRM_REQUIRE_STRICT_SQL", True) and not any(
            mode in str(server["sql_mode"]).upper() for mode in ["STRICT_TRANS_TABLES", "STRICT_ALL_TABLES"]
        ):
            raise RuntimeError("MySQL 未启用严格 SQL mode。")
        ssl_status = self.one("SHOW STATUS LIKE 'Ssl_cipher'") or {}
        ssl_cipher = ssl_status.get("Value") or ssl_status.get("value") or ""
        require_tls = env_bool("CRM_REQUIRE_TLS", False)
        if self.config["ssl_ca"] and not ssl_cipher:
            raise RuntimeError("已配置 ssl_ca，但当前 MySQL 会话未使用 TLS。")
        if require_tls and (not self.config["ssl_ca"] or not ssl_cipher):
            raise RuntimeError("CRM_REQUIRE_TLS=1，但未建立经过 CA 校验的 TLS 会话。")
        required_tables = [
            "planning_projects", "annual_plans", "implementation_versions", "requirements",
            "budget_flows", "artifacts", "users", "operation_logs", "version_baselines",
            "user_project_access", "task_effort_entries", "tag_definitions", "dashboard_preferences",
            "version_baseline_artifacts", "funding_applications", "operation_records",
            "change_requests", "change_request_payloads", "requirement_status_history",
            "version_baseline_requirements",
        ]
        for table in required_tables:
            self.one(f"SELECT 1 probe FROM {table} LIMIT 1")
        self.one("SELECT event_id, result FROM operation_logs LIMIT 1")
        self.one("SELECT business_key, estimated_hours, actual_hours, parent_requirement_id FROM requirements LIMIT 1")
        business_rows = self.query("""SELECT id, project_id, version_id, requirement_name, business_key
                                      FROM requirements WHERE is_deleted=0 ORDER BY id""")
        business_issues = []
        invalid_ids = [row["id"] for row in business_rows if not requirement_business_key(row)]
        if invalid_ids:
            business_issues.append(f"业务标识为空（需求 {invalid_ids[:8]}）")
        noncanonical_ids = [row["id"] for row in business_rows
                            if str(row.get("business_key") or "") != requirement_business_key(row)]
        if noncanonical_ids:
            business_issues.append(f"业务标识未规范化（需求 {noncanonical_ids[:8]}）")
        conflicts = requirement_business_key_conflicts(business_rows)
        if conflicts:
            pairs = [f"{left}/{right}" for left, right, _key, _project, _version in conflicts[:8]]
            business_issues.append(f"同版本业务标识冲突（需求 {', '.join(pairs)}）")
        if business_issues:
            raise RuntimeError("发现无效业务数据：" + "；".join(business_issues))
        self.one("SELECT current_stage FROM planning_projects LIMIT 1")
        self.one("SELECT session_token, session_started_at FROM users LIMIT 1")
        self.one("SELECT visibility, approval_status, change_request_id FROM artifacts LIMIT 1")
        self.one("SELECT estimated_hours, actual_hours FROM version_baselines LIMIT 1")
        self.one("""SELECT requirement_description, business_key, source_role, owner_name, estimated_budget,
                           estimated_hours, planned_finish_date, remark, parent_requirement_id
                    FROM version_baseline_requirements LIMIT 1""")
        self.one("SELECT baseline_id, visibility FROM version_baseline_artifacts LIMIT 1")
        self.one("SELECT project_id, annual_plan_id, amount, status, applicant_name FROM funding_applications LIMIT 1")
        self.one("SELECT project_id, version_id, requirement_id, record_type, status, record_date FROM operation_records LIMIT 1")
        orphan_checks = {
            "年度计划": "SELECT COUNT(*) c FROM annual_plans a LEFT JOIN planning_projects p ON p.id=a.project_id WHERE p.id IS NULL",
            "落地版本": "SELECT COUNT(*) c FROM implementation_versions v LEFT JOIN annual_plans a ON a.id=v.annual_plan_id WHERE a.id IS NULL OR a.project_id<>v.project_id",
            "需求项目": "SELECT COUNT(*) c FROM requirements r LEFT JOIN planning_projects p ON p.id=r.project_id WHERE p.id IS NULL",
            "需求年度": "SELECT COUNT(*) c FROM requirements r LEFT JOIN annual_plans a ON a.id=r.annual_plan_id WHERE r.annual_plan_id IS NOT NULL AND (a.id IS NULL OR a.project_id<>r.project_id)",
            "需求版本": "SELECT COUNT(*) c FROM requirements r LEFT JOIN implementation_versions v ON v.id=r.version_id WHERE r.version_id IS NOT NULL AND (v.id IS NULL OR v.project_id<>r.project_id OR r.annual_plan_id IS NULL OR v.annual_plan_id<>r.annual_plan_id)",
            "原需求": "SELECT COUNT(*) c FROM requirements r LEFT JOIN requirements p ON p.id=r.parent_requirement_id WHERE r.parent_requirement_id IS NOT NULL AND (p.id IS NULL OR p.project_id<>r.project_id)",
            "工时需求": "SELECT COUNT(*) c FROM task_effort_entries e LEFT JOIN requirements r ON r.id=e.requirement_id WHERE r.id IS NULL",
            "资金申报": "SELECT COUNT(*) c FROM funding_applications f LEFT JOIN planning_projects p ON p.id=f.project_id LEFT JOIN annual_plans a ON a.id=f.annual_plan_id WHERE p.id IS NULL OR a.id IS NULL OR a.project_id<>f.project_id",
            "运营项目": "SELECT COUNT(*) c FROM operation_records o LEFT JOIN planning_projects p ON p.id=o.project_id WHERE p.id IS NULL",
            "运营版本": "SELECT COUNT(*) c FROM operation_records o LEFT JOIN implementation_versions v ON v.id=o.version_id WHERE o.version_id IS NOT NULL AND (v.id IS NULL OR v.project_id<>o.project_id)",
            "运营需求": "SELECT COUNT(*) c FROM operation_records o LEFT JOIN requirements r ON r.id=o.requirement_id WHERE o.requirement_id IS NOT NULL AND (r.id IS NULL OR r.project_id<>o.project_id OR (o.version_id IS NOT NULL AND NOT (r.version_id <=> o.version_id)))",
            "基线需求": "SELECT COUNT(*) c FROM version_baseline_requirements r LEFT JOIN version_baselines b ON b.id=r.baseline_id WHERE b.id IS NULL",
            "基线成果物": "SELECT COUNT(*) c FROM version_baseline_artifacts a LEFT JOIN version_baselines b ON b.id=a.baseline_id WHERE b.id IS NULL",
            "变更版本": "SELECT COUNT(*) c FROM change_requests c LEFT JOIN implementation_versions v ON v.id=c.version_id WHERE v.id IS NULL",
            "变更需求": "SELECT COUNT(*) c FROM change_requests c LEFT JOIN requirements r ON r.id=c.requirement_id WHERE c.requirement_id IS NOT NULL AND (r.id IS NULL OR NOT (r.version_id <=> c.version_id))",
            "变更载荷": "SELECT COUNT(*) c FROM change_request_payloads p LEFT JOIN change_requests c ON c.id=p.change_request_id WHERE c.id IS NULL",
        }
        for label, sql in orphan_checks.items():
            if self.one(sql)["c"]:
                raise RuntimeError(f"发现孤立关联数据：{label}")
        value_checks = {
            "项目阶段": "SELECT COUNT(*) c FROM planning_projects WHERE current_stage IS NULL OR current_stage NOT IN ('宏观规划','规划细化','建设落地','招投标','项目交付验收','运维运营')",
            "需求状态": "SELECT COUNT(*) c FROM requirements WHERE status IS NULL OR status NOT IN ('草稿','规划中','已排期','研发中','待验收','已上线运维','已关闭','已驳回','已挂起','已取消','变更中','退回修改')",
            "成果物状态": "SELECT COUNT(*) c FROM artifacts WHERE visibility IS NULL OR visibility NOT IN ('内部','客户可见') OR approval_status IS NULL OR approval_status NOT IN ('pending','approved','rejected')",
            "资金申报状态": "SELECT COUNT(*) c FROM funding_applications WHERE status IS NULL OR status NOT IN ('草稿','已提交','审批中','已批复','已驳回','已拨付') OR amount IS NULL OR amount<=0",
            "运营记录枚举": "SELECT COUNT(*) c FROM operation_records WHERE record_type IS NULL OR record_type NOT IN ('推广活动','线上问题','维护记录','问题解答') OR status IS NULL OR status NOT IN ('待处理','处理中','已完成','已关闭')",
            "变更申请状态": "SELECT COUNT(*) c FROM change_requests WHERE approval_status IS NULL OR approval_status NOT IN ('pending','approved','rejected')",
            "变更载荷类型": "SELECT COUNT(*) c FROM change_request_payloads WHERE change_type IS NULL OR change_type NOT IN ('update','delete','artifact_add')",
        }
        for label, sql in value_checks.items():
            if self.one(sql)["c"]:
                raise RuntimeError(f"发现无效业务数据：{label}")
        for folder in [self.data_dir, self.backups_dir, self.exports_dir, self.logs_dir]:
            verify_directory_writable(folder)
        minimum_free = env_int("CRM_MIN_FREE_BYTES", 512 * 1024 * 1024, 0, 10 * 1024 * 1024 * 1024 * 1024)
        free_bytes = shutil.disk_usage(self.data_dir).free
        if free_bytes < minimum_free:
            raise RuntimeError(f"磁盘剩余空间低于阈值：{free_bytes} < {minimum_free}")
        if self.attachment_storage == "server":
            verify_directory_writable(self.attachments_dir)
            attachment = {
                "storage": "server",
                "target": str(self.attachments_dir),
                "free_bytes": shutil.disk_usage(self.attachments_dir).free,
            }
        else:
            bucket = self.get_oss_bucket()
            key = "/".join(part for part in [
                self.config["oss_prefix"], "_healthchecks", HOST_NAME,
                datetime.now().strftime("%Y%m%d%H%M%S%f") + ".txt",
            ] if part)
            uploaded = False
            try:
                bucket.put_object(key, b"healthcheck")
                uploaded = True
            finally:
                if uploaded:
                    bucket.delete_object(key)
            attachment = {"storage": "oss", "target": f"oss://{self.config['oss_bucket']}/{self.config['oss_prefix']}"}
        return {
            **server,
            "tls_required": require_tls,
            "tls_cipher": ssl_cipher,
            "database": self.db_label,
            "data_dir": str(self.data_dir),
            "free_bytes": free_bytes,
            "attachment": attachment,
        }

    def create_annual_plan(self, record, operator_name, occurred_at):
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT id, total_budget FROM planning_projects WHERE id=%s FOR UPDATE", (record["project_id"],))
            project = cur.fetchone()
            if not project:
                raise ValueError("规划项目不存在。")
            cur.execute("SELECT id, annual_budget FROM annual_plans WHERE project_id=%s FOR UPDATE", (record["project_id"],))
            existing_budget = sum(float(row["annual_budget"] or 0) for row in cur.fetchall())
            validate_cumulative_budget(
                record["annual_budget"], existing_budget, project["total_budget"], "年度预算", "项目总预算"
            )
            cur.execute("""INSERT INTO annual_plans(project_id, plan_year, plan_name, annual_budget,
                                                      business_pain_points, plan_description, created_at, updated_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (record["project_id"], record["plan_year"], record["plan_name"], record["annual_budget"],
                         record.get("business_pain_points", ""), record.get("plan_description", ""),
                         occurred_at, occurred_at))
            plan_id = cur.lastrowid
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'annual_plan',%s,'create','',%s,'新建年度计划',%s,'success')""",
                        (operator_name, occurred_at, plan_id,
                         json.dumps(record, ensure_ascii=False, default=str), event_id))
            self.conn.commit()
            audit_event(operator_name, "annual_plan", plan_id, "create", "新建年度计划", event_id=event_id)
            return plan_id
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("create_annual_plan", exc)
            raise
        finally:
            self.end_transaction(cur)

    def create_implementation_version(self, record, operator_name, occurred_at):
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT id, project_id, annual_budget FROM annual_plans WHERE id=%s FOR UPDATE",
                        (record["annual_plan_id"],))
            plan = cur.fetchone()
            if not plan:
                raise ValueError("年度计划不存在。")
            if plan["project_id"] != record["project_id"]:
                raise ValueError("年度计划不属于当前项目。")
            cur.execute("SELECT id, version_budget FROM implementation_versions WHERE annual_plan_id=%s FOR UPDATE",
                        (record["annual_plan_id"],))
            existing_budget = sum(float(row["version_budget"] or 0) for row in cur.fetchall())
            validate_cumulative_budget(
                record["version_budget"], existing_budget, plan["annual_budget"], "版本预算", "年度预算"
            )
            cur.execute("""INSERT INTO implementation_versions(
                               project_id, annual_plan_id, version_code, version_name, version_goal, version_scope,
                               version_budget, planned_start_date, planned_end_date, created_at, updated_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (record["project_id"], record["annual_plan_id"], record["version_code"],
                         record["version_name"], record.get("version_goal", ""), record.get("version_scope", ""),
                         record["version_budget"], record.get("planned_start_date"), record.get("planned_end_date"),
                         occurred_at, occurred_at))
            version_id = cur.lastrowid
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'implementation_version',%s,'create','',%s,'新建落地版本',%s,'success')""",
                        (operator_name, occurred_at, version_id,
                         json.dumps(record, ensure_ascii=False, default=str), event_id))
            self.conn.commit()
            audit_event(operator_name, "implementation_version", version_id, "create", "新建落地版本", event_id=event_id)
            return version_id
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("create_implementation_version", exc)
            raise
        finally:
            self.end_transaction(cur)

    def create_requirement(self, record, operator_name, occurred_at):
        if record.get("status") != "草稿":
            raise ValueError("新需求必须从草稿状态开始。")
        business_key = requirement_business_key(record)
        estimated_hours = float(record.get("estimated_hours", 0) or 0)
        if not business_key:
            raise ValueError("业务需求标识不能为空。")
        if not math.isfinite(estimated_hours) or estimated_hours < 0:
            raise ValueError("预估工时必须是大于等于 0 的有限数值。")
        cur = self.begin_transaction()
        try:
            version_id = record.get("version_id")
            if version_id:
                cur.execute("SELECT id, project_id, annual_plan_id, is_frozen FROM implementation_versions WHERE id=%s FOR UPDATE", (version_id,))
                version = cur.fetchone()
                if not version:
                    raise ValueError("目标版本不存在。")
                if version["is_frozen"]:
                    raise ValueError("目标版本已冻结，请提交变更申请。")
                if version["project_id"] != record.get("project_id") or version["annual_plan_id"] != record.get("annual_plan_id"):
                    raise ValueError("需求的项目、年度计划与目标版本不一致。")
            else:
                cur.execute("SELECT id FROM planning_projects WHERE id=%s FOR UPDATE", (record.get("project_id"),))
                if not cur.fetchone():
                    raise ValueError("需求所属项目不存在。")
            cur.execute("""SELECT id, business_key, requirement_name FROM requirements
                           WHERE project_id=%s AND version_id <=> %s AND is_deleted=0 FOR UPDATE""",
                        (record.get("project_id"), version_id))
            if any(requirement_business_key(candidate) == business_key for candidate in cur.fetchall()):
                raise ValueError("同一版本内业务需求标识不能重复。")
            parent_id = record.get("parent_requirement_id")
            parent_id = int(parent_id) if parent_id not in (None, "") else None
            if parent_id:
                cur.execute("SELECT id FROM requirements WHERE id=%s AND project_id=%s AND is_deleted=0 FOR UPDATE",
                            (parent_id, record["project_id"]))
                if not cur.fetchone():
                    raise ValueError("关联原需求必须是同一项目内的有效需求。")
            cur.execute("""INSERT INTO requirements(requirement_code, requirement_name, requirement_description,
                               business_key, source_role, proposer_name, owner_name, project_id, annual_plan_id, version_id,
                               requirement_type, tags, priority, status, estimated_budget, allocated_budget,
                               actual_cost, estimated_hours, actual_hours, planned_finish_date, remark,
                               parent_requirement_id, created_at, updated_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (record["requirement_code"], record["requirement_name"], record["requirement_description"],
                         business_key, record["source_role"], record.get("proposer_name", ""), record.get("owner_name", ""),
                         record["project_id"], record.get("annual_plan_id"), record.get("version_id"),
                         record.get("requirement_type", ""), record.get("tags", ""), record.get("priority", "P1"),
                         "草稿", record.get("estimated_budget", 0), record.get("allocated_budget", 0),
                         record.get("actual_cost", 0), estimated_hours, 0, record.get("planned_finish_date"),
                         record.get("remark", ""), parent_id, record["created_at"], record["updated_at"]))
            requirement_id = cur.lastrowid
            cur.execute("""INSERT INTO requirement_status_history(requirement_id, from_status, to_status,
                                                                    operator_name, transition_note, changed_at)
                           VALUES(%s,'',%s,%s,'新建需求',%s)""",
                        (requirement_id, record["status"], operator_name, occurred_at))
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'requirement',%s,'create','',%s,'新建需求任务',%s,'success')""",
                        (operator_name, occurred_at, requirement_id,
                         json.dumps(record, ensure_ascii=False, default=str), event_id))
            self.conn.commit()
            audit_event(operator_name, "requirement", requirement_id, "create", "新建需求任务", event_id=event_id)
            return requirement_id
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("create_requirement", exc)
            raise
        finally:
            self.end_transaction(cur)

    def update_requirement(self, requirement_id, record, operator_name, occurred_at):
        business_key = requirement_business_key(record)
        estimated_hours = float(record.get("estimated_hours", 0) or 0)
        if not business_key:
            raise ValueError("业务需求标识不能为空。")
        if not math.isfinite(estimated_hours) or estimated_hours < 0:
            raise ValueError("预估工时必须是大于等于 0 的有限数值。")
        current = self.one("SELECT version_id, project_id FROM requirements WHERE id=? AND is_deleted=0", (requirement_id,))
        if not current:
            raise ValueError("需求不存在或已删除。")
        expected_version_id = current["version_id"]
        cur = self.begin_transaction()
        try:
            if expected_version_id:
                cur.execute("SELECT project_id, is_frozen FROM implementation_versions WHERE id=%s FOR UPDATE", (expected_version_id,))
                version = cur.fetchone()
                if not version:
                    raise ValueError("需求所属版本不存在。")
                if version["project_id"] != current["project_id"]:
                    raise ValueError("需求所属版本与项目不一致。")
                if version["is_frozen"]:
                    raise ValueError("VERSION_FROZEN")
            else:
                cur.execute("SELECT id FROM planning_projects WHERE id=%s FOR UPDATE", (current["project_id"],))
                if not cur.fetchone():
                    raise ValueError("需求所属项目不存在。")
            cur.execute("SELECT * FROM requirements WHERE id=%s AND is_deleted=0 FOR UPDATE", (requirement_id,))
            before = cur.fetchone()
            if (not before or before["version_id"] != expected_version_id
                    or before["project_id"] != current["project_id"]):
                raise ValueError("需求所属版本已被其他操作更新，请刷新后重试。")
            cur.execute("""SELECT id, business_key, requirement_name FROM requirements
                           WHERE project_id=%s AND version_id <=> %s AND id<>%s AND is_deleted=0 FOR UPDATE""",
                        (before["project_id"], before["version_id"], requirement_id))
            if any(requirement_business_key(candidate) == business_key for candidate in cur.fetchall()):
                raise ValueError("同一版本内业务需求标识不能重复。")
            parent_id = record.get("parent_requirement_id")
            parent_id = int(parent_id) if parent_id not in (None, "") else None
            if parent_id:
                cur.execute("SELECT id FROM requirements WHERE id=%s AND project_id=%s AND is_deleted=0 FOR UPDATE",
                            (parent_id, before["project_id"]))
                if not cur.fetchone() or parent_id == requirement_id:
                    raise ValueError("关联原需求必须是同一项目内的其他有效需求。")
                cur.execute("""WITH RECURSIVE descendants(id) AS (
                                   SELECT id FROM requirements WHERE parent_requirement_id=%s AND is_deleted=0
                                   UNION
                                   SELECT r.id FROM requirements r JOIN descendants d ON r.parent_requirement_id=d.id
                                   WHERE r.is_deleted=0
                               ) SELECT 1 probe FROM descendants WHERE id=%s LIMIT 1""",
                            (requirement_id, parent_id))
                if cur.fetchone():
                    raise ValueError("关联原需求会形成循环关系。")
            cur.execute("""UPDATE requirements SET requirement_name=%s, requirement_description=%s, business_key=%s,
                               source_role=%s, proposer_name=%s, owner_name=%s, requirement_type=%s, tags=%s,
                               priority=%s, estimated_budget=%s, estimated_hours=%s, planned_finish_date=%s, remark=%s,
                               parent_requirement_id=%s, updated_at=%s
                           WHERE id=%s AND is_deleted=0""",
                        (record["requirement_name"], record["requirement_description"], business_key, record["source_role"],
                         record.get("proposer_name", ""), record.get("owner_name", ""), record.get("requirement_type", ""),
                         record.get("tags", ""), record.get("priority", "P1"), record["estimated_budget"],
                         estimated_hours, record.get("planned_finish_date"), record.get("remark", ""), parent_id,
                         occurred_at, requirement_id))
            if cur.rowcount != 1:
                raise ValueError("需求已被其他操作更新，请刷新后重试。")
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'requirement',%s,'update',%s,%s,'编辑需求任务',%s,'success')""",
                        (operator_name, occurred_at, requirement_id, json.dumps(before, ensure_ascii=False, default=str),
                         json.dumps(record, ensure_ascii=False, default=str), event_id))
            self.conn.commit()
            audit_event(operator_name, "requirement", requirement_id, "update", "编辑需求任务", event_id=event_id)
            return before
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("update_requirement", exc)
            raise
        finally:
            self.end_transaction(cur)

    def assign_requirement(self, requirement_id, target_version_id, annual_plan_id, operator_name, occurred_at):
        if not target_version_id:
            raise ValueError("目标版本不能为空。")
        current = self.one("SELECT version_id, project_id FROM requirements WHERE id=? AND is_deleted=0", (requirement_id,))
        if not current:
            raise ValueError("需求不存在或已删除。")
        expected_source_id = current["version_id"]
        cur = self.begin_transaction()
        try:
            versions = {}
            for version_id in sorted({value for value in [expected_source_id, target_version_id] if value}):
                cur.execute("SELECT id, project_id, annual_plan_id, is_frozen FROM implementation_versions WHERE id=%s FOR UPDATE", (version_id,))
                versions[version_id] = cur.fetchone()
                if not versions[version_id]:
                    raise ValueError("关联版本不存在。")
            if expected_source_id and versions[expected_source_id]["is_frozen"] and expected_source_id != target_version_id:
                raise ValueError("源版本已冻结，不能直接移出需求。")
            if versions[target_version_id]["is_frozen"]:
                raise ValueError("目标版本已冻结，不能直接分配需求。")
            if versions[target_version_id]["annual_plan_id"] != annual_plan_id:
                raise ValueError("目标版本与年度计划不一致。")
            if versions[target_version_id]["project_id"] != current["project_id"]:
                raise ValueError("目标版本与需求所属项目不一致。")
            cur.execute("""SELECT id, version_id, project_id, business_key, requirement_name
                           FROM requirements WHERE id=%s AND is_deleted=0 FOR UPDATE""", (requirement_id,))
            locked = cur.fetchone()
            if not locked or locked["version_id"] != expected_source_id or locked["project_id"] != current["project_id"]:
                raise ValueError("需求所属版本已被其他操作更新，请刷新后重试。")
            business_key = requirement_business_key(locked)
            # Lock the target version's live rows as well as the version row. This serializes
            # legacy blank-key checks with concurrent creates, edits, and assignments.
            cur.execute("""SELECT id, business_key, requirement_name FROM requirements
                           WHERE project_id=%s AND version_id=%s AND id<>%s AND is_deleted=0
                           FOR UPDATE""",
                        (locked["project_id"], target_version_id, requirement_id))
            candidates = cur.fetchall()
            if business_key and any(requirement_business_key(candidate) == business_key for candidate in candidates):
                raise ValueError("同一版本内业务需求标识不能重复。")
            cur.execute("""UPDATE requirements SET annual_plan_id=%s, version_id=%s, business_key=%s, updated_at=%s
                           WHERE id=%s AND is_deleted=0""",
                        (annual_plan_id, target_version_id, business_key, occurred_at, requirement_id))
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'requirement',%s,'assign_version',%s,%s,'需求分配到当前版本',%s,'success')""",
                        (operator_name, occurred_at, requirement_id, str(expected_source_id or ""), str(target_version_id), event_id))
            self.conn.commit()
            audit_event(operator_name, "requirement", requirement_id, "assign_version", "需求分配到当前版本", event_id=event_id)
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("assign_requirement", exc)
            raise
        finally:
            self.end_transaction(cur)

    def soft_delete_requirement(self, requirement_id, operator_name, occurred_at):
        current = self.one("SELECT version_id FROM requirements WHERE id=? AND is_deleted=0", (requirement_id,))
        if not current:
            raise ValueError("需求不存在或已删除。")
        expected_version_id = current["version_id"]
        cur = self.begin_transaction()
        try:
            if expected_version_id:
                cur.execute("SELECT is_frozen FROM implementation_versions WHERE id=%s FOR UPDATE", (expected_version_id,))
                version = cur.fetchone()
                if not version:
                    raise ValueError("需求所属版本不存在。")
                if version["is_frozen"]:
                    raise ValueError("VERSION_FROZEN")
            cur.execute("SELECT * FROM requirements WHERE id=%s AND is_deleted=0 FOR UPDATE", (requirement_id,))
            before = cur.fetchone()
            if not before or before["version_id"] != expected_version_id:
                raise ValueError("需求所属版本已变化，请刷新后重试。")
            cur.execute("UPDATE requirements SET is_deleted=1, updated_at=%s WHERE id=%s AND is_deleted=0",
                        (occurred_at, requirement_id))
            if cur.rowcount != 1:
                raise ValueError("需求已被其他操作删除，请刷新后重试。")
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'requirement',%s,'delete',%s,'','软删除需求',%s,'success')""",
                        (operator_name, occurred_at, requirement_id,
                         json.dumps(before, ensure_ascii=False, default=str), event_id))
            self.conn.commit()
            audit_event(operator_name, "requirement", requirement_id, "delete", "软删除需求", event_id=event_id)
            return before
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("soft_delete_requirement", exc)
            raise
        finally:
            self.end_transaction(cur)

    def create_change_request_record(self, requirement_id, change_type, proposed, values, operator_name, occurred_at):
        if not str(values.get("change_title") or "").strip() or not str(values.get("change_reason") or "").strip():
            raise ValueError("变更标题和原因不能为空。")
        if change_type not in {"update", "delete"}:
            raise ValueError("需求变更类型无效。")
        if not isinstance(proposed if proposed is not None else {}, dict):
            raise ValueError("变更申请载荷必须是对象结构。")
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT version_id FROM requirements WHERE id=%s AND is_deleted=0", (requirement_id,))
            probe = cur.fetchone()
            if not probe or not probe["version_id"]:
                raise ValueError("只有已分配到冻结版本的需求才能提交变更申请。")
            cur.execute("SELECT is_frozen FROM implementation_versions WHERE id=%s FOR UPDATE", (probe["version_id"],))
            version = cur.fetchone()
            if not version or not version["is_frozen"]:
                raise ValueError("目标版本尚未冻结，请直接编辑需求。")
            cur.execute("SELECT id, version_id FROM requirements WHERE id=%s AND is_deleted=0 FOR UPDATE", (requirement_id,))
            requirement = cur.fetchone()
            if not requirement or requirement["version_id"] != probe["version_id"]:
                raise ValueError("需求所属版本已变化，请刷新后重试。")
            cur.execute("SELECT id FROM change_requests WHERE requirement_id=%s AND approval_status='pending' FOR UPDATE",
                        (requirement_id,))
            pending = cur.fetchone()
            if pending:
                raise ValueError(f"该需求已有待审批变更 #{pending['id']}，请先完成审批。")
            cur.execute("""INSERT INTO change_requests(version_id, requirement_id, change_title, change_reason,
                                                        impact_scope, approval_status, requested_by, requested_at)
                           VALUES(%s,%s,%s,%s,%s,'pending',%s,%s)""",
                        (probe["version_id"], requirement_id, values["change_title"].strip(), values["change_reason"].strip(),
                         values.get("impact_scope", ""), operator_name, occurred_at))
            change_id = cur.lastrowid
            cur.execute("INSERT INTO change_request_payloads(change_request_id, change_type, proposed_value) VALUES(%s,%s,%s)",
                        (change_id, change_type, json.dumps(proposed or {}, ensure_ascii=False, default=str)))
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'change_request',%s,'create','',%s,'冻结版本需求变更申请',%s,'success')""",
                        (operator_name, occurred_at, change_id, json.dumps(values, ensure_ascii=False, default=str), event_id))
            self.conn.commit()
            audit_event(operator_name, "change_request", change_id, "create", "冻结版本需求变更申请", event_id=event_id)
            return change_id
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("create_change_request", exc)
            raise
        finally:
            self.end_transaction(cur)

    def create_artifact_record(self, record, operator_name, occurred_at):
        cur = self.begin_transaction()
        try:
            object_type = record["related_object_type"]
            object_id = record["related_object_id"]
            artifact_type = record["artifact_type"]
            if object_type not in ARTIFACT_TARGET_TYPES.get(artifact_type, set()):
                raise ValueError(f"“{artifact_type}”不能挂载到“{object_type}”。")
            if str(record.get("file_ext") or "").lower() in DANGEROUS_ARTIFACT_EXTENSIONS:
                raise ValueError("不允许挂载可执行或脚本文件。")
            visibility = record.get("visibility", "内部")
            if visibility not in {"内部", "客户可见"}:
                raise ValueError("成果物可见范围无效。")
            version_id = None
            requirement_id = None
            frozen = False
            target_project_id = None
            if object_type == "项目":
                cur.execute("SELECT id FROM planning_projects WHERE id=%s FOR UPDATE", (object_id,))
                target = cur.fetchone()
                if not target:
                    raise ValueError("关联项目不存在。")
                target_project_id = target["id"]
            elif object_type == "年度":
                cur.execute("SELECT id, project_id FROM annual_plans WHERE id=%s FOR UPDATE", (object_id,))
                target = cur.fetchone()
                if not target:
                    raise ValueError("关联年度计划不存在。")
                target_project_id = target["project_id"]
            elif object_type == "版本":
                cur.execute("SELECT id, project_id, is_frozen FROM implementation_versions WHERE id=%s FOR UPDATE", (object_id,))
                target = cur.fetchone()
                if not target:
                    raise ValueError("关联版本不存在。")
                version_id = target["id"]
                target_project_id = target["project_id"]
                frozen = bool(target["is_frozen"])
            elif object_type == "需求":
                cur.execute("""SELECT r.id, r.project_id, r.version_id, COALESCE(v.is_frozen,0) is_frozen
                               FROM requirements r LEFT JOIN implementation_versions v ON v.id=r.version_id
                               WHERE r.id=%s AND r.is_deleted=0 FOR UPDATE""", (object_id,))
                target = cur.fetchone()
                if not target:
                    raise ValueError("关联需求不存在。")
                requirement_id = target["id"]
                target_project_id = target["project_id"]
                version_id = target["version_id"]
                frozen = bool(target["is_frozen"])
            if record.get("project_id") and target_project_id != record["project_id"]:
                raise ValueError("成果物挂载对象不属于当前项目。")
            approval_status = "pending" if frozen else "approved"
            cur.execute("""INSERT INTO artifacts(
                               artifact_code, artifact_name, artifact_type, file_path, file_ext, file_size,
                               related_object_type, related_object_id, version_no, description, visibility,
                               approval_status, uploaded_by, uploaded_at, created_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (record["artifact_code"], record["artifact_name"], artifact_type, record["file_path"],
                         record.get("file_ext", ""), record.get("file_size", 0), object_type, object_id,
                         record.get("version_no", ""), record.get("description", ""), visibility,
                         approval_status, operator_name, occurred_at, occurred_at))
            artifact_id = cur.lastrowid
            change_id = None
            if approval_status == "pending":
                if requirement_id:
                    cur.execute("SELECT id FROM change_requests WHERE requirement_id=%s AND approval_status='pending' FOR UPDATE",
                                (requirement_id,))
                    pending = cur.fetchone()
                    if pending:
                        raise ValueError(f"该需求已有待审批变更 #{pending['id']}，请先完成审批。")
                cur.execute("""INSERT INTO change_requests(
                                   version_id, requirement_id, change_title, change_reason, impact_scope,
                                   approval_status, requested_by, requested_at)
                               VALUES(%s,%s,%s,%s,%s,'pending',%s,%s)""",
                            (version_id, requirement_id, f"新增成果物：{record['artifact_name']}",
                             record.get("description") or "冻结版本新增成果物", f"{object_type} #{object_id}",
                             operator_name, occurred_at))
                change_id = cur.lastrowid
                cur.execute("INSERT INTO change_request_payloads(change_request_id, change_type, proposed_value) VALUES(%s,%s,%s)",
                            (change_id, "artifact_add", json.dumps({"artifact_id": artifact_id}, ensure_ascii=False)))
                cur.execute("UPDATE artifacts SET change_request_id=%s WHERE id=%s", (change_id, artifact_id))
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'artifact',%s,'create','',%s,%s,%s,'success')""",
                        (operator_name, occurred_at, artifact_id, approval_status,
                         f"挂载成果物：{record['artifact_name']}", event_id))
            self.conn.commit()
            audit_event(operator_name, "artifact", artifact_id, "create", f"挂载成果物：{record['artifact_name']}", event_id=event_id)
            return {"artifact_id": artifact_id, "approval_status": approval_status, "change_id": change_id}
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("create_artifact", exc)
            raise
        finally:
            self.end_transaction(cur)

    def create_funding_application(self, record, operator_name, occurred_at):
        amount = float(record.get("amount", 0) or 0)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("申报金额必须是大于 0 的有限数值。")
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT id FROM planning_projects WHERE id=%s FOR UPDATE", (record["project_id"],))
            if not cur.fetchone():
                raise ValueError("申报项目不存在。")
            cur.execute("SELECT id, project_id FROM annual_plans WHERE id=%s FOR UPDATE", (record["annual_plan_id"],))
            plan = cur.fetchone()
            if not plan or plan["project_id"] != record["project_id"]:
                raise ValueError("申报年度不属于当前项目。")
            cur.execute("""INSERT INTO funding_applications(
                               application_code, project_id, annual_plan_id, amount, status, applicant_name,
                               description, created_at, updated_at)
                           VALUES(%s,%s,%s,%s,'草稿',%s,%s,%s,%s)""",
                        (record["application_code"], record["project_id"], record["annual_plan_id"], amount,
                         record["applicant_name"], record.get("description", ""), occurred_at, occurred_at))
            application_id = cur.lastrowid
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'funding_application',%s,'create','',%s,'新建资金申报',%s,'success')""",
                        (operator_name, occurred_at, application_id,
                         json.dumps(record, ensure_ascii=False, default=str), event_id))
            self.conn.commit()
            audit_event(operator_name, "funding_application", application_id, "create", "新建资金申报", event_id=event_id)
            return application_id
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("create_funding_application", exc)
            raise
        finally:
            self.end_transaction(cur)

    def transition_funding_application(self, application_id, from_status, to_status, operator_name, occurred_at):
        if to_status not in FUNDING_TRANSITIONS.get(from_status, []):
            raise ValueError(f"不允许从“{from_status}”流转到“{to_status}”。")
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT status FROM funding_applications WHERE id=%s FOR UPDATE", (application_id,))
            row = cur.fetchone()
            if not row or row["status"] != from_status:
                self.conn.rollback()
                return False
            submitted_at = occurred_at if to_status == "已提交" else None
            reviewed_by = operator_name if to_status in {"审批中", "已批复", "已驳回", "已拨付"} else None
            reviewed_at = occurred_at if reviewed_by else None
            cur.execute("""UPDATE funding_applications SET status=%s,
                               submitted_at=COALESCE(%s, submitted_at), reviewed_by=COALESCE(%s, reviewed_by),
                               reviewed_at=COALESCE(%s, reviewed_at), updated_at=%s
                           WHERE id=%s AND status=%s""",
                        (to_status, submitted_at, reviewed_by, reviewed_at, occurred_at, application_id, from_status))
            if cur.rowcount != 1:
                self.conn.rollback()
                return False
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'funding_application',%s,'status_change',%s,%s,%s,%s,'success')""",
                        (operator_name, occurred_at, application_id, from_status, to_status,
                         f"资金申报状态：{from_status} -> {to_status}", event_id))
            self.conn.commit()
            audit_event(operator_name, "funding_application", application_id, "status_change",
                        f"{from_status} -> {to_status}", event_id=event_id)
            return True
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("transition_funding_application", exc)
            raise
        finally:
            self.end_transaction(cur)

    def create_operation_record(self, record, operator_name, occurred_at):
        if record.get("record_type") not in OPERATION_TYPES or record.get("status", "待处理") not in OPERATION_STATUSES:
            raise ValueError("运营记录类型或状态无效。")
        record_date = normalize_date(record.get("record_date"), "记录日期")
        if not str(record.get("description") or "").strip():
            raise ValueError("运营记录说明不能为空。")
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT id FROM planning_projects WHERE id=%s FOR UPDATE", (record["project_id"],))
            if not cur.fetchone():
                raise ValueError("运营记录项目不存在。")
            version_id = record.get("version_id")
            requirement_id = record.get("requirement_id")
            if version_id:
                cur.execute("SELECT project_id FROM implementation_versions WHERE id=%s FOR UPDATE", (version_id,))
                version = cur.fetchone()
                if not version or version["project_id"] != record["project_id"]:
                    raise ValueError("关联版本不属于当前项目。")
            if requirement_id:
                cur.execute("SELECT project_id, version_id FROM requirements WHERE id=%s AND is_deleted=0 FOR UPDATE",
                            (requirement_id,))
                requirement = cur.fetchone()
                if not requirement or requirement["project_id"] != record["project_id"]:
                    raise ValueError("关联需求不属于当前项目。")
                if version_id and requirement["version_id"] != version_id:
                    raise ValueError("关联需求不属于所选版本。")
            cur.execute("""INSERT INTO operation_records(
                               record_code, project_id, version_id, requirement_id, record_type, status,
                               record_date, owner_name, description, result, created_by, created_at, updated_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (record["record_code"], record["project_id"], version_id, requirement_id,
                         record["record_type"], record.get("status", "待处理"), record_date,
                         record.get("owner_name", ""), record["description"].strip(), record.get("result", ""),
                         operator_name, occurred_at, occurred_at))
            operation_id = cur.lastrowid
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'operation_record',%s,'create','',%s,'新增运营服务记录',%s,'success')""",
                        (operator_name, occurred_at, operation_id,
                         json.dumps(record, ensure_ascii=False, default=str), event_id))
            self.conn.commit()
            audit_event(operator_name, "operation_record", operation_id, "create", "新增运营服务记录", event_id=event_id)
            return operation_id
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("create_operation_record", exc)
            raise
        finally:
            self.end_transaction(cur)

    def update_operation_record(self, operation_id, record, operator_name, occurred_at):
        if record.get("record_type") not in OPERATION_TYPES or record.get("status") not in OPERATION_STATUSES:
            raise ValueError("运营记录类型或状态无效。")
        record_date = normalize_date(record.get("record_date"), "记录日期")
        if not str(record.get("description") or "").strip():
            raise ValueError("运营记录说明不能为空。")
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT * FROM operation_records WHERE id=%s FOR UPDATE", (operation_id,))
            before = cur.fetchone()
            if not before:
                raise ValueError("运营记录不存在。")
            cur.execute("""UPDATE operation_records SET record_type=%s, status=%s, record_date=%s,
                               owner_name=%s, description=%s, result=%s, updated_at=%s WHERE id=%s""",
                        (record["record_type"], record["status"], record_date, record.get("owner_name", ""),
                         record["description"].strip(), record.get("result", ""), occurred_at, operation_id))
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'operation_record',%s,'update',%s,%s,'更新运营服务记录',%s,'success')""",
                        (operator_name, occurred_at, operation_id,
                         json.dumps(before, ensure_ascii=False, default=str),
                         json.dumps(record, ensure_ascii=False, default=str), event_id))
            self.conn.commit()
            audit_event(operator_name, "operation_record", operation_id, "update", "更新运营服务记录", event_id=event_id)
            return before
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("update_operation_record", exc)
            raise
        finally:
            self.end_transaction(cur)

    def update_project_stage(self, project_id, stage, operator_name, occurred_at):
        if stage not in PROJECT_STAGES:
            raise ValueError("项目阶段无效。")
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT current_stage FROM planning_projects WHERE id=%s FOR UPDATE", (project_id,))
            before = cur.fetchone()
            if not before:
                raise ValueError("项目不存在。")
            cur.execute("UPDATE planning_projects SET current_stage=%s, updated_at=%s WHERE id=%s",
                        (stage, occurred_at, project_id))
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'planning_project',%s,'stage_change',%s,%s,'更新项目阶段',%s,'success')""",
                        (operator_name, occurred_at, project_id, before["current_stage"], stage, event_id))
            self.conn.commit()
            audit_event(operator_name, "planning_project", project_id, "stage_change", "更新项目阶段", event_id=event_id)
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("update_project_stage", exc)
            raise
        finally:
            self.end_transaction(cur)

    def record_budget_flow(self, flow_code, project_id, annual_plan_id, version_id, requirement_id,
                           flow_type, amount, description, operator_name, occurred_at, allow_actual_overrun=False):
        cur = self.begin_transaction()
        try:
            amount = float(amount or 0)
            if not math.isfinite(amount):
                raise ValueError("资金金额必须是有限数值。")
            amount = round(amount, 2)
            req = None
            version = None
            if version_id:
                cur.execute("SELECT version_budget, is_frozen FROM implementation_versions WHERE id=%s FOR UPDATE", (version_id,))
                version = cur.fetchone()
                if not version:
                    raise ValueError("关联版本不存在。")
                if version["is_frozen"] and flow_type != "实际消耗":
                    raise ValueError("版本已冻结，只允许继续登记实际消耗；预算分配或调整需在冻结前完成。")
            linked_types = {"已分配预算", "实际消耗", "调整金额"}
            if flow_type in linked_types:
                if not requirement_id:
                    raise ValueError(f"资金类型“{flow_type}”必须关联具体需求。")
                cur.execute("SELECT * FROM requirements WHERE id=%s AND is_deleted=0 FOR UPDATE", (requirement_id,))
                req = cur.fetchone()
                if not req:
                    raise ValueError("关联需求不存在或已删除。")
                if req["version_id"] != version_id:
                    raise ValueError("关联需求不属于当前版本。")
            if req and flow_type in {"已分配预算", "调整金额"}:
                new_value = round(float(req["allocated_budget"] or 0) + amount, 2)
                if new_value < 0:
                    raise ValueError("调整后的需求分配预算不能小于 0。")
                cur.execute("SELECT id FROM requirements WHERE version_id=%s AND is_deleted=0 FOR UPDATE", (version_id,))
                cur.fetchall()
                cur.execute("SELECT COALESCE(SUM(allocated_budget),0) total FROM requirements WHERE version_id=%s AND is_deleted=0", (version_id,))
                projected = round(float(cur.fetchone()["total"] or 0) + amount, 2)
                if version and budget_limit_exceeded(projected, version["version_budget"]):
                    raise ValueError(f"分配后版本需求预算 {money_text(projected)} 将超过版本预算 {money_text(version['version_budget'])}。")
            if req and flow_type == "实际消耗":
                new_actual = round(float(req["actual_cost"] or 0) + amount, 2)
                if budget_limit_exceeded(new_actual, req["allocated_budget"]) and not allow_actual_overrun:
                    raise ValueError("ACTUAL_OVERRUN")
            before_snapshot = {
                "allocated_budget": float(req["allocated_budget"] or 0),
                "actual_cost": float(req["actual_cost"] or 0),
            } if req else {"flow_amount": 0.0}
            cur.execute("""INSERT INTO budget_flows(flow_code, project_id, annual_plan_id, version_id, requirement_id, flow_type, amount, description, operator_name, occurred_at, created_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (flow_code, project_id, annual_plan_id, version_id, requirement_id, flow_type, amount, description, operator_name, occurred_at, occurred_at))
            if req and flow_type in {"已分配预算", "调整金额"}:
                cur.execute("UPDATE requirements SET allocated_budget=%s, updated_at=%s WHERE id=%s", (new_value, occurred_at, requirement_id))
            elif req and flow_type == "实际消耗":
                cur.execute("UPDATE requirements SET actual_cost=%s, updated_at=%s WHERE id=%s", (new_actual, occurred_at, requirement_id))
            after_snapshot = dict(before_snapshot)
            if req and flow_type in {"已分配预算", "调整金额"}:
                after_snapshot["allocated_budget"] = new_value
            elif req and flow_type == "实际消耗":
                after_snapshot["actual_cost"] = new_actual
            else:
                after_snapshot = {"flow_type": flow_type, "flow_amount": amount}
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'budget_flow',%s,'create',%s,%s,%s,%s,'success')""",
                        (operator_name, occurred_at, requirement_id,
                         json.dumps(before_snapshot, ensure_ascii=False),
                         json.dumps(after_snapshot, ensure_ascii=False),
                         f"登记资金流水：{flow_type} / {flow_code}", event_id))
            self.conn.commit()
            audit_event(operator_name, "budget_flow", requirement_id, "create", f"登记资金流水：{flow_type}", event_id=event_id)
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("record_budget_flow", exc)
            raise
        finally:
            self.end_transaction(cur)

    def freeze_version_with_baseline(self, version_id, operator_name, occurred_at):
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT * FROM implementation_versions WHERE id=%s FOR UPDATE", (version_id,))
            version = cur.fetchone()
            if not version:
                raise ValueError("版本不存在。")
            if version["is_frozen"]:
                self.conn.rollback()
                return None
            # Serialize snapshot reads with status, effort, budget and artifact writes on the same requirements.
            cur.execute("SELECT id FROM requirements WHERE version_id=%s AND is_deleted=0 FOR UPDATE", (version_id,))
            cur.fetchall()
            cur.execute("""SELECT COUNT(*) requirement_count, COALESCE(SUM(allocated_budget),0) allocated_budget,
                                  COALESCE(SUM(actual_cost),0) actual_cost,
                                  COALESCE(SUM(estimated_hours),0) estimated_hours,
                                  COALESCE(SUM(actual_hours),0) actual_hours
                           FROM requirements WHERE version_id=%s AND is_deleted=0""", (version_id,))
            summary = cur.fetchone()
            cur.execute("SELECT COALESCE(MAX(snapshot_no),0) snapshot_no FROM version_baselines WHERE version_id=%s", (version_id,))
            snapshot_no = int(cur.fetchone()["snapshot_no"] or 0) + 1
            cur.execute("""INSERT INTO version_baselines(version_id, snapshot_no, version_budget, requirement_count,
                                                          allocated_budget, actual_cost, estimated_hours, actual_hours,
                                                          created_by, created_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (version_id, snapshot_no, version["version_budget"], summary["requirement_count"],
                         summary["allocated_budget"], summary["actual_cost"], summary["estimated_hours"],
                         summary["actual_hours"], operator_name, occurred_at))
            baseline_id = cur.lastrowid
            cur.execute("""INSERT INTO version_baseline_requirements(
                               baseline_id, requirement_id, requirement_code, requirement_name, requirement_description,
                               business_key, source_role, proposer_name, owner_name, requirement_type, tags, status,
                               priority, estimated_budget, allocated_budget, actual_cost, estimated_hours, actual_hours,
                               planned_finish_date, actual_finish_date, remark, parent_requirement_id,
                               parent_requirement_code, created_at, updated_at)
                           SELECT %s, r.id, r.requirement_code, r.requirement_name, r.requirement_description,
                                  r.business_key, r.source_role, r.proposer_name, r.owner_name, r.requirement_type,
                                  r.tags, r.status, r.priority, r.estimated_budget, r.allocated_budget, r.actual_cost,
                                  r.estimated_hours, r.actual_hours, r.planned_finish_date, r.actual_finish_date,
                                  r.remark, r.parent_requirement_id, p.requirement_code, r.created_at, r.updated_at
                           FROM requirements r LEFT JOIN requirements p ON p.id=r.parent_requirement_id
                           WHERE r.version_id=%s AND r.is_deleted=0""", (baseline_id, version_id))
            cur.execute("""INSERT INTO version_baseline_artifacts(
                               baseline_id, artifact_id, artifact_code, artifact_name, artifact_type, file_path,
                               related_object_type, related_object_id, version_no, description, visibility,
                               uploaded_by, uploaded_at)
                           SELECT %s, a.id, a.artifact_code, a.artifact_name, a.artifact_type, a.file_path,
                                  a.related_object_type, a.related_object_id, a.version_no, a.description,
                                  a.visibility, a.uploaded_by, a.uploaded_at
                           FROM artifacts a
                           WHERE a.approval_status='approved' AND (
                               (a.related_object_type='版本' AND a.related_object_id=%s) OR
                               (a.related_object_type='需求' AND a.related_object_id IN
                                   (SELECT id FROM requirements WHERE version_id=%s AND is_deleted=0))
                           )""", (baseline_id, version_id, version_id))
            cur.execute("""UPDATE implementation_versions
                           SET is_frozen=1, status='published', updated_at=%s
                           WHERE id=%s AND is_frozen=0""", (occurred_at, version_id))
            if cur.rowcount != 1:
                raise ValueError("版本冻结状态已被其他操作更新，请刷新后重试。")
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'success')""",
                        (operator_name, occurred_at, "implementation_version", version_id, "publish", "",
                         f"baseline:{baseline_id}", f"发布版本并生成基线 #{snapshot_no}", event_id))
            self.conn.commit()
            audit_event(operator_name, "implementation_version", version_id, "publish", f"发布版本并生成基线 #{snapshot_no}", event_id=event_id)
            return {"baseline_id": baseline_id, "snapshot_no": snapshot_no}
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("freeze_version", exc)
            raise
        finally:
            self.end_transaction(cur)

    def review_change_request(self, change_id, status, operator_name, occurred_at):
        if status not in {"approved", "rejected"}:
            raise ValueError("无效的审批状态。")
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT * FROM change_requests WHERE id=%s FOR UPDATE", (change_id,))
            change = cur.fetchone()
            if not change:
                raise ValueError("变更申请不存在。")
            if change["approval_status"] != "pending":
                raise ValueError("该变更申请已被其他操作处理，请刷新后重试。")
            cur.execute("SELECT * FROM change_request_payloads WHERE change_request_id=%s", (change_id,))
            payload = cur.fetchone()
            if not payload:
                raise ValueError("变更申请缺少变更载荷，不能审批。")
            try:
                proposed = json.loads(payload["proposed_value"] or "{}")
            except (TypeError, ValueError) as exc:
                raise ValueError("变更申请载荷不是有效 JSON。") from exc
            if not isinstance(proposed, dict):
                raise ValueError("变更申请载荷必须是对象结构。")
            change_type = payload["change_type"]
            if change_type == "artifact_add":
                artifact_id = int(proposed.get("artifact_id") or 0)
                cur.execute("""SELECT id FROM artifacts
                               WHERE id=%s AND change_request_id=%s AND approval_status='pending' FOR UPDATE""",
                            (artifact_id, change_id))
                if not cur.fetchone():
                    raise ValueError("待审批成果物不存在或状态不一致。")
                cur.execute("UPDATE artifacts SET approval_status=%s WHERE id=%s",
                            ("approved" if status == "approved" else "rejected", artifact_id))
            elif change_type not in {"update", "delete"}:
                raise ValueError("变更申请内容类型无效。")
            elif status == "approved":
                if not change["requirement_id"]:
                    raise ValueError("需求变更申请缺少关联需求。")
                cur.execute("SELECT version_id, project_id FROM requirements WHERE id=%s", (change["requirement_id"],))
                expected_scope = cur.fetchone()
                if not expected_scope:
                    raise ValueError("关联需求不存在或已删除，无法应用变更。")
                if expected_scope["version_id"]:
                    cur.execute("SELECT id FROM implementation_versions WHERE id=%s FOR UPDATE",
                                (expected_scope["version_id"],))
                    if not cur.fetchone():
                        raise ValueError("需求所属版本不存在。")
                else:
                    cur.execute("SELECT id FROM planning_projects WHERE id=%s FOR UPDATE",
                                (expected_scope["project_id"],))
                    if not cur.fetchone():
                        raise ValueError("需求所属项目不存在。")
                cur.execute("SELECT * FROM requirements WHERE id=%s FOR UPDATE", (change["requirement_id"],))
                requirement = cur.fetchone()
                if not requirement or requirement["is_deleted"]:
                    raise ValueError("关联需求不存在或已删除，无法应用变更。")
                if (requirement["version_id"] != expected_scope["version_id"]
                        or requirement["project_id"] != expected_scope["project_id"]):
                    raise ValueError("需求所属版本已被其他操作更新，请刷新后重试。")
                if change_type == "delete":
                    cur.execute("UPDATE requirements SET is_deleted=1, updated_at=%s WHERE id=%s",
                                (occurred_at, change["requirement_id"]))
                else:
                    required_values = [proposed.get("requirement_name"), proposed.get("requirement_description"), proposed.get("source_role")]
                    if not all(str(value or "").strip() for value in required_values):
                        raise ValueError("变更内容缺少需求名称、描述或来源角色。")
                    estimated_budget = float(proposed.get("estimated_budget", 0) or 0)
                    if not math.isfinite(estimated_budget) or estimated_budget < 0:
                        raise ValueError("变更后的预估预算必须是大于等于 0 的有限数值。")
                    estimated_hours = float(proposed.get("estimated_hours", requirement["estimated_hours"] or 0) or 0)
                    if not math.isfinite(estimated_hours) or estimated_hours < 0:
                        raise ValueError("变更后的预估工时必须是大于等于 0 的有限数值。")
                    business_key = requirement_business_key(proposed)
                    cur.execute("""SELECT id, business_key, requirement_name FROM requirements
                                   WHERE project_id=%s AND version_id <=> %s AND id<>%s AND is_deleted=0 FOR UPDATE""",
                                (requirement["project_id"], requirement["version_id"], requirement["id"]))
                    if any(requirement_business_key(candidate) == business_key for candidate in cur.fetchall()):
                        raise ValueError("同一版本内业务需求标识不能重复。")
                    parent_id = proposed.get("parent_requirement_id", requirement["parent_requirement_id"])
                    parent_id = int(parent_id) if parent_id not in (None, "") else None
                    if parent_id:
                        cur.execute("SELECT id FROM requirements WHERE id=%s AND project_id=%s AND is_deleted=0 FOR UPDATE",
                                    (parent_id, requirement["project_id"]))
                        if not cur.fetchone() or parent_id == requirement["id"]:
                            raise ValueError("关联原需求必须是同一项目内的其他有效需求。")
                        cur.execute("""WITH RECURSIVE descendants(id) AS (
                                           SELECT id FROM requirements WHERE parent_requirement_id=%s AND is_deleted=0
                                           UNION
                                           SELECT r.id FROM requirements r JOIN descendants d ON r.parent_requirement_id=d.id
                                           WHERE r.is_deleted=0
                                       ) SELECT 1 probe FROM descendants WHERE id=%s LIMIT 1""",
                                    (requirement["id"], parent_id))
                        if cur.fetchone():
                            raise ValueError("关联原需求会形成循环关系。")
                    planned_finish = normalize_date(proposed.get("planned_finish_date", ""), "预计完成时间")
                    cur.execute("""UPDATE requirements SET requirement_name=%s, requirement_description=%s,
                                      business_key=%s, source_role=%s, proposer_name=%s, owner_name=%s, requirement_type=%s,
                                      tags=%s, priority=%s, estimated_budget=%s, estimated_hours=%s, planned_finish_date=%s,
                                      remark=%s, parent_requirement_id=%s, status='变更中', updated_at=%s WHERE id=%s""",
                                 (proposed.get("requirement_name", ""), proposed.get("requirement_description", ""),
                                  business_key, proposed.get("source_role", ""), proposed.get("proposer_name", ""), proposed.get("owner_name", ""),
                                  proposed.get("requirement_type", ""), proposed.get("tags", ""), proposed.get("priority", "P1"),
                                  estimated_budget, estimated_hours, planned_finish, proposed.get("remark", ""), parent_id,
                                  occurred_at, change["requirement_id"]))
                    cur.execute("""INSERT INTO requirement_status_history(requirement_id, from_status, to_status,
                                                                           operator_name, transition_note, changed_at)
                                   VALUES(%s,%s,'变更中',%s,%s,%s)""",
                                (change["requirement_id"], requirement["status"], operator_name,
                                 f"变更申请 #{change_id} 审批通过", occurred_at))
            cur.execute("""UPDATE change_requests SET approval_status=%s, approved_by=%s, approved_at=%s
                           WHERE id=%s AND approval_status='pending'""", (status, operator_name, occurred_at, change_id))
            if cur.rowcount != 1:
                raise ValueError("该变更申请已被其他操作处理，请刷新后重试。")
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'success')""",
                        (operator_name, occurred_at, "change_request", change_id, status,
                         str(change), status, "变更申请通过" if status == "approved" else "变更申请驳回", event_id))
            self.conn.commit()
            audit_event(operator_name, "change_request", change_id, status, "变更申请通过" if status == "approved" else "变更申请驳回", event_id=event_id)
            return change
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("review_change_request", exc)
            raise
        finally:
            self.end_transaction(cur)

    def claim_requirement(self, requirement_id, operator_name, occurred_at):
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT status, owner_name FROM requirements WHERE id=%s AND is_deleted=0 FOR UPDATE", (requirement_id,))
            requirement = cur.fetchone()
            if not requirement:
                raise ValueError("需求不存在或已删除。")
            if requirement["status"] not in {"规划中", "已排期", "研发中", "退回修改"}:
                raise ValueError("当前状态不允许领取研发任务。")
            if requirement["owner_name"] and requirement["owner_name"] != operator_name:
                raise ValueError(f"该任务已由 {requirement['owner_name']} 负责。")
            new_status = "研发中" if requirement["status"] == "已排期" else requirement["status"]
            cur.execute("UPDATE requirements SET owner_name=%s, status=%s, updated_at=%s WHERE id=%s",
                        (operator_name, new_status, occurred_at, requirement_id))
            if new_status != requirement["status"]:
                cur.execute("""INSERT INTO requirement_status_history(requirement_id, from_status, to_status,
                                                                        operator_name, transition_note, changed_at)
                               VALUES(%s,%s,%s,%s,%s,%s)""",
                            (requirement_id, requirement["status"], new_status, operator_name, "研发人员领取任务", occurred_at))
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                        operation_type, before_value, after_value, description,
                                                        event_id, result)
                           VALUES(%s,%s,'requirement',%s,'claim',%s,%s,'领取研发任务',%s,'success')""",
                        (operator_name, occurred_at, requirement_id, requirement["owner_name"] or "", operator_name, event_id))
            self.conn.commit()
            audit_event(operator_name, "requirement", requirement_id, "claim", "领取研发任务", event_id=event_id)
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("claim_requirement", exc)
            raise
        finally:
            self.end_transaction(cur)

    def record_effort(self, requirement_id, contributor_name, hours, work_date, description, occurred_at):
        hours = float(hours)
        if not math.isfinite(hours) or hours <= 0:
            raise ValueError("工时必须是大于 0 的有限数值。")
        work_date = normalize_date(work_date, "工作日期")
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT id FROM requirements WHERE id=%s AND is_deleted=0 FOR UPDATE", (requirement_id,))
            if not cur.fetchone():
                raise ValueError("需求不存在或已删除。")
            cur.execute("""INSERT INTO task_effort_entries(requirement_id, contributor_name, hours, work_date, description, created_at)
                           VALUES(%s,%s,%s,%s,%s,%s)""", (requirement_id, contributor_name, hours, work_date, description, occurred_at))
            cur.execute("UPDATE requirements SET actual_hours=COALESCE(actual_hours,0)+%s, updated_at=%s WHERE id=%s",
                        (hours, occurred_at, requirement_id))
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                        operation_type, before_value, after_value, description,
                                                        event_id, result)
                           VALUES(%s,%s,'requirement',%s,'record_effort','',%s,%s,%s,'success')""",
                        (contributor_name, occurred_at, requirement_id, str(hours), f"登记工时：{work_date}", event_id))
            self.conn.commit()
            audit_event(contributor_name, "requirement", requirement_id, "record_effort", f"登记工时 {hours}", event_id=event_id)
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("record_effort", exc)
            raise
        finally:
            self.end_transaction(cur)

    def transition_requirement_status(self, requirement_id, from_status, to_status, note, operator_name, occurred_at):
        if to_status not in STATUS_TRANSITIONS.get(from_status, []):
            raise ValueError(f"不允许从“{from_status}”直接流转到“{to_status}”。")
        if not str(note or "").strip():
            raise ValueError("状态流转说明不能为空。")
        cur = self.begin_transaction()
        try:
            cur.execute("SELECT status FROM requirements WHERE id=%s AND is_deleted=0 FOR UPDATE", (requirement_id,))
            requirement = cur.fetchone()
            if not requirement or requirement["status"] != from_status:
                self.conn.rollback()
                return False
            cur.execute("""UPDATE requirements SET status=%s, updated_at=%s,
                           actual_finish_date=CASE WHEN %s='已关闭' THEN %s WHEN %s!='已关闭' THEN NULL ELSE actual_finish_date END
                           WHERE id=%s AND status=%s""",
                        (to_status, occurred_at, to_status, occurred_at[:10], to_status, requirement_id, from_status))
            if cur.rowcount != 1:
                self.conn.rollback()
                return False
            cur.execute("""INSERT INTO requirement_status_history(requirement_id, from_status, to_status,
                                                                   operator_name, transition_note, changed_at)
                           VALUES(%s,%s,%s,%s,%s,%s)""",
                        (requirement_id, from_status, to_status, operator_name, note, occurred_at))
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'requirement',%s,'status_change',%s,%s,%s,%s,'success')""",
                        (operator_name, occurred_at, requirement_id, from_status, to_status, f"需求状态流转：{note}", event_id))
            self.conn.commit()
            audit_event(operator_name, "requirement", requirement_id, "status_change", f"{from_status} -> {to_status}", event_id=event_id)
            return True
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("transition_requirement_status", exc)
            raise
        finally:
            self.end_transaction(cur)

    def init_schema(self):
        statements = [
            """
            CREATE TABLE IF NOT EXISTS planning_projects (
                id INT AUTO_INCREMENT PRIMARY KEY,
                project_code VARCHAR(80) NOT NULL UNIQUE,
                project_name VARCHAR(255) NOT NULL,
                customer_name VARCHAR(255),
                project_background TEXT,
                total_budget DECIMAL(14,2) DEFAULT 0,
                status VARCHAR(40) DEFAULT 'active',
                current_stage VARCHAR(40) DEFAULT '宏观规划',
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS annual_plans (
                id INT AUTO_INCREMENT PRIMARY KEY,
                project_id INT NOT NULL,
                plan_year INT NOT NULL,
                plan_name VARCHAR(255) NOT NULL,
                annual_budget DECIMAL(14,2) DEFAULT 0,
                business_pain_points TEXT,
                plan_description TEXT,
                status VARCHAR(40) DEFAULT 'draft',
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL,
                INDEX idx_annual_project(project_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS implementation_versions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                project_id INT NOT NULL,
                annual_plan_id INT NOT NULL,
                version_code VARCHAR(80) NOT NULL,
                version_name VARCHAR(255) NOT NULL,
                version_goal TEXT,
                version_scope TEXT,
                version_budget DECIMAL(14,2) DEFAULT 0,
                status VARCHAR(40) DEFAULT 'planning',
                is_frozen TINYINT DEFAULT 0,
                planned_start_date VARCHAR(32),
                planned_end_date VARCHAR(32),
                actual_start_date VARCHAR(32),
                actual_end_date VARCHAR(32),
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL,
                INDEX idx_version_project_plan(project_id, annual_plan_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS requirements (
                id INT AUTO_INCREMENT PRIMARY KEY,
                requirement_code VARCHAR(80) NOT NULL UNIQUE,
                requirement_name VARCHAR(255) NOT NULL,
                requirement_description TEXT NOT NULL,
                business_key VARCHAR(255),
                source_role VARCHAR(40) NOT NULL,
                proposer_name VARCHAR(120),
                owner_name VARCHAR(120),
                project_id INT NOT NULL,
                annual_plan_id INT,
                version_id INT,
                requirement_type VARCHAR(80),
                tags TEXT,
                priority VARCHAR(20) DEFAULT 'P1',
                status VARCHAR(40) DEFAULT '草稿',
                estimated_budget DECIMAL(14,2) DEFAULT 0,
                allocated_budget DECIMAL(14,2) DEFAULT 0,
                actual_cost DECIMAL(14,2) DEFAULT 0,
                planned_finish_date VARCHAR(32),
                actual_finish_date VARCHAR(32),
                remark TEXT,
                parent_requirement_id INT,
                is_deleted TINYINT DEFAULT 0,
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL,
                INDEX idx_req_project(project_id),
                INDEX idx_req_version(version_id),
                INDEX idx_req_status(status),
                INDEX idx_req_business_key(business_key),
                INDEX idx_req_parent(parent_requirement_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS budget_flows (
                id INT AUTO_INCREMENT PRIMARY KEY,
                flow_code VARCHAR(80) NOT NULL UNIQUE,
                project_id INT NOT NULL,
                annual_plan_id INT,
                version_id INT,
                requirement_id INT,
                flow_type VARCHAR(80) NOT NULL,
                amount DECIMAL(14,2) NOT NULL,
                description TEXT,
                operator_name VARCHAR(120),
                occurred_at VARCHAR(32) NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                INDEX idx_budget_project(project_id),
                INDEX idx_budget_version(version_id),
                INDEX idx_budget_requirement(requirement_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                artifact_code VARCHAR(80) NOT NULL UNIQUE,
                artifact_name VARCHAR(255) NOT NULL,
                artifact_type VARCHAR(80) NOT NULL,
                file_path TEXT NOT NULL,
                file_ext VARCHAR(30),
                file_size BIGINT,
                related_object_type VARCHAR(40) NOT NULL,
                related_object_id INT NOT NULL,
                version_no VARCHAR(80),
                description TEXT,
                visibility VARCHAR(40) DEFAULT '内部',
                approval_status VARCHAR(40) DEFAULT 'approved',
                change_request_id INT,
                uploaded_by VARCHAR(120),
                uploaded_at VARCHAR(32) NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                INDEX idx_artifact_related(related_object_type, related_object_id),
                INDEX idx_artifact_change(change_request_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(120) NOT NULL UNIQUE,
                display_name VARCHAR(120) NOT NULL,
                password_hash VARCHAR(255),
                role_name VARCHAR(80) NOT NULL,
                is_active TINYINT DEFAULT 1,
                session_token VARCHAR(64),
                session_started_at VARCHAR(32),
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS operation_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                operator_name VARCHAR(120),
                operation_time VARCHAR(32) NOT NULL,
                object_type VARCHAR(80) NOT NULL,
                object_id INT,
                operation_type VARCHAR(80) NOT NULL,
                before_value MEDIUMTEXT,
                after_value MEDIUMTEXT,
                description TEXT,
                event_id VARCHAR(40),
                result VARCHAR(20) DEFAULT 'success',
                INDEX idx_log_time(operation_time),
                UNIQUE KEY uk_log_event_id(event_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS change_requests (
                id INT AUTO_INCREMENT PRIMARY KEY,
                version_id INT NOT NULL,
                requirement_id INT,
                change_title VARCHAR(255) NOT NULL,
                change_reason TEXT,
                impact_scope TEXT,
                approval_status VARCHAR(40) DEFAULT 'pending',
                requested_by VARCHAR(120),
                requested_at VARCHAR(32) NOT NULL,
                approved_by VARCHAR(120),
                approved_at VARCHAR(32),
                pending_requirement_id INT GENERATED ALWAYS AS (
                    CASE WHEN approval_status='pending' THEN requirement_id ELSE NULL END
                ) STORED,
                INDEX idx_change_status(approval_status),
                UNIQUE KEY uk_change_pending_requirement(pending_requirement_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS user_project_access (
                user_id INT NOT NULL,
                project_id INT NOT NULL,
                created_by VARCHAR(120),
                created_at VARCHAR(32) NOT NULL,
                PRIMARY KEY(user_id, project_id),
                INDEX idx_access_project(project_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS requirement_status_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                requirement_id INT NOT NULL,
                from_status VARCHAR(40),
                to_status VARCHAR(40) NOT NULL,
                operator_name VARCHAR(120),
                transition_note TEXT,
                changed_at VARCHAR(32) NOT NULL,
                INDEX idx_status_history_requirement(requirement_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS version_baselines (
                id INT AUTO_INCREMENT PRIMARY KEY,
                version_id INT NOT NULL,
                snapshot_no INT NOT NULL,
                version_budget DECIMAL(14,2) DEFAULT 0,
                requirement_count INT DEFAULT 0,
                allocated_budget DECIMAL(14,2) DEFAULT 0,
                actual_cost DECIMAL(14,2) DEFAULT 0,
                estimated_hours DECIMAL(10,2) DEFAULT 0,
                actual_hours DECIMAL(10,2) DEFAULT 0,
                created_by VARCHAR(120),
                created_at VARCHAR(32) NOT NULL,
                UNIQUE KEY uk_baseline_version_no(version_id, snapshot_no)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS version_baseline_requirements (
                id INT AUTO_INCREMENT PRIMARY KEY,
                baseline_id INT NOT NULL,
                requirement_id INT NOT NULL,
                requirement_code VARCHAR(80) NOT NULL,
                requirement_name VARCHAR(255) NOT NULL,
                requirement_description TEXT,
                business_key VARCHAR(255),
                source_role VARCHAR(40),
                proposer_name VARCHAR(120),
                owner_name VARCHAR(120),
                requirement_type VARCHAR(80),
                tags TEXT,
                status VARCHAR(40),
                priority VARCHAR(20),
                estimated_budget DECIMAL(14,2) DEFAULT 0,
                allocated_budget DECIMAL(14,2) DEFAULT 0,
                actual_cost DECIMAL(14,2) DEFAULT 0,
                estimated_hours DECIMAL(10,2) DEFAULT 0,
                actual_hours DECIMAL(10,2) DEFAULT 0,
                planned_finish_date VARCHAR(32),
                actual_finish_date VARCHAR(32),
                remark TEXT,
                parent_requirement_id INT,
                parent_requirement_code VARCHAR(80),
                created_at VARCHAR(32),
                updated_at VARCHAR(32),
                INDEX idx_baseline_requirement(baseline_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS version_baseline_artifacts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                baseline_id INT NOT NULL,
                artifact_id INT NOT NULL,
                artifact_code VARCHAR(80) NOT NULL,
                artifact_name VARCHAR(255) NOT NULL,
                artifact_type VARCHAR(80) NOT NULL,
                file_path TEXT NOT NULL,
                related_object_type VARCHAR(40) NOT NULL,
                related_object_id INT NOT NULL,
                version_no VARCHAR(80),
                description TEXT,
                visibility VARCHAR(40),
                uploaded_by VARCHAR(120),
                uploaded_at VARCHAR(32),
                INDEX idx_baseline_artifacts(baseline_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS change_request_payloads (
                change_request_id INT PRIMARY KEY,
                change_type VARCHAR(40) NOT NULL,
                proposed_value MEDIUMTEXT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS task_effort_entries (
                id INT AUTO_INCREMENT PRIMARY KEY,
                requirement_id INT NOT NULL,
                contributor_name VARCHAR(120) NOT NULL,
                hours DECIMAL(10,2) NOT NULL,
                work_date VARCHAR(32) NOT NULL,
                description TEXT,
                created_at VARCHAR(32) NOT NULL,
                INDEX idx_effort_requirement(requirement_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS tag_definitions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                tag_name VARCHAR(120) NOT NULL UNIQUE,
                is_active TINYINT DEFAULT 1,
                created_at VARCHAR(32) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS dashboard_preferences (
                subject_key VARCHAR(160) PRIMARY KEY,
                layout_json TEXT NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS funding_applications (
                id INT AUTO_INCREMENT PRIMARY KEY,
                application_code VARCHAR(80) NOT NULL UNIQUE,
                project_id INT NOT NULL,
                annual_plan_id INT NOT NULL,
                amount DECIMAL(14,2) NOT NULL,
                status VARCHAR(40) DEFAULT '草稿',
                applicant_name VARCHAR(120) NOT NULL,
                description TEXT,
                submitted_at VARCHAR(32),
                reviewed_by VARCHAR(120),
                reviewed_at VARCHAR(32),
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL,
                INDEX idx_funding_project(project_id, annual_plan_id, status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS operation_records (
                id INT AUTO_INCREMENT PRIMARY KEY,
                record_code VARCHAR(80) NOT NULL UNIQUE,
                project_id INT NOT NULL,
                version_id INT,
                requirement_id INT,
                record_type VARCHAR(40) NOT NULL,
                status VARCHAR(40) DEFAULT '待处理',
                record_date VARCHAR(32) NOT NULL,
                owner_name VARCHAR(120),
                description TEXT NOT NULL,
                result TEXT,
                created_by VARCHAR(120),
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL,
                INDEX idx_operation_project(project_id, version_id, requirement_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
        ]
        cur = self.conn.cursor()
        lock_name = f"crm_schema_{self.config['database']}"[:64]
        lock_acquired = False
        try:
            cur.execute("SELECT GET_LOCK(%s, 30)", (lock_name,))
            lock_acquired = cur.fetchone()[0] == 1
            if not lock_acquired:
                raise RuntimeError("等待数据库结构升级锁超时，请稍后重试。")
            for statement in statements:
                cur.execute(statement)
            cur.execute("SHOW COLUMNS FROM users")
            user_columns = {row[0] for row in cur.fetchall()}
            for column, definition in {
                "session_token": "VARCHAR(64) NULL",
                "session_started_at": "VARCHAR(32) NULL",
            }.items():
                if column not in user_columns:
                    cur.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
            cur.execute("SHOW COLUMNS FROM planning_projects")
            project_columns = {row[0] for row in cur.fetchall()}
            if "current_stage" not in project_columns:
                cur.execute("ALTER TABLE planning_projects ADD COLUMN current_stage VARCHAR(40) DEFAULT '宏观规划'")
            cur.execute("""UPDATE planning_projects SET current_stage='宏观规划'
                           WHERE current_stage IS NULL OR TRIM(current_stage)='' OR current_stage NOT IN
                           ('宏观规划','规划细化','建设落地','招投标','项目交付验收','运维运营')""")
            cur.execute("SHOW COLUMNS FROM requirements")
            requirement_columns = {row[0] for row in cur.fetchall()}
            for column, definition in {
                "business_key": "VARCHAR(255) NULL",
                "estimated_hours": "DECIMAL(10,2) DEFAULT 0",
                "actual_hours": "DECIMAL(10,2) DEFAULT 0",
                "parent_requirement_id": "INT NULL",
            }.items():
                if column not in requirement_columns:
                    cur.execute(f"ALTER TABLE requirements ADD COLUMN {column} {definition}")
            cur.execute("SHOW INDEX FROM requirements WHERE Key_name='idx_req_parent'")
            if not cur.fetchone():
                cur.execute("CREATE INDEX idx_req_parent ON requirements(parent_requirement_id)")

            cur.execute("SHOW COLUMNS FROM artifacts")
            artifact_columns = {row[0] for row in cur.fetchall()}
            for column, definition in {
                "visibility": "VARCHAR(40) DEFAULT '内部'",
                "approval_status": "VARCHAR(40) DEFAULT 'approved'",
                "change_request_id": "INT NULL",
            }.items():
                if column not in artifact_columns:
                    cur.execute(f"ALTER TABLE artifacts ADD COLUMN {column} {definition}")
            cur.execute("UPDATE artifacts SET visibility='内部' WHERE visibility IS NULL OR TRIM(visibility)=''")
            cur.execute("UPDATE artifacts SET approval_status='approved' WHERE approval_status IS NULL OR TRIM(approval_status)=''")
            cur.execute("SHOW INDEX FROM artifacts WHERE Key_name='idx_artifact_change'")
            if not cur.fetchone():
                cur.execute("CREATE INDEX idx_artifact_change ON artifacts(change_request_id)")

            cur.execute("SHOW COLUMNS FROM version_baselines")
            baseline_columns = {row[0] for row in cur.fetchall()}
            for column in ["estimated_hours", "actual_hours"]:
                if column not in baseline_columns:
                    cur.execute(f"ALTER TABLE version_baselines ADD COLUMN {column} DECIMAL(10,2) DEFAULT 0")
            cur.execute("SHOW COLUMNS FROM version_baseline_requirements")
            baseline_requirement_columns = {row[0] for row in cur.fetchall()}
            for column, definition in {
                "requirement_description": "TEXT", "business_key": "VARCHAR(255)",
                "source_role": "VARCHAR(40)", "proposer_name": "VARCHAR(120)",
                "owner_name": "VARCHAR(120)", "requirement_type": "VARCHAR(80)", "tags": "TEXT",
                "estimated_budget": "DECIMAL(14,2) DEFAULT 0", "estimated_hours": "DECIMAL(10,2) DEFAULT 0",
                "actual_hours": "DECIMAL(10,2) DEFAULT 0", "planned_finish_date": "VARCHAR(32)",
                "actual_finish_date": "VARCHAR(32)", "remark": "TEXT", "parent_requirement_id": "INT",
                "parent_requirement_code": "VARCHAR(80)", "created_at": "VARCHAR(32)",
            }.items():
                if column not in baseline_requirement_columns:
                    cur.execute(f"ALTER TABLE version_baseline_requirements ADD COLUMN {column} {definition}")

            cur.execute("""UPDATE change_requests c
                           JOIN (
                               SELECT requirement_id, MIN(id) keep_id
                               FROM change_requests
                               WHERE approval_status='pending' AND requirement_id IS NOT NULL
                               GROUP BY requirement_id HAVING COUNT(*)>1
                           ) d ON d.requirement_id=c.requirement_id AND c.id<>d.keep_id
                           SET c.approval_status='rejected', c.approved_by='系统迁移',
                               c.approved_at=COALESCE(c.approved_at,c.requested_at)
                           WHERE c.approval_status='pending'""")
            cur.execute("SHOW COLUMNS FROM change_requests")
            change_columns = {row[0] for row in cur.fetchall()}
            if "pending_requirement_id" not in change_columns:
                cur.execute("""ALTER TABLE change_requests ADD COLUMN pending_requirement_id INT
                               GENERATED ALWAYS AS (CASE WHEN approval_status='pending' THEN requirement_id ELSE NULL END) STORED""")
            cur.execute("SHOW INDEX FROM change_requests WHERE Key_name='uk_change_pending_requirement'")
            if not cur.fetchone():
                cur.execute("CREATE UNIQUE INDEX uk_change_pending_requirement ON change_requests(pending_requirement_id)")
            cur.execute("START TRANSACTION")
            try:
                cur.execute("""SELECT id, project_id, version_id, requirement_name, business_key, is_deleted
                               FROM requirements ORDER BY id FOR UPDATE""")
                requirement_rows = [dict(zip(
                    ("id", "project_id", "version_id", "requirement_name", "business_key", "is_deleted"), row
                )) for row in cur.fetchall()]
                live_rows = [row for row in requirement_rows if not row["is_deleted"]]
                invalid_ids = [row["id"] for row in live_rows if not requirement_business_key(row)]
                if invalid_ids:
                    raise RuntimeError(f"旧数据存在空业务需求标识，迁移已中止：需求 {invalid_ids[:8]}")
                conflicts = requirement_business_key_conflicts(live_rows)
                if conflicts:
                    pairs = [f"{left}/{right}" for left, right, _key, _project, _version in conflicts[:8]]
                    raise RuntimeError(
                        "旧数据规范化后存在同版本业务标识冲突，迁移已中止；请先处理需求：" + ", ".join(pairs)
                    )
                updates = [(requirement_business_key(row), row["id"]) for row in requirement_rows
                           if str(row.get("business_key") or "") != requirement_business_key(row)]
                if updates:
                    cur.executemany("UPDATE requirements SET business_key=%s WHERE id=%s", updates)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            cur.execute("SHOW COLUMNS FROM operation_logs LIKE 'event_id'")
            event_column = cur.fetchone()
            if not event_column:
                cur.execute("ALTER TABLE operation_logs ADD COLUMN event_id VARCHAR(40)")
            elif str(event_column[1]).lower() != "varchar(40)":
                cur.execute("ALTER TABLE operation_logs MODIFY COLUMN event_id VARCHAR(40) NULL")
            cur.execute("SHOW COLUMNS FROM operation_logs LIKE 'result'")
            result_column = cur.fetchone()
            if not result_column:
                cur.execute("ALTER TABLE operation_logs ADD COLUMN result VARCHAR(20) DEFAULT 'legacy'")
            elif str(result_column[1]).lower() != "varchar(20)" or str(result_column[4] or "").lower() != "success":
                cur.execute("ALTER TABLE operation_logs MODIFY COLUMN result VARCHAR(20) DEFAULT 'success'")
            cur.execute("UPDATE operation_logs SET result='legacy' WHERE event_id IS NULL")
            if not result_column:
                cur.execute("ALTER TABLE operation_logs MODIFY COLUMN result VARCHAR(20) DEFAULT 'success'")
            cur.execute("SHOW INDEX FROM operation_logs WHERE Key_name='uk_log_event_id'")
            event_index = cur.fetchone()
            if event_index and int(event_index[1]) != 0:
                cur.execute("ALTER TABLE operation_logs DROP INDEX uk_log_event_id")
                event_index = None
            if not event_index:
                cur.execute("ALTER TABLE operation_logs ADD UNIQUE INDEX uk_log_event_id(event_id)")
            self.conn.commit()
        finally:
            if lock_acquired:
                try:
                    cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                    cur.fetchone()
                except Exception:
                    LOGGER.exception("schema_lock_release_failed lock=%s", lock_name)
            cur.close()

    def seed_defaults(self):
        initialized = []
        if not self.one("SELECT id FROM users WHERE username='admin'"):
            self.execute(
                "INSERT INTO users(username, display_name, password_hash, role_name, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                ("admin", "默认管理员", "", "管理员", now_text(), now_text()),
            )
            initialized.append("默认管理员")
        if not self.one("SELECT id FROM tag_definitions LIMIT 1"):
            for tag_name in ["业务痛点", "功能优化", "运维 Bug", "招投标要求", "验收整改", "客户新增", "版本必做", "待确认"]:
                self.execute("INSERT INTO tag_definitions(tag_name, created_at) VALUES(?,?)", (tag_name, now_text()))
            initialized.append("标签字典")
        if self.config["seed_demo_data"] and not self.one("SELECT id FROM planning_projects"):
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
            self.execute(
                "INSERT INTO implementation_versions(project_id, annual_plan_id, version_code, version_name, version_goal, version_budget, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (project_id, plan_id, "V1.1", "示例优化版本", "完善资金可视化并优化统一需求池。", 220000, t, t),
            )
            v1_version_id = self.one(
                "SELECT id FROM implementation_versions WHERE project_id=? AND version_code='V1.0'",
                (project_id,),
            )["id"]
            v2_version_id = self.one(
                "SELECT id FROM implementation_versions WHERE project_id=? AND version_code='V1.1'",
                (project_id,),
            )["id"]
            self.execute(
                """INSERT INTO requirements(requirement_code, requirement_name, requirement_description, business_key, source_role, proposer_name, owner_name,
                   project_id, annual_plan_id, version_id, requirement_type, tags, priority, status, estimated_budget, allocated_budget, actual_cost,
                   estimated_hours, actual_hours, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("REQ-DEMO-001", "建立统一需求池", "将客户、销售、研发、运营等来源的需求统一登记并跟踪状态。", "统一需求池", "咨询负责人", "咨询负责人", "默认管理员",
                 project_id, plan_id, v1_version_id, "功能优化", "版本必做,待确认", "P0", "规划中", 80000, 60000, 12000, 80, 12, t, t),
            )
            self.execute(
                """INSERT INTO requirements(requirement_code, requirement_name, requirement_description, business_key, source_role, proposer_name, owner_name,
                   project_id, annual_plan_id, version_id, requirement_type, tags, priority, status, estimated_budget, allocated_budget, actual_cost,
                   estimated_hours, actual_hours, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("REQ-DEMO-101", "建立统一需求池", "补充分角色视图与快速检索，统一跟踪全部来源需求。", "统一需求池", "咨询负责人", "咨询负责人", "默认管理员",
                 project_id, plan_id, v2_version_id, "功能优化", "版本必做,客户新增", "P0", "研发中", 90000, 70000, 18000, 96, 28, t, t),
            )
            self.execute(
                """INSERT INTO requirements(requirement_code, requirement_name, requirement_description, business_key, source_role, proposer_name, owner_name,
                   project_id, annual_plan_id, version_id, requirement_type, tags, priority, status, estimated_budget, allocated_budget, actual_cost,
                   estimated_hours, actual_hours, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("REQ-DEMO-102", "资金链路可视化", "展示项目、年度、版本到需求的预算分配与实际消耗。", "资金链路可视化", "销售", "示例销售", "默认管理员",
                 project_id, plan_id, v2_version_id, "业务痛点", "客户新增,版本必做", "P1", "已排期", 50000, 40000, 5000, 56, 8, t, t),
            )
            initialized.append("演示业务数据")
        if not self.one("SELECT id FROM requirement_status_history LIMIT 1"):
            for row in self.query("SELECT id, status, created_at FROM requirements WHERE is_deleted=0"):
                self.execute(
                    "INSERT INTO requirement_status_history(requirement_id, from_status, to_status, operator_name, transition_note, changed_at) VALUES(?,?,?,?,?,?)",
                    (row["id"], "", row["status"], "系统", "初始化需求状态历史", row["created_at"]),
                )
            initialized.append("需求状态历史")
        if initialized:
            self.log("系统", "system", None, "init", "", "", "初始化：" + "、".join(initialized))

    def log(self, operator, object_type, object_id, operation_type, before_value, after_value, description, result="success"):
        event_id = new_event_id()
        self.execute(
            "INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id, operation_type, before_value, after_value, description, event_id, result) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (operator, now_text(), object_type, object_id, operation_type, str(before_value or ""), str(after_value or ""), description, event_id, result),
        )
        audit_event(operator, object_type, object_id, operation_type, description, result=result, event_id=event_id)
        return event_id


class DetailDialog(tk.Toplevel):
    def __init__(self, parent, title, sections):
        super().__init__(parent)
        self.title(title)
        self.geometry("760x560")
        self.minsize(640, 420)
        colors = getattr(parent, "colors", THEME_PALETTES[DEFAULT_THEME])
        self.configure(background=colors["surface"])
        body = ttk.Frame(self, padding=16, style="Surface.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(body, wrap=tk.WORD, relief=tk.FLAT, padx=12, pady=12,
                       bg=colors["surface"], fg=colors["text"], selectbackground=colors["selection"],
                       selectforeground=colors["selection_text"])
        ybar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=ybar.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        for section_title, rows in sections:
            text.insert(tk.END, f"{section_title}\n", "h")
            for label, value in rows:
                text.insert(tk.END, f"  {label}: {value or ''}\n")
            text.insert(tk.END, "\n")
        text.tag_configure("h", font=("Microsoft YaHei UI", 12, "bold"), foreground=colors["primary"])
        text.configure(state=tk.DISABLED)
        ttk.Button(self, text="关闭", command=self.destroy).pack(pady=(0, 12))
        self.transient(parent)
        self.grab_set()
        self.wait_visibility()
        self.wait_window()


class FieldDialog(tk.Toplevel):
    def __init__(self, parent, title, fields, initial=None, required=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(True, True)
        self.result = None
        self.vars = {}
        self.required = set(required or [])
        initial = initial or {}
        colors = getattr(parent, "colors", THEME_PALETTES[DEFAULT_THEME])
        self.configure(background=colors["surface"])
        container = ttk.Frame(self, style="Surface.TFrame")
        container.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(container, bg=colors["surface"], highlightthickness=0, width=620,
                           height=min(620, max(300, len(fields) * 48 + 90)))
        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        body = ttk.Frame(canvas, padding=18, style="Surface.TFrame")
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        self.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        for row, field in enumerate(fields):
            key, label, kind, options = field
            label_text = f"{label} *" if key in self.required else label
            ttk.Label(body, text=label_text, style="Surface.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
            value = initial.get(key, "")
            if kind == "text":
                var = tk.StringVar(value=value)
                widget = ttk.Entry(body, textvariable=var, width=42)
            elif kind == "password":
                var = tk.StringVar(value=value)
                widget = ttk.Entry(body, textvariable=var, width=42, show="*")
            elif kind == "combo":
                var = tk.StringVar(value=value or (options[0] if options else ""))
                widget = ttk.Combobox(body, textvariable=var, values=options, state="readonly", width=40)
            elif kind == "readonly":
                var = tk.StringVar(value=value)
                widget = ttk.Entry(body, textvariable=var, width=42, state="readonly")
            elif kind == "multiselect":
                var = tk.StringVar(value=value)
                widget = tk.Listbox(body, selectmode=tk.MULTIPLE, exportselection=False, height=min(7, max(3, len(options or []))),
                                    bg=colors["surface"], fg=colors["text"], relief=tk.FLAT, activestyle="none",
                                    selectbackground=colors["selection"], selectforeground=colors["selection_text"],
                                    highlightthickness=1, highlightbackground=colors["line"],
                                    highlightcolor=colors["focus"])
                selected = {item.strip() for item in str(value or "").replace("，", ",").split(",") if item.strip()}
                for index, option in enumerate(options or []):
                    widget.insert(tk.END, option)
                    if option in selected:
                        widget.selection_set(index)
            else:
                var = tk.StringVar(value=value)
                widget = tk.Text(body, width=42, height=5, bg=colors["surface_alt"], fg=colors["text"],
                                 insertbackground=colors["text"], highlightthickness=1,
                                 highlightbackground=colors["line"], highlightcolor=colors["focus"], relief=tk.FLAT)
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
        self.update_idletasks()
        max_height = max(360, self.winfo_screenheight() - 140)
        if self.winfo_height() > max_height:
            self.geometry(f"{self.winfo_width()}x{max_height}")
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_width()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")
        self.wait_window()

    def save(self):
        values = {}
        for key, (var, widget, kind) in self.vars.items():
            if kind == "memo":
                values[key] = widget.get("1.0", tk.END).strip()
            elif kind == "multiselect":
                values[key] = ",".join(widget.get(index) for index in widget.curselection())
            else:
                values[key] = var.get().strip()
        missing = [key for key in self.required if not values.get(key)]
        if missing:
            messagebox.showwarning("必填项缺失", "请补充标记为 * 的必填项。", parent=self)
            return
        self.result = values
        self.destroy()


class CredentialDialog(tk.Toplevel):
    def __init__(self, parent, setup=False):
        super().__init__(parent)
        self.result = None
        self.setup = setup
        self.title("初始化管理员密码" if setup else "登录")
        self.resizable(False, False)
        colors = getattr(parent, "colors", THEME_PALETTES[DEFAULT_THEME])
        self.configure(background=colors["bg"])
        body = ttk.Frame(self, padding=28, style="Surface.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=APP_NAME, style="Surface.TLabel", font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text=f"{APP_RELEASE_LABEL} · MySQL 8.x LTS 远程协同版", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 18)
        )
        ttk.Label(body, text="用户名", style="Surface.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=7)
        self.username = tk.StringVar(value="admin" if setup else "")
        username_entry = ttk.Entry(body, textvariable=self.username, width=32, state="readonly" if setup else "normal")
        username_entry.grid(row=2, column=1, pady=7)
        ttk.Label(body, text="密码", style="Surface.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 12), pady=7)
        self.password = tk.StringVar()
        password_entry = ttk.Entry(body, textvariable=self.password, show="*", width=32)
        password_entry.grid(row=3, column=1, pady=7)
        self.confirm = tk.StringVar()
        if setup:
            ttk.Label(body, text="确认密码", style="Surface.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 12), pady=7)
            ttk.Entry(body, textvariable=self.confirm, show="*", width=32).grid(row=4, column=1, pady=7)
        button_row = 5 if setup else 4
        buttons = ttk.Frame(body, style="Surface.TFrame")
        buttons.grid(row=button_row, column=0, columnspan=2, sticky="e", pady=(16, 0))
        if not setup:
            ttk.Button(buttons, text="忘记密码", command=self.show_password_help).pack(side=tk.LEFT, padx=(0, 18))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="保存" if setup else "登录", command=self.submit, style="Primary.TButton").pack(side=tk.RIGHT)
        password_entry.bind("<Return>", lambda _event: self.submit())
        try:
            parent_is_withdrawn = parent.state() == "withdrawn"
        except tk.TclError:
            parent_is_withdrawn = False
        if not parent_is_withdrawn:
            self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.deiconify()
        self.update_idletasks()
        self.lift()
        self.wait_visibility()
        self.grab_set()
        try:
            self.focus_force()
        except tk.TclError:
            self.focus_set()
        self.wait_window()

    def submit(self):
        username = self.username.get().strip()
        password = self.password.get()
        if not username or not password:
            messagebox.showwarning("提示", "用户名和密码不能为空。", parent=self)
            return
        if self.setup:
            if len(password) < 8:
                messagebox.showwarning("提示", "管理员密码至少 8 位。", parent=self)
                return
            if password != self.confirm.get():
                messagebox.showwarning("提示", "两次输入的密码不一致。", parent=self)
                return
        self.result = (username, password)
        self.destroy()

    def show_password_help(self):
        messagebox.showinfo(
            "忘记密码",
            "系统不开放自助注册或短信、邮箱找回。\n\n"
            "普通用户：联系系统管理员，在“系统设置 > 用户与角色”中重置密码。\n"
            "管理员：由另一名管理员重置；正式环境建议至少保留两名管理员。",
            parent=self,
        )


class DashboardLayoutDialog(tk.Toplevel):
    def __init__(self, parent, loader, saver):
        super().__init__(parent)
        self.title("角色看板配置")
        self.geometry("520x470")
        self.loader = loader
        self.saver = saver
        self.role = tk.StringVar(value="客户")
        colors = getattr(parent, "colors", THEME_PALETTES[DEFAULT_THEME])
        self.configure(background=colors["surface"])
        body = ttk.Frame(self, padding=18, style="Surface.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="目标角色", style="Surface.TLabel").pack(anchor="w")
        role_box = ttk.Combobox(body, textvariable=self.role, values=ROLES, state="readonly")
        role_box.pack(fill=tk.X, pady=(6, 12))
        ttk.Label(body, text="拖动排序，双击切换显示状态", style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.listbox = tk.Listbox(
            body, exportselection=False, height=12, relief=tk.FLAT, activestyle="none",
            bg=colors["surface_alt"], fg=colors["text"], selectbackground=colors["selection"],
            selectforeground=colors["selection_text"], highlightthickness=1,
            highlightbackground=colors["line"], highlightcolor=colors["focus"],
        )
        self.listbox.pack(fill=tk.BOTH, expand=True)
        self.items = []
        self.drag_index = None
        role_box.bind("<<ComboboxSelected>>", lambda _e: self.load_role())
        self.listbox.bind("<Button-1>", self.start_drag)
        self.listbox.bind("<B1-Motion>", self.drag)
        self.listbox.bind("<Double-Button-1>", self.toggle_visible)
        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="保存配置", command=self.save).pack(side=tk.RIGHT)
        self.load_role()
        self.transient(parent)
        self.grab_set()
        self.wait_window()

    def load_role(self):
        self.items = self.loader(self.role.get())
        self.refresh()

    def refresh(self):
        labels = dict(DASHBOARD_SECTIONS)
        self.listbox.delete(0, tk.END)
        for item in self.items:
            self.listbox.insert(tk.END, f"[{'显示' if item['visible'] else '隐藏'}] {labels[item['key']]}")

    def start_drag(self, event):
        self.drag_index = self.listbox.nearest(event.y)

    def drag(self, event):
        if self.drag_index is None:
            return
        target = self.listbox.nearest(event.y)
        if target != self.drag_index and 0 <= target < len(self.items):
            self.items.insert(target, self.items.pop(self.drag_index))
            self.drag_index = target
            self.refresh()
            self.listbox.selection_set(target)

    def toggle_visible(self, event):
        index = self.listbox.nearest(event.y)
        if 0 <= index < len(self.items):
            self.items[index]["visible"] = not self.items[index]["visible"]
            self.refresh()
            self.listbox.selection_set(index)

    def save(self):
        self.saver(self.role.get(), self.items)
        self.destroy()


class App(tk.Tk):
    def __init__(self, skip_login=False):
        if skip_login and os.environ.get("CRM_MYSQL_INTEGRATION_SELFTEST") != "1":
            raise RuntimeError("skip_login 仅允许 CRM_MYSQL_INTEGRATION_SELFTEST=1 的集成测试使用。")
        super().__init__()
        self.withdraw()
        self.base_dir = app_base_dir()
        self.db = Database(self.base_dir)
        self.title(APP_NAME)
        self.geometry("1280x760")
        self.minsize(1100, 680)
        admin = self.db.one("SELECT id, username, display_name, role_name, password_hash FROM users WHERE username='admin'")
        self.current_user_id = admin["id"] if admin else None
        self.current_username = admin["username"] if admin else "admin"
        self.current_user = admin["display_name"] if admin else "默认管理员"
        self.current_role = tk.StringVar(value=admin["role_name"] if admin else "管理员")
        self.selected_project = tk.StringVar()
        self.selected_plan = tk.StringVar()
        self.selected_version = tk.StringVar()
        self.search_var = tk.StringVar()
        self.requirement_scope = tk.StringVar(value="当前版本")
        self.requirement_status_filter = tk.StringVar(value="全部状态")
        self.funding_status_filter = tk.StringVar(value="全部")
        self.operation_status_filter = tk.StringVar(value="全部")
        self.change_status_filter = tk.StringVar(value="全部")
        self.operation_log_type_filter = tk.StringVar(value="全部")
        self.operation_log_keyword = tk.StringVar()
        self.content = None
        self.current_page = "首页工作台"
        self.version_compare_ids = None
        self.session_token = None
        self.session_invalid = False
        self.session_invalid_reason = ""
        self.session_check_job = None
        self._closing = False
        self.theme_name = tk.StringVar(value=DEFAULT_THEME)
        self.configure_style()
        if skip_login:
            if not self.start_user_session(self.current_user_id, admin.get("password_hash") if admin else "",
                                           admin.get("role_name") if admin else "管理员"):
                self.db.close()
                close_logging()
                self.destroy()
                raise RuntimeError("集成测试管理员会话初始化失败。")
        elif not self.authenticate():
            LOGGER.info("login_cancelled")
            self.db.close()
            close_logging()
            self.destroy()
            raise SystemExit("登录已取消")
        self.theme_name.set(self.load_theme_preference())
        self.configure_style()
        self.build_layout()
        self.refresh_contexts()
        self.show_dashboard()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.deiconify()
        self.schedule_session_check()
        LOGGER.info("application_started version=%s variant=mysql user=%s role=%s", APP_VERSION, self.current_username, self.current_role.get())

    def report_callback_exception(self, exc_type, exc_value, exc_traceback):
        LOGGER.error("unhandled_tk_callback", exc_info=(exc_type, exc_value, exc_traceback))
        messagebox.showerror("系统错误", f"操作执行失败：{exc_value}\n详细信息已写入：{self.db.logs_dir / 'error.log'}")

    def open_logs_directory(self):
        if not self.require_action("approve", "打开运行日志"):
            return
        try:
            if os.name == "nt":
                os.startfile(str(self.db.logs_dir))
            else:
                subprocess.Popen(["xdg-open", str(self.db.logs_dir)])
        except Exception as exc:
            LOGGER.exception("open_logs_directory_failed path=%s", self.db.logs_dir)
            messagebox.showerror("打开失败", str(exc))

    def run_healthcheck(self):
        if not self.require_action("approve", "运行健康检查"):
            return
        try:
            details = self.db.healthcheck()
            self.db.log(self.current_user, "healthcheck", None, "check", "", "success", "MySQL 远程版部署健康检查通过")
            LOGGER.info("healthcheck_succeeded variant=mysql")
            messagebox.showinfo("健康检查通过", json.dumps(details, ensure_ascii=False, indent=2))
        except Exception as exc:
            LOGGER.exception("healthcheck_failed variant=mysql")
            try:
                self.db.log(self.current_user, "healthcheck", None, "check", "", "failed",
                            "MySQL 远程版部署健康检查失败", result="failed")
            except Exception:
                audit_event(self.current_user, "healthcheck", None, "check", "MySQL 远程版部署健康检查失败", result="failed")
            messagebox.showerror("健康检查失败", f"{exc}\n请查看 {self.db.logs_dir / 'error.log'}")

    def on_close(self):
        if self._closing:
            return
        self._closing = True
        if self.session_check_job is not None:
            try:
                self.after_cancel(self.session_check_job)
            except tk.TclError:
                pass
            self.session_check_job = None
        LOGGER.info("application_stopping user=%s role=%s", self.current_username, self.current_role.get())
        try:
            self.db.release_user_session(self.current_user_id, self.session_token)
        except Exception:
            LOGGER.exception("session_release_failed user_id=%s", self.current_user_id)
        self.db.close()
        LOGGER.info("application_stopped")
        close_logging()
        self.destroy()

    def start_user_session(self, user_id, expected_password_hash, expected_role):
        if not user_id:
            return False
        token = new_session_token()
        if not self.db.claim_user_session(
            user_id, token, now_text(), expected_password_hash, expected_role
        ):
            return False
        self.session_token = token
        self.session_invalid = False
        self.session_invalid_reason = ""
        return True

    def schedule_session_check(self):
        if not self._closing:
            self.session_check_job = self.after(SESSION_HEARTBEAT_MS, self.poll_user_session)

    def poll_user_session(self):
        self.session_check_job = None
        if self._closing:
            return
        try:
            valid = self.ensure_live_session(notify=False)
        except Exception:
            LOGGER.exception("session_heartbeat_failed user_id=%s", self.current_user_id)
            self.schedule_session_check()
            return
        if valid:
            self.schedule_session_check()
            return
        messagebox.showwarning("会话已结束", self.session_invalid_reason or "当前会话已失效，请重新登录。")
        self.on_close()

    def theme_preference_key(self):
        return f"ui-theme:user:{self.current_user_id or self.current_username}"

    def load_theme_preference(self):
        row = self.db.one(
            "SELECT layout_json FROM dashboard_preferences WHERE subject_key=?",
            (self.theme_preference_key(),),
        )
        if not row:
            return DEFAULT_THEME
        try:
            value = json.loads(row["layout_json"])
            theme_name = value.get("theme") if isinstance(value, dict) else value
        except (TypeError, ValueError, json.JSONDecodeError):
            return DEFAULT_THEME
        return theme_name if isinstance(theme_name, str) and theme_name in THEME_PALETTES else DEFAULT_THEME

    def save_theme_preference(self, theme_name):
        payload = json.dumps({"theme": theme_name}, ensure_ascii=False)
        self.db.execute(
            """INSERT INTO dashboard_preferences(subject_key, layout_json, updated_at) VALUES(?,?,?)
               ON DUPLICATE KEY UPDATE layout_json=VALUES(layout_json), updated_at=VALUES(updated_at)""",
            (self.theme_preference_key(), payload, now_text()),
        )

    def apply_theme(self, _event=None, persist=True):
        theme_name = self.theme_name.get()
        if theme_name not in THEME_PALETTES:
            theme_name = DEFAULT_THEME
            self.theme_name.set(theme_name)
        self.configure_style()
        if hasattr(self, "content_canvas"):
            self.content_canvas.configure(bg=self.colors["bg"])
        if persist:
            try:
                self.save_theme_preference(theme_name)
            except Exception as exc:
                LOGGER.exception("theme_preference_save_failed user=%s theme=%s", self.current_username, theme_name)
                messagebox.showwarning("主题未保存", f"主题已切换，但无法保存偏好：{exc}")
            else:
                try:
                    self.db.log(self.current_user, "ui_theme", None, "switch", "", theme_name, "切换界面主题")
                except Exception:
                    LOGGER.exception("theme_preference_audit_failed user=%s theme=%s", self.current_username, theme_name)
        if self.content is not None:
            self.reload_page()
        self.update_idletasks()

    def authenticate(self):
        self.withdraw()
        admin = self.db.one("SELECT * FROM users WHERE username='admin'")
        if admin and not admin.get("password_hash"):
            initial = os.environ.get("CRM_INITIAL_ADMIN_PASSWORD", "")
            if initial:
                if len(initial) < 8:
                    messagebox.showerror("配置错误", "CRM_INITIAL_ADMIN_PASSWORD 至少需要 8 位。")
                    return False
                changed = self.db.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=? AND (password_hash IS NULL OR password_hash='')",
                                          (hash_password(initial), now_text(), admin["id"]))
                if changed.rowcount != 1:
                    messagebox.showwarning("初始化冲突", "管理员密码已由另一客户端初始化，请使用最新密码登录。")
                else:
                    self.db.log("系统", "user", admin["id"], "initialize_password", "", "", "初始化管理员密码")
            else:
                setup = CredentialDialog(self, setup=True)
                if not setup.result:
                    return False
                changed = self.db.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=? AND (password_hash IS NULL OR password_hash='')",
                                          (hash_password(setup.result[1]), now_text(), admin["id"]))
                if changed.rowcount != 1:
                    messagebox.showwarning("初始化冲突", "管理员密码已由另一客户端初始化，请使用最新密码登录。")
                else:
                    self.db.log("系统", "user", admin["id"], "initialize_password", "", "", "初始化管理员密码")
        while True:
            dialog = CredentialDialog(self)
            if not dialog.result:
                return False
            username, password = dialog.result
            user = self.db.one("SELECT * FROM users WHERE username=? AND is_active=1", (username,))
            if user and verify_password(password, user.get("password_hash", "")):
                self.current_user_id = user["id"]
                self.current_username = user["username"]
                self.current_user = user["display_name"]
                self.current_role.set(user["role_name"])
                if not self.start_user_session(user["id"], user.get("password_hash"), user["role_name"]):
                    messagebox.showerror("登录失败", "账号状态已变化，请重新输入用户名和密码。")
                    continue
                self.db.log(self.current_user, "authentication", user["id"], "login", "", "", "用户登录成功")
                return True
            try:
                self.db.log(username or "未知用户", "authentication", None, "login_failed", "", "denied",
                            "用户名、密码错误或账号停用", result="denied")
            except Exception:
                LOGGER.exception("central_login_failure_audit_failed username=%s", username)
                audit_event(username or "未知用户", "authentication", None, "login_failed",
                            "用户名、密码错误或账号停用", result="denied")
            LOGGER.warning("login_failed username=%s", username)
            messagebox.showerror("登录失败", "用户名或密码错误，或账号已停用。")

    def configure_style(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        selected_theme = self.theme_name.get() if hasattr(self, "theme_name") else DEFAULT_THEME
        self.colors = dict(THEME_PALETTES.get(selected_theme, THEME_PALETTES[DEFAULT_THEME]))
        self.configure(background=self.colors["bg"])
        font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=font, foreground=self.colors["text"])
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("Surface.TFrame", background=self.colors["surface"])
        style.configure("Topbar.TFrame", background=self.colors["surface"])
        style.configure("Statusbar.TFrame", background=self.colors["status_bg"])
        style.configure("Side.TFrame", background=self.colors["side"])
        style.configure("Side.TButton", background=self.colors["side"], foreground=self.colors["side_text"], anchor="w", padding=(16, 6), borderwidth=0)
        style.map("Side.TButton", background=[("active", self.colors["side_hover"])], foreground=[("active", self.colors["side_text"])])
        style.configure("SideActive.TButton", background=self.colors["side_active"], foreground=self.colors["side_active_text"], anchor="w", padding=(16, 6), borderwidth=0)
        style.map("SideActive.TButton", background=[("active", self.colors["side_active"])], foreground=[("active", self.colors["side_active_text"])])
        style.configure("TButton", background=self.colors["surface"], foreground=self.colors["text"], padding=(11, 7), borderwidth=1, relief="solid")
        style.map("TButton", background=[("active", self.colors["control_hover"]), ("disabled", self.colors["status_bg"])],
                  foreground=[("disabled", self.colors["muted"])], bordercolor=[("focus", self.colors["focus"])])
        style.configure("Primary.TButton", background=self.colors["primary"], foreground="#ffffff", padding=(12, 7), borderwidth=0)
        style.map("Primary.TButton", background=[("active", self.colors["primary_active"]), ("disabled", "#A8B0BA")], foreground=[("disabled", "#FFFFFF")])
        style.configure("TEntry", fieldbackground=self.colors["surface"], foreground=self.colors["text"], bordercolor=self.colors["line"], insertcolor=self.colors["text"], padding=5)
        style.map("TEntry", bordercolor=[("focus", self.colors["focus"])])
        style.configure("TCombobox", fieldbackground=self.colors["surface"], foreground=self.colors["text"], bordercolor=self.colors["line"], padding=4)
        style.map("TCombobox", bordercolor=[("focus", self.colors["focus"])], fieldbackground=[("readonly", self.colors["surface"])])
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"), background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("SubTitle.TLabel", font=("Microsoft YaHei UI", 10), background=self.colors["bg"], foreground=self.colors["muted"])
        style.configure("Surface.TLabel", background=self.colors["surface"], foreground=self.colors["text"])
        style.configure("Muted.TLabel", background=self.colors["surface"], foreground=self.colors["muted"])
        style.configure("Brand.TLabel", background=self.colors["side"], foreground=self.colors["brand_text"], font=("Microsoft YaHei UI", 11, "bold"), anchor="w")
        style.configure("RoleBanner.TLabel", background=self.colors["role_bg"], foreground=self.colors["role_text"], padding=(10, 8))
        style.configure("Status.TLabel", background=self.colors["status_bg"], foreground=self.colors["status_text"])
        style.configure("Metric.TLabel", font=("Microsoft YaHei UI", 20, "bold"), background=self.colors["surface"], foreground=self.colors["text"])
        style.configure("Card.TFrame", background=self.colors["surface"], relief="solid", borderwidth=1,
                        bordercolor=self.colors["line"], lightcolor=self.colors["line"], darkcolor=self.colors["line"])
        style.configure("Treeview", rowheight=31, fieldbackground=self.colors["surface"], background=self.colors["surface"], foreground=self.colors["text"], bordercolor=self.colors["line"])
        style.map("Treeview", background=[("selected", self.colors["selection"])], foreground=[("selected", self.colors["selection_text"])])
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), background=self.colors["heading_bg"], foreground=self.colors["heading_text"], padding=(7, 8), relief="flat")
        style.map("Treeview.Heading", background=[("active", self.colors["heading_hover"])])
        style.configure("TNotebook", background=self.colors["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=self.colors["heading_bg"], foreground=self.colors["muted"], padding=(12, 7), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", self.colors["surface"]), ("active", self.colors["control_hover"])], foreground=[("selected", self.colors["text"])])
        style.configure("TLabelframe", background=self.colors["surface"], bordercolor=self.colors["line"], relief="solid")
        style.configure("TLabelframe.Label", background=self.colors["surface"], foreground=self.colors["text"])

    def build_layout(self):
        top = ttk.Frame(self, padding=(14, 9), style="Topbar.TFrame")
        top.pack(fill=tk.X)
        selector_row = ttk.Frame(top, style="Surface.TFrame")
        selector_row.pack(fill=tk.X)
        ttk.Label(selector_row, text="项目", style="Surface.TLabel").pack(side=tk.LEFT)
        self.project_box = ttk.Combobox(selector_row, textvariable=self.selected_project, state="readonly", width=24)
        self.project_box.pack(side=tk.LEFT, padx=(6, 12))
        self.project_box.bind("<<ComboboxSelected>>", lambda _e: self.run_guarded(self.on_project_change))
        ttk.Label(selector_row, text="年度", style="Surface.TLabel").pack(side=tk.LEFT)
        self.plan_box = ttk.Combobox(selector_row, textvariable=self.selected_plan, state="readonly", width=20)
        self.plan_box.pack(side=tk.LEFT, padx=(6, 12))
        self.plan_box.bind("<<ComboboxSelected>>", lambda _e: self.run_guarded(self.on_plan_change))
        ttk.Label(selector_row, text="版本", style="Surface.TLabel").pack(side=tk.LEFT)
        self.version_box = ttk.Combobox(selector_row, textvariable=self.selected_version, state="readonly", width=20)
        self.version_box.pack(side=tk.LEFT, padx=(6, 12))
        self.version_box.bind("<<ComboboxSelected>>", lambda _e: self.run_guarded(self.reload_page))
        utility_row = ttk.Frame(top, style="Surface.TFrame")
        utility_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(utility_row, text="全局搜索", style="Surface.TLabel").pack(side=tk.LEFT)
        ttk.Entry(utility_row, textvariable=self.search_var, width=34).pack(side=tk.LEFT, padx=(6, 4))
        ttk.Button(utility_row, text="搜索", command=lambda: self.run_guarded(self.show_search), style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(utility_row, text="修改密码", command=self.change_my_password).pack(side=tk.RIGHT, padx=(8, 0))
        self.identity_label = ttk.Label(
            utility_row, text=topbar_identity_text(self.current_user, self.current_role.get()),
            style="Surface.TLabel", width=26, anchor="e",
        )
        self.identity_label.pack(side=tk.RIGHT, padx=(12, 4))
        self.theme_box = ttk.Combobox(
            utility_row, textvariable=self.theme_name, values=tuple(THEME_PALETTES), state="readonly", width=8
        )
        self.theme_box.pack(side=tk.RIGHT)
        ttk.Label(utility_row, text="主题", style="Surface.TLabel").pack(side=tk.RIGHT, padx=(12, 5))
        self.theme_box.bind("<<ComboboxSelected>>", lambda _event: self.run_guarded(self.apply_theme))

        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True)
        side = ttk.Frame(main, style="Side.TFrame", width=196)
        side.pack(side=tk.LEFT, fill=tk.Y)
        side.pack_propagate(False)
        ttk.Label(side, text=APP_NAME, style="Brand.TLabel", wraplength=160).pack(anchor="w", padx=16, pady=(16, 18))
        self.nav_buttons = {}
        nav_items = [
            ("首页工作台", self.show_dashboard), ("项目管理", self.show_projects), ("年度计划", self.show_plans),
            ("版本管理", self.show_versions), ("需求管理", self.show_requirements), ("资金管理", self.show_budget),
            ("资金申报", self.show_funding_applications), ("成果物管理", self.show_artifacts),
            ("运营服务", self.show_operation_records), ("流程里程碑", self.show_milestones),
            ("搜索中心", self.show_search), ("报表导出", self.show_exports),
            ("系统设置", self.show_settings),
        ]
        self.nav_order = [name for name, _cmd in nav_items]
        for name, cmd in nav_items:
            if not self.can_view_page(name):
                continue
            button = ttk.Button(side, text=name, style="Side.TButton", command=lambda command=cmd: self.run_guarded(command))
            button.pack(fill=tk.X, padx=8, pady=2)
            self.nav_buttons[name] = button
        ttk.Separator(main, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y)
        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.content_canvas = tk.Canvas(right, bg=self.colors["bg"], highlightthickness=0)
        self.content_scrollbar = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.content_canvas.yview)
        self.content_canvas.configure(yscrollcommand=self.content_scrollbar.set)
        self.content_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.content_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.content = ttk.Frame(self.content_canvas, padding=16)
        self.content_window = self.content_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self.on_content_configure)
        self.content_canvas.bind("<Configure>", self.on_canvas_configure)
        self.bind("<MouseWheel>", self.on_mousewheel, add="+")
        bottom = ttk.Frame(self, padding=(12, 6), style="Statusbar.TFrame")
        bottom.pack(fill=tk.X)
        ttk.Label(bottom, text=f"数据库: {self.db.db_label}", style="Status.TLabel").pack(side=tk.LEFT)
        ttk.Label(bottom, text=f"{APP_RELEASE_LABEL} · {APP_VARIANT}", style="Status.TLabel").pack(side=tk.RIGHT)

    def on_content_configure(self, _event=None):
        self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))

    def on_canvas_configure(self, event):
        self.content_canvas.itemconfigure(self.content_window, width=event.width)

    def on_mousewheel(self, event):
        try:
            if event.widget.winfo_toplevel() is not self:
                return None
            if isinstance(event.widget, ttk.Treeview):
                first, last = event.widget.yview()
                if first > 0 or last < 1:
                    event.widget.yview_scroll(int(-1 * (event.delta / 120)), "units")
                    return "break"
            if self.content_canvas.winfo_exists():
                self.content_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return "break"
        except tk.TclError:
            return None

    def clear(self, title):
        for child in self.content.winfo_children():
            child.destroy()
        self.content_canvas.yview_moveto(0)
        self.current_page = title
        self.update_nav_state()
        header = ttk.Frame(self.content)
        header.pack(fill=tk.X, pady=(0, 12))
        left = ttk.Frame(header)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(left, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, text=self.context_summary(), style="SubTitle.TLabel").pack(anchor="w", pady=(3, 0))
        right = ttk.Frame(header)
        right.pack(side=tk.RIGHT)
        ttk.Label(right, text=f"当前角色：{self.current_role.get()}", style="SubTitle.TLabel").pack(anchor="e")
        ttk.Label(right, text=datetime.now().strftime("%Y-%m-%d %H:%M"), style="SubTitle.TLabel").pack(anchor="e", pady=(3, 0))
        ttk.Separator(self.content, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 12))

    def update_nav_state(self):
        hidden = set()
        if not self.can_view_money():
            hidden.add("资金管理")
            hidden.add("资金申报")
        if not self.can_action("export"):
            hidden.add("报表导出")
        if not self.can_action("approve"):
            hidden.add("系统设置")
        if not self.can_view_operation_records():
            hidden.add("运营服务")
        active_page = "版本管理" if self.current_page in {"版本基线", "跨版本比对"} else self.current_page
        for button in getattr(self, "nav_buttons", {}).values():
            button.pack_forget()
        for name in getattr(self, "nav_order", []):
            if name in hidden:
                continue
            button = self.nav_buttons.get(name)
            if button is None:
                continue
            button.configure(style="SideActive.TButton" if name == active_page else "Side.TButton")
            button.pack(fill=tk.X, padx=9, pady=2)

    def context_summary(self):
        project = self.selected_project.get() or "未选择项目"
        plan = self.selected_plan.get() or "未选择年度"
        version = self.selected_version.get() or "未选择版本"
        version_row = self.current_version()
        frozen = "已发布" if version_row and version_row["is_frozen"] else "可编辑"
        return f"项目：{project}    年度：{plan}    版本：{version}    基线状态：{frozen}"

    def section_title(self, parent, title, subtitle=""):
        box = ttk.Frame(parent)
        box.pack(fill=tk.X, pady=(10, 8))
        ttk.Label(box, text=title, font=("Microsoft YaHei UI", 12, "bold"), background=self.colors["bg"], foreground=self.colors["text"]).pack(anchor="w")
        if subtitle:
            ttk.Label(box, text=subtitle, style="SubTitle.TLabel").pack(anchor="w", pady=(2, 0))

    def notice_banner(self, parent, text, tone="info"):
        palette = {
            "info": (self.colors["role_bg"], self.colors["primary"]),
            "success": ("#E8F5EC", self.colors["success"]),
            "warning": ("#FFF3D6", self.colors["warning"]),
            "danger": ("#FDE9E7", self.colors["danger"]),
        }
        background, foreground = palette.get(tone, palette["info"])
        frame = tk.Frame(parent, bg=background, highlightbackground=foreground, highlightthickness=1, padx=12, pady=9)
        frame.pack(fill=tk.X, pady=(0, 10))
        tk.Label(frame, text=text, bg=background, fg=foreground, font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(fill=tk.X)
        return frame

    def make_action_bar(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=(0, 10))
        return bar

    def can_action(self, action):
        allowed = ROLE_ACTIONS.get(self.current_role.get(), set())
        return "*" in allowed or action in allowed

    def can_view_money(self):
        return self.current_role.get() not in SENSITIVE_ROLES

    def can_view_effort(self):
        return self.current_role.get() not in {"客户", "销售"}

    def can_view_operation_records(self):
        return self.current_role.get() in {"管理员", "咨询负责人", "项目经理", "运营人员"}

    def visible_export_columns(self, columns):
        visible = list(columns)
        if not self.can_view_effort():
            visible = [key for key in visible if key not in {"estimated_hours", "actual_hours"}]
        if not self.can_view_money():
            visible = [key for key in visible if key not in MONEY_COLUMNS]
        return visible

    def can_access_project_now(self, project_id):
        if not project_id:
            return False
        if not self.ensure_live_session(notify=False):
            return False
        if self.current_role.get() != "客户":
            return True
        if not self.current_user_id:
            return False
        return bool(self.db.one(
            "SELECT user_id FROM user_project_access WHERE user_id=? AND project_id=?",
            (self.current_user_id, project_id),
        ))

    def ensure_live_session(self, project_id=None, notify=True):
        if self.session_invalid or not self.current_user_id:
            if notify:
                messagebox.showwarning("会话已失效", self.session_invalid_reason or "当前账号会话已失效，请重新启动程序并登录。")
            return False
        user = self.db.one("SELECT id, username, display_name, role_name, is_active, session_token FROM users WHERE id=?",
                           (self.current_user_id,))
        identity_valid = bool(user and user["is_active"] and user["username"] == self.current_username
                              and user["role_name"] == self.current_role.get())
        stored_token = str(user.get("session_token") or "") if user else ""
        token_valid = bool(identity_valid and self.session_token and stored_token and
                           hmac.compare_digest(stored_token, self.session_token))
        if not identity_valid or not token_valid:
            self.session_invalid = True
            if identity_valid and stored_token:
                self.session_invalid_reason = "该账号已在另一台设备或另一个客户端登录，当前会话已自动退出。"
                description = "同一账号在其他客户端登录导致当前会话失效"
            elif identity_valid:
                self.session_invalid_reason = "当前会话已由管理员终止或因密码重置失效，请重新登录。"
                description = "管理员强制下线或密码重置导致当前会话失效"
            else:
                self.session_invalid_reason = "账号已停用、删除或角色发生变化，请重新登录。"
                description = "账号停用、删除或角色变化导致会话失效"
            audit_event(self.current_username, "authentication", self.current_user_id, "session_invalidated",
                        description, result="denied")
            if notify:
                messagebox.showwarning("会话已失效", self.session_invalid_reason)
            return False
        if user["role_name"] == "客户" and project_id:
            allowed = self.db.one("SELECT user_id FROM user_project_access WHERE user_id=? AND project_id=?",
                                  (self.current_user_id, project_id))
            if not allowed:
                audit_event(self.current_username, "user_project_access", project_id, "access_denied",
                            "客户项目授权已撤销", result="denied")
                if notify:
                    messagebox.showwarning("项目授权失效", "当前项目授权已被撤销，请重新选择项目。")
                return False
        return True

    def run_guarded(self, command):
        project_id = self.current_project_id() if hasattr(self, "projects") else None
        if not self.ensure_live_session(project_id):
            self.refresh_contexts()
            return None
        return command()

    def tag_options(self):
        return [row["tag_name"] for row in self.db.query("SELECT tag_name FROM tag_definitions WHERE is_active=1 ORDER BY tag_name")]

    def load_dashboard_layout(self, role):
        defaults = [{"key": key, "visible": True} for key, _label in DASHBOARD_SECTIONS]
        row = self.db.one("SELECT layout_json FROM dashboard_preferences WHERE subject_key=?", (f"role:{role}",))
        if not row:
            return defaults
        try:
            saved = json.loads(row["layout_json"])
            known = {key for key, _label in DASHBOARD_SECTIONS}
            result = [{"key": item["key"], "visible": bool(item.get("visible", True))} for item in saved if item.get("key") in known]
            present = {item["key"] for item in result}
            result.extend(item for item in defaults if item["key"] not in present)
            return result
        except (TypeError, ValueError, KeyError):
            return defaults

    def save_dashboard_layout(self, role, items):
        if not self.ensure_live_session(self.current_project_id()) or self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有有效的管理员会话可以保存角色看板配置。")
            return
        payload = json.dumps(items, ensure_ascii=False)
        self.db.execute("""INSERT INTO dashboard_preferences(subject_key, layout_json, updated_at) VALUES(?,?,?)
                           ON DUPLICATE KEY UPDATE layout_json=VALUES(layout_json), updated_at=VALUES(updated_at)""",
                        (f"role:{role}", payload, now_text()))
        self.db.log(self.current_user, "dashboard", None, "configure", "", role, f"配置 {role} 看板")
        if role == self.current_role.get():
            self.show_dashboard()

    def configure_dashboard(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        if self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员可以配置角色看板。")
            return
        DashboardLayoutDialog(self, self.load_dashboard_layout, self.save_dashboard_layout)

    def add_tag_definition(self):
        if not self.ensure_live_session(self.current_project_id()) or self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员可以维护标签字典。")
            return
        d = FieldDialog(self, "新增标签", [("tag_name", "标签名称", "text", None)], required=["tag_name"])
        if not d.result:
            return
        try:
            self.db.execute("INSERT INTO tag_definitions(tag_name, created_at) VALUES(?,?)", (d.result["tag_name"].strip(), now_text()))
            self.db.log(self.current_user, "tag", None, "create", "", d.result["tag_name"], "新增需求标签")
            self.show_settings()
        except Exception as exc:
            messagebox.showerror("保存失败", f"标签可能已存在：{exc}")

    def toggle_tag_definition(self):
        if not self.ensure_live_session(self.current_project_id()) or self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员可以维护标签字典。")
            return
        selection = getattr(self, "tag_tree", None).selection() if hasattr(self, "tag_tree") else ()
        if not selection:
            messagebox.showwarning("提示", "请先选择标签。")
            return
        tag_id = int(self.tag_tree.item(selection[0])["values"][0])
        tag = self.db.one("SELECT is_active FROM tag_definitions WHERE id=?", (tag_id,))
        self.db.execute("UPDATE tag_definitions SET is_active=? WHERE id=?", (0 if tag["is_active"] else 1, tag_id))
        self.show_settings()

    def dashboard_status_section(self, project_id):
        self.section_title(self.content, "需求状态分布", "点击状态卡片可直接进入对应需求清单。")
        rows = self.db.query("""SELECT status, COUNT(*) c FROM requirements
                                WHERE is_deleted=0 AND project_id=? GROUP BY status ORDER BY MIN(id)""", (project_id,)) if project_id else []
        cards = [(row["status"], row["c"], "点击查看", STATUS_ACCENTS.get(row["status"]),
                  lambda s=row["status"]: self.show_requirements_for_status(s)) for row in rows]
        if cards:
            self.metric_grid(self.content, cards, columns=4)
        else:
            ttk.Label(self.content, text="暂无需求状态数据。", style="SubTitle.TLabel").pack(anchor="w", pady=(0, 10))

    def dashboard_trend_section(self, project_id):
        self.section_title(self.content, "需求推进趋势", "最近 8 个有状态变化的日期。")
        history = self.db.query("""SELECT h.changed_at FROM requirement_status_history h
                                   INNER JOIN requirements r ON r.id=h.requirement_id
                                   WHERE r.project_id=? AND r.is_deleted=0 ORDER BY h.changed_at DESC LIMIT 300""", (project_id,)) if project_id else []
        counts = {}
        for row in history:
            day = str(row["changed_at"] or "")[:10]
            if day:
                counts[day] = counts.get(day, 0) + 1
        days = sorted(counts)[-8:]
        frame = ttk.Frame(self.content, style="Surface.TFrame", padding=12)
        frame.pack(fill=tk.X, pady=(0, 12))
        canvas = tk.Canvas(frame, height=190, bg=self.colors["surface"], highlightthickness=0)
        canvas.pack(fill=tk.X, expand=True)
        canvas.update_idletasks()
        width = max(640, canvas.winfo_width())
        if not days:
            canvas.create_text(18, 90, text="暂无状态变化记录", anchor="w", fill=self.colors["muted"])
            return
        maximum = max(counts[day] for day in days)
        slot = (width - 48) / len(days)
        for index, day in enumerate(days):
            value = counts[day]
            bar_height = 110 * value / maximum
            x1 = 28 + index * slot + slot * 0.2
            x2 = 28 + (index + 1) * slot - slot * 0.2
            canvas.create_rectangle(x1, 145 - bar_height, x2, 145, fill=self.colors["primary"], outline="")
            canvas.create_text((x1 + x2) / 2, 137 - bar_height, text=str(value), fill=self.colors["text"])
            canvas.create_text((x1 + x2) / 2, 164, text=day[5:], fill=self.colors["muted"])

    def recent_requirement_rows(self, project_id, version_id):
        if not project_id:
            return []
        return self.db.query("""SELECT requirement_code, requirement_name, source_role, priority, status, owner_name, updated_at
                                FROM requirements
                                WHERE is_deleted=0 AND project_id=? AND (? IS NULL OR version_id=?)
                                ORDER BY updated_at DESC LIMIT 12""", (project_id, version_id, version_id))

    def dashboard_recent_section(self, project_id, version_id):
        self.section_title(self.content, "最近需求", "切换顶部版本后联动刷新。")
        rows = self.recent_requirement_rows(project_id, version_id)
        self.add_table(self.content, [
            ("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 260), ("source_role", "来源", 90),
            ("priority", "优先级", 70), ("status", "状态", 110), ("owner_name", "负责人", 100), ("updated_at", "最近更新", 150),
        ], rows, 10)

    def can_view_page(self, page_name):
        if page_name in {"资金管理", "资金申报"}:
            return self.can_view_money()
        if page_name == "运营服务":
            return self.can_view_operation_records()
        if page_name == "报表导出":
            return self.can_action("export")
        if page_name == "系统设置":
            return self.can_action("approve")
        return True

    def require_action(self, action, label):
        project_id = self.current_project_id() if hasattr(self, "projects") else None
        if not self.ensure_live_session(project_id):
            return False
        if self.can_action(action):
            return True
        description = f"{self.current_role.get()} 无权执行：{label}"
        try:
            self.db.log(self.current_username, "permission", self.current_user_id, "denied", "", action,
                        description, result="denied")
        except Exception:
            LOGGER.exception("central_permission_audit_failed user=%s action=%s", self.current_username, action)
            audit_event(self.current_username, "permission", self.current_user_id, "denied", description, result="denied")
        LOGGER.warning("permission_denied user=%s role=%s action=%s label=%s", self.current_username, self.current_role.get(), action, label)
        messagebox.showwarning("权限不足", f"当前角色“{self.current_role.get()}”无权执行：{label}")
        return False

    def change_my_password(self):
        if not self.ensure_live_session():
            return
        if not self.current_user_id:
            messagebox.showwarning("提示", "未找到当前登录账号。")
            return
        dialog = FieldDialog(self, "修改登录密码", [
            ("old_password", "当前密码", "password", None),
            ("new_password", "新密码", "password", None),
            ("confirm_password", "确认新密码", "password", None),
        ], required=["old_password", "new_password", "confirm_password"])
        if not dialog.result:
            return
        user = self.db.one("SELECT password_hash FROM users WHERE id=? AND is_active=1", (self.current_user_id,))
        if not user or not verify_password(dialog.result["old_password"], user.get("password_hash", "")):
            messagebox.showerror("修改失败", "当前密码不正确。")
            return
        if len(dialog.result["new_password"]) < 8:
            messagebox.showerror("修改失败", "新密码至少 8 位。")
            return
        if dialog.result["new_password"] != dialog.result["confirm_password"]:
            messagebox.showerror("修改失败", "两次输入的新密码不一致。")
            return
        self.db.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                        (hash_password(dialog.result["new_password"]), now_text(), self.current_user_id))
        self.db.log(self.current_user, "user", self.current_user_id, "change_password", "", "", "用户修改登录密码")
        messagebox.showinfo("修改完成", "登录密码已更新。")

    def refresh_contexts(self):
        if not self.ensure_live_session(notify=False):
            projects = []
        elif self.current_role.get() == "客户" and self.current_user_id:
            projects = self.db.query("""SELECT p.id, p.project_name
                                        FROM planning_projects p
                                        INNER JOIN user_project_access a ON a.project_id=p.id
                                        WHERE a.user_id=? ORDER BY p.id""", (self.current_user_id,))
        else:
            projects = self.db.query("SELECT id, project_name FROM planning_projects ORDER BY id")
        self.projects = {f"{r['id']} - {r['project_name']}": r["id"] for r in projects}
        self.project_box["values"] = list(self.projects.keys())
        if self.selected_project.get() not in self.projects and self.projects:
            self.selected_project.set(next(iter(self.projects)))
        elif not self.projects:
            self.selected_project.set("")
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

    def current_version(self):
        version_id = self.current_version_id()
        return self.db.one("SELECT * FROM implementation_versions WHERE id=?", (version_id,)) if version_id else None

    def parse_float(self, value, field_name):
        try:
            number = float(value or 0)
            if not math.isfinite(number):
                raise ValueError
            return number
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是有限数字") from exc

    def parse_int(self, value, field_name):
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是整数") from exc

    def requirement_options(self, include_unplanned=True):
        version_id = self.current_version_id()
        sql = """SELECT id, requirement_code, requirement_name
                 FROM requirements
                 WHERE is_deleted=0 AND project_id=? AND (?=1 OR version_id=?)
                 ORDER BY updated_at DESC"""
        rows = self.db.query(sql, (self.current_project_id(), 1 if include_unplanned else 0, version_id))
        options = ["不关联具体需求"]
        options.extend([f"{r['id']} - {r['requirement_code']} {r['requirement_name']}" for r in rows])
        return options

    def parent_requirement_options(self, exclude_id=None):
        if not self.current_project_id():
            return {"不关联原需求": None}
        params = [self.current_project_id()]
        where = "project_id=? AND is_deleted=0"
        if exclude_id:
            where += " AND id<>?"
            params.append(exclude_id)
        rows = self.db.query(
            f"SELECT id, requirement_code, requirement_name FROM requirements WHERE {where} ORDER BY updated_at DESC",
            tuple(params),
        )
        result = {"不关联原需求": None}
        result.update({f"{row['id']} - {row['requirement_code']} {row['requirement_name']}": row["id"] for row in rows})
        return result

    def validate_parent_requirement(self, parent_id, project_id, requirement_id=None):
        if not parent_id:
            return None
        parent_id = int(parent_id)
        parent = self.db.one("SELECT id FROM requirements WHERE id=? AND project_id=? AND is_deleted=0",
                             (parent_id, project_id))
        if not parent or parent_id == requirement_id:
            raise ValueError("关联原需求必须是同一项目内的其他有效需求。")
        if requirement_id:
            cycle = self.db.one("""WITH RECURSIVE descendants(id) AS (
                                     SELECT id FROM requirements WHERE parent_requirement_id=? AND is_deleted=0
                                     UNION
                                     SELECT r.id FROM requirements r JOIN descendants d ON r.parent_requirement_id=d.id
                                     WHERE r.is_deleted=0
                                   ) SELECT 1 probe FROM descendants WHERE id=? LIMIT 1""",
                                (requirement_id, parent_id))
            if cycle:
                raise ValueError("关联原需求会形成循环关系。")
        return parent_id

    def id_from_option(self, option):
        if not option or option.startswith("不关联"):
            return None
        try:
            return int(option.split(" - ", 1)[0])
        except ValueError:
            return None

    def reload_page(self):
        getattr(self, {
            "首页工作台": "show_dashboard", "项目管理": "show_projects", "年度计划": "show_plans", "版本管理": "show_versions",
            "需求管理": "show_requirements", "资金管理": "show_budget", "资金申报": "show_funding_applications",
            "成果物管理": "show_artifacts", "运营服务": "show_operation_records", "搜索中心": "show_search",
            "流程里程碑": "show_milestones", "报表导出": "show_exports", "系统设置": "show_settings",
            "版本基线": "show_version_baseline", "跨版本比对": "reload_version_comparison",
        }.get(self.current_page, "show_dashboard"))()

    def reload_version_comparison(self):
        self.compare_versions(self.version_compare_ids)

    def add_table(self, parent, columns, rows, height=16, on_double_click=None):
        frame = ttk.Frame(parent, style="Surface.TFrame", padding=1)
        frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        visible_rows = min(height, max(2, len(rows)))
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=visible_rows)
        ybar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        xbar = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        for key, label, width in columns:
            tree.heading(key, text=label, command=lambda col=key: self.sort_treeview(tree, col, False))
            tree.column(key, width=width, anchor="w")
        tree.tag_configure("odd", background=self.colors["surface"])
        tree.tag_configure("even", background=self.colors["surface_alt"])
        for status, color in STATUS_COLORS.items():
            tree.tag_configure(f"status_{status}", background=color)
        for diff_type, color in DIFF_COLORS.items():
            tree.tag_configure(f"diff_{diff_type}", background=color)
        for index, row in enumerate(rows):
            tags = ["even" if index % 2 else "odd"]
            status = self.row_value(row, "status")
            if status in STATUS_COLORS:
                tags.append(f"status_{status}")
            diff_type = self.row_value(row, "diff_type")
            if diff_type in DIFF_COLORS:
                tags.append(f"diff_{diff_type}")
            tree.insert("", tk.END, values=[self.row_value(row, c[0]) for c in columns], tags=tuple(tags))
        tree.grid(row=0, column=0, sticky="nsew")
        if len(rows) > visible_rows:
            ybar.grid(row=0, column=1, sticky="ns")

        def sync_horizontal_scrollbar(_event=None):
            try:
                if not frame.winfo_exists() or not tree.winfo_exists():
                    return
                required_width = sum(int(tree.column(column[0], "width")) for column in columns)
                available = frame.winfo_width() - (ybar.winfo_reqwidth() if len(rows) > visible_rows else 0)
                if available > 1 and required_width > available:
                    xbar.grid(row=1, column=0, sticky="ew")
                else:
                    xbar.grid_remove()
                    tree.xview_moveto(0)
            except tk.TclError:
                return

        frame.bind("<Configure>", sync_horizontal_scrollbar)
        tree.bind("<ButtonRelease-1>", lambda _event: self.after_idle(sync_horizontal_scrollbar), add="+")
        self.after_idle(sync_horizontal_scrollbar)
        if not rows:
            tree.insert("", tk.END, values=["暂无数据"] + [""] * (len(columns) - 1), tags=("even",))
        if on_double_click:
            tree.bind("<Double-Button-1>", lambda _event: on_double_click())
        return tree

    def sort_treeview(self, tree, col, reverse):
        values = []
        for item in tree.get_children(""):
            cell = tree.set(item, col)
            try:
                sort_value = (0, 0, float(str(cell).replace(",", "")))
            except ValueError:
                text_value = str(cell).strip()
                sort_value = (1 if not text_value else 0, 1, text_value.casefold())
            values.append((sort_value, item))
        values.sort(reverse=reverse)
        for index, (_, item) in enumerate(values):
            tree.move(item, "", index)
        tree.heading(col, command=lambda: self.sort_treeview(tree, col, not reverse))

    def row_value(self, row, key):
        if isinstance(row, dict):
            value = row.get(key, "")
        else:
            try:
                value = row[key]
            except (KeyError, IndexError):
                return ""
        if key in MONEY_COLUMNS and value != "":
            return money_text(value)
        if key == "is_frozen":
            return "是" if str(value) in {"1", "True", "true"} else "否"
        return value

    def metric_card(self, parent, title, value, hint="", accent=None, command=None, grid=None):
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        if grid:
            row, col = grid
            card.grid(row=row, column=col, sticky="nsew", padx=(0, 10), pady=(0, 10))
            parent.columnconfigure(col, weight=1, uniform="metric")
        else:
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10), pady=(0, 10))
        if accent:
            tk.Frame(card, bg=accent, width=4, height=54).pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
            body = ttk.Frame(card, style="Surface.TFrame")
            body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        else:
            body = card
        ttk.Label(body, text=title, style="Surface.TLabel").pack(anchor="w")
        value_label = ttk.Label(body, text=str(value), style="Metric.TLabel")
        if accent:
            value_label.configure(foreground=accent)
        value_label.pack(anchor="w", pady=(8, 0))
        if hint:
            ttk.Label(body, text=hint, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        if command:
            self.make_clickable(card, command)
        return card

    def metric_grid(self, parent, cards, columns=4):
        grid = ttk.Frame(parent)
        grid.pack(fill=tk.X, pady=(0, 4))
        for index, card in enumerate(cards):
            row, col = divmod(index, columns)
            self.metric_card(grid, *card, grid=(row, col))
        return grid

    def make_clickable(self, widget, command):
        widget.configure(cursor="hand2")
        widget.bind("<Button-1>", lambda _event: command())
        for child in widget.winfo_children():
            self.make_clickable(child, command)

    def show_requirements_for_status(self, status):
        self.requirement_scope.set("当前项目全部")
        self.requirement_status_filter.set(status)
        self.show_requirements()

    def requirement_count(self, where, params):
        row = self.db.one(f"SELECT COUNT(*) c FROM requirements WHERE is_deleted=0 AND {where}", tuple(params))
        return row["c"] if row else 0

    def role_dashboard_panel(self, project_id, version_id):
        role = self.current_role.get()
        self.section_title(self.content, ROLE_PANEL_TITLES.get(role, "角色工作台"), ROLE_DESCRIPTIONS.get(role, ""))
        metrics = []
        rows = []
        columns = [
            ("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 260),
            ("source_role", "来源", 90), ("priority", "优先级", 70), ("status", "状态", 110),
            ("owner_name", "对接人", 100), ("updated_at", "更新时间", 150),
        ]

        if not project_id:
            empty = ttk.Frame(self.content)
            empty.pack(fill=tk.X)
            self.metric_card(empty, "暂无项目", "0", "请先创建规划项目", self.colors["warning"])
            return

        if role == "客户":
            metrics = [
                ("客户来源需求", self.requirement_count("project_id=? AND source_role='客户'", [project_id]), "客户直接提出的需求", self.colors["primary"]),
                ("推进中", self.requirement_count("project_id=? AND source_role='客户' AND status NOT IN ('已上线运维','已关闭','已取消')", [project_id]), "仍需反馈进展", self.colors["warning"]),
                ("已上线运维", self.requirement_count("project_id=? AND source_role='客户' AND status='已上线运维'", [project_id]), "可同步客户的改进结果", self.colors["success"]),
            ]
            rows = self.db.query("""SELECT requirement_code, requirement_name, source_role, priority, status, owner_name, updated_at
                                    FROM requirements
                                    WHERE is_deleted=0 AND project_id=? AND source_role='客户'
                                    ORDER BY updated_at DESC LIMIT 8""", (project_id,))
        elif role == "销售":
            funding = self.db.one("""SELECT COALESCE(SUM(amount),0) total,
                                             COALESCE(SUM(CASE WHEN status IN ('已提交','审批中') THEN amount ELSE 0 END),0) reviewing,
                                             COALESCE(SUM(CASE WHEN status='已拨付' THEN amount ELSE 0 END),0) paid
                                      FROM funding_applications WHERE project_id=?""", (project_id,))
            metrics = [
                ("申报总额", money_text(funding["total"]), "当前项目全部申报", self.colors["primary"]),
                ("审批中", money_text(funding["reviewing"]), "已提交或审批中", self.colors["warning"]),
                ("已拨付", money_text(funding["paid"]), f"拨付率 {percent_text(funding['paid'], funding['total'])}", self.colors["success"]),
            ]
            columns = [
                ("application_code", "申报编号", 150), ("plan_year", "年度", 80), ("plan_name", "年度计划", 180),
                ("amount", "申报金额", 110), ("status", "状态", 100), ("updated_at", "更新时间", 150),
            ]
            rows = self.db.query("""SELECT f.application_code, a.plan_year, a.plan_name, f.amount, f.status, f.updated_at
                                    FROM funding_applications f JOIN annual_plans a ON a.id=f.annual_plan_id
                                    WHERE f.project_id=? ORDER BY f.updated_at DESC LIMIT 8""", (project_id,))
        elif role == "项目经理":
            metrics = [
                ("当前版本需求", self.requirement_count("version_id=?", [version_id]) if version_id else 0, "当前交付范围", self.colors["primary"]),
                ("待验收", self.requirement_count("version_id=? AND status='待验收'", [version_id]) if version_id else 0, "需要组织验收", self.colors["warning"]),
                ("成本风险", self.requirement_count("version_id=? AND allocated_budget>0 AND actual_cost>allocated_budget+1e-9", [version_id]) if version_id else 0, "实际消耗超过分配预算", self.colors["danger"]),
            ]
            columns = [("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 220), ("priority", "优先级", 70),
                       ("status", "状态", 110), ("owner_name", "负责人", 100), ("estimated_hours", "预估工时", 90),
                       ("actual_hours", "实际工时", 90), ("updated_at", "更新时间", 150)]
            rows = self.db.query("""SELECT requirement_code, requirement_name, priority, status, owner_name, estimated_hours, actual_hours, updated_at
                                    FROM requirements
                                    WHERE is_deleted=0 AND version_id=?
                                    ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END, updated_at DESC LIMIT 8""", (version_id,)) if version_id else []
        elif role == "研发人员":
            metrics = [
                ("待研发任务", self.requirement_count("version_id=? AND status IN ('已排期','研发中')", [version_id]) if version_id else 0, "按优先级推进", self.colors["primary"]),
                ("P0/P1", self.requirement_count("version_id=? AND priority IN ('P0','P1','高')", [version_id]) if version_id else 0, "高优先级任务", self.colors["warning"]),
                ("挂起/退回", self.requirement_count("version_id=? AND status IN ('已挂起','退回修改')", [version_id]) if version_id else 0, "需要协调处理", self.colors["danger"]),
            ]
            columns = [("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 220), ("priority", "优先级", 70),
                       ("status", "状态", 110), ("owner_name", "负责人", 100), ("estimated_hours", "预估工时", 90),
                       ("actual_hours", "实际工时", 90), ("updated_at", "更新时间", 150)]
            rows = self.db.query("""SELECT requirement_code, requirement_name, priority, status, owner_name, estimated_hours, actual_hours, updated_at
                                    FROM requirements
                                    WHERE is_deleted=0 AND version_id=? AND status IN ('已排期','研发中','退回修改','已挂起')
                                    ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN '高' THEN 1 ELSE 2 END, updated_at DESC LIMIT 8""", (version_id,)) if version_id else []
        elif role == "运营人员":
            operation_summary = self.db.one("""SELECT COUNT(*) total,
                                                      SUM(CASE WHEN status IN ('待处理','处理中') THEN 1 ELSE 0 END) open_count,
                                                      SUM(CASE WHEN status IN ('已完成','已关闭') THEN 1 ELSE 0 END) closed_count
                                               FROM operation_records WHERE project_id=?""", (project_id,))
            metrics = [
                ("服务记录", operation_summary["total"] or 0, "推广、问题与维护", self.colors["primary"]),
                ("待处理/处理中", operation_summary["open_count"] or 0, "需要继续跟进", self.colors["warning"]),
                ("已完成/关闭", operation_summary["closed_count"] or 0, "已形成服务闭环", self.colors["success"]),
            ]
            columns = [("record_code", "记录编号", 150), ("record_type", "类型", 100), ("requirement_code", "原需求", 120),
                       ("status", "状态", 90), ("record_date", "日期", 110), ("owner_name", "负责人", 100),
                       ("description", "说明", 260)]
            rows = self.db.query("""SELECT o.record_code, o.record_type, r.requirement_code, o.status,
                                           o.record_date, o.owner_name, o.description
                                    FROM operation_records o LEFT JOIN requirements r ON r.id=o.requirement_id
                                    WHERE o.project_id=? ORDER BY o.record_date DESC, o.id DESC LIMIT 8""", (project_id,))
        else:
            metrics = [
                ("待规划池", self.requirement_count("project_id=? AND version_id IS NULL", [project_id]), "尚未确认落地版本", self.colors["warning"]),
                ("已发布版本", self.db.one("SELECT COUNT(*) c FROM implementation_versions WHERE project_id=? AND is_frozen=1", (project_id,))["c"], "已形成基线", self.colors["primary"]),
                ("待审批变更", self.db.one("SELECT COUNT(*) c FROM change_requests WHERE approval_status='pending'")["c"], "冻结版本变更入口", self.colors["danger"]),
            ]
            rows = self.db.query("""SELECT requirement_code, requirement_name, source_role, priority, status, owner_name, updated_at
                                    FROM requirements
                                    WHERE is_deleted=0 AND project_id=?
                                    ORDER BY updated_at DESC LIMIT 8""", (project_id,))

        self.metric_grid(self.content, metrics, columns=3)
        self.add_table(self.content, columns, rows, height=7)

    def show_dashboard(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        self.clear("首页工作台")
        project_id = self.current_project_id()
        version_id = self.current_version_id()
        budget = self.db.one("""SELECT
                                    COALESCE(SUM(allocated_budget), 0) allocated,
                                    COALESCE(SUM(actual_cost), 0) cost
                                FROM requirements
                                WHERE is_deleted=0 AND project_id=?""", (project_id,)) if project_id else {"allocated": 0, "cost": 0}
        project_budget = self.db.one("SELECT total_budget FROM planning_projects WHERE id=?", (project_id,)) if project_id else {"total_budget": 0}
        counts = {
            "规划项目": len(self.projects),
            "年度计划": self.db.one("SELECT COUNT(*) c FROM annual_plans WHERE project_id=?", (project_id,))["c"] if project_id else 0,
            "落地版本": self.db.one("SELECT COUNT(*) c FROM implementation_versions WHERE project_id=?", (project_id,))["c"] if project_id else 0,
            "版本需求": self.db.one("SELECT COUNT(*) c FROM requirements WHERE is_deleted=0 AND version_id=?", (version_id,))["c"] if version_id else 0,
            "待规划需求": self.db.one("SELECT COUNT(*) c FROM requirements WHERE is_deleted=0 AND project_id=? AND version_id IS NULL", (project_id,))["c"] if project_id else 0,
        }
        self.metric_grid(self.content, [
            ("规划项目", counts["规划项目"], "系统项目总数", self.colors["primary"]),
            ("落地版本", counts["落地版本"], "当前项目版本数", self.colors["success"]),
            ("版本需求", counts["版本需求"], "当前版本需求数", self.colors["primary"]),
            ("待规划需求", counts["待规划需求"], "尚未分配版本", self.colors["warning"] if counts["待规划需求"] else self.colors["success"]),
        ], columns=4)
        if self.can_view_money():
            self.metric_grid(self.content, [
                ("项目总预算", money_text(project_budget["total_budget"] if project_budget else 0), "宏观规划资金", self.colors["primary"]),
                ("需求已分配", money_text(budget["allocated"]), f"占总预算 {percent_text(budget['allocated'], project_budget['total_budget'] if project_budget else 0)}", self.colors["success"]),
                ("实际消耗", money_text(budget["cost"]), f"执行率 {percent_text(budget['cost'], budget['allocated'])}", self.colors["danger"] if budget["allocated"] and budget_limit_exceeded(budget["cost"], budget["allocated"]) else self.colors["warning"]),
            ], columns=3)
        role = self.current_role.get()
        msg = ROLE_DESCRIPTIONS.get(role, ROLE_DESCRIPTIONS["管理员"])
        ttk.Label(self.content, text=f"当前视角：{role}。{msg}", style="RoleBanner.TLabel", wraplength=920).pack(fill=tk.X, pady=(2, 12))
        if role == "管理员":
            ttk.Button(self.content, text="配置角色看板", command=self.configure_dashboard).pack(anchor="e", pady=(0, 8))
        renderers = {
            "role_panel": lambda: self.role_dashboard_panel(project_id, version_id),
            "status": lambda: self.dashboard_status_section(project_id),
            "trend": lambda: self.dashboard_trend_section(project_id),
            "recent": lambda: self.dashboard_recent_section(project_id, version_id),
        }
        for section in self.load_dashboard_layout(role):
            if section["visible"]:
                renderers[section["key"]]()

    def show_projects(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        self.clear("项目管理")
        bar = ttk.Frame(self.content)
        bar.pack(fill=tk.X, pady=(0, 8))
        if self.can_action("project"):
            ttk.Button(bar, text="新建项目", command=self.add_project, style="Primary.TButton").pack(side=tk.LEFT)
        if self.current_role.get() == "客户":
            rows = self.db.query("""SELECT p.id, p.project_code, p.project_name, p.customer_name, p.total_budget,
                                           p.current_stage, p.status, p.updated_at
                                    FROM planning_projects p
                                    INNER JOIN user_project_access a ON a.project_id=p.id
                                    WHERE a.user_id=? ORDER BY p.id DESC""", (self.current_user_id,))
        else:
            rows = self.db.query("SELECT id, project_code, project_name, customer_name, total_budget, current_stage, status, updated_at FROM planning_projects ORDER BY id DESC")
        columns = [("id", "ID", 50), ("project_code", "项目编号", 120), ("project_name", "项目名称", 240), ("customer_name", "客户", 160)]
        if self.can_view_money():
            columns.append(("total_budget", "总预算", 100))
        columns += [("current_stage", "当前阶段", 120), ("status", "状态", 90), ("updated_at", "更新时间", 150)]
        self.add_table(self.content, columns, rows)

    def add_project(self):
        if not self.require_action("project", "新建项目"):
            return
        d = FieldDialog(self, "新建项目", [
            ("project_code", "项目编号", "text", None), ("project_name", "项目名称", "text", None),
            ("customer_name", "客户名称", "text", None), ("total_budget", "总预算", "text", None),
            ("project_background", "项目背景", "memo", None),
        ], required=["project_code", "project_name"])
        if d.result:
            try:
                total_budget = self.parse_float(d.result["total_budget"], "总预算")
                if total_budget < 0:
                    raise ValueError("总预算不能小于 0")
                t = now_text()
                self.db.execute("INSERT INTO planning_projects(project_code, project_name, customer_name, project_background, total_budget, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                                (d.result["project_code"], d.result["project_name"], d.result["customer_name"], d.result["project_background"], total_budget, t, t))
                self.db.log(self.current_user, "planning_project", None, "create", "", d.result, "新建规划项目")
                self.refresh_contexts()
                self.show_projects()
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))

    def show_plans(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        self.clear("年度计划")
        if self.can_action("plan"):
            ttk.Button(self.content, text="新建年度计划", command=self.add_plan, style="Primary.TButton").pack(anchor="w", pady=(0, 8))
        rows = self.db.query("SELECT id, plan_year, plan_name, annual_budget, status, updated_at FROM annual_plans WHERE project_id=? ORDER BY plan_year DESC, id DESC", (self.current_project_id(),))
        columns = [("id", "ID", 50), ("plan_year", "年度", 80), ("plan_name", "计划名称", 260)]
        if self.can_view_money():
            columns.append(("annual_budget", "年度预算", 110))
        columns += [("status", "状态", 90), ("updated_at", "更新时间", 150)]
        self.add_table(self.content, columns, rows)

    def add_plan(self):
        if not self.require_action("plan", "新建年度计划"):
            return
        if not self.current_project_id():
            messagebox.showwarning("提示", "请先新建项目")
            return
        d = FieldDialog(self, "新建年度计划", [
            ("plan_year", "年度", "text", None), ("plan_name", "计划名称", "text", None), ("annual_budget", "年度预算", "text", None),
            ("business_pain_points", "业务痛点", "memo", None), ("plan_description", "计划说明", "memo", None),
        ], required=["plan_year", "plan_name"])
        if d.result:
            try:
                plan_year = self.parse_int(d.result["plan_year"], "年度")
                if not 2000 <= plan_year <= 2100:
                    raise ValueError("年度必须在 2000 到 2100 之间")
                annual_budget = self.parse_float(d.result["annual_budget"], "年度预算")
                t = now_text()
                self.db.create_annual_plan({
                    "project_id": self.current_project_id(), "plan_year": plan_year,
                    "plan_name": d.result["plan_name"], "annual_budget": annual_budget,
                    "business_pain_points": d.result["business_pain_points"],
                    "plan_description": d.result["plan_description"],
                }, self.current_user, t)
                self.refresh_contexts()
                self.show_plans()
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))

    def show_versions(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        self.clear("版本管理")
        bar = self.make_action_bar(self.content)
        if self.can_action("version"):
            ttk.Button(bar, text="新建版本", command=self.add_version, style="Primary.TButton").pack(side=tk.LEFT)
            ttk.Button(bar, text="发布并生成基线", command=self.freeze_version).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bar, text="查看最新基线", command=self.show_version_baseline).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bar, text="跨版本比对", command=self.compare_versions).pack(side=tk.LEFT, padx=(8, 0))
        rows = self.db.query("SELECT id, version_code, version_name, version_budget, status, is_frozen, planned_start_date, planned_end_date FROM implementation_versions WHERE project_id=? AND annual_plan_id=? ORDER BY id DESC", (self.current_project_id(), self.current_plan_id()))
        columns = [("id", "ID", 50), ("version_code", "版本编号", 100), ("version_name", "版本名称", 220)]
        if self.can_view_money():
            columns.append(("version_budget", "版本预算", 100))
        columns += [("status", "状态", 90), ("is_frozen", "已冻结", 70), ("planned_start_date", "计划开始", 110), ("planned_end_date", "计划结束", 110)]
        self.add_table(self.content, columns, rows)

    def add_version(self):
        if not self.require_action("version", "新建版本"):
            return
        if not self.current_plan_id():
            messagebox.showwarning("提示", "请先新建年度计划")
            return
        d = FieldDialog(self, "新建落地版本", [
            ("version_code", "版本编号", "text", None), ("version_name", "版本名称", "text", None), ("version_budget", "版本预算", "text", None),
            ("planned_start_date", "计划开始日期", "text", None), ("planned_end_date", "计划结束日期", "text", None), ("version_goal", "版本目标", "memo", None), ("version_scope", "版本范围", "memo", None),
        ], required=["version_code", "version_name"])
        if d.result:
            try:
                version_budget = self.parse_float(d.result["version_budget"], "版本预算")
                planned_start = normalize_date(d.result["planned_start_date"], "计划开始日期")
                planned_end = normalize_date(d.result["planned_end_date"], "计划结束日期")
                if planned_start and planned_end and planned_end < planned_start:
                    raise ValueError("计划结束日期不能早于计划开始日期")
                t = now_text()
                self.db.create_implementation_version({
                    "project_id": self.current_project_id(), "annual_plan_id": self.current_plan_id(),
                    "version_code": d.result["version_code"], "version_name": d.result["version_name"],
                    "version_goal": d.result["version_goal"], "version_scope": d.result["version_scope"],
                    "version_budget": version_budget, "planned_start_date": planned_start,
                    "planned_end_date": planned_end,
                }, self.current_user, t)
                self.refresh_contexts()
                self.show_versions()
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))

    def freeze_version(self):
        if not self.require_action("version", "发布版本"):
            return
        version_id = self.current_version_id()
        if not version_id:
            messagebox.showwarning("提示", "请先选择版本")
            return
        version = self.db.one("SELECT * FROM implementation_versions WHERE id=?", (version_id,))
        if version["is_frozen"]:
            messagebox.showinfo("提示", "当前版本已经发布，并已生成基线。")
            return
        if not messagebox.askyesno("确认发布", "发布后自动生成版本基线，后续修改需走变更流程。是否继续？"):
            return
        try:
            result = self.db.freeze_version_with_baseline(version_id, self.current_user, now_text())
            if result is None:
                messagebox.showinfo("提示", "当前版本已经由其他操作发布，请刷新查看。")
            else:
                messagebox.showinfo("发布完成", f"版本已发布并自动生成基线 #{result['snapshot_no']}。")
            self.show_versions()
        except Exception as exc:
            messagebox.showerror("发布失败", str(exc))

    def show_version_baseline(self):
        version_id = self.current_version_id()
        version = self.db.one("SELECT project_id FROM implementation_versions WHERE id=?", (version_id,)) if version_id else None
        if not version or version["project_id"] != self.current_project_id() or not self.can_access_project_now(version["project_id"]):
            messagebox.showwarning("版本基线", "当前版本不可访问。")
            return
        baseline = self.db.one("SELECT * FROM version_baselines WHERE version_id=? ORDER BY snapshot_no DESC LIMIT 1", (version_id,)) if version_id else None
        if not baseline:
            messagebox.showinfo("版本基线", "当前版本尚未生成基线，请先冻结版本。")
            return
        rows = self.db.query("SELECT * FROM version_baseline_requirements WHERE baseline_id=? ORDER BY id", (baseline["id"],))
        self.clear("版本基线")
        metrics = [
            ("基线编号", f"#{baseline['snapshot_no']}", baseline["created_at"], self.colors["primary"]),
            ("需求数量", baseline["requirement_count"], "冻结时需求", self.colors["success"]),
        ]
        if self.can_view_money():
            metrics += [
                ("版本预算", money_text(baseline["version_budget"]), "冻结时预算", self.colors["warning"]),
                ("实际消耗", money_text(baseline["actual_cost"]), f"已分配 {money_text(baseline['allocated_budget'])}", None),
            ]
        if self.can_view_effort():
            metrics.append(("工时投入", f"{baseline['actual_hours'] or 0} 小时",
                            f"预估 {baseline['estimated_hours'] or 0} 小时", self.colors["primary"]))
        self.metric_grid(self.content, metrics, columns=min(4, max(1, len(metrics))))
        columns = [
            ("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 220),
            ("parent_requirement_code", "原需求", 120), ("business_key", "业务标识", 150),
            ("source_role", "来源", 90), ("proposer_name", "提出人", 100), ("owner_name", "负责人", 100),
            ("requirement_type", "类型", 100), ("tags", "标签", 150), ("status", "状态", 100),
            ("priority", "优先级", 80),
        ]
        if self.can_view_money():
            columns += [("allocated_budget", "分配预算", 110), ("actual_cost", "实际消耗", 110)]
        if self.can_view_effort():
            columns += [("estimated_hours", "预估工时", 90), ("actual_hours", "实际工时", 90)]
        columns += [("planned_finish_date", "预计完成", 110), ("actual_finish_date", "实际完成", 110),
                    ("created_at", "创建时间", 150), ("updated_at", "更新时间", 150),
                    ("requirement_description", "需求描述", 260), ("remark", "备注", 180)]
        self.section_title(self.content, "基线需求", "冻结时完整保留需求内容、投入、日期及原需求关系。")
        self.baseline_requirement_tree = self.add_table(self.content, columns, rows, 12)
        visibility = " AND visibility='客户可见'" if self.current_role.get() == "客户" else ""
        artifacts = self.db.query("""SELECT artifact_code, artifact_name, artifact_type, related_object_type,
                                            version_no, visibility, uploaded_by, uploaded_at
                                     FROM version_baseline_artifacts WHERE baseline_id=?""" + visibility + " ORDER BY id", (baseline["id"],))
        self.section_title(self.content, "基线成果物", "仅快照冻结时已经审批通过的版本及需求成果物。")
        self.baseline_artifact_tree = self.add_table(self.content, [
            ("artifact_code", "成果物编号", 130), ("artifact_name", "名称", 220),
            ("artifact_type", "类型", 110), ("related_object_type", "挂载对象", 90),
            ("version_no", "文件版本", 90), ("visibility", "可见范围", 90),
            ("uploaded_by", "上传人", 90), ("uploaded_at", "上传时间", 150),
        ], artifacts, 8)

    def compare_versions(self, selected_ids=None):
        project_id = self.current_project_id()
        if not project_id or not self.ensure_live_session(project_id):
            return
        versions = self.db.query("""SELECT v.id, a.plan_year, v.version_code, v.version_name
                                    FROM implementation_versions v
                                    INNER JOIN annual_plans a ON a.id=v.annual_plan_id
                                    WHERE v.project_id=?
                                    ORDER BY a.plan_year DESC, v.id DESC""", (project_id,))
        version_map = {
            f"{row['id']} - {row['plan_year']} | {row['version_code']} {row['version_name']}": row["id"]
            for row in versions
        }
        version_options = list(version_map.keys())
        if len(version_options) < 2:
            self.version_compare_ids = None
            self.clear("跨版本比对")
            version_count = len(version_options)
            self.notice_banner(
                self.content,
                f"当前项目共有 {version_count} 个版本，至少需要 2 个版本才能生成差异结果。",
                "warning",
            )
            self.section_title(
                self.content,
                "还缺少一个可比版本",
                "创建第二个落地版本后，可比较需求新增、移除、字段变化以及预算差额。",
            )
            actions = self.make_action_bar(self.content)
            if self.can_action("version") and self.current_plan_id():
                ttk.Button(actions, text="新建第二个版本", command=self.add_version,
                           style="Primary.TButton").pack(side=tk.LEFT)
            ttk.Button(actions, text="返回版本管理", command=self.show_versions).pack(side=tk.LEFT, padx=(8, 0))
            return
        if selected_ids and len(selected_ids) == 2:
            left_id, right_id = selected_ids
        else:
            d = FieldDialog(self, "跨版本比对", [
                ("left", "基准版本", "combo", version_options),
                ("right", "对比版本", "combo", version_options),
            ], {"left": version_options[0], "right": version_options[-1]}, required=["left", "right"])
            if not d.result:
                return
            left_id = version_map.get(d.result["left"])
            right_id = version_map.get(d.result["right"])
        if left_id == right_id:
            self.version_compare_ids = None
            messagebox.showwarning("提示", "请选择两个不同版本")
            return
        left = self.db.one("SELECT version_code, version_name, version_budget FROM implementation_versions WHERE id=? AND project_id=?", (left_id, project_id))
        right = self.db.one("SELECT version_code, version_name, version_budget FROM implementation_versions WHERE id=? AND project_id=?", (right_id, project_id))
        if not left or not right:
            self.version_compare_ids = None
            if selected_ids:
                self.show_versions()
            else:
                messagebox.showwarning("提示", "所选版本已不存在或不属于当前项目。")
            return
        self.version_compare_ids = (left_id, right_id)
        self.clear("跨版本比对")
        if self.can_view_money():
            row = ttk.Frame(self.content)
            row.pack(fill=tk.X)
            self.metric_card(row, "基准版本预算", money_text(left["version_budget"]), f"{left['version_code']} {left['version_name']}")
            self.metric_card(row, "对比版本预算", money_text(right["version_budget"]), f"{right['version_code']} {right['version_name']}")
            self.metric_card(row, "预算差额", money_text((right["version_budget"] or 0) - (left["version_budget"] or 0)), "对比版本 - 基准版本")
        include_money = self.can_view_money()
        include_effort = self.can_view_effort()
        compare_fields = list(REQUIREMENT_COMPARE_FIELDS)
        if include_money:
            compare_fields.extend(REQUIREMENT_COMPARE_MONEY_FIELDS)
        if include_effort:
            compare_fields.extend(REQUIREMENT_COMPARE_EFFORT_FIELDS)
        compare_columns = ["r.business_key"]
        for name, _label in compare_fields:
            if name == "parent_requirement_business_key":
                compare_columns.append("""COALESCE((SELECT COALESCE(NULLIF(TRIM(parent.business_key), ''),
                                                           TRIM(parent.requirement_name))
                                                   FROM requirements parent
                                                   WHERE parent.id=r.parent_requirement_id), '')
                                          AS parent_requirement_business_key""")
            else:
                compare_columns.append(f"r.{name}")
        compare_sql = f"SELECT {', '.join(compare_columns)} FROM requirements r WHERE r.is_deleted=0 AND r.version_id=?"
        left_rows = {requirement_business_key(r): r for r in self.db.query(compare_sql, (left_id,))}
        right_rows = {requirement_business_key(r): r for r in self.db.query(compare_sql, (right_id,))}
        diff_rows = []
        for key in sorted(set(left_rows) | set(right_rows)):
            l = left_rows.get(key)
            r = right_rows.get(key)
            if not l:
                diff_type = "新增"
                changes = ["新增需求"]
            elif not r:
                diff_type = "移除"
                changes = ["移除需求"]
            else:
                changes = requirement_compare_changes(l, r, include_money, include_effort)
                diff_type = "变更" if changes else "一致"
            diff_rows.append({
                "diff_type": diff_type,
                "changed_fields": "、".join(changes),
                "requirement_code": f"{l['requirement_code'] if l else '-'} / {r['requirement_code'] if r else '-'}",
                "left_name": l["requirement_name"] if l else "",
                "right_name": r["requirement_name"] if r else "",
                "left_status": l["status"] if l else "",
                "right_status": r["status"] if r else "",
                "left_budget": l["allocated_budget"] if l and include_money else "",
                "right_budget": r["allocated_budget"] if r and include_money else "",
                "left_hours": l["actual_hours"] if l and include_effort else "",
                "right_hours": r["actual_hours"] if r and include_effort else "",
            })
        self.section_title(self.content, "需求差异", "覆盖当前项目全部年度版本；变更字段按当前角色可见范围计算并列出。")
        columns = [("diff_type", "差异类型", 90), ("changed_fields", "变更字段", 220), ("requirement_code", "需求编号", 130), ("left_name", "基准版本需求", 220), ("right_name", "对比版本需求", 220), ("left_status", "基准状态", 100), ("right_status", "对比状态", 100)]
        if include_money:
            columns += [("left_budget", "基准预算", 100), ("right_budget", "对比预算", 100)]
        if include_effort:
            columns += [("left_hours", "基准工时", 90), ("right_hours", "对比工时", 90)]
        self.version_diff_tree = self.add_table(self.content, columns, diff_rows, 14)

    def show_requirements(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        self.clear("需求管理")
        version = self.current_version()
        if version and version["is_frozen"]:
            self.notice_banner(self.content, "当前版本已冻结并形成基线。需求核心信息不可直接修改，编辑或删除将自动转入变更申请。", "warning")
        bar = self.make_action_bar(self.content)
        primary_actions = ttk.Frame(bar)
        primary_actions.pack(fill=tk.X, pady=(0, 6))
        workflow_actions = ttk.Frame(bar)
        workflow_actions.pack(fill=tk.X)
        self.requirement_action_rows = (primary_actions, workflow_actions)
        if self.can_action("requirement_create"):
            ttk.Button(primary_actions, text="新建需求", command=self.add_requirement, style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(primary_actions, text="查看详情", command=self.show_requirement_detail).pack(side=tk.LEFT, padx=(8, 0))
        if self.can_action("requirement_edit"):
            ttk.Button(primary_actions, text="编辑需求", command=self.edit_requirement).pack(side=tk.LEFT, padx=(8, 0))
        if self.can_action("task_claim"):
            ttk.Button(workflow_actions, text="领取任务", command=self.claim_requirement).pack(side=tk.LEFT)
        if self.can_action("effort"):
            ttk.Button(workflow_actions, text="登记工时", command=self.add_effort).pack(side=tk.LEFT, padx=(8, 0))
        if self.can_action("status"):
            ttk.Button(workflow_actions, text="状态流转", command=self.advance_requirement_status).pack(side=tk.LEFT, padx=(8, 0))
        if self.can_action("requirement_assign"):
            ttk.Button(workflow_actions, text="分配到当前版本", command=self.assign_requirement_to_current_version).pack(side=tk.LEFT, padx=(8, 0))
        if self.can_action("requirement_delete"):
            ttk.Button(workflow_actions, text="删除需求", command=self.delete_requirement).pack(side=tk.LEFT, padx=(8, 0))
        if self.can_action("export"):
            ttk.Button(workflow_actions, text="导出需求清单", command=self.export_requirements).pack(side=tk.LEFT, padx=(8, 0))
        filters = ttk.Frame(self.content)
        filters.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(filters, text="范围").pack(side=tk.LEFT)
        scope_box = ttk.Combobox(filters, textvariable=self.requirement_scope, values=["当前版本", "待规划池", "当前项目全部"], state="readonly", width=14)
        scope_box.pack(side=tk.LEFT, padx=(6, 12))
        scope_box.bind("<<ComboboxSelected>>", lambda _e: self.run_guarded(self.show_requirements))
        ttk.Label(filters, text="状态").pack(side=tk.LEFT)
        status_box = ttk.Combobox(filters, textvariable=self.requirement_status_filter, values=["全部状态"] + STATUS_FLOW + EXTRA_STATUSES, state="readonly", width=14)
        status_box.pack(side=tk.LEFT, padx=(6, 12))
        status_box.bind("<<ComboboxSelected>>", lambda _e: self.run_guarded(self.show_requirements))
        role = self.current_role.get()
        cols = [("id", "ID", 50), ("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 230),
                ("parent_requirement_code", "原需求编号", 130), ("source_role", "来源", 90),
                ("requirement_type", "类型", 100), ("tags", "标签", 160), ("priority", "优先级", 70), ("status", "状态", 110)]
        if role not in SENSITIVE_ROLES:
            cols += [("estimated_budget", "预估预算", 100), ("allocated_budget", "分配预算", 100), ("actual_cost", "实际消耗", 100)]
        if self.can_view_effort():
            cols += [("estimated_hours", "预估工时", 90), ("actual_hours", "实际工时", 90)]
        cols += [("version_name", "所属版本", 160), ("owner_name", "负责人", 100), ("updated_at", "更新时间", 150)]
        where = ["r.is_deleted=0", "r.project_id=?"]
        params = [self.current_project_id()]
        if self.requirement_scope.get() == "当前版本":
            where.append("r.version_id=?")
            params.append(self.current_version_id())
        elif self.requirement_scope.get() == "待规划池":
            where.append("r.version_id IS NULL")
        if self.requirement_status_filter.get() != "全部状态":
            where.append("r.status=?")
            params.append(self.requirement_status_filter.get())
        rows = self.db.query(f"""SELECT r.*, parent.requirement_code parent_requirement_code,
                                         COALESCE(v.version_code || ' ' || v.version_name, '待规划') version_name
                                 FROM requirements r
                                 LEFT JOIN implementation_versions v ON r.version_id=v.id
                                 LEFT JOIN requirements parent ON parent.id=r.parent_requirement_id
                                 WHERE {' AND '.join(where)}
                                 ORDER BY r.id DESC""", tuple(params)) if self.current_project_id() else []
        self.req_tree = self.add_table(self.content, cols, rows, on_double_click=self.show_requirement_detail)

    def add_requirement(self):
        if not self.require_action("requirement_create", "新建需求"):
            return
        if not self.current_project_id():
            messagebox.showwarning("提示", "请先选择项目")
            return
        code = "REQ-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
        role = self.current_role.get()
        source_role = {"研发人员": "研发", "运营人员": "运营", "管理员": "咨询负责人"}.get(role, role)
        version_options = ["待规划池"] if role == "客户" else ["待规划池", *self.versions.keys()]
        parent_options = self.parent_requirement_options()
        fields = [
            ("requirement_code", "需求编号", "text", None), ("requirement_name", "需求名称", "text", None),
            ("source_role", "来源角色", "readonly" if role == "客户" else "combo", ["客户", "销售", "项目经理", "研发", "运营", "咨询负责人"]),
            ("proposer_name", "提出人", "readonly" if role == "客户" else "text", None),
            ("parent_requirement_option", "关联原需求", "combo", list(parent_options.keys())),
        ]
        if role != "客户":
            fields += [("business_key", "业务需求标识", "text", None), ("owner_name", "负责人", "text", None)]
        fields += [
            ("requirement_type", "需求类型", "combo", ["业务痛点", "功能优化", "运维 Bug", "招投标要求", "验收整改", "客户新增"]),
            ("version_option", "所属版本", "combo", version_options), ("tags", "标签（可多选）", "multiselect", self.tag_options()),
            ("priority", "优先级", "combo", ["P0", "P1", "P2", "高", "中", "低"]),
        ]
        if self.can_view_effort():
            fields.append(("estimated_hours", "预估工时", "text", None))
        if self.can_view_money():
            fields.append(("estimated_budget", "预估预算", "text", None))
        fields += [("planned_finish_date", "预计完成时间", "text", None),
                   ("requirement_description", "需求描述", "memo", None), ("remark", "备注", "memo", None)]
        d = FieldDialog(self, "新建需求", fields,
                         {"requirement_code": code, "priority": "P1", "source_role": source_role,
                          "proposer_name": self.current_user, "version_option": version_options[0],
                          "parent_requirement_option": "不关联原需求"},
                        required=["requirement_code", "requirement_name", "requirement_description", "source_role"])
        if d.result:
            try:
                version_id = None if d.result["version_option"] == "待规划池" else self.versions.get(d.result["version_option"])
                plan_id = self.current_plan_id() if version_id else None
                selected_version = self.db.one("SELECT is_frozen FROM implementation_versions WHERE id=?", (version_id,)) if version_id else None
                if selected_version and selected_version["is_frozen"]:
                    messagebox.showwarning("版本已冻结", "目标版本已冻结，请先走变更流程。")
                    return
                estimated_budget = self.parse_float(d.result.get("estimated_budget", 0), "预估预算")
                estimated_hours = self.parse_float(d.result.get("estimated_hours", 0), "预估工时")
                planned_finish = normalize_date(d.result["planned_finish_date"], "预计完成时间")
                if estimated_budget < 0 or estimated_hours < 0:
                    raise ValueError("预估预算和预估工时不能小于 0")
                allocated_budget = 0
                actual_cost = 0
                parent_id = self.validate_parent_requirement(
                    parent_options.get(d.result.get("parent_requirement_option")), self.current_project_id()
                )
                t = now_text()
                record = {
                    **d.result,
                    "project_id": self.current_project_id(), "annual_plan_id": plan_id, "version_id": version_id,
                    "estimated_budget": estimated_budget, "allocated_budget": allocated_budget, "actual_cost": actual_cost,
                    "business_key": business_key_text(d.result.get("business_key") or d.result["requirement_name"]),
                    "source_role": source_role if role == "客户" else d.result["source_role"],
                    "proposer_name": self.current_user if role == "客户" else d.result["proposer_name"],
                    "owner_name": d.result.get("owner_name", ""), "status": "草稿", "estimated_hours": estimated_hours,
                    "parent_requirement_id": parent_id,
                    "planned_finish_date": planned_finish, "created_at": t, "updated_at": t,
                }
                self.db.create_requirement(record, self.current_user, t)
                self.show_requirements()
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))

    def selected_requirement_id(self):
        sel = getattr(self, "req_tree", None).selection() if hasattr(self, "req_tree") else ()
        if not sel:
            messagebox.showwarning("提示", "请先在需求表中选择一行")
            return None
        value = self.req_tree.item(sel[0])["values"][0]
        try:
            requirement_id = int(value)
        except (TypeError, ValueError):
            messagebox.showwarning("提示", "当前行不是有效需求")
            return None
        row = self.db.one("SELECT id FROM requirements WHERE id=? AND project_id=? AND is_deleted=0",
                          (requirement_id, self.current_project_id()))
        return requirement_id if row and self.can_access_project_now(self.current_project_id()) else None

    def claim_requirement(self):
        if not self.require_action("task_claim", "领取研发任务"):
            return
        req_id = self.selected_requirement_id()
        if not req_id:
            return
        try:
            self.db.claim_requirement(req_id, self.current_user, now_text())
            self.show_requirements()
        except Exception as exc:
            messagebox.showerror("领取失败", str(exc))

    def add_effort(self):
        if not self.require_action("effort", "登记工时"):
            return
        req_id = self.selected_requirement_id()
        if not req_id:
            return
        d = FieldDialog(self, "登记任务工时", [
            ("hours", "本次工时", "text", None), ("work_date", "工作日期", "text", None),
            ("description", "工作说明", "memo", None),
        ], {"work_date": datetime.now().strftime("%Y-%m-%d")}, required=["hours", "work_date", "description"])
        if not d.result:
            return
        try:
            self.db.record_effort(req_id, self.current_user, self.parse_float(d.result["hours"], "工时"),
                                  d.result["work_date"], d.result["description"], now_text())
            self.show_requirements()
        except Exception as exc:
            messagebox.showerror("登记失败", str(exc))

    def assign_requirement_to_current_version(self):
        if not self.require_action("requirement_assign", "分配需求到版本"):
            return
        req_id = self.selected_requirement_id()
        version_id = self.current_version_id()
        if not req_id or not version_id:
            return
        req = self.db.one("SELECT version_id FROM requirements WHERE id=?", (req_id,))
        source_version = self.db.one("SELECT is_frozen FROM implementation_versions WHERE id=?", (req["version_id"],)) if req and req["version_id"] else None
        if source_version and source_version["is_frozen"] and req["version_id"] != version_id:
            messagebox.showwarning("源版本已冻结", "不能把需求直接移出已冻结版本，请提交变更申请。")
            return
        version = self.current_version()
        if version and version["is_frozen"]:
            messagebox.showwarning("版本已冻结", "当前版本已冻结，请先走变更流程。")
            return
        try:
            self.db.assign_requirement(req_id, version_id, self.current_plan_id(), self.current_user, now_text())
        except Exception as exc:
            messagebox.showerror("分配失败", str(exc))
            return
        self.requirement_scope.set("当前版本")
        self.show_requirements()

    def show_requirement_detail(self):
        req_id = self.selected_requirement_id()
        if not req_id:
            return
        self.open_requirement_detail(req_id)

    def open_requirement_detail(self, req_id):
        req = self.db.one("""SELECT r.*, p.project_name, ap.plan_name, parent.requirement_code parent_requirement_code,
                                    COALESCE(v.version_code || ' ' || v.version_name, '待规划') version_name
                             FROM requirements r
                             LEFT JOIN planning_projects p ON r.project_id=p.id
                             LEFT JOIN annual_plans ap ON r.annual_plan_id=ap.id
                             LEFT JOIN implementation_versions v ON r.version_id=v.id
                             LEFT JOIN requirements parent ON parent.id=r.parent_requirement_id
                             WHERE r.id=?""", (req_id,))
        if not req or req["project_id"] != self.current_project_id() or not self.can_access_project_now(req["project_id"]):
            messagebox.showwarning("提示", "未找到需求详情")
            return
        flows = self.db.query("SELECT flow_type, amount, description, occurred_at FROM budget_flows WHERE requirement_id=? ORDER BY occurred_at DESC LIMIT 8", (req_id,)) if self.can_view_money() else []
        artifact_visibility = " AND visibility='客户可见'" if self.current_role.get() == "客户" else ""
        artifacts = self.db.query(f"""SELECT artifact_type, artifact_name, version_no, uploaded_at FROM artifacts
                                      WHERE related_object_type='需求' AND related_object_id=? AND approval_status='approved'
                                      {artifact_visibility} ORDER BY uploaded_at DESC LIMIT 8""", (req_id,))
        history = self.db.query("SELECT from_status, to_status, operator_name, transition_note, changed_at FROM requirement_status_history WHERE requirement_id=? ORDER BY id DESC LIMIT 12", (req_id,))
        efforts = self.db.query("SELECT contributor_name, hours, work_date, description FROM task_effort_entries WHERE requirement_id=? ORDER BY work_date DESC, id DESC LIMIT 12", (req_id,)) if self.can_view_effort() else []
        sections = [
            ("基础信息", [
                ("需求编号", req["requirement_code"]), ("业务需求标识", req["business_key"]), ("需求名称", req["requirement_name"]), ("项目", req["project_name"]),
                ("年度计划", req["plan_name"]), ("所属版本", req["version_name"]), ("来源角色", req["source_role"]),
                ("提出人", req["proposer_name"]), ("对接人", req["owner_name"]), ("类型", req["requirement_type"]),
                ("标签", req["tags"]), ("优先级", req["priority"]), ("状态", req["status"]),
                ("原需求编号", req["parent_requirement_code"] or "未关联"),
            ]),
            ("计划信息", [("预计完成", req["planned_finish_date"]), ("实际完成", req["actual_finish_date"])]),
            ("需求描述", [("描述", req["requirement_description"]), ("备注", req["remark"])]),
            ("状态历史", [(h["changed_at"], f"{h['from_status'] or '创建'} -> {h['to_status']} / {h['operator_name'] or ''} / {h['transition_note'] or ''}") for h in history] or [("暂无", "")]),
            ("关联成果物", [(f"{a['artifact_type']} {a['version_no'] or ''}", f"{a['artifact_name']} {a['uploaded_at']}") for a in artifacts] or [("暂无", "")]),
        ]
        if self.can_view_effort():
            sections.insert(1, ("工时投入", [
                ("预估工时", req["estimated_hours"]), ("实际工时", req["actual_hours"]),
            ]))
            sections.insert(-1, ("工时明细", [
                (f"{e['work_date']} {e['contributor_name']}", f"{e['hours']} 小时 / {e['description'] or ''}") for e in efforts
            ] or [("暂无", "")]))
        if self.can_view_money():
            sections.insert(1, ("资金信息", [
                ("预估预算", money_text(req["estimated_budget"])), ("分配预算", money_text(req["allocated_budget"])),
                ("实际消耗", money_text(req["actual_cost"])),
            ]))
            sections.insert(-1, ("最近资金流水", [(f"{f['occurred_at']} {f['flow_type']}", f"{money_text(f['amount'])} {f['description']}") for f in flows] or [("暂无", "")]))
        DetailDialog(self, f"需求详情 - {req['requirement_code']}", sections)

    def edit_requirement(self):
        if not self.require_action("requirement_edit", "编辑需求"):
            return
        req_id = self.selected_requirement_id()
        if not req_id:
            return
        req = self.db.one("SELECT * FROM requirements WHERE id=? AND project_id=?", (req_id, self.current_project_id()))
        if not req:
            return
        version = self.db.one("SELECT is_frozen FROM implementation_versions WHERE id=?", (req["version_id"],)) if req["version_id"] else None
        parent_options = self.parent_requirement_options(exclude_id=req_id)
        parent_initial = next((label for label, value in parent_options.items() if value == req["parent_requirement_id"]), "不关联原需求")
        edit_fields = [
            ("requirement_name", "需求名称", "text", None), ("business_key", "业务需求标识", "text", None),
            ("source_role", "来源角色", "readonly" if self.current_role.get() in {"研发人员", "运营人员"} else "combo", ["客户", "销售", "项目经理", "研发", "运营", "咨询负责人"]),
            ("proposer_name", "提出人", "readonly" if self.current_role.get() in {"研发人员", "运营人员"} else "text", None), ("owner_name", "对接人", "text", None), ("requirement_type", "需求类型", "combo", ["业务痛点", "功能优化", "运维 Bug", "招投标要求", "验收整改", "客户新增"]),
            ("tags", "标签（可多选）", "multiselect", self.tag_options()), ("priority", "优先级", "combo", ["P0", "P1", "P2", "高", "中", "低"]),
            ("parent_requirement_option", "关联原需求", "combo", list(parent_options.keys())),
        ]
        if self.can_view_effort():
            edit_fields.append(("estimated_hours", "预估工时", "text", None))
        if self.can_view_money():
            edit_fields.append(("estimated_budget", "预估预算", "text", None))
        edit_fields += [("planned_finish_date", "预计完成时间", "text", None),
                        ("requirement_description", "需求描述", "memo", None), ("remark", "备注", "memo", None)]
        initial = dict(req)
        initial["parent_requirement_option"] = parent_initial
        d = FieldDialog(self, "编辑需求", edit_fields, initial, required=["requirement_name", "requirement_description", "source_role"])
        if not d.result:
            return
        try:
            estimated_budget = self.parse_float(d.result.get("estimated_budget", req["estimated_budget"]), "预估预算")
            estimated_hours = self.parse_float(d.result.get("estimated_hours", req["estimated_hours"]), "预估工时")
            planned_finish = normalize_date(d.result["planned_finish_date"], "预计完成时间")
            if estimated_budget < 0 or estimated_hours < 0:
                raise ValueError("预估预算和预估工时不能小于 0")
            parent_id = self.validate_parent_requirement(
                parent_options.get(d.result.get("parent_requirement_option")), req["project_id"], req_id
            )
            if version and version["is_frozen"]:
                proposed = dict(d.result)
                proposed["estimated_budget"] = estimated_budget
                proposed["estimated_hours"] = estimated_hours
                proposed["planned_finish_date"] = planned_finish
                proposed["parent_requirement_id"] = parent_id
                self.create_change_request(req, "update", proposed)
                return
            record = {**d.result, "estimated_budget": estimated_budget, "estimated_hours": estimated_hours,
                      "business_key": business_key_text(d.result["business_key"] or d.result["requirement_name"]),
                      "planned_finish_date": planned_finish, "parent_requirement_id": parent_id}
            self.db.update_requirement(req_id, record, self.current_user, now_text())
            self.show_requirements()
        except ValueError as exc:
            if str(exc) == "VERSION_FROZEN":
                proposed = {**d.result, "estimated_budget": estimated_budget, "estimated_hours": estimated_hours,
                            "business_key": business_key_text(d.result["business_key"] or d.result["requirement_name"]),
                            "planned_finish_date": planned_finish, "parent_requirement_id": parent_id}
                self.create_change_request(req, "update", proposed)
            else:
                messagebox.showerror("保存失败", str(exc))

    def create_change_request(self, req, change_type="update", proposed=None):
        d = FieldDialog(self, "冻结版本变更申请", [
            ("change_title", "变更标题", "text", None),
            ("change_reason", "变更原因", "memo", None),
            ("impact_scope", "影响范围", "memo", None),
        ], {"change_title": f"{'删除' if change_type == 'delete' else '调整'}需求：{req['requirement_code']} {req['requirement_name']}"}, required=["change_title", "change_reason"])
        if not d.result:
            return
        try:
            self.db.create_change_request_record(req["id"], change_type, proposed, d.result, self.current_user, now_text())
            messagebox.showinfo("已提交", "当前版本已冻结，变更申请已提交到系统设置中的变更申请列表。")
        except Exception as exc:
            messagebox.showerror("提交失败", str(exc))

    def delete_requirement(self):
        if not self.require_action("requirement_delete", "删除需求"):
            return
        req_id = self.selected_requirement_id()
        if not req_id:
            return
        req = self.db.one("SELECT * FROM requirements WHERE id=?", (req_id,))
        version = self.db.one("SELECT is_frozen FROM implementation_versions WHERE id=?", (req["version_id"],)) if req["version_id"] else None
        if version and version["is_frozen"]:
            messagebox.showwarning("版本已冻结", "冻结版本内需求不能直接删除，请提交变更申请。")
            self.create_change_request(req, "delete", {})
            return
        if not messagebox.askyesno("确认删除", f"确定删除需求 {req['requirement_code']}？该操作为软删除，可在数据库日志中追溯。"):
            return
        try:
            self.db.soft_delete_requirement(req_id, self.current_user, now_text())
            self.show_requirements()
        except ValueError as exc:
            if str(exc) == "VERSION_FROZEN":
                self.create_change_request(req, "delete", {})
            else:
                messagebox.showerror("删除失败", str(exc))

    def advance_requirement_status(self):
        if not self.require_action("status", "需求状态流转"):
            return
        req_id = self.selected_requirement_id()
        if not req_id:
            return
        req = self.db.one("SELECT * FROM requirements WHERE id=?", (req_id,))
        targets = STATUS_TRANSITIONS.get(req["status"], [])
        if not targets:
            messagebox.showinfo("状态流转", f"状态“{req['status']}”当前没有可执行的后续流转。")
            return
        d = FieldDialog(self, "状态流转", [("status", "目标状态", "combo", targets), ("remark", "流转说明", "memo", None)], {"status": targets[0]}, required=["status", "remark"])
        if d.result:
            before = req["status"]
            after = d.result["status"]
            if after not in targets:
                messagebox.showerror("非法流转", f"不允许从“{before}”直接流转到“{after}”。")
                return
            t = now_text()
            try:
                changed = self.db.transition_requirement_status(req_id, before, after, d.result["remark"], self.current_user, t)
            except Exception as exc:
                messagebox.showerror("状态流转失败", str(exc))
                return
            if not changed:
                messagebox.showwarning("状态冲突", "需求状态已被其他操作更新，请刷新后重试。")
                return
            self.show_requirements()

    def show_budget(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        if not self.can_view_money():
            messagebox.showwarning("权限不足", "当前角色无权查看项目资金和成本明细。")
            self.show_dashboard()
            return
        self.clear("资金管理")
        bar = self.make_action_bar(self.content)
        if self.can_action("budget"):
            ttk.Button(bar, text="登记资金流水", command=self.add_budget_flow, style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(bar, text="导出资金明细", command=self.export_budget).pack(side=tk.LEFT, padx=(8, 0))
        project_id = self.current_project_id()
        version_id = self.current_version_id()
        p = self.db.one("SELECT total_budget FROM planning_projects WHERE id=?", (project_id,)) if project_id else {"total_budget": 0}
        a = self.db.one("SELECT annual_budget FROM annual_plans WHERE id=?", (self.current_plan_id(),)) if self.current_plan_id() else {"annual_budget": 0}
        v = self.db.one("SELECT version_budget FROM implementation_versions WHERE id=?", (version_id,)) if version_id else {"version_budget": 0}
        r = self.db.one("SELECT SUM(allocated_budget) allocated, SUM(actual_cost) cost FROM requirements WHERE version_id=? AND is_deleted=0", (version_id,)) if version_id else {"allocated": 0, "cost": 0}
        self.metric_grid(self.content, [
            ("项目总预算", money_text(p["total_budget"] or 0), "宏观规划", self.colors["primary"]),
            ("年度预算", money_text(a["annual_budget"] or 0), "年度拆分", self.colors["success"]),
            ("版本预算", money_text(v["version_budget"] or 0), "版本隔离", self.colors["warning"]),
            ("需求已分配", money_text(r["allocated"] or 0), f"占版本预算 {percent_text(r['allocated'], v['version_budget'] if v else 0)}", None),
            ("实际消耗", money_text(r["cost"] or 0), f"执行率 {percent_text(r['cost'], r['allocated'])}", self.colors["danger"] if (r["allocated"] or 0) and budget_limit_exceeded(r["cost"], r["allocated"]) else self.colors["success"]),
        ], columns=2)
        version_budget = float(v["version_budget"] or 0) if v else 0
        allocated = float(r["allocated"] or 0)
        actual_cost = float(r["cost"] or 0)
        if version_budget and budget_limit_exceeded(allocated, version_budget):
            self.notice_banner(self.content, f"预算超支：需求已分配 {money_text(allocated)}，超过版本预算 {money_text(version_budget)}。", "danger")
        elif allocated and budget_limit_exceeded(actual_cost, allocated):
            self.notice_banner(self.content, f"执行超支：实际消耗 {money_text(actual_cost)}，超过已分配预算 {money_text(allocated)}。", "danger")
        elif version_budget:
            self.notice_banner(self.content, f"预算状态正常，版本剩余可分配预算 {money_text(version_budget - allocated)}。", "success")
        self.section_title(self.content, "项目资金全景树", "展示当前项目全部年度、版本和需求；双击年度、版本或需求可钻取。")
        self.draw_budget_flow(project_id, self.current_plan_id(), version_id)
        self.section_title(self.content, "资金流水", "流水可关联到版本或具体需求，用于后续追溯预算调整与实际消耗。")
        rows = self.db.query("""SELECT b.flow_code, b.flow_type, b.amount, COALESCE(r.requirement_code, '未关联') requirement_code,
                                       b.description, b.operator_name, b.occurred_at
                                FROM budget_flows b
                                LEFT JOIN requirements r ON b.requirement_id=r.id
                                WHERE b.project_id=? AND (? IS NULL OR b.version_id=? OR b.version_id IS NULL)
                                ORDER BY b.occurred_at DESC""", (project_id, version_id, version_id)) if project_id else []
        self.add_table(self.content, [("flow_code", "流水编号", 140), ("flow_type", "类型", 100), ("amount", "金额", 100), ("requirement_code", "关联需求", 130), ("description", "说明", 280), ("operator_name", "操作人", 100), ("occurred_at", "发生时间", 150)], rows, 10)

    def draw_budget_flow(self, project_id, plan_id, version_id):
        frame = ttk.Frame(self.content, style="Surface.TFrame", padding=12)
        frame.pack(fill=tk.X, pady=(0, 10))
        canvas = tk.Canvas(frame, height=260, bg=self.colors["surface"], highlightthickness=0)
        canvas.pack(fill=tk.X, expand=True)
        self.budget_flow_canvas = canvas
        project = self.db.one("""SELECT p.project_name, p.total_budget,
                                         COALESCE(SUM(r.allocated_budget),0) allocated_budget,
                                         COALESCE(SUM(r.actual_cost),0) actual_cost
                                  FROM planning_projects p
                                  LEFT JOIN requirements r ON r.project_id=p.id AND r.is_deleted=0
                                  WHERE p.id=? GROUP BY p.id, p.project_name, p.total_budget""", (project_id,)) if project_id else None
        plan_rows = self.db.query("""SELECT a.id, a.plan_year, a.plan_name, a.annual_budget,
                                            COALESCE(SUM(r.allocated_budget),0) allocated_budget,
                                            COALESCE(SUM(r.actual_cost),0) actual_cost
                                     FROM annual_plans a
                                     LEFT JOIN requirements r ON r.annual_plan_id=a.id AND r.is_deleted=0
                                     WHERE a.project_id=?
                                     GROUP BY a.id, a.plan_year, a.plan_name, a.annual_budget
                                     ORDER BY a.plan_year, a.id""", (project_id,)) if project_id else []
        version_rows = self.db.query("""SELECT v.id, v.annual_plan_id, v.version_code, v.version_name, v.version_budget,
                                               COALESCE(SUM(r.allocated_budget),0) allocated_budget,
                                               COALESCE(SUM(r.actual_cost),0) actual_cost
                                        FROM implementation_versions v
                                        LEFT JOIN requirements r ON r.version_id=v.id AND r.is_deleted=0
                                        WHERE v.project_id=?
                                        GROUP BY v.id, v.annual_plan_id, v.version_code, v.version_name, v.version_budget
                                        ORDER BY v.id""", (project_id,)) if project_id else []
        requirements = self.db.query("""SELECT id, annual_plan_id, version_id, requirement_code, requirement_name,
                                                allocated_budget, actual_cost
                                         FROM requirements WHERE project_id=? AND is_deleted=0
                                         ORDER BY version_id, allocated_budget DESC, id""", (project_id,)) if project_id else []
        versions_by_plan = {}
        for row in version_rows:
            versions_by_plan.setdefault(row["annual_plan_id"], []).append(row)
        requirements_by_version = {}
        unplanned_by_plan = {}
        for row in requirements:
            if row["version_id"] is None:
                unplanned_by_plan.setdefault(row["annual_plan_id"], []).append(row)
            else:
                requirements_by_version.setdefault(row["version_id"], []).append(row)

        display_plans = list(plan_rows)
        if unplanned_by_plan.get(None):
            display_plans.append({
                "id": None, "plan_year": "--", "plan_name": "待规划池", "annual_budget": 0,
                "allocated_budget": sum(float(row["allocated_budget"] or 0) for row in unplanned_by_plan[None]),
                "actual_cost": sum(float(row["actual_cost"] or 0) for row in unplanned_by_plan[None]),
            })
        node_height = 72
        node_gap = 8
        leaf_count = 0
        for plan_row in display_plans:
            versions = versions_by_plan.get(plan_row["id"], [])
            count = sum(max(1, len(requirements_by_version.get(version_row["id"], []))) for version_row in versions)
            if unplanned_by_plan.get(plan_row["id"]):
                count += len(unplanned_by_plan[plan_row["id"]])
            leaf_count += max(1, count)
        leaf_count = max(1, leaf_count)
        canvas_height = max(260, leaf_count * (node_height + node_gap) - node_gap + 24)
        canvas.configure(height=canvas_height)

        def render(event=None):
            if not canvas.winfo_exists():
                return
            width = int(event.width if event is not None else canvas.winfo_width())
            if width <= 1:
                return
            canvas.delete("all")
            margin = max(8, min(14, width * 0.02))
            gap = max(12, min(38, width * 0.035))
            node_width = (width - margin * 2 - gap * 3) / 4
            if node_width < 20:
                margin, gap = 2, 3
                node_width = max(1, (width - margin * 2 - gap * 3) / 4)
            x_positions = [margin + index * (node_width + gap) for index in range(4)]
            title_font = ("Microsoft YaHei UI", 9, "bold")
            body_font = ("Microsoft YaHei UI", 9)
            small_font = ("Microsoft YaHei UI", 8)
            measure_fonts = {
                title_font: tkfont.Font(root=canvas, font=title_font),
                body_font: tkfont.Font(root=canvas, font=body_font),
                small_font: tkfont.Font(root=canvas, font=small_font),
            }

            def clipped(text, pixel_width, font_spec):
                value = str(text or "")
                font = measure_fonts[font_spec]
                available = max(1, int(pixel_width) - 24)
                if font.measure(value) <= available:
                    return value
                ellipsis = "..."
                low, high = 0, len(value)
                while low < high:
                    middle = (low + high + 1) // 2
                    if font.measure(value[:middle] + ellipsis) <= available:
                        low = middle
                    else:
                        high = middle - 1
                return value[:low] + ellipsis

            def risk_color(budget, allocated, actual):
                budget = float(budget or 0)
                allocated = float(allocated or 0)
                actual = float(actual or 0)
                if budget_limit_exceeded(actual, budget) or budget_limit_exceeded(allocated, budget):
                    return "#FDE9E7"
                if budget <= 0:
                    return self.colors["surface_alt"]
                return "#E5F3EA"

            def node(x, y, title, detail, color, tag=None, second_detail=None, selected=False):
                base_tags = (tag,) if tag else ()
                canvas.create_rectangle(x, y, x + node_width, y + node_height, fill=color,
                                        outline=self.colors["primary"] if selected else self.colors["line"],
                                        width=2 if selected else 1, tags=base_tags)
                canvas.create_text(x + 10, y + 15, text=clipped(title, node_width, title_font), anchor="w",
                                   fill=self.colors["text"], font=title_font, tags=base_tags)
                if second_detail is None:
                    canvas.create_text(x + 10, y + 48, text=clipped(detail, node_width, body_font), anchor="w",
                                       fill=self.colors["muted"], font=body_font, tags=base_tags)
                else:
                    canvas.create_text(x + 10, y + 38, text=clipped(detail, node_width, small_font), anchor="w",
                                       fill=self.colors["muted"], font=small_font, tags=base_tags)
                    canvas.create_text(x + 10, y + 57, text=clipped(second_detail, node_width, small_font), anchor="w",
                                       fill=self.colors["muted"], font=small_font, tags=base_tags)

            cursor_y = 12
            plan_layouts = []
            for plan_row in display_plans:
                version_layouts = []
                versions = versions_by_plan.get(plan_row["id"], [])
                for version_row in versions:
                    reqs = requirements_by_version.get(version_row["id"], [])
                    req_layouts = []
                    for req in reqs or [None]:
                        req_layouts.append((req, cursor_y))
                        cursor_y += node_height + node_gap
                    version_y = sum(y for _req, y in req_layouts) / len(req_layouts)
                    version_layouts.append((version_row, version_y, req_layouts))
                unplanned = unplanned_by_plan.get(plan_row["id"], [])
                if unplanned:
                    req_layouts = []
                    for req in unplanned:
                        req_layouts.append((req, cursor_y))
                        cursor_y += node_height + node_gap
                    version_layouts.append((None, sum(y for _req, y in req_layouts) / len(req_layouts), req_layouts))
                if not version_layouts:
                    version_layouts.append((None, cursor_y, [(None, cursor_y)]))
                    cursor_y += node_height + node_gap
                plan_y = sum(version_y for _version, version_y, _reqs in version_layouts) / len(version_layouts)
                plan_layouts.append((plan_row, plan_y, version_layouts))
            if not plan_layouts:
                middle = (canvas_height - node_height) / 2
                plan_layouts = [(None, middle, [(None, middle, [(None, middle)])])]
            project_y = sum(y for _plan, y, _versions in plan_layouts) / len(plan_layouts)

            for _plan_row, plan_y, version_layouts in plan_layouts:
                canvas.create_line(x_positions[0] + node_width, project_y + node_height / 2,
                                   x_positions[1], plan_y + node_height / 2, arrow=tk.LAST, fill=self.colors["line"], width=2)
                for _version_row, version_y, req_layouts in version_layouts:
                    canvas.create_line(x_positions[1] + node_width, plan_y + node_height / 2,
                                       x_positions[2], version_y + node_height / 2, arrow=tk.LAST, fill=self.colors["line"], width=1)
                    for _req, req_y in req_layouts:
                        canvas.create_line(x_positions[2] + node_width, version_y + node_height / 2,
                                           x_positions[3], req_y + node_height / 2, arrow=tk.LAST, fill=self.colors["line"], width=1)

            project_budget = project["total_budget"] if project else 0
            project_allocated = project["allocated_budget"] if project else 0
            project_actual = project["actual_cost"] if project else 0
            annual_budget_total = sum(float(row["annual_budget"] or 0) for row in plan_rows)
            node(x_positions[0], project_y, project["project_name"] if project else "未选择项目",
                 f"预算 {money_text(project_budget)}",
                 risk_color(project_budget, max(project_allocated, annual_budget_total), project_actual),
                 second_detail=f"实际 {money_text(project_actual)}")
            for plan_row, plan_y, version_layouts in plan_layouts:
                if plan_row is None:
                    node(x_positions[1], plan_y, "暂无年度计划", "预算 0.00", self.colors["surface_alt"], second_detail="实际 0.00")
                else:
                    plan_tag = f"plan_node_{plan_row['id']}" if plan_row["id"] is not None else None
                    version_budget_total = sum(float(v["version_budget"] or 0) for v, _y, _reqs in version_layouts if v)
                    node(x_positions[1], plan_y, f"{plan_row['plan_year']} {plan_row['plan_name']}",
                         f"预算 {money_text(plan_row['annual_budget'])}",
                         risk_color(plan_row["annual_budget"], max(plan_row["allocated_budget"], version_budget_total), plan_row["actual_cost"]),
                         tag=plan_tag, second_detail=f"实际 {money_text(plan_row['actual_cost'])}", selected=plan_row["id"] == plan_id)
                    if plan_tag:
                        canvas.tag_bind(plan_tag, "<Enter>", lambda _event: canvas.configure(cursor="hand2"))
                        canvas.tag_bind(plan_tag, "<Leave>", lambda _event: canvas.configure(cursor=""))
                        canvas.tag_bind(plan_tag, "<Double-Button-1>", lambda _event, pid=plan_row["id"]: self.drill_budget_plan(pid))
                for version_row, version_y, req_layouts in version_layouts:
                    if version_row is None:
                        title = "待规划需求" if any(req is not None for req, _y in req_layouts) else "暂无落地版本"
                        node(x_positions[2], version_y, title, "预算 0.00", self.colors["surface_alt"], second_detail="尚未分配版本")
                    else:
                        version_tag = f"version_node_{version_row['id']}"
                        node(x_positions[2], version_y, f"{version_row['version_code']} {version_row['version_name']}",
                             f"预算 {money_text(version_row['version_budget'])}",
                             risk_color(version_row["version_budget"], version_row["allocated_budget"], version_row["actual_cost"]),
                             tag=version_tag, second_detail=f"实际 {money_text(version_row['actual_cost'])}", selected=version_row["id"] == version_id)
                        canvas.tag_bind(version_tag, "<Enter>", lambda _event: canvas.configure(cursor="hand2"))
                        canvas.tag_bind(version_tag, "<Leave>", lambda _event: canvas.configure(cursor=""))
                        canvas.tag_bind(version_tag, "<Double-Button-1>",
                                        lambda _event, pid=version_row["annual_plan_id"], vid=version_row["id"]: self.drill_budget_version(pid, vid))
                    for req, req_y in req_layouts:
                        if req is None:
                            node(x_positions[3], req_y, "暂无版本需求", "分配 0.00", self.colors["surface_alt"], second_detail="实际 0.00")
                            continue
                        req_tag = f"req_node_{req['id']}"
                        node(x_positions[3], req_y, f"{req['requirement_code']} {req['requirement_name']}",
                             f"分配 {money_text(req['allocated_budget'])}",
                             risk_color(req["allocated_budget"], req["allocated_budget"], req["actual_cost"]),
                             tag=req_tag, second_detail=f"实际 {money_text(req['actual_cost'])}")
                        canvas.tag_bind(req_tag, "<Enter>", lambda _event: canvas.configure(cursor="hand2"))
                        canvas.tag_bind(req_tag, "<Leave>", lambda _event: canvas.configure(cursor=""))
                        canvas.tag_bind(req_tag, "<Double-Button-1>", lambda _event, rid=req["id"]: self.open_requirement_detail(rid))

        canvas.bind("<Configure>", render)
        canvas.update_idletasks()
        render()

    def drill_budget_plan(self, plan_id):
        project_id = self.current_project_id()
        if not project_id or not self.ensure_live_session(project_id):
            return
        if not self.db.one("SELECT id FROM annual_plans WHERE id=? AND project_id=?", (plan_id, project_id)):
            messagebox.showwarning("提示", "年度计划已不存在或不属于当前项目。")
            return
        label = next((label for label, value in self.plans.items() if value == plan_id), None)
        if not label:
            self.refresh_contexts()
            label = next((label for label, value in self.plans.items() if value == plan_id), None)
        if not label:
            return
        self.selected_plan.set(label)
        self.on_plan_change(refresh_only=True)
        self.show_budget()

    def drill_budget_version(self, plan_id, version_id):
        project_id = self.current_project_id()
        if not project_id or not self.ensure_live_session(project_id):
            return
        version = self.db.one("SELECT id FROM implementation_versions WHERE id=? AND annual_plan_id=? AND project_id=?",
                              (version_id, plan_id, project_id))
        if not version:
            messagebox.showwarning("提示", "版本已不存在或不属于当前项目。")
            return
        plan_label = next((label for label, value in self.plans.items() if value == plan_id), None)
        if not plan_label:
            self.refresh_contexts()
            plan_label = next((label for label, value in self.plans.items() if value == plan_id), None)
        if not plan_label:
            return
        self.selected_plan.set(plan_label)
        self.on_plan_change(refresh_only=True)
        version_label = next((label for label, value in self.versions.items() if value == version_id), None)
        if version_label:
            self.selected_version.set(version_label)
        self.show_budget()

    def add_budget_flow(self):
        if not self.require_action("budget", "登记资金流水"):
            return
        if not self.current_project_id():
            messagebox.showwarning("提示", "请先选择项目")
            return
        requirement_options = self.requirement_options(include_unplanned=False)
        d = FieldDialog(self, "登记资金流水", [
            ("flow_type", "资金类型", "combo", ["计划预算", "已分配预算", "实际消耗", "调整金额", "冻结金额"]),
            ("requirement_option", "关联需求", "combo", requirement_options),
            ("amount", "金额", "text", None), ("description", "说明", "memo", None),
        ], required=["amount"])
        if d.result:
            try:
                t = now_text()
                code = "BF-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
                amount = self.parse_float(d.result["amount"], "金额")
                flow_type = d.result["flow_type"]
                requirement_id = self.id_from_option(d.result["requirement_option"])
                if flow_type != "调整金额" and amount <= 0:
                    raise ValueError("金额必须大于 0；只有调整金额允许填写负数。")
                if flow_type == "调整金额" and amount == 0:
                    raise ValueError("调整金额不能为 0。")
                try:
                    self.db.record_budget_flow(code, self.current_project_id(), self.current_plan_id(), self.current_version_id(), requirement_id,
                                               flow_type, amount, d.result["description"], self.current_user, t)
                except ValueError as exc:
                    if str(exc) != "ACTUAL_OVERRUN":
                        raise
                    if not messagebox.askyesno("预算预警", "登记后实际消耗将超过该需求的分配预算，是否仍要继续？"):
                        return
                    self.db.record_budget_flow(code, self.current_project_id(), self.current_plan_id(), self.current_version_id(), requirement_id,
                                               flow_type, amount, d.result["description"], self.current_user, t, allow_actual_overrun=True)
                self.show_budget()
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))

    def show_funding_applications(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        if not self.can_view_money():
            messagebox.showwarning("权限不足", "当前角色无权查看资金申报。")
            self.show_dashboard()
            return
        self.clear("资金申报")
        bar = self.make_action_bar(self.content)
        if self.can_action("funding_create"):
            ttk.Button(bar, text="新建申报", command=self.add_funding_application, style="Primary.TButton").pack(side=tk.LEFT)
        if self.can_action("funding_submit") or self.can_action("funding_review"):
            ttk.Button(bar, text="推进状态", command=self.advance_funding_application).pack(side=tk.LEFT, padx=(8, 0))
        if self.can_action("export"):
            ttk.Button(bar, text="导出申报 CSV", command=self.export_funding_applications).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(bar, text="状态").pack(side=tk.LEFT, padx=(16, 0))
        status_box = ttk.Combobox(bar, textvariable=self.funding_status_filter,
                                  values=["全部"] + FUNDING_STATUSES, state="readonly", width=12)
        status_box.pack(side=tk.LEFT, padx=(6, 0))
        status_box.bind("<<ComboboxSelected>>", lambda _e: self.run_guarded(self.show_funding_applications))
        project_id = self.current_project_id()
        where = ["f.project_id=?"]
        params = [project_id]
        if self.funding_status_filter.get() != "全部":
            where.append("f.status=?")
            params.append(self.funding_status_filter.get())
        rows = self.db.query(f"""SELECT f.id, f.application_code, a.plan_year, a.plan_name, f.amount, f.status,
                                        f.applicant_name, f.description, f.submitted_at, f.reviewed_by,
                                        f.reviewed_at, f.updated_at
                                 FROM funding_applications f JOIN annual_plans a ON a.id=f.annual_plan_id
                                 WHERE {' AND '.join(where)} ORDER BY f.id DESC""", tuple(params)) if project_id else []
        summary = self.db.one("""SELECT COALESCE(SUM(amount),0) total,
                                        COALESCE(SUM(CASE WHEN status IN ('已提交','审批中') THEN amount ELSE 0 END),0) reviewing,
                                        COALESCE(SUM(CASE WHEN status='已拨付' THEN amount ELSE 0 END),0) paid
                                 FROM funding_applications WHERE project_id=?""", (project_id,)) if project_id else {"total": 0, "reviewing": 0, "paid": 0}
        self.metric_grid(self.content, [
            ("申报总额", money_text(summary["total"]), "当前项目", self.colors["primary"]),
            ("审批中金额", money_text(summary["reviewing"]), "已提交或审批中", self.colors["warning"]),
            ("已拨付金额", money_text(summary["paid"]), f"拨付率 {percent_text(summary['paid'], summary['total'])}", self.colors["success"]),
        ], columns=2)
        self.funding_tree = self.add_table(self.content, [
            ("id", "ID", 50), ("application_code", "申报编号", 150), ("plan_year", "年度", 70),
            ("plan_name", "年度计划", 180), ("amount", "申报金额", 110), ("status", "状态", 90),
            ("applicant_name", "申请人", 100), ("description", "说明", 240), ("submitted_at", "提交时间", 150),
            ("reviewed_by", "审批人", 100), ("reviewed_at", "审批时间", 150), ("updated_at", "更新时间", 150),
        ], rows, 12)

    def add_funding_application(self):
        if not self.require_action("funding_create", "新建资金申报"):
            return
        project_id = self.current_project_id()
        plan_id = self.current_plan_id()
        if not project_id or not plan_id:
            messagebox.showwarning("提示", "请先选择项目和年度计划。")
            return
        d = FieldDialog(self, "新建资金申报", [
            ("amount", "申报金额", "text", None), ("description", "申报说明", "memo", None),
        ], required=["amount", "description"])
        if not d.result:
            return
        try:
            amount = self.parse_float(d.result["amount"], "申报金额")
            t = now_text()
            self.db.create_funding_application({
                "application_code": "FUND-" + datetime.now().strftime("%Y%m%d%H%M%S%f"),
                "project_id": project_id, "annual_plan_id": plan_id, "amount": amount,
                "applicant_name": self.current_user, "description": d.result["description"],
            }, self.current_user, t)
            self.show_funding_applications()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def selected_funding_application_id(self):
        selection = getattr(self, "funding_tree", None).selection() if hasattr(self, "funding_tree") else ()
        if not selection:
            messagebox.showwarning("提示", "请先选择一条资金申报。")
            return None
        try:
            application_id = int(self.funding_tree.item(selection[0])["values"][0])
        except (TypeError, ValueError):
            return None
        row = self.db.one("SELECT id FROM funding_applications WHERE id=? AND project_id=?",
                          (application_id, self.current_project_id()))
        return application_id if row else None

    def advance_funding_application(self):
        action = "funding_review" if self.can_action("funding_review") else "funding_submit"
        if not self.require_action(action, "推进资金申报"):
            return
        application_id = self.selected_funding_application_id()
        if not application_id:
            return
        row = self.db.one("SELECT * FROM funding_applications WHERE id=? AND project_id=?",
                          (application_id, self.current_project_id()))
        targets = []
        if row and row["status"] == "草稿" and self.can_action("funding_submit"):
            targets = ["已提交"]
        elif row and row["status"] != "草稿" and self.can_action("funding_review"):
            targets = FUNDING_TRANSITIONS.get(row["status"], [])
        if not targets:
            messagebox.showinfo("资金申报", "当前状态没有可执行的后续操作，或当前角色无权处理。")
            return
        d = FieldDialog(self, "推进资金申报", [("status", "目标状态", "combo", targets)],
                        {"status": targets[0]}, required=["status"])
        if not d.result:
            return
        try:
            changed = self.db.transition_funding_application(
                application_id, row["status"], d.result["status"], self.current_user, now_text()
            )
            if not changed:
                messagebox.showwarning("状态冲突", "申报状态已被其他操作更新，请刷新后重试。")
            self.show_funding_applications()
        except Exception as exc:
            messagebox.showerror("状态更新失败", str(exc))

    def export_funding_applications(self):
        if not self.require_action("export", "导出资金申报") or not self.can_view_money():
            return
        rows = self.db.query("""SELECT f.application_code, a.plan_year, a.plan_name, f.amount, f.status,
                                       f.applicant_name, f.description, f.submitted_at, f.reviewed_by,
                                       f.reviewed_at, f.created_at, f.updated_at
                                FROM funding_applications f JOIN annual_plans a ON a.id=f.annual_plan_id
                                WHERE f.project_id=? ORDER BY f.id DESC""", (self.current_project_id(),))
        self.export_csv("current_project_funding_applications", rows)

    def show_operation_records(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        if not self.can_view_operation_records():
            messagebox.showwarning("权限不足", "当前角色无权查看运营服务记录。")
            self.show_dashboard()
            return
        self.clear("运营服务")
        bar = self.make_action_bar(self.content)
        if self.can_action("operation_record"):
            ttk.Button(bar, text="新增服务记录", command=self.add_operation_record, style="Primary.TButton").pack(side=tk.LEFT)
            ttk.Button(bar, text="更新服务记录", command=self.edit_operation_record).pack(side=tk.LEFT, padx=(8, 0))
        if self.can_action("export"):
            ttk.Button(bar, text="导出运营 CSV", command=self.export_operation_records).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(bar, text="状态").pack(side=tk.LEFT, padx=(16, 0))
        status_box = ttk.Combobox(bar, textvariable=self.operation_status_filter,
                                  values=["全部"] + OPERATION_STATUSES, state="readonly", width=12)
        status_box.pack(side=tk.LEFT, padx=(6, 0))
        status_box.bind("<<ComboboxSelected>>", lambda _e: self.run_guarded(self.show_operation_records))
        project_id = self.current_project_id()
        where = ["o.project_id=?"]
        params = [project_id]
        if self.operation_status_filter.get() != "全部":
            where.append("o.status=?")
            params.append(self.operation_status_filter.get())
        rows = self.db.query(f"""SELECT o.id, o.record_code, o.record_type, v.version_code,
                                        r.requirement_code, o.status, o.record_date, o.owner_name,
                                        o.description, o.result, o.created_by, o.updated_at
                                 FROM operation_records o
                                 LEFT JOIN implementation_versions v ON v.id=o.version_id
                                 LEFT JOIN requirements r ON r.id=o.requirement_id
                                 WHERE {' AND '.join(where)} ORDER BY o.record_date DESC, o.id DESC""", tuple(params)) if project_id else []
        self.operation_tree = self.add_table(self.content, [
            ("id", "ID", 50), ("record_code", "记录编号", 150), ("record_type", "类型", 100),
            ("version_code", "版本", 90), ("requirement_code", "原需求", 130), ("status", "状态", 90),
            ("record_date", "日期", 105), ("owner_name", "负责人", 100), ("description", "说明", 260),
            ("result", "处理结果", 240), ("created_by", "创建人", 90), ("updated_at", "更新时间", 150),
        ], rows, 12)

    def add_operation_record(self):
        if not self.require_action("operation_record", "新增运营服务记录"):
            return
        project_id = self.current_project_id()
        version_id = self.current_version_id()
        if not project_id or not version_id:
            messagebox.showwarning("提示", "请先选择项目和关联版本。")
            return
        requirement_options = self.requirement_options(include_unplanned=False)
        d = FieldDialog(self, "新增运营服务记录", [
            ("record_type", "记录类型", "combo", OPERATION_TYPES),
            ("requirement_option", "关联原需求", "combo", requirement_options),
            ("record_date", "记录日期", "text", None), ("owner_name", "负责人", "text", None),
            ("description", "服务说明", "memo", None), ("result", "处理结果", "memo", None),
        ], {"record_type": OPERATION_TYPES[0], "requirement_option": "不关联具体需求",
            "record_date": datetime.now().strftime("%Y-%m-%d"), "owner_name": self.current_user},
           required=["record_type", "record_date", "description"])
        if not d.result:
            return
        try:
            self.db.create_operation_record({
                "record_code": "OPS-" + datetime.now().strftime("%Y%m%d%H%M%S%f"),
                "project_id": project_id, "version_id": version_id,
                "requirement_id": self.id_from_option(d.result["requirement_option"]),
                "record_type": d.result["record_type"], "status": "待处理",
                "record_date": d.result["record_date"], "owner_name": d.result["owner_name"],
                "description": d.result["description"], "result": d.result["result"],
            }, self.current_user, now_text())
            self.show_operation_records()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def selected_operation_record_id(self):
        selection = getattr(self, "operation_tree", None).selection() if hasattr(self, "operation_tree") else ()
        if not selection:
            messagebox.showwarning("提示", "请先选择一条运营服务记录。")
            return None
        try:
            record_id = int(self.operation_tree.item(selection[0])["values"][0])
        except (TypeError, ValueError):
            return None
        row = self.db.one("SELECT id FROM operation_records WHERE id=? AND project_id=?",
                          (record_id, self.current_project_id()))
        return record_id if row else None

    def edit_operation_record(self):
        if not self.require_action("operation_record", "更新运营服务记录"):
            return
        record_id = self.selected_operation_record_id()
        if not record_id:
            return
        row = self.db.one("SELECT * FROM operation_records WHERE id=? AND project_id=?",
                          (record_id, self.current_project_id()))
        if not row:
            return
        d = FieldDialog(self, "更新运营服务记录", [
            ("record_type", "记录类型", "combo", OPERATION_TYPES), ("status", "状态", "combo", OPERATION_STATUSES),
            ("record_date", "记录日期", "text", None), ("owner_name", "负责人", "text", None),
            ("description", "服务说明", "memo", None), ("result", "处理结果", "memo", None),
        ], dict(row), required=["record_type", "status", "record_date", "description"])
        if not d.result:
            return
        try:
            self.db.update_operation_record(record_id, d.result, self.current_user, now_text())
            self.show_operation_records()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def export_operation_records(self):
        if not self.require_action("export", "导出运营服务记录") or not self.can_view_operation_records():
            return
        rows = self.db.query("""SELECT o.record_code, o.record_type, v.version_code, r.requirement_code,
                                       o.status, o.record_date, o.owner_name, o.description, o.result,
                                       o.created_by, o.created_at, o.updated_at
                                FROM operation_records o
                                LEFT JOIN implementation_versions v ON v.id=o.version_id
                                LEFT JOIN requirements r ON r.id=o.requirement_id
                                WHERE o.project_id=? ORDER BY o.record_date DESC, o.id DESC""", (self.current_project_id(),))
        self.export_csv("current_project_operation_records", rows)

    def artifact_rows_for_context(self, project_id, plan_id, version_id):
        if not project_id or not self.can_access_project_now(project_id):
            return []
        where = ["(related_object_type='项目' AND related_object_id=?)"]
        params = [project_id]
        if plan_id:
            where.append("(related_object_type IN ('年度','年度计划') AND related_object_id=?)")
            params.append(plan_id)
        if version_id:
            where.append("(related_object_type='版本' AND related_object_id=?)")
            params.append(version_id)
            req_ids = [row["id"] for row in self.db.query(
                "SELECT id FROM requirements WHERE is_deleted=0 AND project_id=? AND version_id=?",
                (project_id, version_id),
            )]
            if req_ids:
                where.append(f"(related_object_type='需求' AND related_object_id IN ({','.join(['?'] * len(req_ids))}))")
                params.extend(req_ids)
        visibility = " AND visibility='客户可见'" if self.current_role.get() == "客户" else ""
        sql = """SELECT artifact_code, artifact_name, artifact_type, related_object_type, related_object_id,
                        version_no, file_path, visibility, approval_status, uploaded_by, uploaded_at
                 FROM artifacts WHERE approval_status='approved'""" + visibility + " AND (" + " OR ".join(where) + ") ORDER BY id DESC"
        return self.db.query(sql, tuple(params))

    def artifact_project_id(self, artifact):
        object_type = artifact.get("related_object_type")
        object_id = artifact.get("related_object_id")
        queries = {
            "项目": ("SELECT id project_id FROM planning_projects WHERE id=?", (object_id,)),
            "年度": ("SELECT project_id FROM annual_plans WHERE id=?", (object_id,)),
            "年度计划": ("SELECT project_id FROM annual_plans WHERE id=?", (object_id,)),
            "版本": ("SELECT project_id FROM implementation_versions WHERE id=?", (object_id,)),
            "需求": ("SELECT project_id FROM requirements WHERE id=? AND is_deleted=0", (object_id,)),
        }
        sql, params = queries.get(object_type, (None, None))
        row = self.db.one(sql, params) if sql else None
        return row["project_id"] if row else None

    def artifact_is_authorized(self, artifact):
        project_id = self.artifact_project_id(artifact)
        visible = artifact.get("approval_status") == "approved"
        if self.current_role.get() == "客户":
            visible = visible and artifact.get("visibility") == "客户可见"
        return bool(visible and project_id and project_id == self.current_project_id()
                    and self.can_access_project_now(project_id))

    def show_artifacts(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        self.clear("成果物管理")
        bar = self.make_action_bar(self.content)
        if self.can_action("artifact"):
            ttk.Button(bar, text="挂载本地文件", command=self.add_artifact, style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(bar, text="打开/下载附件", command=self.open_selected_artifact).pack(side=tk.LEFT, padx=(8, 0))
        raw_rows = self.artifact_rows_for_context(
            self.current_project_id(), self.current_plan_id(), self.current_version_id(),
        )
        rows = []
        for r in raw_rows:
            item = dict(r)
            item["stage"] = ARTIFACT_STAGE_HINTS.get(item["artifact_type"], "其他")
            rows.append(item)
        self.artifact_tree = self.add_table(self.content, [("artifact_code", "成果物编号", 130), ("artifact_name", "名称", 180), ("stage", "业务阶段", 110), ("artifact_type", "类型", 110), ("related_object_type", "挂载对象", 90), ("related_object_id", "对象ID", 70), ("version_no", "文件版本", 90), ("visibility", "可见范围", 90), ("file_path", "文件路径", 320), ("uploaded_by", "上传人", 90), ("uploaded_at", "上传时间", 150)], rows, on_double_click=self.open_selected_artifact)

    def selected_artifact(self):
        selection = getattr(self, "artifact_tree", None).selection() if hasattr(self, "artifact_tree") else ()
        if not selection:
            messagebox.showwarning("提示", "请先选择一个成果物。")
            return None
        artifact_code = self.artifact_tree.item(selection[0])["values"][0]
        artifact = self.db.one("""SELECT id, artifact_code, artifact_name, file_path, visibility, approval_status,
                                        related_object_type, related_object_id
                                 FROM artifacts WHERE artifact_code=?""", (artifact_code,))
        if not artifact or not self.artifact_is_authorized(artifact):
            messagebox.showwarning("访问受限", "该成果物不属于当前项目，或当前客户授权已被撤销。")
            return None
        return artifact

    def open_selected_artifact(self, _event=None):
        artifact = self.selected_artifact()
        if not artifact:
            return
        try:
            artifact = self.db.one("""SELECT id, artifact_code, artifact_name, file_path, visibility, approval_status,
                                             related_object_type, related_object_id
                                      FROM artifacts WHERE id=?""", (artifact["id"],))
            if not artifact or not self.artifact_is_authorized(artifact):
                messagebox.showwarning("访问受限", "附件打开前授权校验未通过。")
                return
            if artifact["file_path"].startswith("oss://"):
                destination = filedialog.asksaveasfilename(title="下载成果物", initialfile=artifact["artifact_name"])
                if not destination:
                    return
                self.db.download_attachment(artifact["file_path"], destination)
                self.db.log(self.current_user, "artifact", artifact["id"], "download", "", destination, "下载 OSS 成果物附件")
                messagebox.showinfo("下载完成", destination)
                return
            path = Path(artifact["file_path"])
            if not path.is_absolute():
                path = self.db.attachments_dir / path
            path.resolve().relative_to(self.db.attachments_dir.resolve())
            if not path.is_file():
                raise FileNotFoundError(f"附件不存在：{path}")
            if os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self.db.log(self.current_user, "artifact", artifact["id"], "open", "", artifact["file_path"], "打开服务器成果物附件")
        except Exception as exc:
            LOGGER.exception("open_or_download_artifact_failed code=%s", artifact["artifact_code"])
            messagebox.showerror("附件操作失败", str(exc))

    def validate_artifact_target(self, object_type, object_id):
        queries = {
            "项目": ("SELECT id FROM planning_projects WHERE id=? AND id=?", (object_id, self.current_project_id())),
            "年度": ("SELECT id FROM annual_plans WHERE id=? AND project_id=?", (object_id, self.current_project_id())),
            "版本": ("SELECT id FROM implementation_versions WHERE id=? AND project_id=?", (object_id, self.current_project_id())),
            "需求": ("SELECT id FROM requirements WHERE id=? AND project_id=? AND is_deleted=0", (object_id, self.current_project_id())),
        }
        sql, params = queries.get(object_type, (None, None))
        return bool(sql and self.db.one(sql, params))

    def add_artifact(self):
        if not self.require_action("artifact", "挂载成果物"):
            return
        if not self.current_project_id():
            messagebox.showwarning("提示", "请先选择项目。")
            return
        source = filedialog.askopenfilename(title="选择成果物文件")
        if not source:
            return
        src = Path(source)
        if src.suffix.lower() in DANGEROUS_ARTIFACT_EXTENSIONS:
            messagebox.showerror("文件不安全", f"不允许上传可执行或脚本文件：{src.suffix.lower()}")
            return
        if self.current_version_id():
            default_type, default_object_type, default_object_id = "任务书方案", "版本", self.current_version_id()
        elif self.current_plan_id():
            default_type, default_object_type, default_object_id = "分年任务申报书", "年度", self.current_plan_id()
        else:
            default_type, default_object_type, default_object_id = "可研报告", "项目", self.current_project_id()
        d = FieldDialog(self, "成果物信息", [
            ("artifact_type", "成果物类型", "combo", list(ARTIFACT_TARGET_TYPES.keys())),
            ("related_object_type", "挂载对象", "combo", ["项目", "年度", "版本", "需求"]),
            ("related_object_id", "对象ID", "text", None), ("version_no", "文件版本", "text", None),
            ("visibility", "可见范围", "combo", ["内部", "客户可见"]), ("description", "说明", "memo", None),
        ], {"artifact_type": default_type, "related_object_type": default_object_type,
            "related_object_id": str(default_object_id or ""), "version_no": "v1", "visibility": "内部"},
           required=["artifact_type", "related_object_type", "related_object_id", "visibility"])
        if d.result:
            stored_path = None
            artifact_id = None
            maintenance_connection = None
            pending_change_id = None
            try:
                object_id = self.parse_int(d.result["related_object_id"], "对象ID")
                if not self.validate_artifact_target(d.result["related_object_type"], object_id):
                    raise ValueError("挂载对象不存在，或不属于当前项目。")
                allowed_targets = ARTIFACT_TARGET_TYPES.get(d.result["artifact_type"], set())
                if d.result["related_object_type"] not in allowed_targets:
                    raise ValueError(f"“{d.result['artifact_type']}”必须挂载到：{'、'.join(sorted(allowed_targets))}。")
                code = "ART-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
                maintenance_connection = self.db.acquire_attachment_maintenance_lock()
                stored_path = self.db.store_attachment(src, code, lock_held=maintenance_connection is not None)
                t = now_text()
                record = {
                    "artifact_code": code, "artifact_name": src.name, "artifact_type": d.result["artifact_type"],
                    "file_path": stored_path, "file_ext": src.suffix, "file_size": src.stat().st_size,
                    "related_object_type": d.result["related_object_type"], "related_object_id": object_id,
                    "version_no": d.result["version_no"], "description": d.result["description"],
                    "uploaded_by": self.current_user, "uploaded_at": t, "created_at": t,
                }
                record["visibility"] = d.result["visibility"]
                record["project_id"] = self.current_project_id()
                result = self.db.create_artifact_record(record, self.current_user, t)
                artifact_id = result["artifact_id"]
                if result["approval_status"] == "pending":
                    pending_change_id = result["change_id"]
            except Exception as exc:
                log_transaction_exception("add_artifact", exc)
                if stored_path and artifact_id is None:
                    try:
                        self.db.delete_attachment(stored_path, lock_held=maintenance_connection is not None)
                    except Exception:
                        LOGGER.exception("attachment_cleanup_failed path=%s", stored_path)
                messagebox.showerror("保存失败", str(exc))
                return
            finally:
                self.db.release_attachment_maintenance_lock(maintenance_connection)
            if pending_change_id:
                messagebox.showinfo("已提交审批", f"冻结范围内新增成果物已进入变更申请 #{pending_change_id}，审批通过后可见。")
            self.show_artifacts()

    def show_search(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        self.clear("搜索中心")
        keyword = self.search_var.get().strip()
        bar = self.make_action_bar(self.content)
        ttk.Label(bar, text="关键词").pack(side=tk.LEFT)
        ttk.Entry(bar, textvariable=self.search_var, width=36).pack(side=tk.LEFT, padx=8)
        ttk.Button(bar, text="查询", command=lambda: self.run_guarded(self.show_search), style="Primary.TButton").pack(side=tk.LEFT)
        like = f"%{keyword}%"
        access_clause = ""
        params = [like, like, like, like, like, like]
        if self.current_role.get() == "客户":
            access_clause = " AND EXISTS (SELECT 1 FROM user_project_access a WHERE a.user_id=? AND a.project_id=r.project_id)"
            params.append(self.current_user_id)
        if keyword:
            rows = self.db.query(f"""SELECT r.requirement_code, r.requirement_name, p.project_name, ap.plan_name,
                                       COALESCE(v.version_code || ' ' || v.version_name, '待规划') version_name,
                                       r.source_role, r.owner_name, r.tags, r.priority, r.status,
                                       r.allocated_budget, r.actual_cost, r.updated_at
                                FROM requirements r
                                LEFT JOIN planning_projects p ON r.project_id=p.id
                                LEFT JOIN annual_plans ap ON r.annual_plan_id=ap.id
                                LEFT JOIN implementation_versions v ON r.version_id=v.id
                                WHERE r.is_deleted=0 AND (
                                     r.requirement_code LIKE ? OR r.requirement_name LIKE ? OR r.requirement_description LIKE ?
                                     OR r.tags LIKE ? OR p.project_name LIKE ? OR v.version_name LIKE ?
                                 ){access_clause}
                                 ORDER BY r.updated_at DESC""", tuple(params))
        else:
            rows = []
        columns = [("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 220), ("project_name", "项目", 160), ("plan_name", "年度计划", 160), ("version_name", "版本", 160), ("source_role", "来源", 90), ("owner_name", "对接人", 90), ("tags", "标签", 160), ("priority", "优先级", 70), ("status", "状态", 110)]
        if self.can_view_money():
            columns += [("allocated_budget", "分配预算", 100), ("actual_cost", "实际消耗", 100)]
        columns.append(("updated_at", "更新时间", 150))
        self.add_table(self.content, columns, rows)

    def show_milestones(self):
        project_id = self.current_project_id()
        if not self.ensure_live_session(project_id):
            return
        self.clear("流程里程碑")
        stages = [
            ("1.宏观规划", "可研报告", "项目"),
            ("2.规划细化", "分年任务申报书", "年度"),
            ("3.建设落地", "任务书方案/需求任务表/任务清单", "版本"),
            ("4.招投标", "招标文件/应标文件", "版本"),
            ("5.项目交付验收", "验收报告/项目总结", "版本"),
            ("6.运维运营", "运维/运营反馈", "需求"),
        ]
        version_id = self.current_version_id()
        project = self.db.one("SELECT current_stage FROM planning_projects WHERE id=?", (project_id,)) if project_id else None
        current_stage = project["current_stage"] if project and project["current_stage"] in PROJECT_STAGES else PROJECT_STAGES[0]
        if self.can_action("project") and project_id:
            ttk.Button(self.content, text="更新当前阶段", command=self.update_project_stage,
                       style="Primary.TButton").pack(anchor="w", pady=(0, 8))
        total = self.db.one("SELECT COUNT(*) c FROM requirements WHERE is_deleted=0 AND version_id=?", (version_id,))["c"] if version_id else 0
        done = self.db.one("SELECT COUNT(*) c FROM requirements WHERE is_deleted=0 AND version_id=? AND status IN ('待验收','已上线运维','已关闭')", (version_id,))["c"] if version_id else 0
        row = ttk.Frame(self.content)
        row.pack(fill=tk.X)
        self.metric_card(row, "当前版本需求", total, "版本范围内需求总数")
        self.metric_card(row, "验收/上线/关闭", done, f"完成度 {percent_text(done, total)}", self.colors["success"] if total and done == total else self.colors["warning"])
        self.metric_card(row, "待补成果物", self.missing_acceptance_artifact_count(), "版本完成后需关注验收报告", self.colors["danger"] if self.missing_acceptance_artifact_count() else self.colors["success"])
        self.section_title(self.content, "阶段视图", "按照咨询项目全流程展示阶段、关键成果物和当前挂载数量。")
        rows = []
        current_index = PROJECT_STAGES.index(current_stage)
        for index, (stage, artifact_names, obj_type) in enumerate(stages):
            count = self.count_stage_artifacts(stage, project_id, version_id)
            phase_status = "已完成" if index < current_index else "当前阶段" if index == current_index else "未开始"
            rows.append({"stage": stage, "artifact_names": artifact_names, "object_type": obj_type,
                         "artifact_count": count, "status": phase_status})
        self.milestone_tree = self.add_table(self.content, [("stage", "阶段", 150), ("artifact_names", "关键成果物", 260), ("object_type", "建议挂载对象", 120), ("artifact_count", "已审批数量", 100), ("status", "状态", 100)], rows, 8)
        if total and done == total and self.missing_acceptance_artifact_count():
            ttk.Label(self.content, text="提醒：当前版本需求已达到验收/上线条件，请补充验收报告或项目总结。", foreground=self.colors["danger"], background=self.colors["bg"]).pack(anchor="w", pady=(8, 0))

    def update_project_stage(self):
        if not self.require_action("project", "更新项目阶段"):
            return
        project_id = self.current_project_id()
        if not project_id:
            messagebox.showwarning("提示", "请先选择项目")
            return
        project = self.db.one("SELECT current_stage FROM planning_projects WHERE id=?", (project_id,))
        if not project:
            messagebox.showwarning("提示", "当前项目已不存在。")
            return
        dialog = FieldDialog(self, "更新项目阶段", [("current_stage", "当前阶段", "combo", PROJECT_STAGES)],
                             {"current_stage": project["current_stage"] if project["current_stage"] in PROJECT_STAGES else PROJECT_STAGES[0]},
                             required=["current_stage"])
        if not dialog.result:
            return
        try:
            self.db.update_project_stage(project_id, dialog.result["current_stage"], self.current_user, now_text())
            self.show_milestones()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def count_stage_artifacts(self, stage, project_id, version_id):
        stage_name = stage.split(".", 1)[-1]
        types = [k for k, v in ARTIFACT_STAGE_HINTS.items() if v == stage_name]
        if not types:
            return 0
        placeholders = ",".join(["?"] * len(types))
        params = types[:]
        where = [f"artifact_type IN ({placeholders})", "approval_status='approved'"]
        if self.current_role.get() == "客户":
            where.append("visibility='客户可见'")
        if stage_name == "宏观规划":
            where.append("related_object_type='项目' AND related_object_id=?")
            params.append(project_id or 0)
        elif stage_name == "规划细化":
            where.append("related_object_type IN ('年度','年度计划') AND related_object_id=?")
            params.append(self.current_plan_id() or 0)
        elif stage_name == "运维运营":
            req_ids = [row["id"] for row in self.db.query("SELECT id FROM requirements WHERE version_id=? AND is_deleted=0", (version_id,))] if version_id else []
            if not req_ids:
                return 0
            where.append(f"related_object_type='需求' AND related_object_id IN ({','.join(['?'] * len(req_ids))})")
            params.extend(req_ids)
        else:
            where.append("related_object_type='版本' AND related_object_id=?")
            params.append(version_id or 0)
        return self.db.one(f"SELECT COUNT(*) c FROM artifacts WHERE {' AND '.join(where)}", tuple(params))["c"]

    def missing_acceptance_artifact_count(self):
        version_id = self.current_version_id()
        if not version_id:
            return 0
        count = self.db.one("""SELECT COUNT(*) c FROM artifacts
                               WHERE related_object_type='版本' AND related_object_id=?
                               AND approval_status='approved'
                               AND (?<>'客户' OR visibility='客户可见')
                               AND artifact_type IN ('验收报告','项目总结')""",
                            (version_id, self.current_role.get()))["c"]
        return 0 if count else 1

    def show_exports(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        if not self.can_action("export"):
            messagebox.showwarning("权限不足", "当前角色无权导出项目数据。")
            self.show_dashboard()
            return
        self.clear("报表导出")
        ttk.Button(self.content, text="导出角色专用报告 CSV", command=self.export_role_report, style="Primary.TButton").pack(anchor="w", pady=5)
        ttk.Button(self.content, text="导出当前项目需求 CSV", command=self.export_requirements).pack(anchor="w", pady=5)
        if self.can_view_money():
            ttk.Button(self.content, text="导出当前项目资金 CSV", command=self.export_budget).pack(anchor="w", pady=5)
        ttk.Button(self.content, text="导出当前项目成果物 CSV", command=self.export_artifacts).pack(anchor="w", pady=5)
        if self.current_role.get() == "管理员":
            ttk.Button(self.content, text="创建附件备份 ZIP", command=self.create_backup).pack(anchor="w", pady=5)
            ttk.Button(self.content, text="从附件备份 ZIP 恢复", command=self.restore_backup).pack(anchor="w", pady=5)
        ttk.Label(self.content, text=f"导出目录：{self.db.exports_dir}\n备份目录：{self.db.backups_dir}\n日志目录：{self.db.logs_dir}", wraplength=900).pack(anchor="w", pady=(12, 0))

    def export_csv(self, name, rows):
        path = self.db.exports_dir / f"{name}_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
        if not rows:
            messagebox.showinfo("提示", "没有可导出的数据")
            return
        fieldnames = self.visible_export_columns(rows[0].keys())
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows([
                {key: csv_safe(dict(row).get(key, "")) for key in fieldnames}
                for row in rows
            ])
        messagebox.showinfo("导出完成", str(path))

    def export_requirements(self):
        if not self.require_action("export", "导出需求清单"):
            return
        columns = "requirement_code, business_key, requirement_name, source_role, proposer_name, owner_name, requirement_type, tags, priority, status"
        if self.can_view_effort():
            columns += ", estimated_hours, actual_hours"
        columns += ", planned_finish_date, actual_finish_date, updated_at"
        if self.can_view_money():
            columns += ", estimated_budget, allocated_budget, actual_cost"
        rows = self.db.query(f"SELECT {columns} FROM requirements WHERE is_deleted=0 AND project_id=? ORDER BY id DESC", (self.current_project_id(),))
        self.export_csv("current_project_requirements", rows)

    def export_budget(self):
        if not self.require_action("export", "导出资金明细") or not self.can_view_money():
            return
        self.export_csv("current_project_budget_flows", self.db.query("SELECT * FROM budget_flows WHERE project_id=? ORDER BY id DESC", (self.current_project_id(),)))

    def export_artifacts(self):
        if not self.require_action("export", "导出成果物目录"):
            return
        project_id = self.current_project_id()
        visibility = " AND visibility='客户可见'" if self.current_role.get() == "客户" else ""
        rows = self.db.query(f"""SELECT * FROM artifacts
            WHERE approval_status='approved'{visibility} AND (
                (related_object_type='项目' AND related_object_id=?) OR
                (related_object_type IN ('年度','年度计划') AND related_object_id IN (SELECT id FROM annual_plans WHERE project_id=?)) OR
                (related_object_type='版本' AND related_object_id IN (SELECT id FROM implementation_versions WHERE project_id=?)) OR
                (related_object_type='需求' AND related_object_id IN (SELECT id FROM requirements WHERE project_id=? AND is_deleted=0))
            ) ORDER BY id DESC""", (project_id, project_id, project_id, project_id))
        self.export_csv("current_project_artifacts", rows)

    def export_role_report(self):
        if not self.require_action("export", "导出角色专用报告"):
            return
        role = self.current_role.get()
        common = "requirement_code, requirement_name, priority, status, owner_name, planned_finish_date"
        if role == "销售":
            columns = common + ", estimated_budget, allocated_budget, actual_cost"
        elif role == "项目经理":
            columns = common + ", estimated_hours, actual_hours, actual_finish_date"
        elif role == "研发人员":
            columns = common + ", business_key, requirement_type, tags, estimated_hours, actual_hours"
        elif role == "运营人员":
            columns = common + ", requirement_type, tags, actual_finish_date, remark"
        else:
            columns = common + ", business_key, source_role, requirement_type, tags, estimated_hours, actual_hours, estimated_budget, allocated_budget, actual_cost"
        rows = self.db.query(f"SELECT {columns} FROM requirements WHERE is_deleted=0 AND project_id=? ORDER BY priority, updated_at DESC", (self.current_project_id(),))
        self.export_csv(f"{role}_project_report", rows)

    def create_backup(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        if self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员可以执行附件备份。")
            return
        if self.db.attachment_storage == "oss":
            messagebox.showinfo("OSS 备份", "OSS 附件应通过 Bucket 版本控制、生命周期和跨区域复制策略备份；应用不会下载整个 Bucket。")
            return
        backup = self.db.backups_dir / f"backup_{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
        maintenance_connection = None
        backup_error = None
        try:
            maintenance_connection = self.db.acquire_attachment_maintenance_lock()
            with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("README.txt", "MySQL 远程版附件备份；数据库必须使用 mysqldump 或服务端备份。\n")
                safe_config = {key: value for key, value in self.db.config.items() if key != "password"}
                z.writestr("config_snapshot.json", json.dumps(safe_config, ensure_ascii=False, indent=2))
                z.writestr("attachments/", "")
                for file in self.db.attachments_dir.rglob("*"):
                    if file.is_file() and not file.is_symlink():
                        z.write(file, f"attachments/{file.relative_to(self.db.attachments_dir).as_posix()}")
        except Exception as exc:
            backup_error = exc
            log_transaction_exception("create_attachment_backup", exc)
        finally:
            self.db.release_attachment_maintenance_lock(maintenance_connection)
        if backup_error is not None:
            backup.unlink(missing_ok=True)
            messagebox.showerror("备份失败", str(backup_error))
            return
        audit_warning = ""
        try:
            self.db.log(self.current_user, "backup", None, "create", "", backup, "创建服务器附件备份")
        except Exception:
            LOGGER.exception("attachment_backup_audit_failed path=%s", backup)
            audit_warning = "\n注意：附件已备份，但审计日志写入失败，请检查客户端日志。"
        messagebox.showinfo("备份完成", f"{backup}{audit_warning}")

    def restore_backup(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        if self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员可以执行附件恢复。")
            return
        if self.db.attachment_storage == "oss":
            messagebox.showinfo("OSS 恢复", "请在 OSS 控制台或运维流程中恢复对象版本，应用不执行 Bucket 批量恢复。")
            return
        source = filedialog.askopenfilename(title="选择附件备份 ZIP", filetypes=[("ZIP", "*.zip")])
        if not source:
            return
        if not messagebox.askyesno("确认恢复", "此操作只合并恢复服务器附件，不会恢复 MySQL 数据库。备份中的同名文件会覆盖，其他现有附件会保留。是否继续？"):
            return
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        staged = self.db.attachments_dir.parent / f".{self.db.attachments_dir.name}.restore.{stamp}"
        old = self.db.attachments_dir.parent / f".{self.db.attachments_dir.name}.before_restore.{stamp}"
        maintenance_connection = None
        try:
            with tempfile.TemporaryDirectory(prefix="crm-attachments-") as temp_dir:
                temp_path = Path(temp_dir)
                with zipfile.ZipFile(source, "r") as z:
                    validate_restore_archive(z, required_prefix="attachments/")
                    z.extractall(temp_path)
                restored = temp_path / "attachments"
                maintenance_connection = self.db.acquire_attachment_maintenance_lock()
                shutil.copytree(self.db.attachments_dir, staged)
                shutil.copytree(restored, staged, dirs_exist_ok=True)
                safety = self.db.backups_dir / f"before_attachment_restore_{stamp}.zip"
                with zipfile.ZipFile(safety, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("attachments/", "")
                    for file in self.db.attachments_dir.rglob("*"):
                        if file.is_file() and not file.is_symlink():
                            z.write(file, f"attachments/{file.relative_to(self.db.attachments_dir).as_posix()}")
                os.replace(self.db.attachments_dir, old)
                os.replace(staged, self.db.attachments_dir)
                shutil.rmtree(old, ignore_errors=True)
        except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
            log_transaction_exception("restore_attachment_backup", exc)
            rollback_error = None
            try:
                if old.exists():
                    if self.db.attachments_dir.exists():
                        shutil.rmtree(self.db.attachments_dir)
                    os.replace(old, self.db.attachments_dir)
            except OSError as rollback_exc:
                LOGGER.exception("attachment_restore_rollback_failed")
                rollback_error = rollback_exc
            if staged.exists():
                shutil.rmtree(staged, ignore_errors=True)
            self.db.release_attachment_maintenance_lock(maintenance_connection)
            maintenance_connection = None
            if rollback_error is not None:
                messagebox.showerror("自动回滚失败", f"请使用恢复前附件快照人工恢复：{rollback_error}")
            messagebox.showerror("恢复失败", f"已尝试自动回滚到恢复前附件。错误：{exc}")
            return
        finally:
            self.db.release_attachment_maintenance_lock(maintenance_connection)
        audit_warning = ""
        try:
            self.db.log(self.current_user, "attachment_backup", None, "restore", source, self.db.attachments_dir, "合并恢复服务器附件备份")
        except Exception:
            LOGGER.exception("attachment_restore_audit_failed source=%s", source)
            audit_warning = "\n注意：附件已恢复，但审计日志写入失败，请检查客户端日志。"
        messagebox.showinfo("恢复完成", f"服务器附件已合并恢复；其他现有附件已保留，MySQL 数据库未变更。{audit_warning}")

    def show_settings(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        if not self.can_action("approve"):
            messagebox.showwarning("权限不足", "当前角色无权查看用户、变更审批和系统日志。")
            self.show_dashboard()
            return
        self.clear("系统设置")
        ttk.Label(self.content, text=f"当前账号：{self.current_user}（{self.current_role.get()}）。角色权限由登录账号决定。", style="SubTitle.TLabel").pack(anchor="w", pady=(0, 8))
        self.section_title(self.content, "运行日志", "客户端运行、错误和审计日志按大小分卷；MySQL 操作日志仍可在本页查询。")
        log_bar = self.make_action_bar(self.content)
        ttk.Button(log_bar, text="打开日志目录", command=self.open_logs_directory).pack(side=tk.LEFT)
        ttk.Button(log_bar, text="运行健康检查", command=self.run_healthcheck).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(log_bar, text=f"{self.db.logs_dir}  |  runtime.log / error.log / audit.log").pack(side=tk.LEFT, padx=(10, 0))
        if self.current_role.get() == "管理员":
            self.section_title(self.content, "标签字典", "标签在需求表单中以多选方式使用。")
            tag_bar = self.make_action_bar(self.content)
            ttk.Button(tag_bar, text="新增标签", command=self.add_tag_definition, style="Primary.TButton").pack(side=tk.LEFT)
            ttk.Button(tag_bar, text="启用/停用", command=self.toggle_tag_definition).pack(side=tk.LEFT, padx=(8, 0))
            tags = self.db.query("SELECT id, tag_name, is_active, created_at FROM tag_definitions ORDER BY tag_name")
            self.tag_tree = self.add_table(self.content, [("id", "ID", 50), ("tag_name", "标签名称", 220), ("is_active", "启用", 70), ("created_at", "创建时间", 150)], tags, 5)
        if self.current_role.get() == "管理员":
            self.section_title(self.content, "用户与角色", "账号由管理员预建并分配角色，不开放自助注册；密码仅保存 PBKDF2 哈希。")
            user_bar = self.make_action_bar(self.content)
            account_actions = ttk.Frame(user_bar)
            account_actions.pack(fill=tk.X, pady=(0, 6))
            access_actions = ttk.Frame(user_bar)
            access_actions.pack(fill=tk.X)
            self.user_action_rows = (account_actions, access_actions)
            ttk.Button(account_actions, text="新建用户", command=self.add_user, style="Primary.TButton").pack(side=tk.LEFT)
            ttk.Button(account_actions, text="修改角色", command=self.change_user_role).pack(side=tk.LEFT, padx=(8, 0))
            ttk.Button(account_actions, text="启用/停用", command=self.toggle_user_active).pack(side=tk.LEFT, padx=(8, 0))
            ttk.Button(account_actions, text="重置密码", command=self.reset_user_password).pack(side=tk.LEFT, padx=(8, 0))
            ttk.Button(account_actions, text="强制下线", command=self.force_user_logout).pack(side=tk.LEFT, padx=(8, 0))
            ttk.Button(access_actions, text="授权当前项目", command=self.grant_current_project).pack(side=tk.LEFT)
            ttk.Button(access_actions, text="撤销当前项目", command=self.revoke_current_project).pack(side=tk.LEFT, padx=(8, 0))
            users = self.db.query("""SELECT u.id, u.username, u.display_name, u.role_name, u.is_active,
                                             CASE WHEN u.session_token IS NULL THEN '否' ELSE '是' END session_active,
                                             COALESCE(u.session_started_at, '') session_started_at,
                                             COALESCE(GROUP_CONCAT(DISTINCT p.project_name ORDER BY p.project_name SEPARATOR ', '), '') project_access,
                                             u.created_at, u.updated_at
                                      FROM users u
                                      LEFT JOIN user_project_access a ON a.user_id=u.id
                                      LEFT JOIN planning_projects p ON p.id=a.project_id
                                      GROUP BY u.id, u.username, u.display_name, u.role_name, u.is_active,
                                               u.session_token, u.session_started_at, u.created_at, u.updated_at
                                      ORDER BY u.id""")
            self.user_tree = self.add_table(self.content, [("id", "ID", 50), ("username", "用户名", 120), ("display_name", "显示名称", 130), ("role_name", "角色", 100), ("is_active", "启用", 60), ("session_active", "会话占用", 80), ("session_started_at", "会话开始", 145), ("project_access", "客户项目权限", 240), ("created_at", "创建时间", 145), ("updated_at", "更新时间", 145)], users, 7)
        self.section_title(self.content, "变更申请", "冻结版本内的需求修改、删除及新增成果物会进入此列表，由管理员或咨询负责人审批。")
        bar = self.make_action_bar(self.content)
        ttk.Label(bar, text="状态").pack(side=tk.LEFT)
        status_box = ttk.Combobox(bar, textvariable=self.change_status_filter, values=["全部", "pending", "approved", "rejected"], state="readonly", width=12)
        status_box.pack(side=tk.LEFT, padx=(6, 10))
        status_box.bind("<<ComboboxSelected>>", lambda e: self.show_settings())
        ttk.Button(bar, text="查看变更详情", command=self.show_change_request_detail).pack(side=tk.LEFT)
        ttk.Button(bar, text="通过", command=self.approve_change_request).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bar, text="驳回", command=self.reject_change_request).pack(side=tk.LEFT, padx=(8, 0))
        where = ""
        params = ()
        if self.change_status_filter.get() != "全部":
            where = "WHERE c.approval_status=?"
            params = (self.change_status_filter.get(),)
        changes = self.db.query(f"""SELECT c.id, c.approval_status, v.version_code, r.requirement_code, c.change_title,
                                           c.requested_by, c.requested_at, c.approved_by, c.approved_at
                                    FROM change_requests c
                                    LEFT JOIN implementation_versions v ON c.version_id=v.id
                                    LEFT JOIN requirements r ON c.requirement_id=r.id
                                    {where}
                                    ORDER BY c.id DESC LIMIT 80""", params)
        self.change_tree = self.add_table(self.content, [("id", "ID", 50), ("approval_status", "状态", 90), ("version_code", "版本", 90), ("requirement_code", "需求编号", 130), ("change_title", "标题", 260), ("requested_by", "申请人", 90), ("requested_at", "申请时间", 150), ("approved_by", "审批人", 90), ("approved_at", "审批时间", 150)], changes, 8, self.show_change_request_detail)
        self.section_title(self.content, "操作日志", "集中记录关键业务操作、登录成功/失败和权限拒绝；事件 ID 可与客户端 audit.log 对账。")
        audit_bar = self.make_action_bar(self.content)
        ttk.Label(audit_bar, text="对象类型").pack(side=tk.LEFT)
        type_box = ttk.Combobox(audit_bar, textvariable=self.operation_log_type_filter,
                                values=["全部", "system", "authentication", "permission", "user", "user_project_access", "planning_project", "annual_plan", "implementation_version", "requirement", "budget_flow", "funding_application", "operation_record", "artifact", "change_request", "backup", "healthcheck"],
                                state="readonly", width=22)
        type_box.pack(side=tk.LEFT, padx=(6, 10))
        type_box.bind("<<ComboboxSelected>>", lambda _e: self.show_settings())
        ttk.Label(audit_bar, text="关键词").pack(side=tk.LEFT)
        ttk.Entry(audit_bar, textvariable=self.operation_log_keyword, width=24).pack(side=tk.LEFT, padx=(6, 8))
        ttk.Button(audit_bar, text="查询", command=self.show_settings).pack(side=tk.LEFT)
        ttk.Button(audit_bar, text="导出审计 CSV", command=self.export_operation_logs).pack(side=tk.LEFT, padx=(8, 0))
        rows = self.filtered_operation_logs(limit=200)
        self.add_table(self.content, [("operation_time", "时间", 150), ("operator_name", "操作人", 100), ("object_type", "对象", 110), ("operation_type", "操作", 110), ("result", "结果", 70), ("event_id", "事件ID", 170), ("description", "说明", 280)], rows, 10)

    def filtered_operation_logs(self, limit=None):
        where = []
        params = []
        if self.operation_log_type_filter.get() != "全部":
            where.append("object_type=?")
            params.append(self.operation_log_type_filter.get())
        keyword = self.operation_log_keyword.get().strip()
        if keyword:
            where.append("(operator_name LIKE ? OR operation_type LIKE ? OR event_id LIKE ? OR result LIKE ? OR description LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like, like, like, like])
        sql = "SELECT * FROM operation_logs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self.db.query(sql, tuple(params))

    def export_operation_logs(self):
        if not self.require_action("approve", "导出审计日志"):
            return
        self.export_csv("operation_logs", self.filtered_operation_logs())

    def selected_user_id(self):
        selection = getattr(self, "user_tree", None).selection() if hasattr(self, "user_tree") else ()
        if not selection:
            messagebox.showwarning("提示", "请先选择一个用户。")
            return None
        try:
            return int(self.user_tree.item(selection[0])["values"][0])
        except (TypeError, ValueError):
            return None

    def add_user(self):
        if not self.require_action("user_manage", "新建用户"):
            return
        dialog = FieldDialog(self, "新建用户", [
            ("username", "用户名", "text", None),
            ("display_name", "显示名称", "text", None),
            ("role_name", "角色", "combo", ROLES),
            ("password", "初始密码", "password", None),
        ], required=["username", "display_name", "role_name", "password"])
        if not dialog.result:
            return
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", dialog.result["username"]):
            messagebox.showerror("保存失败", "用户名须为 3-64 位字母、数字、点、下划线或连字符。")
            return
        try:
            display_name = validated_display_name(dialog.result["display_name"])
        except ValueError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        if len(dialog.result["password"]) < 8:
            messagebox.showerror("保存失败", "初始密码至少 8 位。")
            return
        t = now_text()
        try:
            self.db.execute("INSERT INTO users(username, display_name, password_hash, role_name, is_active, created_at, updated_at) VALUES(?,?,?,?,1,?,?)",
                            (dialog.result["username"], display_name, hash_password(dialog.result["password"]), dialog.result["role_name"], t, t))
            self.db.log(self.current_user, "user", None, "create", "", {"username": dialog.result["username"], "role": dialog.result["role_name"]}, "创建用户")
            self.show_settings()
        except Exception as exc:
            messagebox.showerror("保存失败", f"用户名可能已存在：{exc}")

    def toggle_user_active(self):
        if not self.require_action("user_manage", "启用或停用用户"):
            return
        user_id = self.selected_user_id()
        if not user_id:
            return
        user = self.db.one("SELECT * FROM users WHERE id=?", (user_id,))
        if user["id"] == self.current_user_id:
            messagebox.showwarning("禁止操作", "不能停用当前登录账号。")
            return
        new_value = 0 if user["is_active"] else 1
        self.db.execute("""UPDATE users SET is_active=?, session_token=NULL,
                                             session_started_at=NULL, updated_at=? WHERE id=?""",
                        (new_value, now_text(), user_id))
        self.db.log(self.current_user, "user", user_id, "enable" if new_value else "disable", user["is_active"], new_value, "更新用户启用状态")
        self.show_settings()

    def change_user_role(self):
        if not self.require_action("user_manage", "修改用户角色"):
            return
        user_id = self.selected_user_id()
        if not user_id:
            return
        if user_id == self.current_user_id:
            messagebox.showwarning("禁止操作", "不能修改当前登录账号的角色。")
            return
        user = self.db.one("SELECT username, display_name, role_name FROM users WHERE id=?", (user_id,))
        if not user:
            messagebox.showwarning("提示", "用户已不存在。")
            return
        dialog = FieldDialog(self, f"修改角色 - {user['display_name']}", [
            ("role_name", "角色", "combo", ROLES),
        ], {"role_name": user["role_name"]}, required=["role_name"])
        if not dialog.result or dialog.result["role_name"] == user["role_name"]:
            return
        new_role = dialog.result["role_name"]
        if not messagebox.askyesno(
            "确认修改角色",
            "角色变更会立即使该用户当前会话失效，并清空原有客户项目授权。是否继续？",
        ):
            return
        try:
            self.db.update_user_role(user_id, new_role, self.current_user, now_text())
        except Exception as exc:
            messagebox.showerror("修改失败", str(exc))
            return
        messagebox.showinfo("修改完成", f"用户 {user['username']} 已调整为“{new_role}”。")
        self.show_settings()

    def force_user_logout(self):
        if not self.require_action("user_manage", "强制用户下线"):
            return
        user_id = self.selected_user_id()
        if not user_id:
            return
        if user_id == self.current_user_id:
            messagebox.showwarning("禁止操作", "不能在当前会话中强制自己下线。")
            return
        user = self.db.one("SELECT username, session_token FROM users WHERE id=?", (user_id,))
        if not user:
            messagebox.showwarning("提示", "用户已不存在。")
            return
        self.db.execute("UPDATE users SET session_token=NULL, session_started_at=NULL WHERE id=?", (user_id,))
        self.db.log(self.current_user, "user", user_id, "force_logout", "", "", f"强制用户 {user['username']} 下线")
        messagebox.showinfo("操作完成", "该用户的现有客户端会在会话巡检时自动退出。")
        self.show_settings()

    def grant_current_project(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        if self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员可以分配客户项目权限。")
            return
        user_id = self.selected_user_id()
        project_id = self.current_project_id()
        if not user_id or not project_id:
            if not project_id:
                messagebox.showwarning("提示", "请先在顶部选择要授权的项目。")
            return
        user = self.db.one("SELECT username, role_name FROM users WHERE id=?", (user_id,))
        if user["role_name"] != "客户":
            messagebox.showwarning("提示", "项目行级授权仅对客户角色生效。")
            return
        if self.db.one("SELECT user_id FROM user_project_access WHERE user_id=? AND project_id=?", (user_id, project_id)):
            messagebox.showinfo("提示", "该客户已拥有当前项目权限。")
            return
        self.db.execute("INSERT INTO user_project_access(user_id, project_id, created_by, created_at) VALUES(?,?,?,?)",
                        (user_id, project_id, self.current_user, now_text()))
        self.db.log(self.current_user, "user_project_access", user_id, "grant", "", project_id, f"授权客户 {user['username']} 访问当前项目")
        self.show_settings()

    def revoke_current_project(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        if self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员可以撤销客户项目权限。")
            return
        user_id = self.selected_user_id()
        project_id = self.current_project_id()
        if not user_id or not project_id:
            if not project_id:
                messagebox.showwarning("提示", "请先在顶部选择要撤销的项目。")
            return
        user = self.db.one("SELECT username, role_name FROM users WHERE id=?", (user_id,))
        if user["role_name"] != "客户":
            messagebox.showwarning("提示", "项目行级授权仅对客户角色生效。")
            return
        result = self.db.execute("DELETE FROM user_project_access WHERE user_id=? AND project_id=?", (user_id, project_id))
        if result.rowcount != 1:
            messagebox.showinfo("提示", "该客户没有当前项目权限。")
            return
        self.db.log(self.current_user, "user_project_access", user_id, "revoke", project_id, "", f"撤销客户 {user['username']} 当前项目权限")
        self.show_settings()

    def reset_user_password(self):
        if not self.ensure_live_session(self.current_project_id()):
            return
        if self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员可以重置用户密码。")
            return
        user_id = self.selected_user_id()
        if not user_id:
            return
        if user_id == self.current_user_id:
            messagebox.showwarning("提示", "请使用顶部“修改密码”更新当前账号密码。")
            return
        user = self.db.one("SELECT username, display_name FROM users WHERE id=?", (user_id,))
        dialog = FieldDialog(self, f"重置密码 - {user['display_name']}", [
            ("new_password", "新密码", "password", None),
            ("confirm_password", "确认新密码", "password", None),
        ], required=["new_password", "confirm_password"])
        if not dialog.result:
            return
        if len(dialog.result["new_password"]) < 8:
            messagebox.showerror("重置失败", "新密码至少 8 位。")
            return
        if dialog.result["new_password"] != dialog.result["confirm_password"]:
            messagebox.showerror("重置失败", "两次输入的新密码不一致。")
            return
        self.db.execute("""UPDATE users SET password_hash=?, session_token=NULL,
                                             session_started_at=NULL, updated_at=? WHERE id=?""",
                        (hash_password(dialog.result["new_password"]), now_text(), user_id))
        self.db.log(self.current_user, "user", user_id, "reset_password", "", "", f"重置用户 {user['username']} 密码")
        messagebox.showinfo("重置完成", "用户密码已更新，原有会话已下线。请安全告知临时密码，并提醒用户登录后立即修改。")
        self.show_settings()

    def selected_change_id(self):
        sel = getattr(self, "change_tree", None).selection() if hasattr(self, "change_tree") else ()
        if not sel:
            messagebox.showwarning("提示", "请先选择一条变更申请")
            return None
        value = self.change_tree.item(sel[0])["values"][0]
        try:
            return int(value)
        except (TypeError, ValueError):
            messagebox.showwarning("提示", "当前行不是有效变更申请")
            return None

    def approve_change_request(self):
        self.update_change_request("approved")

    def reject_change_request(self):
        self.update_change_request("rejected")

    def change_payload_summary(self, change_id):
        payload = self.db.one("SELECT change_type, proposed_value FROM change_request_payloads WHERE change_request_id=?", (change_id,))
        if not payload:
            return "缺少变更载荷"
        try:
            proposed = json.loads(payload["proposed_value"] or "{}")
        except (TypeError, ValueError):
            return "变更载荷 JSON 无效"
        if not isinstance(proposed, dict):
            return "变更载荷结构无效"
        if payload["change_type"] == "delete":
            return "删除关联需求"
        if payload["change_type"] == "artifact_add":
            artifact = self.db.one("SELECT artifact_code, artifact_name FROM artifacts WHERE id=?", (proposed.get("artifact_id"),))
            return f"新增成果物：{artifact['artifact_code']} {artifact['artifact_name']}" if artifact else "新增成果物（记录缺失）"
        labels = {
            "requirement_name": "需求名称", "business_key": "业务标识", "source_role": "来源角色",
            "owner_name": "负责人", "requirement_type": "需求类型", "tags": "标签", "priority": "优先级",
            "estimated_budget": "预估预算", "estimated_hours": "预估工时", "planned_finish_date": "预计完成",
            "parent_requirement_id": "原需求ID", "remark": "备注",
        }
        parts = [f"{label}={proposed.get(key, '')}" for key, label in labels.items() if key in proposed]
        return "；".join(parts[:8]) or "更新需求内容"

    def show_change_request_detail(self):
        if not self.require_action("approve", "查看变更申请"):
            return
        change_id = self.selected_change_id()
        if not change_id:
            return
        change = self.db.one("""SELECT c.*, v.version_code, v.project_id,
                                        r.requirement_code, r.requirement_name
                                 FROM change_requests c
                                 LEFT JOIN implementation_versions v ON v.id=c.version_id
                                 LEFT JOIN requirements r ON r.id=c.requirement_id
                                 WHERE c.id=?""", (change_id,))
        if not change or not self.can_access_project_now(change["project_id"]):
            messagebox.showwarning("提示", "变更申请已不存在或无权访问。")
            return
        payload = self.db.one("SELECT change_type FROM change_request_payloads WHERE change_request_id=?", (change_id,))
        DetailDialog(self, f"变更申请 #{change_id}", [
            ("申请信息", [("状态", change["approval_status"]), ("版本", change["version_code"]),
                         ("需求", f"{change['requirement_code'] or '-'} {change['requirement_name'] or ''}"),
                         ("标题", change["change_title"]), ("申请人", change["requested_by"]),
                         ("申请时间", change["requested_at"])]),
            ("原因与影响", [("变更原因", change["change_reason"]), ("影响范围", change["impact_scope"])]),
            ("拟变更摘要", [("变更类型", payload["change_type"] if payload else "缺失"),
                            ("摘要", self.change_payload_summary(change_id))]),
            ("审批信息", [("审批人", change["approved_by"]), ("审批时间", change["approved_at"])]),
        ])

    def update_change_request(self, status):
        if not self.require_action("approve", "审批变更申请"):
            return
        change_id = self.selected_change_id()
        if not change_id:
            return
        change = self.db.one("""SELECT c.*, v.project_id FROM change_requests c
                                LEFT JOIN implementation_versions v ON v.id=c.version_id WHERE c.id=?""", (change_id,))
        if not change or not self.can_access_project_now(change["project_id"]):
            messagebox.showwarning("提示", "变更申请已不存在或无权访问。")
            return
        if change["approval_status"] != "pending":
            messagebox.showinfo("提示", "该变更申请已处理")
            return
        action = "通过" if status == "approved" else "驳回"
        summary = self.change_payload_summary(change_id)
        confirm_text = (f"确认{action}该变更申请？\n\n变更原因：{change['change_reason'] or '未填写'}\n"
                        f"影响范围：{change['impact_scope'] or '未填写'}\n拟变更：{summary}")
        if not messagebox.askyesno("确认审批", confirm_text):
            return
        try:
            self.db.review_change_request(change_id, status, self.current_user, now_text())
            self.show_settings()
        except Exception as exc:
            messagebox.showerror("审批失败", str(exc))


if __name__ == "__main__":
    try:
        App().mainloop()
    except SystemExit:
        LOGGER.info("application_exit_requested")
        raise
    except Exception as exc:
        LOGGER.exception("application_fatal_error")
        try:
            messagebox.showerror("启动失败", f"{exc}\n请查看 data/logs/error.log")
        except Exception:
            pass
        raise
