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
from tkinter import filedialog, messagebox, ttk


APP_NAME = "咨询项目全流程需求管理系统"
APP_VARIANT = "MySQL 远程版"
APP_VERSION = "1.5.0-mysql"
HOST_NAME = socket.gethostname()
MYSQL_CONFIG_FILE = "mysql_config.json"
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
    "草稿": "#eef2f7",
    "规划中": "#e8f1ff",
    "已排期": "#e9f8f0",
    "研发中": "#fff5dc",
    "待验收": "#fff0e6",
    "已上线运维": "#e8f7f5",
    "已关闭": "#eceff3",
    "已驳回": "#ffecec",
    "已挂起": "#f2efff",
    "已取消": "#f1f1f1",
    "变更中": "#fdf0ff",
    "退回修改": "#fff0f0",
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


def csv_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


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
                "请先修改 host、port、user、password、database 后重新启动。"
            )
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        for key in ["host", "port", "user", "database"]:
            if key not in config or config[key] in ("", None):
                raise RuntimeError(f"MySQL 配置缺少 {key}：{self.config_path}")
        if not re.fullmatch(r"[A-Za-z0-9_]+", str(config["database"])):
            raise RuntimeError("database 只能包含字母、数字和下划线。")
        config["port"] = int(config["port"])
        config["create_database"] = bool(config.get("create_database", False))
        config["seed_demo_data"] = bool(config.get("seed_demo_data", False))
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

    def store_attachment(self, source, code):
        source = Path(source)
        filename = f"{code}{source.suffix}"
        if self.attachment_storage == "oss":
            key = "/".join(part for part in [self.config["oss_prefix"], datetime.now().strftime("%Y/%m"), filename] if part)
            self.get_oss_bucket().put_object_from_file(key, str(source))
            return f"oss://{self.config['oss_bucket']}/{key}"
        destination = self.attachments_dir / filename
        shutil.copy2(source, destination)
        return filename

    def delete_attachment(self, stored_path):
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
        if path.exists() and path.is_file():
            path.unlink()

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
        cur = self.conn.cursor(dictionary=True)
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
            "user_project_access",
        ]
        for table in required_tables:
            self.one(f"SELECT 1 probe FROM {table} LIMIT 1")
        self.one("SELECT event_id, result FROM operation_logs LIMIT 1")
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

    def create_requirement(self, record, operator_name, occurred_at):
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
            cur.execute("""INSERT INTO requirements(requirement_code, requirement_name, requirement_description,
                               source_role, proposer_name, owner_name, project_id, annual_plan_id, version_id,
                               requirement_type, tags, priority, status, estimated_budget, allocated_budget,
                               actual_cost, planned_finish_date, remark, created_at, updated_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        tuple(record[key] for key in [
                            "requirement_code", "requirement_name", "requirement_description", "source_role",
                            "proposer_name", "owner_name", "project_id", "annual_plan_id", "version_id",
                            "requirement_type", "tags", "priority", "status", "estimated_budget",
                            "allocated_budget", "actual_cost", "planned_finish_date", "remark", "created_at", "updated_at",
                        ]))
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
                raise ValueError("需求所属版本已被其他操作更新，请刷新后重试。")
            cur.execute("""UPDATE requirements SET requirement_name=%s, requirement_description=%s,
                               source_role=%s, proposer_name=%s, owner_name=%s, requirement_type=%s, tags=%s,
                               priority=%s, estimated_budget=%s, planned_finish_date=%s, remark=%s, updated_at=%s
                           WHERE id=%s AND is_deleted=0""",
                        (record["requirement_name"], record["requirement_description"], record["source_role"],
                         record.get("proposer_name", ""), record.get("owner_name", ""), record.get("requirement_type", ""),
                         record.get("tags", ""), record.get("priority", "P1"), record["estimated_budget"],
                         record.get("planned_finish_date"), record.get("remark", ""), occurred_at, requirement_id))
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
            cur.execute("SELECT version_id, project_id FROM requirements WHERE id=%s AND is_deleted=0 FOR UPDATE", (requirement_id,))
            locked = cur.fetchone()
            if not locked or locked["version_id"] != expected_source_id or locked["project_id"] != current["project_id"]:
                raise ValueError("需求所属版本已被其他操作更新，请刷新后重试。")
            cur.execute("UPDATE requirements SET annual_plan_id=%s, version_id=%s, updated_at=%s WHERE id=%s AND is_deleted=0",
                        (annual_plan_id, target_version_id, occurred_at, requirement_id))
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
            cur.execute("""INSERT INTO change_requests(version_id, requirement_id, change_title, change_reason,
                                                        impact_scope, approval_status, requested_by, requested_at)
                           VALUES(%s,%s,%s,%s,%s,'pending',%s,%s)""",
                        (probe["version_id"], requirement_id, values["change_title"], values["change_reason"],
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
            target_checks = {
                "项目": "SELECT id FROM planning_projects WHERE id=%s FOR UPDATE",
                "年度": "SELECT id FROM annual_plans WHERE id=%s FOR UPDATE",
                "版本": "SELECT id FROM implementation_versions WHERE id=%s FOR UPDATE",
                "需求": "SELECT id FROM requirements WHERE id=%s AND is_deleted=0 FOR UPDATE",
            }
            target_sql = target_checks.get(record["related_object_type"])
            if not target_sql:
                raise ValueError("成果物挂载对象类型无效。")
            cur.execute(target_sql, (record["related_object_id"],))
            if not cur.fetchone():
                raise ValueError("成果物挂载对象不存在、已删除或已被其他操作更新。")
            cur.execute("""INSERT INTO artifacts(artifact_code, artifact_name, artifact_type, file_path, file_ext,
                                                  file_size, related_object_type, related_object_id, version_no,
                                                  description, uploaded_by, uploaded_at, created_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        tuple(record[key] for key in [
                            "artifact_code", "artifact_name", "artifact_type", "file_path", "file_ext", "file_size",
                            "related_object_type", "related_object_id", "version_no", "description", "uploaded_by",
                            "uploaded_at", "created_at",
                        ]))
            artifact_id = cur.lastrowid
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'artifact',%s,'create','',%s,'挂载成果物文件',%s,'success')""",
                        (operator_name, occurred_at, artifact_id, record["file_path"], event_id))
            self.conn.commit()
            audit_event(operator_name, "artifact", artifact_id, "create", "挂载成果物文件", event_id=event_id)
            return artifact_id
        except Exception as exc:
            self.conn.rollback()
            log_transaction_exception("create_artifact_record", exc)
            raise
        finally:
            self.end_transaction(cur)

    def record_budget_flow(self, flow_code, project_id, annual_plan_id, version_id, requirement_id,
                           flow_type, amount, description, operator_name, occurred_at, allow_actual_overrun=False):
        cur = self.begin_transaction()
        try:
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
                new_value = float(req["allocated_budget"] or 0) + amount
                if new_value < 0:
                    raise ValueError("调整后的需求分配预算不能小于 0。")
                cur.execute("SELECT id FROM requirements WHERE version_id=%s AND is_deleted=0 FOR UPDATE", (version_id,))
                cur.fetchall()
                cur.execute("SELECT COALESCE(SUM(allocated_budget),0) total FROM requirements WHERE version_id=%s AND is_deleted=0", (version_id,))
                projected = float(cur.fetchone()["total"] or 0) + amount
                if version and projected > float(version["version_budget"] or 0):
                    raise ValueError(f"分配后版本需求预算 {money_text(projected)} 将超过版本预算 {money_text(version['version_budget'])}。")
            if req and flow_type == "实际消耗":
                new_actual = float(req["actual_cost"] or 0) + amount
                if new_actual > float(req["allocated_budget"] or 0) and not allow_actual_overrun:
                    raise ValueError("ACTUAL_OVERRUN")
            cur.execute("""INSERT INTO budget_flows(flow_code, project_id, annual_plan_id, version_id, requirement_id, flow_type, amount, description, operator_name, occurred_at, created_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (flow_code, project_id, annual_plan_id, version_id, requirement_id, flow_type, amount, description, operator_name, occurred_at, occurred_at))
            if req and flow_type in {"已分配预算", "调整金额"}:
                cur.execute("UPDATE requirements SET allocated_budget=allocated_budget+%s, updated_at=%s WHERE id=%s", (amount, occurred_at, requirement_id))
            elif req and flow_type == "实际消耗":
                cur.execute("UPDATE requirements SET actual_cost=actual_cost+%s, updated_at=%s WHERE id=%s", (amount, occurred_at, requirement_id))
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,'budget_flow',%s,'create','',%s,%s,%s,'success')""",
                        (operator_name, occurred_at, requirement_id, flow_code, f"登记资金流水：{flow_type}", event_id))
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
            cur.execute("""SELECT COUNT(*) requirement_count, COALESCE(SUM(allocated_budget),0) allocated_budget,
                                  COALESCE(SUM(actual_cost),0) actual_cost
                           FROM requirements WHERE version_id=%s AND is_deleted=0""", (version_id,))
            summary = cur.fetchone()
            cur.execute("SELECT COALESCE(MAX(snapshot_no),0) snapshot_no FROM version_baselines WHERE version_id=%s", (version_id,))
            snapshot_no = int(cur.fetchone()["snapshot_no"] or 0) + 1
            cur.execute("""INSERT INTO version_baselines(version_id, snapshot_no, version_budget, requirement_count,
                                                          allocated_budget, actual_cost, created_by, created_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (version_id, snapshot_no, version["version_budget"], summary["requirement_count"],
                         summary["allocated_budget"], summary["actual_cost"], operator_name, occurred_at))
            baseline_id = cur.lastrowid
            cur.execute("""INSERT INTO version_baseline_requirements(
                               baseline_id, requirement_id, requirement_code, requirement_name, status, priority,
                               allocated_budget, actual_cost, updated_at)
                           SELECT %s, id, requirement_code, requirement_name, status, priority,
                                  allocated_budget, actual_cost, updated_at
                           FROM requirements WHERE version_id=%s AND is_deleted=0""", (baseline_id, version_id))
            cur.execute("""UPDATE implementation_versions
                           SET is_frozen=1, status='frozen', updated_at=%s
                           WHERE id=%s AND is_frozen=0""", (occurred_at, version_id))
            if cur.rowcount != 1:
                raise ValueError("版本冻结状态已被其他操作更新，请刷新后重试。")
            event_id = new_event_id()
            cur.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, object_id,
                                                       operation_type, before_value, after_value, description,
                                                       event_id, result)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'success')""",
                        (operator_name, occurred_at, "implementation_version", version_id, "freeze", "",
                         f"baseline:{baseline_id}", f"冻结版本并生成基线 #{snapshot_no}", event_id))
            self.conn.commit()
            audit_event(operator_name, "implementation_version", version_id, "freeze", f"冻结版本并生成基线 #{snapshot_no}", event_id=event_id)
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
            if status == "approved" and change["requirement_id"] and not payload:
                raise ValueError("变更申请缺少变更内容，不能审批通过。")
            if status == "approved" and change["requirement_id"] and payload:
                cur.execute("SELECT * FROM requirements WHERE id=%s FOR UPDATE", (change["requirement_id"],))
                requirement = cur.fetchone()
                if not requirement or requirement["is_deleted"]:
                    raise ValueError("关联需求不存在或已删除，无法应用变更。")
                if payload["change_type"] == "delete":
                    cur.execute("UPDATE requirements SET is_deleted=1, updated_at=%s WHERE id=%s",
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
                    cur.execute("""UPDATE requirements SET requirement_name=%s, requirement_description=%s,
                                      source_role=%s, proposer_name=%s, owner_name=%s, requirement_type=%s,
                                      tags=%s, priority=%s, estimated_budget=%s, planned_finish_date=%s,
                                      remark=%s, status='变更中', updated_at=%s WHERE id=%s""",
                                (proposed.get("requirement_name", ""), proposed.get("requirement_description", ""),
                                 proposed.get("source_role", ""), proposed.get("proposer_name", ""), proposed.get("owner_name", ""),
                                 proposed.get("requirement_type", ""), proposed.get("tags", ""), proposed.get("priority", "P1"),
                                 estimated_budget, planned_finish, proposed.get("remark", ""),
                                 occurred_at, change["requirement_id"]))
                    cur.execute("""INSERT INTO requirement_status_history(requirement_id, from_status, to_status,
                                                                           operator_name, transition_note, changed_at)
                                   VALUES(%s,%s,'变更中',%s,%s,%s)""",
                                (change["requirement_id"], requirement["status"], operator_name,
                                 f"变更申请 #{change_id} 审批通过", occurred_at))
                else:
                    raise ValueError("变更申请内容类型无效。")
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
            cur.execute("""UPDATE requirements SET status=%s, remark=%s, updated_at=%s,
                           actual_finish_date=CASE WHEN %s='已关闭' THEN %s WHEN %s!='已关闭' THEN NULL ELSE actual_finish_date END
                           WHERE id=%s AND status=%s""",
                        (to_status, note, occurred_at, to_status, occurred_at[:10], to_status, requirement_id, from_status))
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
                is_deleted TINYINT DEFAULT 0,
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL,
                INDEX idx_req_project(project_id),
                INDEX idx_req_version(version_id),
                INDEX idx_req_status(status)
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
                uploaded_by VARCHAR(120),
                uploaded_at VARCHAR(32) NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                INDEX idx_artifact_related(related_object_type, related_object_id)
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
                INDEX idx_change_status(approval_status)
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
                status VARCHAR(40),
                priority VARCHAR(20),
                allocated_budget DECIMAL(14,2) DEFAULT 0,
                actual_cost DECIMAL(14,2) DEFAULT 0,
                updated_at VARCHAR(32),
                INDEX idx_baseline_requirement(baseline_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS change_request_payloads (
                change_request_id INT PRIMARY KEY,
                change_type VARCHAR(40) NOT NULL,
                proposed_value MEDIUMTEXT
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
        body = ttk.Frame(self, padding=16, style="Surface.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(body, wrap=tk.WORD, relief=tk.FLAT, padx=12, pady=12, bg="#ffffff", fg="#172033")
        ybar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=ybar.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        for section_title, rows in sections:
            text.insert(tk.END, f"{section_title}\n", "h")
            for label, value in rows:
                text.insert(tk.END, f"  {label}: {value or ''}\n")
            text.insert(tk.END, "\n")
        text.tag_configure("h", font=("Microsoft YaHei UI", 12, "bold"), foreground="#2563eb")
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
        container = ttk.Frame(self, style="Surface.TFrame")
        container.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(container, bg="#ffffff", highlightthickness=0, width=620,
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


class CredentialDialog(tk.Toplevel):
    def __init__(self, parent, setup=False):
        super().__init__(parent)
        self.result = None
        self.setup = setup
        self.title("初始化管理员密码" if setup else "登录")
        self.resizable(False, False)
        body = ttk.Frame(self, padding=22)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=APP_NAME, font=("Microsoft YaHei UI", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))
        ttk.Label(body, text="用户名").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=6)
        self.username = tk.StringVar(value="admin" if setup else "")
        username_entry = ttk.Entry(body, textvariable=self.username, width=32, state="readonly" if setup else "normal")
        username_entry.grid(row=1, column=1, pady=6)
        ttk.Label(body, text="密码").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=6)
        self.password = tk.StringVar()
        ttk.Entry(body, textvariable=self.password, show="*", width=32).grid(row=2, column=1, pady=6)
        self.confirm = tk.StringVar()
        if setup:
            ttk.Label(body, text="确认密码").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=6)
            ttk.Entry(body, textvariable=self.confirm, show="*", width=32).grid(row=3, column=1, pady=6)
        button_row = 4 if setup else 3
        buttons = ttk.Frame(body)
        buttons.grid(row=button_row, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="保存" if setup else "登录", command=self.submit).pack(side=tk.RIGHT)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.wait_visibility()
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


class App(tk.Tk):
    def __init__(self, skip_login=False):
        if skip_login and os.environ.get("CRM_MYSQL_INTEGRATION_SELFTEST") != "1":
            raise RuntimeError("skip_login 仅允许 CRM_MYSQL_INTEGRATION_SELFTEST=1 的集成测试使用。")
        super().__init__()
        self.base_dir = app_base_dir()
        self.db = Database(self.base_dir)
        self.title(APP_NAME)
        self.geometry("1280x760")
        self.minsize(1100, 680)
        admin = self.db.one("SELECT id, username, display_name, role_name FROM users WHERE username='admin'")
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
        self.change_status_filter = tk.StringVar(value="全部")
        self.operation_log_type_filter = tk.StringVar(value="全部")
        self.operation_log_keyword = tk.StringVar()
        self.content = None
        self.current_page = "首页工作台"
        self.configure_style()
        if not skip_login and not self.authenticate():
            LOGGER.info("login_cancelled")
            self.db.close()
            close_logging()
            self.destroy()
            raise SystemExit("登录已取消")
        self.build_layout()
        self.refresh_contexts()
        self.show_dashboard()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        LOGGER.info("application_started version=%s variant=mysql user=%s role=%s", APP_VERSION, self.current_username, self.current_role.get())

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
        LOGGER.info("application_stopping user=%s role=%s", self.current_username, self.current_role.get())
        self.db.close()
        LOGGER.info("application_stopped")
        close_logging()
        self.destroy()

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
                self.db.log(self.current_user, "authentication", user["id"], "login", "", "", "用户登录成功")
                self.deiconify()
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
        self.colors = {
            "bg": "#f4f6f8",
            "surface": "#ffffff",
            "side": "#1f2937",
            "side_active": "#334155",
            "text": "#172033",
            "muted": "#64748b",
            "line": "#d9e1ea",
            "primary": "#2563eb",
            "success": "#059669",
            "warning": "#d97706",
            "danger": "#dc2626",
        }
        font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=font)
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("Surface.TFrame", background=self.colors["surface"])
        style.configure("Side.TFrame", background=self.colors["side"])
        style.configure("Side.TButton", background=self.colors["side"], foreground="#ffffff", anchor="w", padding=(16, 11), borderwidth=0)
        style.map("Side.TButton", background=[("active", self.colors["side_active"])])
        style.configure("SideActive.TButton", background=self.colors["side_active"], foreground="#ffffff", anchor="w", padding=(16, 11), borderwidth=0)
        style.map("SideActive.TButton", background=[("active", self.colors["side_active"])])
        style.configure("TButton", padding=(12, 7))
        style.configure("Primary.TButton", background=self.colors["primary"], foreground="#ffffff", padding=(12, 7))
        style.map("Primary.TButton", background=[("active", "#1d4ed8")])
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"), background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("SubTitle.TLabel", font=("Microsoft YaHei UI", 10), background=self.colors["bg"], foreground=self.colors["muted"])
        style.configure("Surface.TLabel", background=self.colors["surface"], foreground=self.colors["text"])
        style.configure("Muted.TLabel", background=self.colors["surface"], foreground=self.colors["muted"])
        style.configure("Metric.TLabel", font=("Microsoft YaHei UI", 20, "bold"), background=self.colors["surface"], foreground=self.colors["text"])
        style.configure("Card.TFrame", background=self.colors["surface"], relief="solid", borderwidth=1)
        style.configure("Treeview", rowheight=30, fieldbackground=self.colors["surface"], background=self.colors["surface"], foreground=self.colors["text"])
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), background="#eef2f7", foreground=self.colors["text"], padding=(6, 7))

    def build_layout(self):
        top = ttk.Frame(self, padding=(14, 10), style="Surface.TFrame")
        top.pack(fill=tk.X)
        ttk.Label(top, text="项目", style="Surface.TLabel").pack(side=tk.LEFT)
        self.project_box = ttk.Combobox(top, textvariable=self.selected_project, state="readonly", width=24)
        self.project_box.pack(side=tk.LEFT, padx=(6, 12))
        self.project_box.bind("<<ComboboxSelected>>", lambda e: self.on_project_change())
        ttk.Label(top, text="年度", style="Surface.TLabel").pack(side=tk.LEFT)
        self.plan_box = ttk.Combobox(top, textvariable=self.selected_plan, state="readonly", width=20)
        self.plan_box.pack(side=tk.LEFT, padx=(6, 12))
        self.plan_box.bind("<<ComboboxSelected>>", lambda e: self.on_plan_change())
        ttk.Label(top, text="版本", style="Surface.TLabel").pack(side=tk.LEFT)
        self.version_box = ttk.Combobox(top, textvariable=self.selected_version, state="readonly", width=20)
        self.version_box.pack(side=tk.LEFT, padx=(6, 12))
        self.version_box.bind("<<ComboboxSelected>>", lambda e: self.reload_page())
        ttk.Entry(top, textvariable=self.search_var, width=28).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Button(top, text="搜索", command=self.show_search, style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(top, text="修改密码", command=self.change_my_password).pack(side=tk.RIGHT, padx=(8, 4))
        ttk.Label(top, text=f"{self.current_user} · {self.current_role.get()}", style="Surface.TLabel").pack(side=tk.RIGHT, padx=(12, 4))

        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True)
        side = ttk.Frame(main, style="Side.TFrame", width=180)
        side.pack(side=tk.LEFT, fill=tk.Y)
        side.pack_propagate(False)
        ttk.Label(side, text=APP_NAME, background="#243142", foreground="#ffffff", font=("Microsoft YaHei UI", 11, "bold"), wraplength=150).pack(anchor="w", padx=14, pady=(16, 18))
        self.nav_buttons = {}
        for name, cmd in [
            ("首页工作台", self.show_dashboard), ("项目管理", self.show_projects), ("年度计划", self.show_plans),
            ("版本管理", self.show_versions), ("需求管理", self.show_requirements), ("资金管理", self.show_budget),
            ("成果物管理", self.show_artifacts), ("流程里程碑", self.show_milestones), ("搜索中心", self.show_search), ("报表导出", self.show_exports),
            ("系统设置", self.show_settings),
        ]:
            if not self.can_view_page(name):
                continue
            button = ttk.Button(side, text=name, style="Side.TButton", command=cmd)
            button.pack(fill=tk.X, padx=8, pady=2)
            self.nav_buttons[name] = button
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
        self.content_canvas.bind_all("<MouseWheel>", self.on_mousewheel)
        bottom = ttk.Frame(self, padding=(10, 5))
        bottom.pack(fill=tk.X)
        ttk.Label(bottom, text=f"数据库: {self.db.db_label}").pack(side=tk.LEFT)
        ttk.Label(bottom, text=f"版本: {APP_VERSION} · {APP_VARIANT}").pack(side=tk.RIGHT)

    def on_content_configure(self, _event=None):
        self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))

    def on_canvas_configure(self, event):
        self.content_canvas.itemconfigure(self.content_window, width=event.width)

    def on_mousewheel(self, event):
        if self.content_canvas.winfo_exists():
            self.content_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

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
            "info": ("#e8f1ff", "#1d4ed8"),
            "success": ("#e9f8f0", "#047857"),
            "warning": ("#fff5dc", "#b45309"),
            "danger": ("#ffecec", "#b91c1c"),
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

    def can_view_page(self, page_name):
        if page_name == "资金管理":
            return self.can_view_money()
        if page_name == "报表导出":
            return self.can_action("export")
        if page_name == "系统设置":
            return self.can_action("approve")
        return True

    def require_action(self, action, label):
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
        if self.current_role.get() == "客户" and self.current_user_id:
            projects = self.db.query("""SELECT p.id, p.project_name
                                        FROM planning_projects p
                                        INNER JOIN user_project_access a ON a.project_id=p.id
                                        WHERE a.user_id=? ORDER BY p.id""", (self.current_user_id,))
        else:
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
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height)
        ybar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        xbar = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        for key, label, width in columns:
            tree.heading(key, text=label, command=lambda col=key: self.sort_treeview(tree, col, False))
            tree.column(key, width=width, anchor="w")
        tree.tag_configure("odd", background="#ffffff")
        tree.tag_configure("even", background="#f8fafc")
        for status, color in STATUS_COLORS.items():
            tree.tag_configure(f"status_{status}", background=color)
        for index, row in enumerate(rows):
            tags = ["even" if index % 2 else "odd"]
            status = self.row_value(row, "status")
            if status in STATUS_COLORS:
                tags.append(f"status_{status}")
            tree.insert("", tk.END, values=[self.row_value(row, c[0]) for c in columns], tags=tuple(tags))
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        xbar.pack(side=tk.BOTTOM, fill=tk.X)
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
        ttk.Label(body, text=title, background="#ffffff").pack(anchor="w")
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
            "规划项目": len(self.projects),
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
        ttk.Label(self.content, text=f"当前视角：{role}。{msg}", wraplength=920).pack(anchor="w", pady=(2, 12))
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
        if self.current_role.get() == "客户":
            project_ids = list(self.projects.values())
            placeholders = ",".join(["?"] * len(project_ids))
            rows = self.db.query(f"SELECT id, project_code, project_name, customer_name, total_budget, status, updated_at FROM planning_projects WHERE id IN ({placeholders}) ORDER BY id DESC", tuple(project_ids)) if project_ids else []
        else:
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
            except Exception as exc:
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
                record = {
                    **d.result,
                    "project_id": self.current_project_id(), "annual_plan_id": plan_id, "version_id": version_id,
                    "estimated_budget": estimated_budget, "allocated_budget": allocated_budget, "actual_cost": actual_cost,
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
            record = {**d.result, "estimated_budget": estimated_budget, "planned_finish_date": planned_finish}
            self.db.update_requirement(req_id, record, self.current_user, now_text())
            self.show_requirements()
        except ValueError as exc:
            if str(exc) == "VERSION_FROZEN":
                proposed = {**d.result, "estimated_budget": estimated_budget, "planned_finish_date": planned_finish}
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
        canvas = tk.Canvas(frame, height=260, bg="#ffffff", highlightthickness=0)
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
            canvas.create_rectangle(x, y, x + w, y + h, fill=color, outline="#d9e1ea", width=1, tags=tags)
            canvas.create_text(x + 12, y + 14, text=title, anchor="w", fill="#172033", font=("Microsoft YaHei UI", 10, "bold"), width=w - 24, tags=tags)
            canvas.create_text(x + 12, y + 42, text=money_text(amount), anchor="w", fill="#334155", font=("Microsoft YaHei UI", 10), tags=tags)

        node(20, 85, 180, 72, project["project_name"] if project else "未选择项目", project["total_budget"] if project else 0, "#e8f1ff")
        node(250, 85, 180, 72, plan["plan_name"] if plan else "未选择年度", plan["annual_budget"] if plan else 0, "#e9f8f0")
        version_amount = version["version_budget"] if version else 0
        node(480, 85, 180, 72, f"{version['version_code']} {version['version_name']}" if version else "未选择版本", version_amount, "#fff5dc")
        for x in (200, 430):
            canvas.create_line(x, 121, x + 50, 121, arrow=tk.LAST, fill="#94a3b8", width=2)
        if not requirements:
            node(720, 85, 220, 72, "暂无版本需求", 0, "#eef2f7")
            canvas.create_line(660, 121, 720, 121, arrow=tk.LAST, fill="#94a3b8", width=2)
            return
        y = 20
        for req in requirements:
            over = (req["actual_cost"] or 0) > (req["allocated_budget"] or 0) and (req["allocated_budget"] or 0) > 0
            color = "#ffecec" if over else "#e8f7f5"
            tag = f"req_node_{req['id']}"
            node(720, y, 240, 42, f"{req['requirement_code']} {req['requirement_name']}", req["allocated_budget"], color, tag=tag)
            canvas.create_text(730, y + 33, text=f"实际 {money_text(req['actual_cost'])}", anchor="w", fill="#64748b", font=("Microsoft YaHei UI", 9), tags=(tag,))
            canvas.create_line(660, 121, 720, y + 21, arrow=tk.LAST, fill="#94a3b8", width=1)
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
        ttk.Button(bar, text="打开/下载附件", command=self.open_selected_artifact).pack(side=tk.LEFT, padx=(8, 0))
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
        try:
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
            stored_path = None
            artifact_id = None
            try:
                object_id = self.parse_int(d.result["related_object_id"], "对象ID")
                if not self.validate_artifact_target(d.result["related_object_type"], object_id):
                    raise ValueError("挂载对象不存在，或不属于当前项目。")
                src = Path(source)
                code = "ART-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
                stored_path = self.db.store_attachment(src, code)
                t = now_text()
                record = {
                    "artifact_code": code, "artifact_name": src.name, "artifact_type": d.result["artifact_type"],
                    "file_path": stored_path, "file_ext": src.suffix, "file_size": src.stat().st_size,
                    "related_object_type": d.result["related_object_type"], "related_object_id": object_id,
                    "version_no": d.result["version_no"], "description": d.result["description"],
                    "uploaded_by": self.current_user, "uploaded_at": t, "created_at": t,
                }
                artifact_id = self.db.create_artifact_record(record, self.current_user, t)
            except Exception as exc:
                log_transaction_exception("add_artifact", exc)
                if stored_path and artifact_id is None:
                    try:
                        self.db.delete_attachment(stored_path)
                    except Exception:
                        LOGGER.exception("attachment_cleanup_failed path=%s", stored_path)
                messagebox.showerror("保存失败", str(exc))
                return
            self.show_artifacts()

    def show_search(self):
        self.clear("搜索中心")
        keyword = self.search_var.get().strip()
        bar = self.make_action_bar(self.content)
        ttk.Label(bar, text="关键词").pack(side=tk.LEFT)
        ttk.Entry(bar, textvariable=self.search_var, width=36).pack(side=tk.LEFT, padx=8)
        ttk.Button(bar, text="查询", command=self.show_search, style="Primary.TButton").pack(side=tk.LEFT)
        like = f"%{keyword}%"
        access_clause = ""
        params = [like, like, like, like, like, like]
        if self.current_role.get() == "客户":
            project_ids = list(self.projects.values())
            if not project_ids:
                rows = []
                access_clause = None
            else:
                access_clause = f" AND r.project_id IN ({','.join(['?'] * len(project_ids))})"
                params.extend(project_ids)
        if keyword and access_clause is not None:
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
        elif not keyword:
            rows = []
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
            ttk.Button(self.content, text="创建附件备份 ZIP", command=self.create_backup).pack(anchor="w", pady=5)
            ttk.Button(self.content, text="从附件备份 ZIP 恢复", command=self.restore_backup).pack(anchor="w", pady=5)
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
            messagebox.showwarning("权限不足", "只有管理员可以执行附件备份。")
            return
        if self.db.attachment_storage == "oss":
            messagebox.showinfo("OSS 备份", "OSS 附件应通过 Bucket 版本控制、生命周期和跨区域复制策略备份；应用不会下载整个 Bucket。")
            return
        backup = self.db.backups_dir / f"backup_{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
        with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.txt", "MySQL 远程版附件备份；数据库必须使用 mysqldump 或服务端备份。\n")
            safe_config = {key: value for key, value in self.db.config.items() if key != "password"}
            z.writestr("config_snapshot.json", json.dumps(safe_config, ensure_ascii=False, indent=2))
            z.writestr("attachments/", "")
            for file in self.db.attachments_dir.rglob("*"):
                if file.is_file() and not file.is_symlink():
                    z.write(file, f"attachments/{file.relative_to(self.db.attachments_dir).as_posix()}")
        self.db.log(self.current_user, "backup", None, "create", "", backup, "创建本地备份")
        messagebox.showinfo("备份完成", str(backup))

    def restore_backup(self):
        if self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员可以执行附件恢复。")
            return
        if self.db.attachment_storage == "oss":
            messagebox.showinfo("OSS 恢复", "请在 OSS 控制台或运维流程中恢复对象版本，应用不执行 Bucket 批量恢复。")
            return
        source = filedialog.askopenfilename(title="选择附件备份 ZIP", filetypes=[("ZIP", "*.zip")])
        if not source:
            return
        if not messagebox.askyesno("确认恢复", "此操作只恢复当前客户端附件，不会恢复 MySQL 数据库。现有附件将被替换，是否继续？"):
            return
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        staged = self.db.attachments_dir.parent / f".{self.db.attachments_dir.name}.restore.{stamp}"
        old = self.db.attachments_dir.parent / f".{self.db.attachments_dir.name}.before_restore.{stamp}"
        try:
            with tempfile.TemporaryDirectory(prefix="crm-attachments-") as temp_dir:
                temp_path = Path(temp_dir)
                with zipfile.ZipFile(source, "r") as z:
                    validate_restore_archive(z, required_prefix="attachments/")
                    z.extractall(temp_path)
                restored = temp_path / "attachments"
                shutil.copytree(restored, staged)
                safety = self.db.backups_dir / f"before_attachment_restore_{stamp}.zip"
                with zipfile.ZipFile(safety, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("attachments/", "")
                    for file in self.db.attachments_dir.rglob("*"):
                        if file.is_file() and not file.is_symlink():
                            z.write(file, f"attachments/{file.relative_to(self.db.attachments_dir).as_posix()}")
                os.replace(self.db.attachments_dir, old)
                os.replace(staged, self.db.attachments_dir)
                shutil.rmtree(old, ignore_errors=True)
            self.db.log(self.current_user, "attachment_backup", None, "restore", source, self.db.attachments_dir, "恢复客户端附件备份")
            messagebox.showinfo("恢复完成", "服务器目录附件已恢复；MySQL 数据库未变更。")
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            log_transaction_exception("restore_attachment_backup", exc)
            try:
                if old.exists():
                    if self.db.attachments_dir.exists():
                        shutil.rmtree(self.db.attachments_dir)
                    os.replace(old, self.db.attachments_dir)
            except OSError as rollback_exc:
                LOGGER.exception("attachment_restore_rollback_failed")
                messagebox.showerror("自动回滚失败", f"请使用恢复前附件快照人工恢复：{rollback_exc}")
            if staged.exists():
                shutil.rmtree(staged, ignore_errors=True)
            messagebox.showerror("恢复失败", f"已尝试自动回滚到恢复前附件。错误：{exc}")

    def show_settings(self):
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
            self.section_title(self.content, "用户与角色", "管理员可创建账号并启用或停用；密码仅保存 PBKDF2 哈希。")
            user_bar = self.make_action_bar(self.content)
            ttk.Button(user_bar, text="新建用户", command=self.add_user, style="Primary.TButton").pack(side=tk.LEFT)
            ttk.Button(user_bar, text="启用/停用", command=self.toggle_user_active).pack(side=tk.LEFT, padx=(8, 0))
            ttk.Button(user_bar, text="重置密码", command=self.reset_user_password).pack(side=tk.LEFT, padx=(8, 0))
            ttk.Button(user_bar, text="授权当前项目", command=self.grant_current_project).pack(side=tk.LEFT, padx=(8, 0))
            ttk.Button(user_bar, text="撤销当前项目", command=self.revoke_current_project).pack(side=tk.LEFT, padx=(8, 0))
            users = self.db.query("""SELECT u.id, u.username, u.display_name, u.role_name, u.is_active,
                                             COALESCE(GROUP_CONCAT(DISTINCT p.project_name ORDER BY p.project_name SEPARATOR ', '), '') project_access,
                                             u.created_at, u.updated_at
                                      FROM users u
                                      LEFT JOIN user_project_access a ON a.user_id=u.id
                                      LEFT JOIN planning_projects p ON p.id=a.project_id
                                      GROUP BY u.id, u.username, u.display_name, u.role_name, u.is_active, u.created_at, u.updated_at
                                      ORDER BY u.id""")
            self.user_tree = self.add_table(self.content, [("id", "ID", 50), ("username", "用户名", 120), ("display_name", "显示名称", 130), ("role_name", "角色", 100), ("is_active", "启用", 60), ("project_access", "客户项目权限", 240), ("created_at", "创建时间", 145), ("updated_at", "更新时间", 145)], users, 7)
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
        self.section_title(self.content, "操作日志", "集中记录关键业务操作、登录成功/失败和权限拒绝；事件 ID 可与客户端 audit.log 对账。")
        audit_bar = self.make_action_bar(self.content)
        ttk.Label(audit_bar, text="对象类型").pack(side=tk.LEFT)
        type_box = ttk.Combobox(audit_bar, textvariable=self.operation_log_type_filter,
                                values=["全部", "system", "authentication", "permission", "user", "user_project_access", "planning_project", "annual_plan", "implementation_version", "requirement", "budget_flow", "artifact", "change_request", "backup", "healthcheck"],
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
        if len(dialog.result["password"]) < 8:
            messagebox.showerror("保存失败", "初始密码至少 8 位。")
            return
        t = now_text()
        try:
            self.db.execute("INSERT INTO users(username, display_name, password_hash, role_name, is_active, created_at, updated_at) VALUES(?,?,?,?,1,?,?)",
                            (dialog.result["username"], dialog.result["display_name"], hash_password(dialog.result["password"]), dialog.result["role_name"], t, t))
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
        self.db.execute("UPDATE users SET is_active=?, updated_at=? WHERE id=?", (new_value, now_text(), user_id))
        self.db.log(self.current_user, "user", user_id, "enable" if new_value else "disable", user["is_active"], new_value, "更新用户启用状态")
        self.show_settings()

    def grant_current_project(self):
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
        if self.current_role.get() != "管理员":
            messagebox.showwarning("权限不足", "只有管理员可以重置用户密码。")
            return
        user_id = self.selected_user_id()
        if not user_id:
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
        self.db.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                        (hash_password(dialog.result["new_password"]), now_text(), user_id))
        self.db.log(self.current_user, "user", user_id, "reset_password", "", "", f"重置用户 {user['username']} 密码")
        messagebox.showinfo("重置完成", "用户密码已更新。")

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
