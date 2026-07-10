import csv
import json
import logging
import math
import os
import shutil
import sqlite3
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
from tkinter import filedialog, messagebox, ttk


APP_NAME = "咨询项目全流程需求管理系统"
APP_VERSION = "1.3.2"
APP_VARIANT = "SQLite 本地版"
HOST_NAME = socket.gethostname()
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
    "咨询负责人": {"project", "plan", "version", "requirement_create", "requirement_edit", "requirement_delete", "requirement_assign", "status", "budget", "artifact", "approve", "export"},
    "客户": {"requirement_create"},
    "销售": {"requirement_create", "export"},
    "项目经理": {"requirement_create", "requirement_edit", "status", "budget", "artifact", "export"},
    "研发人员": {"requirement_edit", "status", "artifact"},
    "运营人员": {"requirement_create", "requirement_edit", "status", "artifact"},
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
STATUS_COLORS = {
    "草稿": "#edf1ef",
    "规划中": "#e6f0f5",
    "已排期": "#e7f3ed",
    "研发中": "#fff2d9",
    "待验收": "#fcebdc",
    "已上线运维": "#e3f2ef",
    "已关闭": "#e8edeb",
    "已驳回": "#fbe7e5",
    "已挂起": "#eee9f6",
    "已取消": "#eceeed",
    "变更中": "#f5e9f2",
    "退回修改": "#f9e8e7",
}
ARTIFACT_STAGE_HINTS = {
    "可研报告": "宏观规划",
    "分年任务申报书": "规划细化",
    "任务书方案": "建设落地",
    "招标文件": "招投标",
    "应标文件": "招投标",
    "验收报告": "项目交付验收",
    "项目总结": "项目交付验收",
    "运维反馈": "运维运营",
    "运营反馈": "运维运营",
}
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


def csv_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def app_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def env_int(name, default, minimum, maximum):
    try:
        return min(maximum, max(minimum, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


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


class Database:
    def __init__(self, base_dir: Path, data_dir=None):
        self.base_dir = base_dir
        configured_data_dir = data_dir or os.environ.get("CRM_DATA_DIR", "")
        self.data_dir = Path(configured_data_dir).expanduser().resolve() if configured_data_dir else base_dir / "data"
        if self.data_dir.resolve() == Path(self.data_dir.resolve().anchor):
            raise RuntimeError("CRM_DATA_DIR 不能配置为磁盘根目录。")
        self.attachments_dir = self.data_dir / "attachments"
        self.backups_dir = self.data_dir / "backups"
        self.exports_dir = self.data_dir / "exports"
        self.logs_dir = self.data_dir / "logs"
        for folder in [self.data_dir, self.attachments_dir, self.backups_dir, self.exports_dir, self.logs_dir]:
            folder.mkdir(parents=True, exist_ok=True)
        configure_logging(self.logs_dir, "SQLite local")
        LOGGER.info("database_initializing engine=sqlite path=%s", self.data_dir / "app.db")
        self.db_path = self.data_dir / "app.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()
        self.seed_defaults()

    def execute(self, sql, params=()):
        try:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur
        except Exception:
            self.conn.rollback()
            LOGGER.exception("sqlite_execute_failed sql=%s", sql_summary(sql))
            raise

    def record_budget_flow(self, flow_code, project_id, annual_plan_id, version_id, requirement_id,
                           flow_type, amount, description, operator_name, occurred_at, allow_actual_overrun=False):
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            req = None
            version = self.conn.execute("SELECT version_budget, is_frozen FROM implementation_versions WHERE id=?", (version_id,)).fetchone() if version_id else None
            if version_id and not version:
                raise ValueError("关联版本不存在。")
            if version and version["is_frozen"] and flow_type != "实际消耗":
                raise ValueError("版本已冻结，只允许继续登记实际消耗；预算分配或调整需在冻结前完成。")
            linked_types = {"已分配预算", "实际消耗", "调整金额"}
            if flow_type in linked_types:
                if not requirement_id:
                    raise ValueError(f"资金类型“{flow_type}”必须关联具体需求。")
                req = self.conn.execute("SELECT * FROM requirements WHERE id=? AND is_deleted=0", (requirement_id,)).fetchone()
                if not req:
                    raise ValueError("关联需求不存在或已删除。")
                if req["version_id"] != version_id:
                    raise ValueError("关联需求不属于当前版本。")
            if req and flow_type in {"已分配预算", "调整金额"}:
                new_value = float(req["allocated_budget"] or 0) + amount
                if new_value < 0:
                    raise ValueError("调整后的需求分配预算不能小于 0。")
                total = self.conn.execute("SELECT COALESCE(SUM(allocated_budget),0) total FROM requirements WHERE version_id=? AND is_deleted=0", (version_id,)).fetchone()
                projected = float(total["total"] or 0) + amount
                if version and projected > float(version["version_budget"] or 0):
                    raise ValueError(f"分配后版本需求预算 {money_text(projected)} 将超过版本预算 {money_text(version['version_budget'])}。")
            if req and flow_type == "实际消耗":
                new_actual = float(req["actual_cost"] or 0) + amount
                if new_actual > float(req["allocated_budget"] or 0) and not allow_actual_overrun:
                    raise ValueError("ACTUAL_OVERRUN")
            self.conn.execute("""INSERT INTO budget_flows(flow_code, project_id, annual_plan_id, version_id,
                                                           requirement_id, flow_type, amount, description,
                                                           operator_name, occurred_at, created_at)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                              (flow_code, project_id, annual_plan_id, version_id, requirement_id, flow_type,
                               amount, description, operator_name, occurred_at, occurred_at))
            if req and flow_type in {"已分配预算", "调整金额"}:
                self.conn.execute("UPDATE requirements SET allocated_budget=allocated_budget+?, updated_at=? WHERE id=?",
                                  (amount, occurred_at, requirement_id))
            elif req and flow_type == "实际消耗":
                self.conn.execute("UPDATE requirements SET actual_cost=actual_cost+?, updated_at=? WHERE id=?",
                                  (amount, occurred_at, requirement_id))
            event_id = new_event_id()
            self.conn.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                              operation_type, before_value, after_value, description,
                                                              event_id, result)
                                 VALUES(?,?,'budget_flow',?,'create','',?,?,?,'success')""",
                              (operator_name, occurred_at, requirement_id, flow_code, f"登记资金流水：{flow_type}", event_id))
            self.conn.commit()
            audit_event(operator_name, "budget_flow", requirement_id, "create", f"登记资金流水：{flow_type}", event_id=event_id)
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("record_budget_flow", exc)
            raise

    def freeze_version_with_baseline(self, version_id, operator_name, occurred_at):
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            version = self.conn.execute("SELECT * FROM implementation_versions WHERE id=?", (version_id,)).fetchone()
            if not version:
                raise ValueError("版本不存在。")
            if version["is_frozen"]:
                self.conn.rollback()
                return None
            summary = self.conn.execute("""SELECT COUNT(*) requirement_count,
                                                  COALESCE(SUM(allocated_budget),0) allocated_budget,
                                                  COALESCE(SUM(actual_cost),0) actual_cost
                                           FROM requirements WHERE version_id=? AND is_deleted=0""", (version_id,)).fetchone()
            last = self.conn.execute("SELECT COALESCE(MAX(snapshot_no),0) snapshot_no FROM version_baselines WHERE version_id=?", (version_id,)).fetchone()
            snapshot_no = int(last["snapshot_no"] or 0) + 1
            cur = self.conn.execute("""INSERT INTO version_baselines(version_id, snapshot_no, version_budget,
                                                                     requirement_count, allocated_budget, actual_cost,
                                                                     created_by, created_at)
                                      VALUES(?,?,?,?,?,?,?,?)""",
                                    (version_id, snapshot_no, version["version_budget"], summary["requirement_count"],
                                     summary["allocated_budget"], summary["actual_cost"], operator_name, occurred_at))
            baseline_id = cur.lastrowid
            self.conn.execute("""INSERT INTO version_baseline_requirements(
                                     baseline_id, requirement_id, requirement_code, requirement_name, status,
                                     priority, allocated_budget, actual_cost, updated_at)
                                 SELECT ?, id, requirement_code, requirement_name, status, priority,
                                        allocated_budget, actual_cost, updated_at
                                 FROM requirements WHERE version_id=? AND is_deleted=0""", (baseline_id, version_id))
            cur = self.conn.execute("""UPDATE implementation_versions SET is_frozen=1, status='frozen', updated_at=?
                                       WHERE id=? AND is_frozen=0""", (occurred_at, version_id))
            if cur.rowcount != 1:
                raise ValueError("版本冻结状态已被其他操作更新，请刷新后重试。")
            event_id = new_event_id()
            self.conn.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                              operation_type, before_value, after_value, description,
                                                              event_id, result)
                                 VALUES(?,?,?,?,?,?,?,?,?,'success')""",
                              (operator_name, occurred_at, "implementation_version", version_id, "freeze", "",
                               f"baseline:{baseline_id}", f"冻结版本并生成基线 #{snapshot_no}", event_id))
            self.conn.commit()
            audit_event(operator_name, "implementation_version", version_id, "freeze", f"冻结版本并生成基线 #{snapshot_no}", event_id=event_id)
            return {"baseline_id": baseline_id, "snapshot_no": snapshot_no}
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("freeze_version", exc)
            raise

    def review_change_request(self, change_id, status, operator_name, occurred_at):
        if status not in {"approved", "rejected"}:
            raise ValueError("无效的审批状态。")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            change = self.conn.execute("SELECT * FROM change_requests WHERE id=?", (change_id,)).fetchone()
            if not change:
                raise ValueError("变更申请不存在。")
            if change["approval_status"] != "pending":
                raise ValueError("该变更申请已被其他操作处理，请刷新后重试。")
            payload = self.conn.execute("SELECT * FROM change_request_payloads WHERE change_request_id=?", (change_id,)).fetchone()
            if status == "approved" and change["requirement_id"] and payload:
                requirement = self.conn.execute("SELECT * FROM requirements WHERE id=?", (change["requirement_id"],)).fetchone()
                if not requirement or requirement["is_deleted"]:
                    raise ValueError("关联需求不存在或已删除，无法应用变更。")
                if payload["change_type"] == "delete":
                    self.conn.execute("UPDATE requirements SET is_deleted=1, updated_at=? WHERE id=?",
                                      (occurred_at, change["requirement_id"]))
                elif payload["change_type"] == "update":
                    proposed = json.loads(payload["proposed_value"] or "{}")
                    required_values = [proposed.get("requirement_name"), proposed.get("requirement_description"), proposed.get("source_role")]
                    if not all(str(value or "").strip() for value in required_values):
                        raise ValueError("变更内容缺少需求名称、描述或来源角色。")
                    estimated_budget = float(proposed.get("estimated_budget", 0) or 0)
                    if not math.isfinite(estimated_budget) or estimated_budget < 0:
                        raise ValueError("变更后的预估预算必须是大于等于 0 的有限数值。")
                    planned_finish = normalize_date(proposed.get("planned_finish_date", ""), "预计完成时间")
                    self.conn.execute("""UPDATE requirements SET requirement_name=?, requirement_description=?,
                                           source_role=?, proposer_name=?, owner_name=?, requirement_type=?, tags=?,
                                           priority=?, estimated_budget=?, planned_finish_date=?, remark=?,
                                           status='变更中', updated_at=? WHERE id=?""",
                                      (proposed.get("requirement_name", ""), proposed.get("requirement_description", ""),
                                       proposed.get("source_role", ""), proposed.get("proposer_name", ""), proposed.get("owner_name", ""),
                                       proposed.get("requirement_type", ""), proposed.get("tags", ""), proposed.get("priority", "P1"),
                                       estimated_budget, planned_finish, proposed.get("remark", ""),
                                       occurred_at, change["requirement_id"]))
                    self.conn.execute("""INSERT INTO requirement_status_history(requirement_id, from_status, to_status,
                                                                                  operator_name, transition_note, changed_at)
                                         VALUES(?,?,'变更中',?,?,?)""",
                                      (change["requirement_id"], requirement["status"], operator_name,
                                       f"变更申请 #{change_id} 审批通过", occurred_at))
                else:
                    raise ValueError("变更申请内容类型无效。")
            cur = self.conn.execute("""UPDATE change_requests SET approval_status=?, approved_by=?, approved_at=?
                                       WHERE id=? AND approval_status='pending'""",
                                    (status, operator_name, occurred_at, change_id))
            if cur.rowcount != 1:
                raise ValueError("该变更申请已被其他操作处理，请刷新后重试。")
            event_id = new_event_id()
            self.conn.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                              operation_type, before_value, after_value, description,
                                                              event_id, result)
                                 VALUES(?,?,?,?,?,?,?,?,?,'success')""",
                              (operator_name, occurred_at, "change_request", change_id, status, str(dict(change)), status,
                               "变更申请通过" if status == "approved" else "变更申请驳回", event_id))
            self.conn.commit()
            audit_event(operator_name, "change_request", change_id, status, "变更申请通过" if status == "approved" else "变更申请驳回", event_id=event_id)
            return change
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("review_change_request", exc)
            raise

    def transition_requirement_status(self, requirement_id, from_status, to_status, note, operator_name, occurred_at):
        if to_status not in STATUS_TRANSITIONS.get(from_status, []):
            raise ValueError(f"不允许从“{from_status}”直接流转到“{to_status}”。")
        if not str(note or "").strip():
            raise ValueError("状态流转说明不能为空。")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            requirement = self.conn.execute("SELECT status FROM requirements WHERE id=? AND is_deleted=0", (requirement_id,)).fetchone()
            if not requirement or requirement["status"] != from_status:
                self.conn.rollback()
                return False
            cur = self.conn.execute("""UPDATE requirements SET status=?, remark=?, updated_at=?,
                                       actual_finish_date=CASE WHEN ?='已关闭' THEN ? WHEN ?!='已关闭' THEN NULL ELSE actual_finish_date END
                                       WHERE id=? AND status=?""",
                                    (to_status, note, occurred_at, to_status, occurred_at[:10], to_status, requirement_id, from_status))
            if cur.rowcount != 1:
                self.conn.rollback()
                return False
            self.conn.execute("""INSERT INTO requirement_status_history(requirement_id, from_status, to_status,
                                                                          operator_name, transition_note, changed_at)
                                 VALUES(?,?,?,?,?,?)""",
                              (requirement_id, from_status, to_status, operator_name, note, occurred_at))
            event_id = new_event_id()
            self.conn.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                              operation_type, before_value, after_value, description,
                                                              event_id, result)
                                 VALUES(?,?,'requirement',?,'status_change',?,?,?,?, 'success')""",
                              (operator_name, occurred_at, requirement_id, from_status, to_status, f"需求状态流转：{note}", event_id))
            self.conn.commit()
            audit_event(operator_name, "requirement", requirement_id, "status_change", f"{from_status} -> {to_status}", event_id=event_id)
            return True
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("transition_requirement_status", exc)
            raise

    def query(self, sql, params=()):
        try:
            return self.conn.execute(sql, params).fetchall()
        except Exception:
            LOGGER.exception("sqlite_query_failed sql=%s", sql_summary(sql))
            raise

    def one(self, sql, params=()):
        try:
            return self.conn.execute(sql, params).fetchone()
        except Exception:
            LOGGER.exception("sqlite_query_one_failed sql=%s", sql_summary(sql))
            raise

    def close(self):
        try:
            self.conn.close()
            LOGGER.info("database_connection_closed engine=sqlite")
        except Exception:
            LOGGER.exception("database_close_failed engine=sqlite")

    def healthcheck(self):
        integrity = self.conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")
        required_tables = {
            "planning_projects", "annual_plans", "implementation_versions", "requirements",
            "budget_flows", "artifacts", "operation_logs", "version_baselines",
        }
        existing = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(required_tables - existing)
        if missing:
            raise RuntimeError(f"缺少数据表：{', '.join(missing)}")
        log_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(operation_logs)")}
        if not {"event_id", "result"}.issubset(log_columns):
            raise RuntimeError("operation_logs 缺少 event_id/result 审计字段。")
        for folder in [self.data_dir, self.attachments_dir, self.backups_dir, self.exports_dir, self.logs_dir]:
            verify_directory_writable(folder)
        minimum_free = env_int("CRM_MIN_FREE_BYTES", 512 * 1024 * 1024, 0, 10 * 1024 * 1024 * 1024 * 1024)
        free_bytes = shutil.disk_usage(self.data_dir).free
        if free_bytes < minimum_free:
            raise RuntimeError(f"磁盘剩余空间低于阈值：{free_bytes} < {minimum_free}")
        return {"database": str(self.db_path), "data_dir": str(self.data_dir), "free_bytes": free_bytes}

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
            description TEXT,
            event_id TEXT,
            result TEXT DEFAULT 'success'
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
        CREATE TABLE IF NOT EXISTS requirement_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requirement_id INTEGER NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            operator_name TEXT,
            transition_note TEXT,
            changed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS version_baselines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id INTEGER NOT NULL,
            snapshot_no INTEGER NOT NULL,
            version_budget REAL DEFAULT 0,
            requirement_count INTEGER DEFAULT 0,
            allocated_budget REAL DEFAULT 0,
            actual_cost REAL DEFAULT 0,
            created_by TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(version_id, snapshot_no)
        );
        CREATE TABLE IF NOT EXISTS version_baseline_requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baseline_id INTEGER NOT NULL,
            requirement_id INTEGER NOT NULL,
            requirement_code TEXT NOT NULL,
            requirement_name TEXT NOT NULL,
            status TEXT,
            priority TEXT,
            allocated_budget REAL DEFAULT 0,
            actual_cost REAL DEFAULT 0,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS change_request_payloads (
            change_request_id INTEGER PRIMARY KEY,
            change_type TEXT NOT NULL,
            proposed_value TEXT
        );
        """
        self.conn.executescript(schema)
        operation_log_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(operation_logs)")}
        if "event_id" not in operation_log_columns:
            self.conn.execute("ALTER TABLE operation_logs ADD COLUMN event_id TEXT")
        if "result" not in operation_log_columns:
            self.conn.execute("ALTER TABLE operation_logs ADD COLUMN result TEXT DEFAULT 'legacy'")
        self.conn.execute("UPDATE operation_logs SET result='legacy' WHERE event_id IS NULL")
        log_indexes = {row[1]: row[2] for row in self.conn.execute("PRAGMA index_list(operation_logs)")}
        if log_indexes.get("idx_operation_logs_event_id") != 1:
            self.conn.execute("DROP INDEX IF EXISTS idx_operation_logs_event_id")
            self.conn.execute("CREATE UNIQUE INDEX idx_operation_logs_event_id ON operation_logs(event_id) WHERE event_id IS NOT NULL")
        self.conn.commit()

    def seed_defaults(self):
        initialized = []
        if not self.one("SELECT id FROM users WHERE username='admin'"):
            self.execute(
                "INSERT INTO users(username, display_name, password_hash, role_name, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                ("admin", "默认管理员", "", "管理员", now_text(), now_text()),
            )
            initialized.append("默认管理员")
        seed_demo_data = os.environ.get("CRM_SEED_DEMO_DATA", "0").strip().lower() in {"1", "true", "yes", "on"}
        if seed_demo_data and not self.one("SELECT id FROM planning_projects"):
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
        colors = getattr(parent, "colors", {"surface": "#ffffff", "text": "#1e2927", "primary": "#176b87"})
        self.configure(background=colors["surface"])
        body = ttk.Frame(self, padding=16, style="Surface.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(body, wrap=tk.WORD, relief=tk.FLAT, padx=12, pady=12,
                       bg=colors["surface"], fg=colors["text"], selectbackground="#cfe2e1")
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
        colors = getattr(parent, "colors", {"surface": "#ffffff", "text": "#1e2927", "line": "#d5ddda"})
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
            elif kind == "combo":
                var = tk.StringVar(value=value or (options[0] if options else ""))
                widget = ttk.Combobox(body, textvariable=var, values=options, state="readonly", width=40)
            elif kind == "readonly":
                var = tk.StringVar(value=value)
                widget = ttk.Entry(body, textvariable=var, width=42, state="readonly")
            else:
                var = tk.StringVar(value=value)
                widget = tk.Text(body, width=42, height=5, bg="#fbfcfc", fg=colors["text"],
                                 insertbackground=colors["text"], highlightthickness=1,
                                 highlightbackground=colors["line"], highlightcolor="#3d7480", relief=tk.FLAT)
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
            values[key] = widget.get("1.0", tk.END).strip() if kind == "memo" else var.get().strip()
        missing = [key for key in self.required if not values.get(key)]
        if missing:
            messagebox.showwarning("必填项缺失", "请补充标记为 * 的必填项。", parent=self)
            return
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
        self.requirement_scope = tk.StringVar(value="当前版本")
        self.requirement_status_filter = tk.StringVar(value="全部状态")
        self.change_status_filter = tk.StringVar(value="全部")
        self.operation_log_type_filter = tk.StringVar(value="全部")
        self.operation_log_keyword = tk.StringVar()
        self.content = None
        self.current_page = "首页工作台"
        self.configure_style()
        self.build_layout()
        self.refresh_contexts()
        self.show_dashboard()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        LOGGER.info("application_started version=%s variant=sqlite user=%s", APP_VERSION, self.current_user)

    def report_callback_exception(self, exc_type, exc_value, exc_traceback):
        LOGGER.error("unhandled_tk_callback", exc_info=(exc_type, exc_value, exc_traceback))
        messagebox.showerror("系统错误", f"操作执行失败：{exc_value}\n详细信息已写入：{self.db.logs_dir / 'error.log'}")

    def open_logs_directory(self):
        try:
            if os.name == "nt":
                os.startfile(str(self.db.logs_dir))
            else:
                subprocess.Popen(["xdg-open", str(self.db.logs_dir)])
        except Exception as exc:
            LOGGER.exception("open_logs_directory_failed path=%s", self.db.logs_dir)
            messagebox.showerror("打开失败", str(exc))

    def run_healthcheck(self):
        try:
            details = self.db.healthcheck()
            self.db.log(self.current_user, "healthcheck", None, "check", "", "success", "本地版部署健康检查通过")
            LOGGER.info("healthcheck_succeeded variant=sqlite")
            messagebox.showinfo("健康检查通过", json.dumps(details, ensure_ascii=False, indent=2))
        except Exception as exc:
            LOGGER.exception("healthcheck_failed variant=sqlite")
            try:
                self.db.log(self.current_user, "healthcheck", None, "check", "", "failed",
                            "本地版部署健康检查失败", result="failed")
            except Exception:
                audit_event(self.current_user, "healthcheck", None, "check", "本地版部署健康检查失败", result="failed")
            messagebox.showerror("健康检查失败", f"{exc}\n请查看 {self.db.logs_dir / 'error.log'}")

    def on_close(self):
        LOGGER.info("application_stopping user=%s", self.current_user)
        self.db.close()
        LOGGER.info("application_stopped")
        close_logging()
        self.destroy()

    def configure_style(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.colors = {
            "bg": "#f2f5f3",
            "surface": "#ffffff",
            "surface_alt": "#f7f9f8",
            "side": "#24312f",
            "side_hover": "#30443f",
            "side_active": "#557f72",
            "text": "#1e2927",
            "muted": "#60706b",
            "line": "#d5ddda",
            "primary": "#176b87",
            "primary_active": "#12566c",
            "success": "#287a4b",
            "warning": "#9a5a00",
            "danger": "#b74643",
        }
        self.configure(background=self.colors["bg"])
        font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=font, foreground=self.colors["text"])
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("Surface.TFrame", background=self.colors["surface"])
        style.configure("Topbar.TFrame", background=self.colors["surface"])
        style.configure("Statusbar.TFrame", background="#e8eeeb")
        style.configure("Side.TFrame", background=self.colors["side"])
        style.configure("Side.TButton", background=self.colors["side"], foreground="#eaf0ee", anchor="w", padding=(16, 10), borderwidth=0)
        style.map("Side.TButton", background=[("active", self.colors["side_hover"])], foreground=[("active", "#ffffff")])
        style.configure("SideActive.TButton", background=self.colors["side_active"], foreground="#ffffff", anchor="w", padding=(16, 10), borderwidth=0)
        style.map("SideActive.TButton", background=[("active", self.colors["side_active"])])
        style.configure("TButton", background=self.colors["surface"], foreground=self.colors["text"], padding=(11, 7), borderwidth=1, relief="solid")
        style.map("TButton", background=[("active", "#eaf0ed")], bordercolor=[("focus", self.colors["primary"])])
        style.configure("Primary.TButton", background=self.colors["primary"], foreground="#ffffff", padding=(12, 7), borderwidth=0)
        style.map("Primary.TButton", background=[("active", self.colors["primary_active"]), ("disabled", "#9eaaa7")], foreground=[("disabled", "#edf1ef")])
        style.configure("TEntry", fieldbackground=self.colors["surface"], foreground=self.colors["text"], bordercolor=self.colors["line"], padding=5)
        style.map("TEntry", bordercolor=[("focus", "#3d7480")])
        style.configure("TCombobox", fieldbackground=self.colors["surface"], foreground=self.colors["text"], bordercolor=self.colors["line"], padding=4)
        style.map("TCombobox", bordercolor=[("focus", "#3d7480")], fieldbackground=[("readonly", self.colors["surface"])])
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 15, "bold"), background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("SubTitle.TLabel", font=("Microsoft YaHei UI", 10), background=self.colors["bg"], foreground=self.colors["muted"])
        style.configure("Surface.TLabel", background=self.colors["surface"], foreground=self.colors["text"])
        style.configure("Muted.TLabel", background=self.colors["surface"], foreground=self.colors["muted"])
        style.configure("RoleBanner.TLabel", background="#e3efec", foreground="#315f56", padding=(10, 8))
        style.configure("Status.TLabel", background="#e8eeeb", foreground="#4f625d")
        style.configure("Metric.TLabel", font=("Microsoft YaHei UI", 19, "bold"), background=self.colors["surface"], foreground=self.colors["text"])
        style.configure("Card.TFrame", background=self.colors["surface"], relief="solid", borderwidth=1,
                        bordercolor=self.colors["line"], lightcolor=self.colors["line"], darkcolor=self.colors["line"])
        style.configure("Treeview", rowheight=31, fieldbackground=self.colors["surface"], background=self.colors["surface"], foreground=self.colors["text"], bordercolor=self.colors["line"])
        style.map("Treeview", background=[("selected", self.colors["side_active"])], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), background="#e6ece9", foreground="#30413d", padding=(7, 8), relief="flat")
        style.map("Treeview.Heading", background=[("active", "#dce5e1")])

    def build_layout(self):
        top = ttk.Frame(self, padding=(14, 11), style="Topbar.TFrame")
        top.pack(fill=tk.X)
        ttk.Label(top, text="项目", style="Surface.TLabel").pack(side=tk.LEFT)
        self.project_box = ttk.Combobox(top, textvariable=self.selected_project, state="readonly", width=22)
        self.project_box.pack(side=tk.LEFT, padx=(6, 10))
        self.project_box.bind("<<ComboboxSelected>>", lambda e: self.on_project_change())
        ttk.Label(top, text="年度", style="Surface.TLabel").pack(side=tk.LEFT)
        self.plan_box = ttk.Combobox(top, textvariable=self.selected_plan, state="readonly", width=18)
        self.plan_box.pack(side=tk.LEFT, padx=(6, 10))
        self.plan_box.bind("<<ComboboxSelected>>", lambda e: self.on_plan_change())
        ttk.Label(top, text="版本", style="Surface.TLabel").pack(side=tk.LEFT)
        self.version_box = ttk.Combobox(top, textvariable=self.selected_version, state="readonly", width=18)
        self.version_box.pack(side=tk.LEFT, padx=(6, 10))
        self.version_box.bind("<<ComboboxSelected>>", lambda e: self.reload_page())
        ttk.Entry(top, textvariable=self.search_var, width=20).pack(side=tk.LEFT, padx=(6, 5))
        ttk.Button(top, text="搜索", command=self.show_search, style="Primary.TButton", width=8).pack(side=tk.LEFT)
        role_box = ttk.Combobox(top, textvariable=self.current_role, values=ROLES, state="readonly", width=12)
        role_box.pack(side=tk.RIGHT)
        ttk.Label(top, text="角色", style="Surface.TLabel").pack(side=tk.RIGHT, padx=(12, 5))
        role_box.bind("<<ComboboxSelected>>", lambda e: self.reload_page())
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True)
        side = ttk.Frame(main, style="Side.TFrame", width=196)
        side.pack(side=tk.LEFT, fill=tk.Y)
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=self.colors["side"], height=78)
        brand.pack(fill=tk.X)
        brand.pack_propagate(False)
        tk.Label(brand, text="咨询项目全流程\n需求管理系统", bg=self.colors["side"], fg="#ffffff",
                 font=("Microsoft YaHei UI", 11, "bold"), justify=tk.LEFT, anchor="w").pack(fill=tk.BOTH, padx=16, pady=(15, 12))
        self.nav_buttons = {}
        for name, cmd in [
            ("首页工作台", self.show_dashboard), ("项目管理", self.show_projects), ("年度计划", self.show_plans),
            ("版本管理", self.show_versions), ("需求管理", self.show_requirements), ("资金管理", self.show_budget),
            ("成果物管理", self.show_artifacts), ("流程里程碑", self.show_milestones), ("搜索中心", self.show_search), ("报表导出", self.show_exports),
            ("系统设置", self.show_settings),
        ]:
            button = ttk.Button(side, text=name, style="Side.TButton", command=cmd)
            button.pack(fill=tk.X, padx=9, pady=2)
            self.nav_buttons[name] = button
        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.content_canvas = tk.Canvas(right, bg=self.colors["bg"], highlightthickness=0)
        self.content_scrollbar = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.content_canvas.yview)
        self.content_canvas.configure(yscrollcommand=self.content_scrollbar.set)
        self.content_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.content_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.content = ttk.Frame(self.content_canvas, padding=(18, 16))
        self.content_window = self.content_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self.on_content_configure)
        self.content_canvas.bind("<Configure>", self.on_canvas_configure)
        self.bind("<MouseWheel>", self.on_mousewheel, add="+")
        bottom = ttk.Frame(self, padding=(12, 6), style="Statusbar.TFrame")
        bottom.pack(fill=tk.X)
        ttk.Label(bottom, text=f"数据库: {self.db.db_path}", style="Status.TLabel").pack(side=tk.LEFT)
        ttk.Label(bottom, text=f"版本: {APP_VERSION} · SQLite 本地版", style="Status.TLabel").pack(side=tk.RIGHT)

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
        for name, button in getattr(self, "nav_buttons", {}).items():
            button.configure(style="SideActive.TButton" if name == self.current_page else "Side.TButton")

    def context_summary(self):
        project = self.selected_project.get() or "未选择项目"
        plan = self.selected_plan.get() or "未选择年度"
        version = self.selected_version.get() or "未选择版本"
        version_row = self.current_version()
        frozen = "已冻结" if version_row and version_row["is_frozen"] else "可编辑"
        return f"项目：{project}    年度：{plan}    版本：{version}    基线状态：{frozen}"

    def section_title(self, parent, title, subtitle=""):
        box = ttk.Frame(parent)
        box.pack(fill=tk.X, pady=(10, 8))
        ttk.Label(box, text=title, font=("Microsoft YaHei UI", 12, "bold"), background=self.colors["bg"], foreground=self.colors["text"]).pack(anchor="w")
        if subtitle:
            ttk.Label(box, text=subtitle, style="SubTitle.TLabel").pack(anchor="w", pady=(2, 0))

    def notice_banner(self, parent, text, tone="info"):
        palette = {
            "info": ("#e5f0f4", "#176b87"),
            "success": ("#e4f1e9", "#287a4b"),
            "warning": ("#fff1d8", "#9a5a00"),
            "danger": ("#f9e7e5", "#a33d39"),
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

    def require_action(self, action, label):
        if self.can_action(action):
            return True
        description = f"{self.current_role.get()} 无权执行：{label}"
        try:
            self.db.log(self.current_user, "permission", None, "denied", "", action, description, result="denied")
        except Exception:
            LOGGER.exception("central_permission_audit_failed action=%s", action)
            audit_event(self.current_user, "permission", None, "denied", description, result="denied")
        LOGGER.warning("permission_denied role=%s action=%s label=%s", self.current_role.get(), action, label)
        messagebox.showwarning("权限不足", f"当前角色“{self.current_role.get()}”无权执行：{label}")
        return False

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
            "需求管理": "show_requirements", "资金管理": "show_budget", "成果物管理": "show_artifacts", "搜索中心": "show_search",
            "流程里程碑": "show_milestones", "报表导出": "show_exports", "系统设置": "show_settings",
        }.get(self.current_page, "show_dashboard"))()

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
        for index, row in enumerate(rows):
            tags = ["even" if index % 2 else "odd"]
            status = self.row_value(row, "status")
            if status in STATUS_COLORS:
                tags.append(f"status_{status}")
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
                # Configure/idle callbacks can arrive after a page switch has
                # already destroyed the previous table widgets.
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
            budget = self.db.one("""SELECT COALESCE(SUM(allocated_budget), 0) allocated, COALESCE(SUM(actual_cost), 0) cost
                                    FROM requirements WHERE is_deleted=0 AND project_id=?""", (project_id,))
            project = self.db.one("SELECT total_budget FROM planning_projects WHERE id=?", (project_id,))
            metrics = [
                ("项目总预算", money_text(project["total_budget"] if project else 0), "客户资金规划总盘", self.colors["primary"]),
                ("已分配预算", money_text(budget["allocated"]), f"占总预算 {percent_text(budget['allocated'], project['total_budget'] if project else 0)}", self.colors["success"]),
                ("实际消耗", money_text(budget["cost"]), f"执行率 {percent_text(budget['cost'], budget['allocated'])}", self.colors["danger"] if budget["allocated"] and budget["cost"] > budget["allocated"] else self.colors["warning"]),
            ]
            columns = [
                ("version_code", "版本编号", 110), ("version_name", "版本名称", 220), ("version_budget", "版本预算", 110),
                ("allocated_budget", "需求已分配", 110), ("actual_cost", "实际消耗", 110), ("status", "状态", 100),
            ]
            rows = self.db.query("""SELECT v.version_code, v.version_name, v.version_budget,
                                           COALESCE(SUM(r.allocated_budget), 0) allocated_budget,
                                           COALESCE(SUM(r.actual_cost), 0) actual_cost,
                                           v.status
                                    FROM implementation_versions v
                                    LEFT JOIN requirements r ON r.version_id=v.id AND r.is_deleted=0
                                    WHERE v.project_id=?
                                    GROUP BY v.id
                                    ORDER BY v.id DESC LIMIT 8""", (project_id,))
        elif role == "项目经理":
            metrics = [
                ("当前版本需求", self.requirement_count("version_id=?", [version_id]) if version_id else 0, "当前交付范围", self.colors["primary"]),
                ("待验收", self.requirement_count("version_id=? AND status='待验收'", [version_id]) if version_id else 0, "需要组织验收", self.colors["warning"]),
                ("成本风险", self.requirement_count("version_id=? AND allocated_budget>0 AND actual_cost>allocated_budget", [version_id]) if version_id else 0, "实际消耗超过分配预算", self.colors["danger"]),
            ]
            rows = self.db.query("""SELECT requirement_code, requirement_name, source_role, priority, status, owner_name, updated_at
                                    FROM requirements
                                    WHERE is_deleted=0 AND version_id=?
                                    ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END, updated_at DESC LIMIT 8""", (version_id,)) if version_id else []
        elif role == "研发人员":
            metrics = [
                ("待研发任务", self.requirement_count("version_id=? AND status IN ('已排期','研发中')", [version_id]) if version_id else 0, "按优先级推进", self.colors["primary"]),
                ("P0/P1", self.requirement_count("version_id=? AND priority IN ('P0','P1','高')", [version_id]) if version_id else 0, "高优先级任务", self.colors["warning"]),
                ("挂起/退回", self.requirement_count("version_id=? AND status IN ('已挂起','退回修改')", [version_id]) if version_id else 0, "需要协调处理", self.colors["danger"]),
            ]
            rows = self.db.query("""SELECT requirement_code, requirement_name, source_role, priority, status, owner_name, updated_at
                                    FROM requirements
                                    WHERE is_deleted=0 AND version_id=? AND status IN ('已排期','研发中','退回修改','已挂起')
                                    ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN '高' THEN 1 ELSE 2 END, updated_at DESC LIMIT 8""", (version_id,)) if version_id else []
        elif role == "运营人员":
            metrics = [
                ("上线运维", self.requirement_count("project_id=? AND status='已上线运维'", [project_id]), "上线后持续跟踪", self.colors["success"]),
                ("运维类需求", self.requirement_count("project_id=? AND requirement_type IN ('运维 Bug','验收整改')", [project_id]), "问题反馈池", self.colors["warning"]),
                ("待规划反馈", self.requirement_count("project_id=? AND version_id IS NULL", [project_id]), "需回归版本规划", self.colors["primary"]),
            ]
            rows = self.db.query("""SELECT requirement_code, requirement_name, source_role, priority, status, owner_name, updated_at
                                    FROM requirements
                                    WHERE is_deleted=0 AND project_id=? AND (requirement_type IN ('运维 Bug','验收整改') OR status='已上线运维')
                                    ORDER BY updated_at DESC LIMIT 8""", (project_id,))
        else:
            metrics = [
                ("待规划池", self.requirement_count("project_id=? AND version_id IS NULL", [project_id]), "尚未确认落地版本", self.colors["warning"]),
                ("冻结版本", self.db.one("SELECT COUNT(*) c FROM implementation_versions WHERE project_id=? AND is_frozen=1", (project_id,))["c"], "已形成基线", self.colors["primary"]),
                ("待审批变更", self.db.one("SELECT COUNT(*) c FROM change_requests WHERE approval_status='pending'")["c"], "冻结版本变更入口", self.colors["danger"]),
            ]
            rows = self.db.query("""SELECT requirement_code, requirement_name, source_role, priority, status, owner_name, updated_at
                                    FROM requirements
                                    WHERE is_deleted=0 AND project_id=?
                                    ORDER BY updated_at DESC LIMIT 8""", (project_id,))

        self.metric_grid(self.content, metrics, columns=3)
        self.add_table(self.content, columns, rows, height=7)

    def show_dashboard(self):
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
            "规划项目": self.db.one("SELECT COUNT(*) c FROM planning_projects")["c"],
            "年度计划": self.db.one("SELECT COUNT(*) c FROM annual_plans WHERE project_id=?", (project_id,))["c"] if project_id else 0,
            "落地版本": self.db.one("SELECT COUNT(*) c FROM implementation_versions WHERE project_id=?", (project_id,))["c"] if project_id else 0,
            "版本需求": self.db.one("SELECT COUNT(*) c FROM requirements WHERE is_deleted=0 AND version_id=?", (version_id,))["c"] if version_id else 0,
            "待规划需求": self.db.one("SELECT COUNT(*) c FROM requirements WHERE is_deleted=0 AND project_id=? AND version_id IS NULL", (project_id,))["c"] if project_id else 0,
        }
        row = ttk.Frame(self.content)
        row.pack(fill=tk.X)
        self.metric_card(row, "规划项目", counts["规划项目"], "系统项目总数")
        self.metric_card(row, "落地版本", counts["落地版本"], "当前项目版本数")
        self.metric_card(row, "版本需求", counts["版本需求"], "当前版本需求数")
        self.metric_card(row, "待规划需求", counts["待规划需求"], "尚未分配版本", self.colors["warning"] if counts["待规划需求"] else self.colors["success"])
        if self.can_view_money():
            row2 = ttk.Frame(self.content)
            row2.pack(fill=tk.X)
            self.metric_card(row2, "项目总预算", money_text(project_budget["total_budget"] if project_budget else 0), "宏观规划资金")
            self.metric_card(row2, "需求已分配", money_text(budget["allocated"]), f"占总预算 {percent_text(budget['allocated'], project_budget['total_budget'] if project_budget else 0)}")
            self.metric_card(row2, "实际消耗", money_text(budget["cost"]), f"执行率 {percent_text(budget['cost'], budget['allocated'])}", self.colors["danger"] if budget["cost"] > budget["allocated"] and budget["allocated"] else None)
        role = self.current_role.get()
        msg = ROLE_DESCRIPTIONS.get(role, ROLE_DESCRIPTIONS["管理员"])
        ttk.Label(self.content, text=f"当前视角：{role}。{msg}", style="RoleBanner.TLabel",
                  wraplength=920).pack(fill=tk.X, pady=(2, 12))
        self.role_dashboard_panel(project_id, version_id)
        self.section_title(self.content, "需求状态分布", "按当前项目统计，辅助判断项目推进节奏。")
        status_rows = self.db.query("""SELECT status, COUNT(*) c
                                       FROM requirements
                                       WHERE is_deleted=0 AND project_id=?
                                       GROUP BY status
                                       ORDER BY MIN(id)""", (project_id,)) if project_id else []
        status_cards = [
            (row_data["status"], row_data["c"], "点击查看该状态需求", None, lambda s=row_data["status"]: self.show_requirements_for_status(s))
            for row_data in status_rows
        ]
        if status_cards:
            self.metric_grid(self.content, status_cards, columns=4)
        if not status_rows:
            ttk.Label(self.content, text="暂无需求状态数据。", style="SubTitle.TLabel").pack(anchor="w", pady=(0, 10))
        self.section_title(self.content, "最近需求", "展示当前版本最近更新的需求，切换顶部版本后联动刷新。")
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
        columns = [("id", "ID", 50), ("project_code", "项目编号", 120), ("project_name", "项目名称", 240), ("customer_name", "客户", 160)]
        if self.can_view_money():
            columns.append(("total_budget", "总预算", 100))
        columns += [("status", "状态", 90), ("updated_at", "更新时间", 150)]
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
            except (ValueError, sqlite3.IntegrityError) as exc:
                messagebox.showerror("保存失败", str(exc))

    def show_plans(self):
        self.clear("年度计划")
        ttk.Button(self.content, text="新建年度计划", command=self.add_plan).pack(anchor="w", pady=(0, 8))
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
                project = self.db.one("SELECT total_budget FROM planning_projects WHERE id=?", (self.current_project_id(),))
                if annual_budget < 0:
                    raise ValueError("年度预算不能小于 0")
                if project and annual_budget > float(project["total_budget"] or 0):
                    raise ValueError("年度预算不能超过项目总预算")
                t = now_text()
                self.db.execute("INSERT INTO annual_plans(project_id, plan_year, plan_name, annual_budget, business_pain_points, plan_description, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                (self.current_project_id(), plan_year, d.result["plan_name"], annual_budget, d.result["business_pain_points"], d.result["plan_description"], t, t))
                self.db.log(self.current_user, "annual_plan", None, "create", "", d.result, "新建年度计划")
                self.refresh_contexts()
                self.show_plans()
            except ValueError as exc:
                messagebox.showerror("保存失败", str(exc))

    def show_versions(self):
        self.clear("版本管理")
        bar = self.make_action_bar(self.content)
        ttk.Button(bar, text="新建版本", command=self.add_version, style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(bar, text="冻结当前版本", command=self.freeze_version).pack(side=tk.LEFT, padx=(8, 0))
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
                plan = self.db.one("SELECT annual_budget FROM annual_plans WHERE id=?", (self.current_plan_id(),))
                if version_budget < 0:
                    raise ValueError("版本预算不能小于 0")
                if plan and version_budget > float(plan["annual_budget"] or 0):
                    raise ValueError("版本预算不能超过年度预算")
                t = now_text()
                self.db.execute("""INSERT INTO implementation_versions(project_id, annual_plan_id, version_code, version_name, version_goal, version_scope, version_budget, planned_start_date, planned_end_date, created_at, updated_at)
                                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                (self.current_project_id(), self.current_plan_id(), d.result["version_code"], d.result["version_name"], d.result["version_goal"], d.result["version_scope"], version_budget, planned_start, planned_end, t, t))
                self.db.log(self.current_user, "implementation_version", None, "create", "", d.result, "新建落地版本")
                self.refresh_contexts()
                self.show_versions()
            except ValueError as exc:
                messagebox.showerror("保存失败", str(exc))

    def freeze_version(self):
        if not self.require_action("version", "冻结版本"):
            return
        version_id = self.current_version_id()
        if not version_id:
            messagebox.showwarning("提示", "请先选择版本")
            return
        version = self.db.one("SELECT * FROM implementation_versions WHERE id=?", (version_id,))
        if version["is_frozen"]:
            messagebox.showinfo("提示", "当前版本已经冻结，并已生成基线。")
            return
        if not messagebox.askyesno("确认冻结", "冻结后该版本将作为基线，新增需求需走分配或变更流程。是否继续？"):
            return
        try:
            result = self.db.freeze_version_with_baseline(version_id, self.current_user, now_text())
            if result is None:
                messagebox.showinfo("提示", "当前版本已经由其他操作冻结，请刷新查看。")
            else:
                messagebox.showinfo("冻结完成", f"版本已冻结并生成基线 #{result['snapshot_no']}。")
            self.show_versions()
        except Exception as exc:
            messagebox.showerror("冻结失败", str(exc))

    def show_version_baseline(self):
        version_id = self.current_version_id()
        baseline = self.db.one("SELECT * FROM version_baselines WHERE version_id=? ORDER BY snapshot_no DESC LIMIT 1", (version_id,)) if version_id else None
        if not baseline:
            messagebox.showinfo("版本基线", "当前版本尚未生成基线，请先冻结版本。")
            return
        rows = self.db.query("SELECT requirement_code, requirement_name, status, priority, allocated_budget, actual_cost FROM version_baseline_requirements WHERE baseline_id=? ORDER BY id", (baseline["id"],))
        self.clear("版本基线")
        self.metric_grid(self.content, [
            ("基线编号", f"#{baseline['snapshot_no']}", baseline["created_at"], self.colors["primary"]),
            ("版本预算", money_text(baseline["version_budget"]), "冻结时预算", self.colors["warning"]),
            ("需求数量", baseline["requirement_count"], "冻结时需求", self.colors["success"]),
            ("实际消耗", money_text(baseline["actual_cost"]), f"已分配 {money_text(baseline['allocated_budget'])}", None),
        ], columns=4)
        self.add_table(self.content, [("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 240), ("status", "状态", 100), ("priority", "优先级", 80), ("allocated_budget", "分配预算", 110), ("actual_cost", "实际消耗", 110)], rows, 14)

    def compare_versions(self):
        version_options = list(self.versions.keys())
        if len(version_options) < 2:
            messagebox.showwarning("提示", "至少需要两个版本才能比对")
            return
        d = FieldDialog(self, "跨版本比对", [
            ("left", "基准版本", "combo", version_options),
            ("right", "对比版本", "combo", version_options),
        ], {"left": version_options[0], "right": version_options[-1]}, required=["left", "right"])
        if not d.result:
            return
        left_id = self.versions.get(d.result["left"])
        right_id = self.versions.get(d.result["right"])
        if left_id == right_id:
            messagebox.showwarning("提示", "请选择两个不同版本")
            return
        self.clear("跨版本比对")
        left = self.db.one("SELECT version_code, version_name, version_budget FROM implementation_versions WHERE id=?", (left_id,))
        right = self.db.one("SELECT version_code, version_name, version_budget FROM implementation_versions WHERE id=?", (right_id,))
        if self.can_view_money():
            row = ttk.Frame(self.content)
            row.pack(fill=tk.X)
            self.metric_card(row, "基准版本预算", money_text(left["version_budget"]), f"{left['version_code']} {left['version_name']}")
            self.metric_card(row, "对比版本预算", money_text(right["version_budget"]), f"{right['version_code']} {right['version_name']}")
            self.metric_card(row, "预算差额", money_text((right["version_budget"] or 0) - (left["version_budget"] or 0)), "对比版本 - 基准版本")
        left_rows = {r["requirement_name"].strip().casefold(): r for r in self.db.query("SELECT requirement_code, requirement_name, status, allocated_budget, actual_cost FROM requirements WHERE is_deleted=0 AND version_id=?", (left_id,))}
        right_rows = {r["requirement_name"].strip().casefold(): r for r in self.db.query("SELECT requirement_code, requirement_name, status, allocated_budget, actual_cost FROM requirements WHERE is_deleted=0 AND version_id=?", (right_id,))}
        diff_rows = []
        for key in sorted(set(left_rows) | set(right_rows)):
            l = left_rows.get(key)
            r = right_rows.get(key)
            if not l:
                diff_type = "新增"
            elif not r:
                diff_type = "移除"
            elif (l["status"], l["allocated_budget"], l["actual_cost"], l["requirement_name"]) != (r["status"], r["allocated_budget"], r["actual_cost"], r["requirement_name"]):
                diff_type = "变更"
            else:
                diff_type = "一致"
            diff_rows.append({
                "diff_type": diff_type,
                "requirement_code": f"{l['requirement_code'] if l else '-'} / {r['requirement_code'] if r else '-'}",
                "left_name": l["requirement_name"] if l else "",
                "right_name": r["requirement_name"] if r else "",
                "left_status": l["status"] if l else "",
                "right_status": r["status"] if r else "",
                "left_budget": l["allocated_budget"] if l else "",
                "right_budget": r["allocated_budget"] if r else "",
            })
        self.section_title(self.content, "需求差异", "按标准化需求名称匹配，编号列展示“基准 / 对比”。")
        columns = [("diff_type", "差异类型", 90), ("requirement_code", "需求编号", 130), ("left_name", "基准版本需求", 220), ("right_name", "对比版本需求", 220), ("left_status", "基准状态", 100), ("right_status", "对比状态", 100)]
        if self.can_view_money():
            columns += [("left_budget", "基准预算", 100), ("right_budget", "对比预算", 100)]
        self.add_table(self.content, columns, diff_rows, 14)

    def show_requirements(self):
        self.clear("需求管理")
        version = self.current_version()
        if version and version["is_frozen"]:
            self.notice_banner(self.content, "当前版本已冻结并形成基线。需求核心信息不可直接修改，编辑或删除将自动转入变更申请。", "warning")
        bar = self.make_action_bar(self.content)
        ttk.Button(bar, text="新建需求", command=self.add_requirement, style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(bar, text="查看详情", command=self.show_requirement_detail).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bar, text="编辑需求", command=self.edit_requirement).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bar, text="状态流转", command=self.advance_requirement_status).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bar, text="分配到当前版本", command=self.assign_requirement_to_current_version).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bar, text="删除需求", command=self.delete_requirement).pack(side=tk.LEFT, padx=(8, 0))
        if self.can_action("export"):
            ttk.Button(bar, text="导出需求清单", command=self.export_requirements).pack(side=tk.LEFT, padx=(8, 0))
        filters = ttk.Frame(self.content)
        filters.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(filters, text="范围").pack(side=tk.LEFT)
        scope_box = ttk.Combobox(filters, textvariable=self.requirement_scope, values=["当前版本", "待规划池", "当前项目全部"], state="readonly", width=14)
        scope_box.pack(side=tk.LEFT, padx=(6, 12))
        scope_box.bind("<<ComboboxSelected>>", lambda e: self.show_requirements())
        ttk.Label(filters, text="状态").pack(side=tk.LEFT)
        status_box = ttk.Combobox(filters, textvariable=self.requirement_status_filter, values=["全部状态"] + STATUS_FLOW + EXTRA_STATUSES, state="readonly", width=14)
        status_box.pack(side=tk.LEFT, padx=(6, 12))
        status_box.bind("<<ComboboxSelected>>", lambda e: self.show_requirements())
        role = self.current_role.get()
        cols = [("id", "ID", 50), ("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 230), ("source_role", "来源", 90), ("requirement_type", "类型", 100), ("tags", "标签", 160), ("priority", "优先级", 70), ("status", "状态", 110)]
        if role not in SENSITIVE_ROLES:
            cols += [("estimated_budget", "预估预算", 100), ("allocated_budget", "分配预算", 100), ("actual_cost", "实际消耗", 100)]
        cols += [("version_name", "所属版本", 160), ("owner_name", "对接人", 100), ("updated_at", "更新时间", 150)]
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
        rows = self.db.query(f"""SELECT r.*, COALESCE(v.version_code || ' ' || v.version_name, '待规划') version_name
                                 FROM requirements r
                                 LEFT JOIN implementation_versions v ON r.version_id=v.id
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
        version_options = ["待规划池"]
        version_options.extend(list(self.versions.keys()))
        d = FieldDialog(self, "新建需求", [
            ("requirement_code", "需求编号", "text", None), ("requirement_name", "需求名称", "text", None), ("source_role", "来源角色", "combo", ["客户", "销售", "项目经理", "研发", "运营", "咨询负责人"]),
            ("proposer_name", "提出人", "text", None), ("owner_name", "对接人", "text", None), ("requirement_type", "需求类型", "combo", ["业务痛点", "功能优化", "运维 Bug", "招投标要求", "验收整改", "客户新增"]),
            ("version_option", "所属版本", "combo", version_options),
            ("tags", "标签", "text", None), ("priority", "优先级", "combo", ["P0", "P1", "P2", "高", "中", "低"]), ("status", "状态", "combo", STATUS_FLOW + EXTRA_STATUSES),
            ("estimated_budget", "预估预算", "text", None),
            ("planned_finish_date", "预计完成时间", "text", None), ("requirement_description", "需求描述", "memo", None), ("remark", "备注", "memo", None),
        ], {"requirement_code": code, "priority": "P1", "status": "草稿", "version_option": self.selected_version.get() or "待规划池"}, required=["requirement_code", "requirement_name", "requirement_description", "source_role"])
        if d.result:
            try:
                version_id = None if d.result["version_option"] == "待规划池" else self.versions.get(d.result["version_option"])
                plan_id = self.current_plan_id() if version_id else None
                selected_version = self.db.one("SELECT is_frozen FROM implementation_versions WHERE id=?", (version_id,)) if version_id else None
                if selected_version and selected_version["is_frozen"]:
                    messagebox.showwarning("版本已冻结", "目标版本已冻结，请先走变更流程。")
                    return
                estimated_budget = self.parse_float(d.result["estimated_budget"], "预估预算")
                planned_finish = normalize_date(d.result["planned_finish_date"], "预计完成时间")
                if estimated_budget < 0:
                    raise ValueError("预估预算不能小于 0")
                allocated_budget = 0
                actual_cost = 0
                t = now_text()
                cur = self.db.execute("""INSERT INTO requirements(requirement_code, requirement_name, requirement_description, source_role, proposer_name, owner_name, project_id, annual_plan_id, version_id,
                                   requirement_type, tags, priority, status, estimated_budget, allocated_budget, actual_cost, planned_finish_date, remark, created_at, updated_at)
                                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (d.result["requirement_code"], d.result["requirement_name"], d.result["requirement_description"], d.result["source_role"], d.result["proposer_name"], d.result["owner_name"],
                                 self.current_project_id(), plan_id, version_id, d.result["requirement_type"], d.result["tags"], d.result["priority"], d.result["status"],
                                 estimated_budget, allocated_budget, actual_cost, planned_finish, d.result["remark"], t, t))
                requirement_id = cur.lastrowid
                self.db.execute("INSERT INTO requirement_status_history(requirement_id, from_status, to_status, operator_name, transition_note, changed_at) VALUES(?,?,?,?,?,?)",
                                (requirement_id, "", d.result["status"], self.current_user, "新建需求", t))
                self.db.log(self.current_user, "requirement", requirement_id, "create", "", d.result, "新建需求任务")
                self.show_requirements()
            except (ValueError, sqlite3.IntegrityError) as exc:
                messagebox.showerror("保存失败", str(exc))

    def selected_requirement_id(self):
        sel = getattr(self, "req_tree", None).selection() if hasattr(self, "req_tree") else ()
        if not sel:
            messagebox.showwarning("提示", "请先在需求表中选择一行")
            return None
        value = self.req_tree.item(sel[0])["values"][0]
        try:
            return int(value)
        except (TypeError, ValueError):
            messagebox.showwarning("提示", "当前行不是有效需求")
            return None

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
        self.db.execute("UPDATE requirements SET annual_plan_id=?, version_id=?, updated_at=? WHERE id=?",
                        (self.current_plan_id(), version_id, now_text(), req_id))
        self.db.log(self.current_user, "requirement", req_id, "assign_version", "", version_id, "需求分配到当前版本")
        self.requirement_scope.set("当前版本")
        self.show_requirements()

    def show_requirement_detail(self):
        req_id = self.selected_requirement_id()
        if not req_id:
            return
        self.open_requirement_detail(req_id)

    def open_requirement_detail(self, req_id):
        req = self.db.one("""SELECT r.*, p.project_name, ap.plan_name, COALESCE(v.version_code || ' ' || v.version_name, '待规划') version_name
                             FROM requirements r
                             LEFT JOIN planning_projects p ON r.project_id=p.id
                             LEFT JOIN annual_plans ap ON r.annual_plan_id=ap.id
                             LEFT JOIN implementation_versions v ON r.version_id=v.id
                             WHERE r.id=?""", (req_id,))
        if not req:
            messagebox.showwarning("提示", "未找到需求详情")
            return
        flows = self.db.query("SELECT flow_type, amount, description, occurred_at FROM budget_flows WHERE requirement_id=? ORDER BY occurred_at DESC LIMIT 8", (req_id,)) if self.can_view_money() else []
        artifacts = self.db.query("SELECT artifact_type, artifact_name, version_no, uploaded_at FROM artifacts WHERE related_object_type='需求' AND related_object_id=? ORDER BY uploaded_at DESC LIMIT 8", (req_id,))
        history = self.db.query("SELECT from_status, to_status, operator_name, transition_note, changed_at FROM requirement_status_history WHERE requirement_id=? ORDER BY id DESC LIMIT 12", (req_id,))
        sections = [
            ("基础信息", [
                ("需求编号", req["requirement_code"]), ("需求名称", req["requirement_name"]), ("项目", req["project_name"]),
                ("年度计划", req["plan_name"]), ("所属版本", req["version_name"]), ("来源角色", req["source_role"]),
                ("提出人", req["proposer_name"]), ("对接人", req["owner_name"]), ("类型", req["requirement_type"]),
                ("标签", req["tags"]), ("优先级", req["priority"]), ("状态", req["status"]),
            ]),
            ("计划信息", [("预计完成", req["planned_finish_date"]), ("实际完成", req["actual_finish_date"])]),
            ("需求描述", [("描述", req["requirement_description"]), ("备注", req["remark"])]),
            ("状态历史", [(h["changed_at"], f"{h['from_status'] or '创建'} -> {h['to_status']} / {h['operator_name'] or ''} / {h['transition_note'] or ''}") for h in history] or [("暂无", "")]),
            ("关联成果物", [(f"{a['artifact_type']} {a['version_no'] or ''}", f"{a['artifact_name']} {a['uploaded_at']}") for a in artifacts] or [("暂无", "")]),
        ]
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
        req = self.db.one("SELECT * FROM requirements WHERE id=?", (req_id,))
        version = self.db.one("SELECT is_frozen FROM implementation_versions WHERE id=?", (req["version_id"],)) if req["version_id"] else None
        d = FieldDialog(self, "编辑需求", [
            ("requirement_name", "需求名称", "text", None), ("source_role", "来源角色", "combo", ["客户", "销售", "项目经理", "研发", "运营", "咨询负责人"]),
            ("proposer_name", "提出人", "text", None), ("owner_name", "对接人", "text", None), ("requirement_type", "需求类型", "combo", ["业务痛点", "功能优化", "运维 Bug", "招投标要求", "验收整改", "客户新增"]),
            ("tags", "标签", "text", None), ("priority", "优先级", "combo", ["P0", "P1", "P2", "高", "中", "低"]),
            ("estimated_budget", "预估预算", "text", None),
            ("planned_finish_date", "预计完成时间", "text", None), ("requirement_description", "需求描述", "memo", None), ("remark", "备注", "memo", None),
        ], dict(req), required=["requirement_name", "requirement_description", "source_role"])
        if not d.result:
            return
        try:
            estimated_budget = self.parse_float(d.result["estimated_budget"], "预估预算")
            planned_finish = normalize_date(d.result["planned_finish_date"], "预计完成时间")
            if estimated_budget < 0:
                raise ValueError("预估预算不能小于 0")
            if version and version["is_frozen"]:
                proposed = dict(d.result)
                proposed["estimated_budget"] = estimated_budget
                proposed["planned_finish_date"] = planned_finish
                self.create_change_request(req, "update", proposed)
                return
            allocated_budget = float(req["allocated_budget"] or 0)
            actual_cost = float(req["actual_cost"] or 0)
            before = dict(req)
            self.db.execute("""UPDATE requirements SET requirement_name=?, requirement_description=?, source_role=?, proposer_name=?, owner_name=?,
                               requirement_type=?, tags=?, priority=?, estimated_budget=?, allocated_budget=?, actual_cost=?, planned_finish_date=?, remark=?, updated_at=?
                               WHERE id=?""",
                            (d.result["requirement_name"], d.result["requirement_description"], d.result["source_role"], d.result["proposer_name"], d.result["owner_name"],
                             d.result["requirement_type"], d.result["tags"], d.result["priority"], estimated_budget, allocated_budget, actual_cost, planned_finish, d.result["remark"], now_text(), req_id))
            self.db.log(self.current_user, "requirement", req_id, "update", before, d.result, "编辑需求任务")
            self.show_requirements()
        except ValueError as exc:
            messagebox.showerror("保存失败", str(exc))

    def create_change_request(self, req, change_type="update", proposed=None):
        d = FieldDialog(self, "冻结版本变更申请", [
            ("change_title", "变更标题", "text", None),
            ("change_reason", "变更原因", "memo", None),
            ("impact_scope", "影响范围", "memo", None),
        ], {"change_title": f"{'删除' if change_type == 'delete' else '调整'}需求：{req['requirement_code']} {req['requirement_name']}"}, required=["change_title", "change_reason"])
        if not d.result:
            return
        t = now_text()
        cur = self.db.execute("""INSERT INTO change_requests(version_id, requirement_id, change_title, change_reason, impact_scope, approval_status, requested_by, requested_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (req["version_id"], req["id"], d.result["change_title"], d.result["change_reason"], d.result["impact_scope"], "pending", self.current_user, t))
        change_id = cur.lastrowid
        self.db.execute("INSERT INTO change_request_payloads(change_request_id, change_type, proposed_value) VALUES(?,?,?)",
                        (change_id, change_type, json.dumps(proposed or {}, ensure_ascii=False)))
        self.db.log(self.current_user, "change_request", change_id, "create", "", d.result, "冻结版本需求变更申请")
        messagebox.showinfo("已提交", "当前版本已冻结，变更申请已提交到系统设置中的变更申请列表。")

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
        self.db.execute("UPDATE requirements SET is_deleted=1, updated_at=? WHERE id=?", (now_text(), req_id))
        self.db.log(self.current_user, "requirement", req_id, "delete", dict(req), "", "软删除需求")
        self.show_requirements()

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
        if not self.can_view_money():
            messagebox.showwarning("权限不足", "当前角色无权查看项目资金和成本明细。")
            self.show_dashboard()
            return
        self.clear("资金管理")
        bar = self.make_action_bar(self.content)
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
            ("实际消耗", money_text(r["cost"] or 0), f"执行率 {percent_text(r['cost'], r['allocated'])}", self.colors["danger"] if (r["cost"] or 0) > (r["allocated"] or 0) and (r["allocated"] or 0) else self.colors["success"]),
        ], columns=5)
        version_budget = float(v["version_budget"] or 0) if v else 0
        allocated = float(r["allocated"] or 0)
        actual_cost = float(r["cost"] or 0)
        if version_budget and allocated > version_budget:
            self.notice_banner(self.content, f"预算超支：需求已分配 {money_text(allocated)}，超过版本预算 {money_text(version_budget)}。", "danger")
        elif allocated and actual_cost > allocated:
            self.notice_banner(self.content, f"执行超支：实际消耗 {money_text(actual_cost)}，超过已分配预算 {money_text(allocated)}。", "danger")
        elif version_budget:
            self.notice_banner(self.content, f"预算状态正常，版本剩余可分配预算 {money_text(version_budget - allocated)}。", "success")
        self.section_title(self.content, "资金四级穿透", "项目总预算 -> 年度预算 -> 版本预算 -> 需求预算/实际消耗。")
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
        project = self.db.one("SELECT project_name, total_budget FROM planning_projects WHERE id=?", (project_id,)) if project_id else None
        plan = self.db.one("SELECT plan_name, annual_budget FROM annual_plans WHERE id=?", (plan_id,)) if plan_id else None
        version = self.db.one("SELECT version_code, version_name, version_budget FROM implementation_versions WHERE id=?", (version_id,)) if version_id else None
        requirements = self.db.query("""SELECT id, requirement_code, requirement_name, allocated_budget, actual_cost
                                        FROM requirements
                                        WHERE is_deleted=0 AND version_id=?
                                        ORDER BY allocated_budget DESC, id DESC LIMIT 5""", (version_id,)) if version_id else []

        def node(x, y, w, h, title, amount, color, tag=None):
            tags = (tag,) if tag else ()
            canvas.create_rectangle(x, y, x + w, y + h, fill=color, outline=self.colors["line"], width=1, tags=tags)
            canvas.create_text(x + 12, y + 14, text=title, anchor="w", fill=self.colors["text"], font=("Microsoft YaHei UI", 10, "bold"), width=w - 24, tags=tags)
            canvas.create_text(x + 12, y + 42, text=money_text(amount), anchor="w", fill="#3c514c", font=("Microsoft YaHei UI", 10), tags=tags)

        node(20, 85, 180, 72, project["project_name"] if project else "未选择项目", project["total_budget"] if project else 0, "#e5f0f4")
        node(250, 85, 180, 72, plan["plan_name"] if plan else "未选择年度", plan["annual_budget"] if plan else 0, "#e4f1e9")
        version_amount = version["version_budget"] if version else 0
        node(480, 85, 180, 72, f"{version['version_code']} {version['version_name']}" if version else "未选择版本", version_amount, "#fff1d8")
        for x in (200, 430):
            canvas.create_line(x, 121, x + 50, 121, arrow=tk.LAST, fill="#8aa09a", width=2)
        if not requirements:
            node(720, 85, 220, 72, "暂无版本需求", 0, "#edf1ef")
            canvas.create_line(660, 121, 720, 121, arrow=tk.LAST, fill="#8aa09a", width=2)
            return
        y = 20
        for req in requirements:
            over = (req["actual_cost"] or 0) > (req["allocated_budget"] or 0) and (req["allocated_budget"] or 0) > 0
            color = "#f9e7e5" if over else "#e3f2ef"
            tag = f"req_node_{req['id']}"
            node(720, y, 240, 42, f"{req['requirement_code']} {req['requirement_name']}", req["allocated_budget"], color, tag=tag)
            canvas.create_text(730, y + 33, text=f"实际 {money_text(req['actual_cost'])}", anchor="w", fill=self.colors["muted"], font=("Microsoft YaHei UI", 9), tags=(tag,))
            canvas.create_line(660, 121, 720, y + 21, arrow=tk.LAST, fill="#8aa09a", width=1)
            canvas.tag_bind(tag, "<Enter>", lambda _event: canvas.configure(cursor="hand2"))
            canvas.tag_bind(tag, "<Leave>", lambda _event: canvas.configure(cursor=""))
            canvas.tag_bind(tag, "<Double-Button-1>", lambda _event, rid=req["id"]: self.open_requirement_detail(rid))
            y += 48

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

    def show_artifacts(self):
        self.clear("成果物管理")
        bar = self.make_action_bar(self.content)
        ttk.Button(bar, text="挂载本地文件", command=self.add_artifact, style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(bar, text="打开附件", command=self.open_selected_artifact).pack(side=tk.LEFT, padx=(8, 0))
        context_ids = [
            ("项目", self.current_project_id()),
            ("年度", self.current_plan_id()),
            ("版本", self.current_version_id()),
        ]
        req_ids = [r["id"] for r in self.db.query("SELECT id FROM requirements WHERE is_deleted=0 AND version_id=?", (self.current_version_id(),))] if self.current_version_id() else []
        where = ["(related_object_type=? AND related_object_id=?)" for obj, obj_id in context_ids if obj_id]
        params = [item for obj, obj_id in context_ids if obj_id for item in (obj, obj_id)]
        if req_ids:
            where.append(f"(related_object_type='需求' AND related_object_id IN ({','.join(['?'] * len(req_ids))}))")
            params.extend(req_ids)
        sql = "SELECT artifact_code, artifact_name, artifact_type, related_object_type, related_object_id, version_no, file_path, uploaded_by, uploaded_at FROM artifacts"
        if where:
            sql += " WHERE " + " OR ".join(where)
        sql += " ORDER BY id DESC"
        raw_rows = self.db.query(sql, tuple(params))
        rows = []
        for r in raw_rows:
            item = dict(r)
            item["stage"] = ARTIFACT_STAGE_HINTS.get(item["artifact_type"], "其他")
            rows.append(item)
        self.artifact_tree = self.add_table(self.content, [("artifact_code", "成果物编号", 130), ("artifact_name", "名称", 180), ("stage", "业务阶段", 110), ("artifact_type", "类型", 110), ("related_object_type", "挂载对象", 90), ("related_object_id", "对象ID", 70), ("version_no", "文件版本", 90), ("file_path", "文件路径", 320), ("uploaded_by", "上传人", 90), ("uploaded_at", "上传时间", 150)], rows, on_double_click=self.open_selected_artifact)

    def selected_artifact(self):
        selection = getattr(self, "artifact_tree", None).selection() if hasattr(self, "artifact_tree") else ()
        if not selection:
            messagebox.showwarning("提示", "请先选择一个成果物。")
            return None
        artifact_code = self.artifact_tree.item(selection[0])["values"][0]
        return self.db.one("SELECT id, artifact_code, artifact_name, file_path FROM artifacts WHERE artifact_code=?", (artifact_code,))

    def open_selected_artifact(self, _event=None):
        artifact = self.selected_artifact()
        if not artifact:
            return
        stored = Path(artifact["file_path"])
        candidates = [stored] if stored.is_absolute() else [self.db.data_dir / stored, self.db.base_dir / stored]
        allowed_roots = [self.db.data_dir.resolve(), self.db.base_dir.resolve()]
        path = None
        for candidate in candidates:
            resolved = candidate.resolve()
            try:
                if not any(resolved.is_relative_to(root) for root in allowed_roots):
                    continue
            except AttributeError:
                if not any(str(resolved).startswith(str(root) + os.sep) for root in allowed_roots):
                    continue
            if resolved.is_file():
                path = resolved
                break
        if not path:
            messagebox.showerror("附件不存在", f"未找到文件：{artifact['file_path']}")
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self.db.log(self.current_user, "artifact", artifact["id"], "open", "", artifact["file_path"], "打开成果物附件")
        except Exception as exc:
            LOGGER.exception("open_artifact_failed code=%s", artifact["artifact_code"])
            messagebox.showerror("打开失败", str(exc))

    def validate_artifact_target(self, object_type, object_id):
        queries = {
            "项目": ("SELECT id FROM planning_projects WHERE id=?", (object_id,)),
            "年度": ("SELECT id FROM annual_plans WHERE id=? AND project_id=?", (object_id, self.current_project_id())),
            "版本": ("SELECT id FROM implementation_versions WHERE id=? AND project_id=?", (object_id, self.current_project_id())),
            "需求": ("SELECT id FROM requirements WHERE id=? AND project_id=? AND is_deleted=0", (object_id, self.current_project_id())),
        }
        sql, params = queries.get(object_type, (None, None))
        return bool(sql and self.db.one(sql, params))

    def add_artifact(self):
        if not self.require_action("artifact", "挂载成果物"):
            return
        source = filedialog.askopenfilename(title="选择成果物文件")
        if not source:
            return
        d = FieldDialog(self, "成果物信息", [
            ("artifact_type", "成果物类型", "combo", ["可研报告", "分年任务申报书", "任务书方案", "招标文件", "应标文件", "验收报告", "项目总结", "运维反馈", "运营反馈", "其他"]),
            ("related_object_type", "挂载对象", "combo", ["项目", "年度", "版本", "需求"]),
            ("related_object_id", "对象ID", "text", None), ("version_no", "文件版本", "text", None), ("description", "说明", "memo", None),
        ], {"related_object_type": "版本", "related_object_id": str(self.current_version_id() or self.current_project_id() or ""), "version_no": "v1"}, required=["related_object_type", "related_object_id"])
        if d.result:
            dest = None
            try:
                object_id = self.parse_int(d.result["related_object_id"], "对象ID")
                if not self.validate_artifact_target(d.result["related_object_type"], object_id):
                    raise ValueError("挂载对象不存在，或不属于当前项目。")
                src = Path(source)
                code = "ART-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
                dest = self.db.attachments_dir / f"{code}{src.suffix}"
                shutil.copy2(src, dest)
                t = now_text()
                self.db.execute("""INSERT INTO artifacts(artifact_code, artifact_name, artifact_type, file_path, file_ext, file_size, related_object_type, related_object_id, version_no, description, uploaded_by, uploaded_at, created_at)
                                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (code, src.name, d.result["artifact_type"], str(dest.relative_to(self.db.data_dir)), src.suffix, dest.stat().st_size, d.result["related_object_type"], object_id, d.result["version_no"], d.result["description"], self.current_user, t, t))
                self.db.log(self.current_user, "artifact", None, "create", source, dest, "挂载成果物文件")
                self.show_artifacts()
            except (OSError, ValueError, sqlite3.DatabaseError) as exc:
                log_transaction_exception("add_artifact", exc)
                if dest and dest.exists():
                    dest.unlink()
                messagebox.showerror("保存失败", str(exc))

    def show_search(self):
        self.clear("搜索中心")
        keyword = self.search_var.get().strip()
        bar = self.make_action_bar(self.content)
        ttk.Label(bar, text="关键词").pack(side=tk.LEFT)
        ttk.Entry(bar, textvariable=self.search_var, width=36).pack(side=tk.LEFT, padx=8)
        ttk.Button(bar, text="查询", command=self.show_search, style="Primary.TButton").pack(side=tk.LEFT)
        like = f"%{keyword}%"
        rows = self.db.query("""SELECT r.requirement_code, r.requirement_name, p.project_name, ap.plan_name,
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
                                )
                                ORDER BY r.updated_at DESC""", (like, like, like, like, like, like)) if keyword else []
        columns = [("requirement_code", "需求编号", 130), ("requirement_name", "需求名称", 220), ("project_name", "项目", 160), ("plan_name", "年度计划", 160), ("version_name", "版本", 160), ("source_role", "来源", 90), ("owner_name", "对接人", 90), ("tags", "标签", 160), ("priority", "优先级", 70), ("status", "状态", 110)]
        if self.can_view_money():
            columns += [("allocated_budget", "分配预算", 100), ("actual_cost", "实际消耗", 100)]
        columns.append(("updated_at", "更新时间", 150))
        self.add_table(self.content, columns, rows)

    def show_milestones(self):
        self.clear("流程里程碑")
        stages = [
            ("1.宏观规划", "可研报告", "项目"),
            ("2.规划细化", "分年任务申报书", "年度"),
            ("3.建设落地", "任务书方案", "版本"),
            ("4.招投标", "招标文件/应标文件", "版本"),
            ("5.项目交付验收", "验收报告/项目总结", "版本"),
            ("6.运维运营", "运维/运营反馈", "需求"),
        ]
        version_id = self.current_version_id()
        project_id = self.current_project_id()
        total = self.db.one("SELECT COUNT(*) c FROM requirements WHERE is_deleted=0 AND version_id=?", (version_id,))["c"] if version_id else 0
        done = self.db.one("SELECT COUNT(*) c FROM requirements WHERE is_deleted=0 AND version_id=? AND status IN ('待验收','已上线运维','已关闭')", (version_id,))["c"] if version_id else 0
        row = ttk.Frame(self.content)
        row.pack(fill=tk.X)
        self.metric_card(row, "当前版本需求", total, "版本范围内需求总数")
        self.metric_card(row, "验收/上线/关闭", done, f"完成度 {percent_text(done, total)}", self.colors["success"] if total and done == total else self.colors["warning"])
        self.metric_card(row, "待补成果物", self.missing_acceptance_artifact_count(), "版本完成后需关注验收报告", self.colors["danger"] if self.missing_acceptance_artifact_count() else self.colors["success"])
        self.section_title(self.content, "阶段视图", "按照咨询项目全流程展示阶段、关键成果物和当前挂载数量。")
        rows = []
        for stage, artifact_names, obj_type in stages:
            count = self.count_stage_artifacts(stage, project_id, version_id)
            rows.append({"stage": stage, "artifact_names": artifact_names, "object_type": obj_type, "artifact_count": count, "status": "已补充" if count else "待补充"})
        self.add_table(self.content, [("stage", "阶段", 150), ("artifact_names", "关键成果物", 240), ("object_type", "建议挂载对象", 120), ("artifact_count", "已挂载数量", 100), ("status", "状态", 100)], rows, 8)
        if total and done == total and self.missing_acceptance_artifact_count():
            ttk.Label(self.content, text="提醒：当前版本需求已达到验收/上线条件，请补充验收报告或项目总结。", foreground=self.colors["danger"], background=self.colors["bg"]).pack(anchor="w", pady=(8, 0))

    def count_stage_artifacts(self, stage, project_id, version_id):
        stage_name = stage.split(".", 1)[-1]
        types = [k for k, v in ARTIFACT_STAGE_HINTS.items() if v == stage_name]
        if not types:
            return 0
        placeholders = ",".join(["?"] * len(types))
        params = types[:]
        where = [f"artifact_type IN ({placeholders})"]
        if stage_name == "宏观规划":
            where.append("related_object_type='项目' AND related_object_id=?")
            params.append(project_id or 0)
        elif stage_name == "规划细化":
            where.append("related_object_type='年度' AND related_object_id=?")
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
                               AND artifact_type IN ('验收报告','项目总结')""", (version_id,))["c"]
        return 0 if count else 1

    def show_exports(self):
        if not self.can_action("export"):
            messagebox.showwarning("权限不足", "当前角色无权导出项目数据。")
            self.show_dashboard()
            return
        self.clear("报表导出")
        ttk.Button(self.content, text="导出需求清单 CSV", command=self.export_requirements).pack(anchor="w", pady=5)
        ttk.Button(self.content, text="导出资金明细 CSV", command=self.export_budget).pack(anchor="w", pady=5)
        ttk.Button(self.content, text="导出成果物目录 CSV", command=self.export_artifacts).pack(anchor="w", pady=5)
        if self.current_role.get() == "管理员":
            ttk.Button(self.content, text="创建本地备份 ZIP", command=self.create_backup).pack(anchor="w", pady=5)
            ttk.Button(self.content, text="从备份 ZIP 恢复", command=self.restore_backup).pack(anchor="w", pady=5)
        ttk.Label(self.content, text=f"导出目录：{self.db.exports_dir}\n备份目录：{self.db.backups_dir}\n日志目录：{self.db.logs_dir}", wraplength=900).pack(anchor="w", pady=(12, 0))

    def export_csv(self, name, rows):
        path = self.db.exports_dir / f"{name}_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
        if not rows:
            messagebox.showinfo("提示", "没有可导出的数据")
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows([{key: csv_safe(value) for key, value in dict(r).items()} for r in rows])
        messagebox.showinfo("导出完成", str(path))

    def export_requirements(self):
        if not self.require_action("export", "导出需求清单"):
            return
        self.export_csv("requirements", self.db.query("SELECT * FROM requirements WHERE is_deleted=0 ORDER BY id DESC"))

    def export_budget(self):
        if not self.require_action("export", "导出资金明细") or not self.can_view_money():
            return
        self.export_csv("budget_flows", self.db.query("SELECT * FROM budget_flows ORDER BY id DESC"))

    def export_artifacts(self):
        if not self.require_action("export", "导出成果物目录"):
            return
        self.export_csv("artifacts", self.db.query("SELECT * FROM artifacts ORDER BY id DESC"))

    def create_backup(self):
        if self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员视角可以执行本地备份。")
            return
        backup = self.db.backups_dir / f"backup_{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
        with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(self.db.db_path, "app.db")
            z.writestr("attachments/", "")
            for file in self.db.attachments_dir.rglob("*"):
                if file.is_file() and not file.is_symlink():
                    z.write(file, f"attachments/{file.relative_to(self.db.attachments_dir).as_posix()}")
        self.db.log(self.current_user, "backup", None, "create", "", backup, "创建本地备份")
        messagebox.showinfo("备份完成", str(backup))

    def restore_backup(self):
        if self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员视角可以执行本地恢复。")
            return
        source = filedialog.askopenfilename(title="选择备份 ZIP", filetypes=[("ZIP", "*.zip")])
        if not source:
            return
        if not messagebox.askyesno("确认恢复", "恢复会覆盖当前数据库和附件，请确认已另行备份。是否继续？"):
            return
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        staged_db = self.db.data_dir / f".app.restore.{stamp}.tmp"
        staged_attachments = self.db.data_dir / f".attachments.restore.{stamp}"
        old_db = self.db.data_dir / f".app.before_restore.{stamp}"
        old_attachments = self.db.data_dir / f".attachments.before_restore.{stamp}"
        connection_closed = False
        try:
            with tempfile.TemporaryDirectory(prefix="crm-restore-") as temp_dir:
                temp_path = Path(temp_dir)
                with zipfile.ZipFile(source, "r") as z:
                    validate_restore_archive(z, required_names=("app.db",))
                    z.extractall(temp_path)
                restored_db = temp_path / "app.db"
                check_conn = sqlite3.connect(restored_db)
                integrity = check_conn.execute("PRAGMA integrity_check").fetchone()[0]
                required = check_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='requirements'").fetchone()
                check_conn.close()
                if integrity != "ok" or not required:
                    raise ValueError("备份数据库完整性或结构校验失败。")

                shutil.copy2(restored_db, staged_db)
                restored_attachments = temp_path / "attachments"
                if restored_attachments.exists():
                    shutil.copytree(restored_attachments, staged_attachments)
                else:
                    staged_attachments.mkdir(parents=True)

                safety = self.db.backups_dir / f"before_restore_{stamp}.zip"
                with zipfile.ZipFile(safety, "w", zipfile.ZIP_DEFLATED) as z:
                    z.write(self.db.db_path, "app.db")
                    for file in self.db.attachments_dir.rglob("*"):
                        if file.is_file() and not file.is_symlink():
                            z.write(file, f"attachments/{file.relative_to(self.db.attachments_dir).as_posix()}")

                self.db.conn.close()
                connection_closed = True
                os.replace(self.db.db_path, old_db)
                os.replace(staged_db, self.db.db_path)
                os.replace(self.db.attachments_dir, old_attachments)
                os.replace(staged_attachments, self.db.attachments_dir)
                self.db.conn = sqlite3.connect(self.db.db_path)
                self.db.conn.row_factory = sqlite3.Row
                try:
                    self.db.init_schema()
                    self.db.log(self.current_user, "backup", None, "restore", str(source), str(safety), "恢复本地备份")
                finally:
                    self.db.close()
                try:
                    old_db.unlink(missing_ok=True)
                    shutil.rmtree(old_attachments, ignore_errors=True)
                except OSError:
                    pass
            LOGGER.info("backup_restore_succeeded source=%s safety=%s", source, safety)
            messagebox.showinfo("恢复完成", f"已恢复备份。恢复前快照：{safety}\n请重新启动程序。")
            close_logging()
            self.destroy()
        except (OSError, ValueError, sqlite3.DatabaseError, zipfile.BadZipFile) as exc:
            log_transaction_exception("restore_backup", exc)
            try:
                if old_db.exists():
                    if self.db.db_path.exists():
                        self.db.db_path.unlink()
                    os.replace(old_db, self.db.db_path)
                if old_attachments.exists():
                    if self.db.attachments_dir.exists():
                        shutil.rmtree(self.db.attachments_dir)
                    os.replace(old_attachments, self.db.attachments_dir)
            except OSError as rollback_exc:
                LOGGER.exception("restore_rollback_failed")
                messagebox.showerror("自动回滚失败", f"请使用恢复前安全快照人工恢复：{rollback_exc}")
            if staged_db.exists():
                staged_db.unlink(missing_ok=True)
            if staged_attachments.exists():
                shutil.rmtree(staged_attachments, ignore_errors=True)
            if connection_closed and self.db.db_path.exists():
                self.db.conn = sqlite3.connect(self.db.db_path)
                self.db.conn.row_factory = sqlite3.Row
            messagebox.showerror("恢复失败", f"已尝试自动回滚到恢复前数据。错误：{exc}")

    def show_settings(self):
        if not self.can_action("approve"):
            messagebox.showwarning("权限不足", "当前角色无权查看变更审批和系统日志。")
            self.show_dashboard()
            return
        self.clear("系统设置")
        ttk.Label(self.content, text="本机账号：默认管理员。支持通过右上角角色选择器查看不同角色视图。", style="SubTitle.TLabel").pack(anchor="w", pady=(0, 8))
        self.section_title(self.content, "运行日志", "运行、错误和审计日志按大小自动分卷；数据库操作日志仍可在本页查询。")
        log_bar = self.make_action_bar(self.content)
        ttk.Button(log_bar, text="打开日志目录", command=self.open_logs_directory).pack(side=tk.LEFT)
        ttk.Button(log_bar, text="运行健康检查", command=self.run_healthcheck).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(log_bar, text=f"{self.db.logs_dir}  |  runtime.log / error.log / audit.log").pack(side=tk.LEFT, padx=(10, 0))
        self.section_title(self.content, "变更申请", "冻结版本内的需求修改、删除会进入此列表，由管理员或咨询负责人审批。")
        bar = self.make_action_bar(self.content)
        ttk.Label(bar, text="状态").pack(side=tk.LEFT)
        status_box = ttk.Combobox(bar, textvariable=self.change_status_filter, values=["全部", "pending", "approved", "rejected"], state="readonly", width=12)
        status_box.pack(side=tk.LEFT, padx=(6, 10))
        status_box.bind("<<ComboboxSelected>>", lambda e: self.show_settings())
        ttk.Button(bar, text="通过", command=self.approve_change_request).pack(side=tk.LEFT)
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
        self.change_tree = self.add_table(self.content, [("id", "ID", 50), ("approval_status", "状态", 90), ("version_code", "版本", 90), ("requirement_code", "需求编号", 130), ("change_title", "标题", 260), ("requested_by", "申请人", 90), ("requested_at", "申请时间", 150), ("approved_by", "审批人", 90), ("approved_at", "审批时间", 150)], changes, 8)
        self.section_title(self.content, "操作日志", "记录关键数据修改、状态流转、版本冻结、成果物上传和备份等动作。")
        audit_bar = self.make_action_bar(self.content)
        ttk.Label(audit_bar, text="对象类型").pack(side=tk.LEFT)
        type_box = ttk.Combobox(audit_bar, textvariable=self.operation_log_type_filter,
                                values=["全部", "system", "permission", "planning_project", "annual_plan", "implementation_version", "requirement", "budget_flow", "artifact", "change_request", "backup", "healthcheck"],
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

    def update_change_request(self, status):
        if not self.require_action("approve", "审批变更申请"):
            return
        change_id = self.selected_change_id()
        if not change_id:
            return
        change = self.db.one("SELECT * FROM change_requests WHERE id=?", (change_id,))
        if change["approval_status"] != "pending":
            messagebox.showinfo("提示", "该变更申请已处理")
            return
        action = "通过" if status == "approved" else "驳回"
        if not messagebox.askyesno("确认审批", f"确认{action}该变更申请？"):
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
