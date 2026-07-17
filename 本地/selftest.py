import traceback
import tempfile
import os
import sqlite3
from pathlib import Path

import app


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def button_texts(widget):
    labels = []
    for child in widget.winfo_children():
        if child.winfo_class() in {"TButton", "Button"}:
            labels.append(str(child.cget("text")))
        labels.extend(button_texts(child))
    return labels


def contrast_ratio(foreground, background):
    def luminance(value):
        channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        channels = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
                    for channel in channels]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def assert_log_files(logs_dir):
    app.LOGGER.info("selftest_runtime_marker")
    app.LOGGER.error("selftest_error_marker")
    app.audit_event("自测", "logging", None, "selftest", "日志分流检查")
    for logger in (app.LOGGER, app.AUDIT_LOGGER):
        for handler in logger.handlers:
            handler.flush()
    runtime_path = logs_dir / "runtime.log"
    error_path = logs_dir / "error.log"
    audit_path = logs_dir / "audit.log"
    assert_true(runtime_path.exists() and "selftest_runtime_marker" in runtime_path.read_text(encoding="utf-8"), "运行日志未写入")
    assert_true(error_path.exists() and "selftest_error_marker" in error_path.read_text(encoding="utf-8"), "错误日志未分流")
    audit_lines = [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert_true(audit_lines and app.json.loads(audit_lines[-1])["operation_type"] == "selftest", "审计日志不是有效 JSON Lines")


def assert_critical_error_routing():
    old_level = os.environ.get("CRM_LOG_LEVEL")
    with tempfile.TemporaryDirectory(prefix="crm-critical-log-selftest-") as temp_dir:
        try:
            os.environ["CRM_LOG_LEVEL"] = "CRITICAL"
            logs_dir = app.configure_logging(Path(temp_dir), "critical routing selftest")
            app.LOGGER.error("error_must_not_be_suppressed")
            for handler in app.LOGGER.handlers:
                handler.flush()
            assert_true("error_must_not_be_suppressed" in (logs_dir / "error.log").read_text(encoding="utf-8"),
                        "CRM_LOG_LEVEL=CRITICAL 时错误日志被错误过滤")
        finally:
            app.close_logging()
            if old_level is None:
                os.environ.pop("CRM_LOG_LEVEL", None)
            else:
                os.environ["CRM_LOG_LEVEL"] = old_level


def test_fresh_database_initialization():
    business_tables = [
        "planning_projects",
        "annual_plans",
        "implementation_versions",
        "requirements",
        "budget_flows",
        "funding_applications",
        "artifacts",
        "operation_records",
        "change_requests",
        "change_request_payloads",
        "requirement_status_history",
        "version_baselines",
        "version_baseline_requirements",
        "version_baseline_artifacts",
        "task_effort_entries",
        "dashboard_preferences",
        "operation_logs",
    ]
    default_tags = {
        "业务痛点",
        "功能优化",
        "运维 Bug",
        "招投标要求",
        "验收整改",
        "客户新增",
        "版本必做",
        "待确认",
    }
    old_seed_demo = os.environ.pop("CRM_SEED_DEMO_DATA", None)
    db = None
    try:
        with tempfile.TemporaryDirectory(prefix="crm-fresh-database-selftest-") as temp_dir:
            base_dir = Path(temp_dir) / "application"
            data_dir = Path(temp_dir) / "data"
            base_dir.mkdir()
            try:
                db = app.Database(base_dir, data_dir=data_dir)
                tables = {
                    row["name"]
                    for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")
                }
                assert_true(set(business_tables).issubset(tables), "首次启动未创建完整业务表结构")
                for table in business_tables:
                    row_count = db.one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
                    assert_true(row_count == 0, f"首次启动不应预置业务数据：{table}")
                users = db.query("SELECT username, display_name, role_name, is_active FROM users")
                assert_true(len(users) == 1 and users[0]["username"] == "admin" and
                            users[0]["display_name"] == "默认管理员" and
                            users[0]["role_name"] == "管理员" and users[0]["is_active"] == 1,
                            "首次启动只能初始化一个可用的本地默认管理员")
                tags = db.query("SELECT tag_name, is_active FROM tag_definitions")
                assert_true(len(tags) == 8 and {row["tag_name"] for row in tags} == default_tags and
                            all(row["is_active"] == 1 for row in tags),
                            "首次启动必须且只能初始化 8 个可用基础标签")
                audit_lines = [
                    line for line in (db.logs_dir / "audit.log").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                assert_true(audit_lines == [], "首次启动不应生成虚假的业务审计事件")
                assert_true(db.db_path == data_dir / "app.db", "本地数据库未保存在安装路径 data 目录")
            finally:
                if db is not None:
                    db.close()
                    db = None
                app.close_logging()
    finally:
        if db is not None:
            db.close()
        app.close_logging()
        if old_seed_demo is not None:
            os.environ["CRM_SEED_DEMO_DATA"] = old_seed_demo


def test_packaged_database_never_seeds_demo_data():
    old_seed_demo = os.environ.get("CRM_SEED_DEMO_DATA")
    had_frozen = hasattr(app.sys, "frozen")
    old_frozen = getattr(app.sys, "frozen", None)
    db = None
    try:
        os.environ["CRM_SEED_DEMO_DATA"] = "1"
        app.sys.frozen = True
        with tempfile.TemporaryDirectory(prefix="crm-packaged-database-selftest-") as temp_dir:
            base_dir = Path(temp_dir) / "application"
            data_dir = Path(temp_dir) / "data"
            base_dir.mkdir()
            try:
                db = app.Database(base_dir, data_dir=data_dir)
                for table in (
                    "planning_projects",
                    "annual_plans",
                    "implementation_versions",
                    "requirements",
                    "budget_flows",
                    "artifacts",
                ):
                    row_count = db.one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
                    assert_true(row_count == 0, f"正式安装程序不得通过环境变量预置业务数据：{table}")
            finally:
                if db is not None:
                    db.close()
                    db = None
                app.close_logging()
    finally:
        if db is not None:
            db.close()
        app.close_logging()
        if had_frozen:
            app.sys.frozen = old_frozen
        else:
            delattr(app.sys, "frozen")
        if old_seed_demo is None:
            os.environ.pop("CRM_SEED_DEMO_DATA", None)
        else:
            os.environ["CRM_SEED_DEMO_DATA"] = old_seed_demo


def test_legacy_audit_migration():
    with tempfile.TemporaryDirectory(prefix="crm-legacy-audit-selftest-") as temp_dir:
        base_dir = Path(temp_dir) / "application"
        data_dir = Path(temp_dir) / "data"
        base_dir.mkdir()
        data_dir.mkdir()
        legacy = sqlite3.connect(data_dir / "app.db")
        legacy.execute("""CREATE TABLE planning_projects (
                           id INTEGER PRIMARY KEY AUTOINCREMENT, project_code TEXT, project_name TEXT,
                           customer_name TEXT, project_background TEXT, total_budget REAL, status TEXT,
                           created_at TEXT, updated_at TEXT)""")
        legacy.execute("""INSERT INTO planning_projects(project_code, project_name, total_budget, status, created_at, updated_at)
                           VALUES('LEGACY-PRJ','旧项目',1000,'active','2026-01-01','2026-01-01')""")
        legacy.execute("""CREATE TABLE requirements (
                           id INTEGER PRIMARY KEY AUTOINCREMENT, requirement_name TEXT, business_key TEXT,
                           project_id INTEGER, version_id INTEGER, status TEXT, estimated_hours REAL,
                           actual_hours REAL, is_deleted INTEGER, created_at TEXT)""")
        legacy.execute("""INSERT INTO requirements(requirement_name, business_key, project_id, status,
                                                     estimated_hours, actual_hours, is_deleted, created_at)
                           VALUES('旧需求',' Legacy   Key ',1,'草稿',1,0,0,'2026-01-01')""")
        legacy.execute("CREATE TABLE artifacts (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        legacy.execute("INSERT INTO artifacts DEFAULT VALUES")
        legacy.execute("""CREATE TABLE version_baseline_requirements (
                           id INTEGER PRIMARY KEY AUTOINCREMENT, baseline_id INTEGER, requirement_id INTEGER,
                           requirement_code TEXT, requirement_name TEXT, status TEXT, priority TEXT,
                           allocated_budget REAL, actual_cost REAL, updated_at TEXT)""")
        legacy.execute("""CREATE TABLE version_baselines (
                           id INTEGER PRIMARY KEY AUTOINCREMENT, version_id INTEGER, snapshot_no INTEGER,
                           version_budget REAL, requirement_count INTEGER, allocated_budget REAL,
                           actual_cost REAL, created_by TEXT, created_at TEXT)""")
        legacy.execute("""CREATE TABLE change_requests (
                           id INTEGER PRIMARY KEY AUTOINCREMENT, version_id INTEGER, requirement_id INTEGER,
                           change_title TEXT, change_reason TEXT, impact_scope TEXT, approval_status TEXT,
                           requested_by TEXT, requested_at TEXT, approved_by TEXT, approved_at TEXT)""")
        legacy.execute("""INSERT INTO change_requests(version_id, requirement_id, change_title, approval_status, requested_at)
                           VALUES(1,1,'旧待审批1','pending','2026-01-01'),(1,1,'旧待审批2','pending','2026-01-02')""")
        legacy.execute("""CREATE TABLE operation_logs (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           operator_name TEXT, operation_time TEXT NOT NULL, object_type TEXT NOT NULL,
                           object_id INTEGER, operation_type TEXT NOT NULL, before_value TEXT,
                           after_value TEXT, description TEXT)""")
        legacy.execute("""INSERT INTO operation_logs(operator_name, operation_time, object_type, operation_type, description)
                           VALUES('旧用户','2026-01-01 00:00:00','legacy','update','旧版记录')""")
        legacy.commit()
        legacy.close()
        db = None
        try:
            db = app.Database(base_dir, data_dir=data_dir)
            columns = {row[1] for row in db.conn.execute("PRAGMA table_info(operation_logs)")}
            assert_true({"event_id", "result"}.issubset(columns), "旧版操作日志字段未升级")
            row = db.one("SELECT event_id, result FROM operation_logs WHERE object_type='legacy'")
            assert_true(row["event_id"] is None and row["result"] == "legacy", "旧日志被错误标记为成功")
            index = db.one("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_operation_logs_event_id'")
            assert_true(index and "UNIQUE INDEX" in index["sql"].upper(), "审计事件 ID 唯一索引未创建")
            project_columns = {row[1] for row in db.query("PRAGMA table_info(planning_projects)")}
            requirement_columns = {row[1] for row in db.query("PRAGMA table_info(requirements)")}
            artifact_columns = {row[1] for row in db.query("PRAGMA table_info(artifacts)")}
            baseline_columns = {row[1] for row in db.query("PRAGMA table_info(version_baseline_requirements)")}
            baseline_header_columns = {row[1] for row in db.query("PRAGMA table_info(version_baselines)")}
            change_columns = {row[1] for row in db.query("PRAGMA table_info(change_requests)")}
            assert_true("current_stage" in project_columns, "旧库未迁移项目当前阶段")
            assert_true(db.one("SELECT current_stage FROM planning_projects LIMIT 1")["current_stage"] == "宏观规划",
                        "旧库项目阶段默认值错误")
            assert_true("parent_requirement_id" in requirement_columns, "旧库未迁移原需求关联")
            assert_true(db.one("SELECT business_key FROM requirements WHERE id=1")["business_key"] == "legacy key",
                        "旧库业务标识未按 trim/lower/折叠空白规则迁移")
            assert_true({"visibility", "approval_status", "change_request_id", "reviewed_by", "reviewed_at",
                         "review_note"}.issubset(artifact_columns),
                        "旧库未迁移成果物可见性或完整审批字段")
            assert_true({"requirement_description", "business_key", "estimated_budget", "estimated_hours",
                         "parent_requirement_id"}.issubset(baseline_columns), "旧库未迁移完整基线字段")
            assert_true({"estimated_hours", "actual_hours"}.issubset(baseline_header_columns),
                        "旧库未迁移基线工时汇总字段")
            assert_true({"expected_baseline_sequence", "decision_note", "applied_by", "applied_at",
                         "applied_baseline_id"}.issubset(change_columns),
                        "旧库未迁移变更基线并发或独立执行字段")
            pending_count = db.one("SELECT COUNT(*) c FROM change_requests WHERE requirement_id=1 AND approval_status='pending'")["c"]
            rejected_count = db.one("SELECT COUNT(*) c FROM change_requests WHERE requirement_id=1 AND approval_status='rejected'")["c"]
            assert_true(pending_count == 1 and rejected_count == 1, "旧库重复待审批变更未安全去重")
            pending_index = db.one("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_change_pending_requirement'")
            assert_true(pending_index and "UNIQUE INDEX" in pending_index["sql"].upper(), "待审批变更唯一索引未创建")
            for table in ["funding_applications", "operation_records", "version_baseline_artifacts"]:
                assert_true(db.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)) is not None,
                            f"旧库未创建新表：{table}")
        finally:
            if db is not None:
                db.close()
            app.close_logging()


def test_legacy_approved_change_migration():
    with tempfile.TemporaryDirectory(prefix="crm-legacy-approved-change-") as temp_dir:
        base_dir = Path(temp_dir) / "application"
        data_dir = Path(temp_dir) / "data"
        base_dir.mkdir()
        db = None
        try:
            db = app.Database(base_dir, data_dir=data_dir)
            t = app.now_text()
            project_id = db.execute("""INSERT INTO planning_projects(
                                          project_code, project_name, total_budget, created_at, updated_at)
                                       VALUES(?,?,?,?,?)""",
                                    ("LEGACY-CHANGE", "旧变更迁移项目", 1000, t, t)).lastrowid
            plan_id = db.execute("""INSERT INTO annual_plans(
                                       project_id, plan_year, plan_name, annual_budget, created_at, updated_at)
                                    VALUES(?,?,?,?,?,?)""",
                                 (project_id, 2026, "旧变更年度", 1000, t, t)).lastrowid
            version_id = db.execute("""INSERT INTO implementation_versions(
                                          project_id, annual_plan_id, version_code, version_name,
                                          version_budget, created_at, updated_at)
                                       VALUES(?,?,?,?,?,?,?)""",
                                    (project_id, plan_id, "LEGACY-V1", "旧变更版本", 1000, t, t)).lastrowid
            requirement_id = db.execute("""INSERT INTO requirements(
                                              requirement_code, requirement_name, requirement_description,
                                              business_key, source_role, project_id, annual_plan_id, version_id,
                                              status, created_at, updated_at)
                                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                        ("LEGACY-REQ", "执行前名称", "旧版变更迁移验证", "legacy-change",
                                         "咨询负责人", project_id, plan_id, version_id, "草稿", t, t)).lastrowid
            original_baseline = db.freeze_version_with_baseline(version_id, "旧版发布人", t)
            assert_true(original_baseline["snapshot_no"] == 1, "旧变更迁移夹具初始基线错误")
            db.execute(
                "UPDATE requirements SET requirement_name='旧版审批已生效名称', status='变更中', updated_at=? WHERE id=?",
                (t, requirement_id),
            )
            db.conn.execute("DROP INDEX IF EXISTS idx_change_pending_requirement")
            db.conn.execute("DROP INDEX IF EXISTS idx_change_version_status_baseline")
            db.conn.execute("ALTER TABLE change_requests RENAME TO change_requests_new_schema")
            db.conn.execute("""CREATE TABLE change_requests (
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
                               )""")
            change_id = db.conn.execute("""INSERT INTO change_requests(
                                               version_id, requirement_id, change_title, change_reason,
                                               approval_status, requested_by, requested_at, approved_by, approved_at)
                                            VALUES(?,?,?,?,'approved',?,?,?,?)""",
                                        (version_id, requirement_id, "旧版已批准变更", "旧逻辑批准即生效",
                                         "旧申请人", t, "旧审批人", t)).lastrowid
            db.conn.execute(
                "INSERT INTO change_request_payloads(change_request_id, change_type, proposed_value) VALUES(?,?,?)",
                (change_id, "update", app.json.dumps({"requirement_name": "旧版审批已生效名称"}, ensure_ascii=False)),
            )
            db.conn.execute("DROP TABLE change_requests_new_schema")
            db.conn.commit()
            db.close()
            db = None
            app.close_logging()

            db = app.Database(base_dir, data_dir=data_dir)
            migrated = db.one("SELECT * FROM change_requests WHERE id=?", (change_id,))
            migrated_baseline = db.one(
                "SELECT snapshot_no FROM version_baselines WHERE id=?", (migrated["applied_baseline_id"],)
            )
            migrated_snapshot = db.one("""SELECT requirement_name FROM version_baseline_requirements
                                           WHERE baseline_id=? AND requirement_id=?""",
                                       (migrated["applied_baseline_id"], requirement_id))
            assert_true(migrated["approval_status"] == "applied" and migrated["applied_by"] == "旧审批人" and
                        migrated["applied_at"] == t and migrated["expected_baseline_sequence"] == 1 and
                        migrated_baseline and migrated_baseline["snapshot_no"] == 2 and
                        migrated_snapshot and migrated_snapshot["requirement_name"] == "旧版审批已生效名称",
                        "旧版已批准即生效变更未迁移为已执行或未生成当前态基线")
            db.close()
            db = None
            app.close_logging()

            db = app.Database(base_dir, data_dir=data_dir)
            assert_true(db.one("SELECT COUNT(*) c FROM version_baselines WHERE version_id=?", (version_id,))["c"] == 2 and
                        db.one("SELECT approval_status FROM change_requests WHERE id=?", (change_id,))["approval_status"] == "applied",
                        "旧版已执行变更迁移重复运行时不幂等")
        finally:
            if db is not None:
                db.close()
            app.close_logging()


def test_legacy_business_key_conflict_migration():
    with tempfile.TemporaryDirectory(prefix="crm-legacy-business-key-selftest-") as temp_dir:
        base_dir = Path(temp_dir) / "application"
        data_dir = Path(temp_dir) / "data"
        base_dir.mkdir()
        data_dir.mkdir()
        legacy = sqlite3.connect(data_dir / "app.db")
        legacy.execute("""CREATE TABLE planning_projects (
                           id INTEGER PRIMARY KEY AUTOINCREMENT, project_code TEXT, project_name TEXT,
                           total_budget REAL, status TEXT, created_at TEXT, updated_at TEXT)""")
        legacy.execute("""INSERT INTO planning_projects(
                               id, project_code, project_name, total_budget, status, created_at, updated_at)
                           VALUES(1,'LEGACY-BK','业务标识迁移项目',1000,'active','2026-01-01','2026-01-01')""")
        legacy.execute("""CREATE TABLE requirements (
                           id INTEGER PRIMARY KEY AUTOINCREMENT, requirement_name TEXT, business_key TEXT,
                           project_id INTEGER, version_id INTEGER, status TEXT, estimated_hours REAL,
                           actual_hours REAL, is_deleted INTEGER, created_at TEXT)""")
        legacy.execute("""INSERT INTO requirements(
                               id, requirement_name, business_key, project_id, version_id, status,
                               estimated_hours, actual_hours, is_deleted, created_at)
                           VALUES(1,'旧需求 A','foo  bar',1,NULL,'草稿',0,0,0,'2026-01-01'),
                                 (2,'旧需求 B',' Foo Bar ',1,NULL,'草稿',0,0,0,'2026-01-01')""")
        legacy.commit()
        legacy.close()

        failed_db = object.__new__(app.Database)
        try:
            try:
                failed_db.__init__(base_dir, data_dir=data_dir)
                raise AssertionError("旧库规范业务标识冲突未阻止迁移")
            except RuntimeError as exc:
                assert_true("规范化后存在同版本业务标识冲突" in str(exc), "旧库业务标识冲突提示异常")
        finally:
            if hasattr(failed_db, "conn"):
                failed_db.close()
            app.close_logging()

        legacy = sqlite3.connect(data_dir / "app.db")
        unchanged = [row[0] for row in legacy.execute("SELECT business_key FROM requirements ORDER BY id")]
        assert_true(unchanged == ["foo  bar", " Foo Bar "], "迁移冲突后旧业务标识被部分覆盖")
        legacy.execute("DELETE FROM requirements WHERE id=2")
        legacy.commit()
        legacy.close()

        db = None
        try:
            db = app.Database(base_dir, data_dir=data_dir)
            assert_true(db.one("SELECT business_key FROM requirements WHERE id=1")["business_key"] == "foo bar",
                        "无冲突旧业务标识未完成规范化迁移")
            db.conn.execute("""INSERT INTO requirements(
                                     id, requirement_name, business_key, project_id, version_id, status,
                                     estimated_hours, actual_hours, is_deleted, created_at)
                                  VALUES(2,'旧需求 B',' Foo Bar ',1,NULL,'草稿',0,0,0,'2026-01-01')""")
            db.conn.commit()
            try:
                db.healthcheck()
                raise AssertionError("健康检查未发现同版本规范业务标识冲突")
            except RuntimeError as exc:
                assert_true("业务标识未规范化" in str(exc) and "同版本业务标识冲突" in str(exc),
                            "业务标识健康检查未同时报告规范差异与同版本冲突")
        finally:
            if db is not None:
                db.close()
            app.close_logging()


def test_database_transactions():
    with tempfile.TemporaryDirectory(prefix="crm-selftest-") as temp_dir:
        base_dir = Path(temp_dir) / "application"
        base_dir.mkdir()
        db = None
        try:
            db = app.Database(base_dir, data_dir=Path(temp_dir) / "runtime-data")
            req = db.one("SELECT * FROM requirements WHERE requirement_code='REQ-DEMO-001'")
            demo_version_count = db.one(
                "SELECT COUNT(*) count FROM implementation_versions WHERE project_id=?",
                (req["project_id"],),
            )["count"]
            assert_true(demo_version_count >= 2, "演示数据不足两个版本，无法验证跨版本比对")
            db.execute("UPDATE requirements SET remark='原始业务备注' WHERE id=?", (req["id"],))
            req = db.one("SELECT * FROM requirements WHERE id=?", (req["id"],))
            project = db.one("SELECT total_budget FROM planning_projects WHERE id=?", (req["project_id"],))
            annual_total = db.one("SELECT COALESCE(SUM(annual_budget), 0) total FROM annual_plans WHERE project_id=?", (req["project_id"],))
            assert_true(
                app.validate_cumulative_budget(400000, annual_total["total"], project["total_budget"], "年度预算", "项目总预算") == 1000000,
                "年度累计预算边界校验错误",
            )
            try:
                app.validate_cumulative_budget(400001, annual_total["total"], project["total_budget"], "年度预算", "项目总预算")
                raise AssertionError("年度累计预算超出项目总预算未被拒绝")
            except ValueError:
                pass
            plan = db.one("SELECT annual_budget FROM annual_plans WHERE id=?", (req["annual_plan_id"],))
            version_total = db.one("SELECT COALESCE(SUM(version_budget), 0) total FROM implementation_versions WHERE annual_plan_id=?", (req["annual_plan_id"],))
            remaining_version_budget = float(plan["annual_budget"]) - float(version_total["total"])
            assert_true(
                app.validate_cumulative_budget(remaining_version_budget, version_total["total"], plan["annual_budget"], "版本预算", "年度预算")
                == float(plan["annual_budget"]),
                "版本累计预算边界校验错误",
            )
            try:
                app.validate_cumulative_budget(remaining_version_budget + 0.01, version_total["total"], plan["annual_budget"], "版本预算", "年度预算")
                raise AssertionError("版本累计预算超出年度预算未被拒绝")
            except ValueError:
                pass
            before_allocated = float(req["allocated_budget"] or 0)
            assert_true(app.BUDGET_FLOW_TYPES == ("已分配预算", "实际消耗", "调整金额"),
                        "本地资金流水类型未与远程业务口径保持一致")
            for unsupported_type in ("计划预算", "冻结金额"):
                try:
                    db.record_budget_flow(f"BF-SELFTEST-TYPE-{unsupported_type}", req["project_id"], req["annual_plan_id"],
                                          req["version_id"], req["id"], unsupported_type, 100,
                                          "unsupported", "自测", app.now_text())
                    raise AssertionError(f"已停用资金类型仍可新增：{unsupported_type}")
                except ValueError as exc:
                    assert_true("资金类型无效" in str(exc), "停用资金类型拒绝提示异常")
            for flow_type, invalid_amount in (("已分配预算", -1), ("实际消耗", -1), ("调整金额", 0)):
                try:
                    db.record_budget_flow(f"BF-SELFTEST-AMOUNT-{flow_type}", req["project_id"], req["annual_plan_id"],
                                          req["version_id"], req["id"], flow_type, invalid_amount,
                                          "非法金额", "自测", app.now_text())
                    raise AssertionError(f"非法资金金额未被拒绝：{flow_type}/{invalid_amount}")
                except ValueError:
                    pass
            db.record_budget_flow("BF-SELFTEST-1", req["project_id"], req["annual_plan_id"], req["version_id"], req["id"],
                                  "已分配预算", 1000, "selftest", "自测", app.now_text())
            after = db.one("SELECT allocated_budget FROM requirements WHERE id=?", (req["id"],))
            assert_true(float(after["allocated_budget"]) == before_allocated + 1000, "资金流水未原子更新需求预算")
            budget_log = db.one("""SELECT before_value, after_value FROM operation_logs
                                   WHERE object_type='budget_flow' AND object_id=? ORDER BY id DESC LIMIT 1""", (req["id"],))
            before_log = app.json.loads(budget_log["before_value"])
            after_log = app.json.loads(budget_log["after_value"])
            assert_true(float(before_log["allocated_budget"]) == before_allocated and
                        float(after_log["allocated_budget"]) == before_allocated + 1000,
                        "资金流水审计未记录预算修改前后值")

            precision_time = app.now_text()
            precision_req_id = db.execute("""INSERT INTO requirements(
                                                 requirement_code, requirement_name, requirement_description,
                                                 business_key, source_role, project_id, annual_plan_id, version_id,
                                                 allocated_budget, actual_cost, status, created_at, updated_at)
                                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                          ("REQ-SELFTEST-PRECISION", "资金精度自测", "验证小数累计和归零",
                                           "selftest-precision", "咨询负责人", req["project_id"],
                                           req["annual_plan_id"], req["version_id"], 0, 0, "草稿",
                                           precision_time, precision_time)).lastrowid
            for index, amount in enumerate((0.1, 0.2), start=1):
                db.record_budget_flow(f"BF-SELFTEST-PRECISION-A{index}", req["project_id"], req["annual_plan_id"],
                                      req["version_id"], precision_req_id, "已分配预算", amount,
                                      "小数分配", "自测", app.now_text())
                db.record_budget_flow(f"BF-SELFTEST-PRECISION-C{index}", req["project_id"], req["annual_plan_id"],
                                      req["version_id"], precision_req_id, "实际消耗", amount,
                                      "小数消耗", "自测", app.now_text())
            precision_row = db.one("SELECT allocated_budget, actual_cost FROM requirements WHERE id=?", (precision_req_id,))
            assert_true(float(precision_row["allocated_budget"]) == 0.3 and float(precision_row["actual_cost"]) == 0.3,
                        "0.1 + 0.2 资金累计未规范为两位小数")
            try:
                db.record_budget_flow("BF-SELFTEST-PRECISION-OVER", req["project_id"], req["annual_plan_id"],
                                      req["version_id"], precision_req_id, "实际消耗", 0.01,
                                      "真实超额", "自测", app.now_text())
                raise AssertionError("真实超额实际消耗未被拒绝")
            except ValueError as exc:
                assert_true(str(exc) == "ACTUAL_OVERRUN", "实际消耗超额提示异常")
            for index, amount in enumerate((-0.1, -0.2), start=1):
                db.record_budget_flow(f"BF-SELFTEST-PRECISION-R{index}", req["project_id"], req["annual_plan_id"],
                                      req["version_id"], precision_req_id, "调整金额", amount,
                                      "负向归零", "自测", app.now_text())
            assert_true(float(db.one("SELECT allocated_budget FROM requirements WHERE id=?", (precision_req_id,))["allocated_budget"]) == 0,
                        "负向预算调整归零后残留浮点误差")
            db.execute("UPDATE requirements SET is_deleted=1 WHERE id=?", (precision_req_id,))

            try:
                db.record_budget_flow("BF-SELFTEST-INVALID", req["project_id"], req["annual_plan_id"], None, req["id"],
                                      "已分配预算", 1000, "invalid", "自测", app.now_text())
                raise AssertionError("版本不一致的资金流水未被拒绝")
            except ValueError:
                pass
            assert_true(db.one("SELECT id FROM budget_flows WHERE flow_code='BF-SELFTEST-INVALID'") is None, "非法资金流水未回滚")

            transitioned = db.transition_requirement_status(req["id"], req["status"], "已排期", "selftest transition", "自测", app.now_text())
            assert_true(transitioned, "合法状态流转未提交")
            state = db.one("SELECT status, remark FROM requirements WHERE id=?", (req["id"],))
            history = db.one("SELECT id FROM requirement_status_history WHERE requirement_id=? AND from_status=? AND to_status='已排期'",
                             (req["id"], req["status"]))
            assert_true(state["status"] == "已排期" and history is not None, "状态与历史未原子写入")
            assert_true(state["remark"] == "原始业务备注", "状态流转说明覆盖了需求业务备注")

            t = app.now_text()
            target_version_id = db.execute("""INSERT INTO implementation_versions(
                                                  project_id, annual_plan_id, version_code, version_name,
                                                  version_budget, created_at, updated_at)
                                                VALUES(?,?,?,?,?,?,?)""",
                                             (req["project_id"], req["annual_plan_id"], "V-SELFTEST-TARGET",
                                              "分配一致性目标版本", 100000, t, t)).lastrowid
            duplicate_id = db.execute("""INSERT INTO requirements(
                                             requirement_code, requirement_name, requirement_description, business_key,
                                             source_role, project_id, annual_plan_id, version_id, status,
                                             created_at, updated_at)
                                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                      ("REQ-SELFTEST-ASSIGN-DUP", "目标版本重复需求", "验证业务标识冲突",
                                       "assign duplicate", "咨询负责人", req["project_id"], req["annual_plan_id"],
                                       target_version_id, "草稿", t, t)).lastrowid
            movable_id = db.execute("""INSERT INTO requirements(
                                           requirement_code, requirement_name, requirement_description, business_key,
                                           source_role, project_id, status, created_at, updated_at)
                                         VALUES(?,?,?,?,?,?,?,?,?)""",
                                    ("REQ-SELFTEST-ASSIGN-MOVE", "待分配重复需求", "验证分配事务回滚",
                                     " Assign   Duplicate ", "咨询负责人", req["project_id"], "草稿", t, t)).lastrowid
            try:
                db.assign_requirement(movable_id, target_version_id, req["annual_plan_id"], "自测", app.now_text())
                raise AssertionError("需求可移动到存在相同业务标识的目标版本")
            except ValueError as exc:
                assert_true("业务需求标识" in str(exc), "需求分配冲突提示异常")
            assert_true(db.one("SELECT version_id FROM requirements WHERE id=?", (movable_id,))["version_id"] is None,
                        "业务标识冲突后需求分配未回滚")
            assert_true(db.one("""SELECT id FROM operation_logs WHERE object_type='requirement' AND object_id=?
                                  AND operation_type='assign_version'""", (movable_id,)) is None,
                        "业务标识冲突后仍写入分配审计")
            db.execute("UPDATE requirements SET is_deleted=1 WHERE id=?", (duplicate_id,))
            db.assign_requirement(movable_id, target_version_id, req["annual_plan_id"], "自测", app.now_text())
            assert_true(db.one("SELECT version_id FROM requirements WHERE id=?", (movable_id,))["version_id"] == target_version_id,
                        "软删除冲突需求后仍无法分配")

            child_id = db.execute("""INSERT INTO requirements(
                                       requirement_code, requirement_name, requirement_description, business_key,
                                       source_role, project_id, annual_plan_id, version_id, status,
                                       parent_requirement_id, created_at, updated_at)
                                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                  ("REQ-SELFTEST-CHILD", "关联原需求的子需求", "验证原需求关联", "selftest-child",
                                   "运营", req["project_id"], req["annual_plan_id"], req["version_id"], "草稿",
                                   req["id"], t, t)).lastrowid
            child = db.one("""SELECT r.parent_requirement_id, p.requirement_code parent_code
                              FROM requirements r JOIN requirements p ON p.id=r.parent_requirement_id WHERE r.id=?""", (child_id,))
            assert_true(child["parent_requirement_id"] == req["id"] and child["parent_code"] == req["requirement_code"],
                        "原需求关联未保存")
            invalid_artifact = {
                "artifact_code": "ART-INVALID-TARGET", "artifact_name": "错误层级.txt", "artifact_type": "可研报告",
                "file_path": "attachments/invalid.txt", "file_ext": ".txt", "file_size": 1,
                "related_object_type": "版本", "related_object_id": req["version_id"], "visibility": "内部",
                "project_id": req["project_id"],
            }
            try:
                db.create_artifact_record(invalid_artifact, "自测", t)
                raise AssertionError("成果物可绕过挂载层级校验")
            except ValueError:
                pass
            invalid_artifact.update({"artifact_code": "ART-INVALID-EXT", "artifact_type": "任务清单",
                                     "artifact_name": "危险脚本.exe", "file_ext": ".exe"})
            try:
                db.create_artifact_record(invalid_artifact, "自测", t)
                raise AssertionError("危险可执行成果物未被拒绝")
            except ValueError:
                pass
            approved_artifact = db.create_artifact_record({
                "artifact_code": "ART-BASELINE-APPROVED", "artifact_name": "基线任务清单.txt",
                "artifact_type": "任务清单", "file_path": "attachments/baseline.txt", "file_ext": ".txt",
                "file_size": 8, "related_object_type": "版本", "related_object_id": req["version_id"],
                "version_no": "v1", "description": "冻结前已批准成果物", "visibility": "客户可见",
                "project_id": req["project_id"],
            }, "自测", t)
            assert_true(approved_artifact["approval_status"] == "draft", "未冻结版本成果物未保存为草稿")
            try:
                db.submit_artifact_for_review(approved_artifact["artifact_id"], "其他上传人", t)
                raise AssertionError("非上传人可提交成果物审批")
            except ValueError:
                pass
            submitted = db.submit_artifact_for_review(approved_artifact["artifact_id"], "自测", t)
            assert_true(submitted["approval_status"] == "submitted" and submitted["change_id"] is None,
                        "未冻结成果物未进入普通审批")
            try:
                db.review_artifact(approved_artifact["artifact_id"], True, "越权审批", "自测", t)
                raise AssertionError("无审批权限可审批成果物")
            except ValueError:
                pass
            assert_true(db.review_artifact(approved_artifact["artifact_id"], True, "可纳入版本基线", "自测", t,
                                           can_review=True), "成果物审批未成功")
            reviewed_artifact = db.one(
                "SELECT approval_status, reviewed_by, reviewed_at, review_note FROM artifacts WHERE id=?",
                (approved_artifact["artifact_id"],),
            )
            assert_true(reviewed_artifact["approval_status"] == "approved" and
                        reviewed_artifact["reviewed_by"] == "自测" and reviewed_artifact["reviewed_at"] and
                        reviewed_artifact["review_note"] == "可纳入版本基线", "成果物审批结果字段不完整")

            managed_attachment = db.attachments_dir / "artifact-review-delete.txt"
            managed_attachment.write_text("artifact selftest", encoding="utf-8")
            rework_artifact = db.create_artifact_record({
                "artifact_code": "ART-REVIEW-REWORK", "artifact_name": "审批返工成果物.txt",
                "artifact_type": "任务清单", "file_path": str(managed_attachment.relative_to(db.data_dir)),
                "file_ext": ".txt", "file_size": managed_attachment.stat().st_size,
                "related_object_type": "版本", "related_object_id": req["version_id"],
                "version_no": "v1", "description": "验证驳回重提", "visibility": "内部",
                "project_id": req["project_id"],
            }, "成果上传人", t)
            db.submit_artifact_for_review(rework_artifact["artifact_id"], "成果上传人", t)
            db.review_artifact(rework_artifact["artifact_id"], False, "请补充验收依据", "成果审批人", t,
                               can_review=True)
            rejected_review = db.one(
                "SELECT approval_status, reviewed_by, reviewed_at, review_note FROM artifacts WHERE id=?",
                (rework_artifact["artifact_id"],),
            )
            assert_true(rejected_review["approval_status"] == "rejected" and
                        rejected_review["reviewed_by"] == "成果审批人" and
                        rejected_review["review_note"] == "请补充验收依据", "成果物驳回信息未保存")
            resubmitted = db.submit_artifact_for_review(rework_artifact["artifact_id"], "成果上传人", t)
            reset_review = db.one(
                "SELECT reviewed_by, reviewed_at, review_note FROM artifacts WHERE id=?",
                (rework_artifact["artifact_id"],),
            )
            assert_true(resubmitted["approval_status"] == "submitted" and
                        all(reset_review[key] is None for key in ("reviewed_by", "reviewed_at", "review_note")),
                        "成果物重提未清空旧审批结果")
            db.review_artifact(rework_artifact["artifact_id"], True, "通过", "成果审批人", t, can_review=True)
            try:
                db.delete_artifact_record(rework_artifact["artifact_id"], "普通用户", t)
                raise AssertionError("非管理角色可删除成果物")
            except ValueError:
                pass
            deleted = db.delete_artifact_record(rework_artifact["artifact_id"], "管理自测", t, can_manage=True)
            assert_true(not deleted["canceled"] and deleted["attachment_deleted"] and
                        not managed_attachment.exists() and
                        db.one("SELECT id FROM artifacts WHERE id=?", (rework_artifact["artifact_id"],)) is None,
                        "未冻结成果物或受管附件未安全删除")

            external_attachment = Path(temp_dir) / "outside-attachments.txt"
            external_attachment.write_text("must stay", encoding="utf-8")
            external_artifact = db.create_artifact_record({
                "artifact_code": "ART-EXTERNAL-PATH", "artifact_name": "外部路径成果物.txt",
                "artifact_type": "其他", "file_path": str(external_attachment), "file_ext": ".txt",
                "file_size": external_attachment.stat().st_size, "related_object_type": "项目",
                "related_object_id": req["project_id"], "visibility": "内部", "project_id": req["project_id"],
            }, "自测", t)
            external_deleted = db.delete_artifact_record(
                external_artifact["artifact_id"], "管理自测", t, can_manage=True,
            )
            assert_true(not external_deleted["attachment_deleted"] and external_attachment.exists(),
                        "成果物删除越界删除了受管目录外文件")

            other_project_id = db.execute("""INSERT INTO planning_projects(
                                                  project_code, project_name, total_budget, created_at, updated_at)
                                                VALUES(?,?,?,?,?)""",
                                             ("PRJ-SELFTEST-OTHER", "成果物归属校验项目", 1000, t, t)).lastrowid
            cross_project_artifact = {
                "artifact_code": "ART-CROSS-PROJECT", "artifact_name": "跨项目.txt", "artifact_type": "其他",
                "file_path": "attachments/cross-project.txt", "file_ext": ".txt", "file_size": 1,
                "related_object_type": "项目", "related_object_id": other_project_id,
                "visibility": "内部", "project_id": req["project_id"],
            }
            try:
                db.create_artifact_record(cross_project_artifact, "自测", t)
                raise AssertionError("成果物数据库写入允许挂载其他项目对象")
            except ValueError as exc:
                assert_true("不属于当前项目" in str(exc), "成果物项目归属错误提示异常")
            assert_true(db.one("SELECT id FROM artifacts WHERE artifact_code='ART-CROSS-PROJECT'") is None,
                        "跨项目成果物校验失败后未回滚")

            try:
                db.create_change_request_record(
                    req["version_id"], req["id"], "未冻结变更", "验证冻结边界", "需求名称",
                    "update", dict(req), "自测", t,
                )
                raise AssertionError("未冻结版本允许创建需求变更申请")
            except ValueError as exc:
                assert_true("尚未冻结" in str(exc), "未冻结版本变更错误提示异常")
            try:
                db.create_change_request_record(
                    req["version_id"], req["id"], "非法变更", "验证类型边界", "需求名称",
                    "artifact_add", {}, "自测", t,
                )
                raise AssertionError("需求变更入口允许非法变更类型")
            except ValueError as exc:
                assert_true("类型无效" in str(exc), "非法需求变更类型提示异常")

            freeze_blocker = db.create_artifact_record({
                "artifact_code": "ART-FREEZE-BLOCKER", "artifact_name": "发布前待审批成果物.txt",
                "artifact_type": "任务清单", "file_path": "attachments/freeze-blocker.txt", "file_ext": ".txt",
                "file_size": 8, "related_object_type": "版本", "related_object_id": req["version_id"],
                "version_no": "v1", "description": "验证发布前审批收口", "visibility": "内部",
                "project_id": req["project_id"],
            }, "自测", app.now_text())
            db.submit_artifact_for_review(freeze_blocker["artifact_id"], "自测", app.now_text())
            try:
                db.freeze_version_with_baseline(req["version_id"], "自测", app.now_text())
                raise AssertionError("版本存在待审批成果物时仍可发布")
            except ValueError as exc:
                assert_true("待审批成果物" in str(exc), "版本发布待审批拦截提示异常")
            assert_true(db.one("SELECT is_frozen FROM implementation_versions WHERE id=?",
                               (req["version_id"],))["is_frozen"] == 0 and
                        db.one("SELECT id FROM version_baselines WHERE version_id=?", (req["version_id"],)) is None,
                        "待审批成果物拦截发布后事务未回滚")
            db.review_artifact(freeze_blocker["artifact_id"], False, "发布前取消", "自测", app.now_text(),
                               can_review=True)
            db.delete_artifact_record(freeze_blocker["artifact_id"], "管理自测", app.now_text(), can_manage=True)

            baseline = db.freeze_version_with_baseline(req["version_id"], "自测", app.now_text())
            assert_true(baseline and baseline["snapshot_no"] == 1, "版本基线未生成")
            version = db.one("SELECT is_frozen FROM implementation_versions WHERE id=?", (req["version_id"],))
            assert_true(version["is_frozen"] == 1, "版本冻结状态未提交")
            snapshot = db.one("""SELECT requirement_name, requirement_description, business_key, source_role,
                                        estimated_budget, estimated_hours, remark, parent_requirement_id
                                 FROM version_baseline_requirements WHERE baseline_id=? AND requirement_id=?""",
                              (baseline["baseline_id"], req["id"]))
            assert_true(snapshot is not None and snapshot["requirement_description"] == req["requirement_description"] and
                        snapshot["business_key"] == req["business_key"] and snapshot["remark"] == "原始业务备注",
                        "版本基线缺少完整需求快照")
            baseline_hours = db.one("SELECT estimated_hours, actual_hours FROM version_baselines WHERE id=?",
                                    (baseline["baseline_id"],))
            snapshot_hours = db.one("""SELECT COALESCE(SUM(estimated_hours),0) estimated_hours,
                                              COALESCE(SUM(actual_hours),0) actual_hours
                                       FROM version_baseline_requirements WHERE baseline_id=?""",
                                    (baseline["baseline_id"],))
            assert_true(float(baseline_hours["estimated_hours"] or 0) == float(snapshot_hours["estimated_hours"] or 0) and
                        float(baseline_hours["actual_hours"] or 0) == float(snapshot_hours["actual_hours"] or 0),
                        "版本基线头表工时汇总与需求快照不一致")
            child_snapshot = db.one("SELECT parent_requirement_id, parent_requirement_code FROM version_baseline_requirements WHERE baseline_id=? AND requirement_id=?",
                                    (baseline["baseline_id"], child_id))
            assert_true(child_snapshot["parent_requirement_id"] == req["id"] and child_snapshot["parent_requirement_code"] == req["requirement_code"],
                        "版本基线未快照原需求关系")
            artifact_snapshot = db.one("SELECT artifact_code, visibility FROM version_baseline_artifacts WHERE baseline_id=?",
                                       (baseline["baseline_id"],))
            assert_true(artifact_snapshot and artifact_snapshot["artifact_code"] == "ART-BASELINE-APPROVED" and
                        artifact_snapshot["visibility"] == "客户可见", "版本基线未快照已批准成果物")
            try:
                db.record_budget_flow("BF-SELFTEST-FROZEN", req["project_id"], req["annual_plan_id"], req["version_id"], req["id"],
                                      "已分配预算", 100, "frozen", "自测", app.now_text())
                raise AssertionError("冻结版本仍允许调整分配预算")
            except ValueError:
                pass
            assert_true(db.one("SELECT id FROM budget_flows WHERE flow_code='BF-SELFTEST-FROZEN'") is None, "冻结版本非法流水未回滚")
            db.record_budget_flow("BF-SELFTEST-ACTUAL", req["project_id"], req["annual_plan_id"], req["version_id"], req["id"],
                                  "实际消耗", 10, "actual", "自测", app.now_text())

            t = app.now_text()
            before_change = db.one("SELECT * FROM requirements WHERE id=?", (req["id"],))
            proposed = dict(before_change)
            proposed["requirement_name"] = "统一需求池（已执行）"
            try:
                db.create_change_request_record(
                    req["version_id"], movable_id, "错配版本变更", "验证需求归属", "需求名称",
                    "update", dict(db.one("SELECT * FROM requirements WHERE id=?", (movable_id,))), "自测", t,
                )
                raise AssertionError("变更申请允许关联不属于目标版本的需求")
            except ValueError as exc:
                assert_true("不属于该版本" in str(exc), "需求与版本错配提示异常")
            change_id = db.create_change_request_record(
                req["version_id"], req["id"], "自测变更", "验证事务", "需求名称",
                "update", proposed, "自测", t,
            )
            payload = db.one("""SELECT p.change_type, c.expected_baseline_sequence
                                FROM change_request_payloads p JOIN change_requests c ON c.id=p.change_request_id
                                WHERE p.change_request_id=?""", (change_id,))
            assert_true(payload and payload["change_type"] == "update" and payload["expected_baseline_sequence"] == 1,
                        "变更申请未与载荷及当前基线在同一事务创建")
            try:
                db.create_change_request_record(
                    req["version_id"], req["id"], "重复变更", "验证单一待审批", "同一需求",
                    "update", proposed, "自测", t,
                )
                raise AssertionError("同一需求允许创建多条待审批变更")
            except ValueError:
                pass
            try:
                db.review_change_request(change_id, "approved", "自测", app.now_text(), "申请人自审")
                raise AssertionError("变更申请人可以审批自己的申请")
            except ValueError as exc:
                assert_true("不能审批自己的申请" in str(exc), "变更申请自审提示异常")
            unchanged = db.one("SELECT requirement_name, status FROM requirements WHERE id=?", (req["id"],))
            assert_true(unchanged["requirement_name"] == before_change["requirement_name"] and
                        unchanged["status"] == before_change["status"] and
                        db.one("SELECT approval_status FROM change_requests WHERE id=?", (change_id,))["approval_status"] == "pending" and
                        db.one("SELECT MAX(snapshot_no) snapshot_no FROM version_baselines WHERE version_id=?",
                               (req["version_id"],))["snapshot_no"] == 1,
                        "自审失败后业务、状态或基线未完整回滚")
            try:
                db.apply_change_request(change_id, "自测执行人", app.now_text())
                raise AssertionError("待审批变更可以直接执行")
            except ValueError:
                pass
            db.review_change_request(change_id, "approved", "自测审批人", app.now_text(), "同意调整需求名称")
            approved = db.one("SELECT * FROM change_requests WHERE id=?", (change_id,))
            unchanged = db.one("SELECT requirement_name, status FROM requirements WHERE id=?", (req["id"],))
            assert_true(approved["approval_status"] == "approved" and approved["approved_by"] == "自测审批人" and
                        approved["decision_note"] == "同意调整需求名称" and
                        unchanged["requirement_name"] == before_change["requirement_name"] and
                        unchanged["status"] == before_change["status"] and
                        db.one("SELECT MAX(snapshot_no) snapshot_no FROM version_baselines WHERE version_id=?",
                               (req["version_id"],))["snapshot_no"] == 1,
                        "批准动作修改了业务数据或提前生成基线")
            applied_baseline = db.apply_change_request(change_id, "自测执行人", app.now_text())
            changed = db.one("SELECT requirement_name, status FROM requirements WHERE id=?", (req["id"],))
            applied = db.one("SELECT * FROM change_requests WHERE id=?", (change_id,))
            applied_snapshot = db.one("""SELECT requirement_name FROM version_baseline_requirements
                                         WHERE baseline_id=? AND requirement_id=?""",
                                      (applied_baseline["baseline_id"], req["id"]))
            assert_true(changed["requirement_name"] == "统一需求池（已执行）" and changed["status"] == "变更中" and
                        applied["approval_status"] == "applied" and applied["applied_by"] == "自测执行人" and
                        applied["applied_baseline_id"] == applied_baseline["baseline_id"] and
                        applied_baseline["snapshot_no"] == 2 and applied_snapshot["requirement_name"] == "统一需求池（已执行）",
                        "已批准变更未在执行时生效并生成完整递增基线")
            try:
                db.apply_change_request(change_id, "重复执行人", app.now_text())
                raise AssertionError("同一变更申请可以重复执行")
            except ValueError:
                pass
            assert_true(db.one("SELECT MAX(snapshot_no) snapshot_no FROM version_baselines WHERE version_id=?",
                               (req["version_id"],))["snapshot_no"] == 2,
                        "重复执行失败后仍生成了额外基线")

            conflicting = dict(db.one("SELECT * FROM requirements WHERE id=?", (child_id,)))
            conflicting["business_key"] = req["business_key"]
            conflict_change = db.create_change_request_record(
                req["version_id"], child_id, "业务标识冲突", "验证审批复核", "业务标识",
                "update", conflicting, "自测", app.now_text(),
            )
            db.review_change_request(conflict_change, "approved", "自测审批人", app.now_text(), "批准后执行阶段复核业务约束")
            try:
                db.apply_change_request(conflict_change, "自测执行人", app.now_text())
                raise AssertionError("执行阶段未复核同版本业务标识唯一性")
            except ValueError as exc:
                assert_true("业务需求标识不能重复" in str(exc), "执行阶段业务标识冲突提示异常")
            assert_true(db.one("SELECT approval_status FROM change_requests WHERE id=?", (conflict_change,))["approval_status"] == "approved" and
                        db.one("SELECT MAX(snapshot_no) snapshot_no FROM version_baselines WHERE version_id=?",
                               (req["version_id"],))["snapshot_no"] == 2,
                        "执行校验失败后变更状态或基线未回滚")

            overlap_proposed = dict(db.one("SELECT * FROM requirements WHERE id=?", (child_id,)))
            overlap_proposed["requirement_name"] = "重叠变更不应获批"
            overlap_change = db.create_change_request_record(
                req["version_id"], child_id, "重叠变更", "验证目标重叠", "同一需求",
                "update", overlap_proposed, "自测", app.now_text(),
            )
            try:
                db.review_change_request(overlap_change, "approved", "另一审批人", app.now_text(), "尝试批准重叠变更")
                raise AssertionError("同基线的重叠变更可以同时批准")
            except ValueError as exc:
                assert_true("重叠" in str(exc), "重叠变更审批提示异常")
            assert_true(db.one("SELECT approval_status FROM change_requests WHERE id=?", (overlap_change,))["approval_status"] == "pending",
                        "重叠审批失败后申请状态未回滚")
            db.review_change_request(overlap_change, "rejected", "另一审批人", app.now_text(), "重叠申请驳回")

            stale_proposed = dict(db.one("SELECT * FROM requirements WHERE id=?", (req["id"],)))
            stale_proposed["requirement_name"] = "旧基线变更不应获批"
            stale_change = db.create_change_request_record(
                req["version_id"], req["id"], "旧基线变更", "验证乐观并发", "需求名称",
                "update", stale_proposed, "自测", app.now_text(),
            )

            missing_payload = db.execute("""INSERT INTO change_requests(
                                              version_id, change_title, change_reason, approval_status, requested_by, requested_at)
                                            VALUES(?,?,?,'pending',?,?)""",
                                         (req["version_id"], "缺少载荷", "验证载荷必填", "自测", app.now_text())).lastrowid
            try:
                db.review_change_request(missing_payload, "rejected", "自测审批人", app.now_text(), "缺少载荷应失败")
                raise AssertionError("缺少载荷的变更申请仍可审批")
            except ValueError:
                pass
            db.execute("DELETE FROM change_requests WHERE id=?", (missing_payload,))

            frozen_artifact = db.create_artifact_record({
                "artifact_code": "ART-FROZEN-PENDING", "artifact_name": "冻结版本验收报告.txt",
                "artifact_type": "验收报告", "file_path": "attachments/frozen.txt", "file_ext": ".txt",
                "file_size": 8, "related_object_type": "版本", "related_object_id": req["version_id"],
                "version_no": "v2", "description": "冻结后新增", "visibility": "内部",
                "project_id": req["project_id"],
            }, "自测", app.now_text())
            assert_true(frozen_artifact["approval_status"] == "draft" and not frozen_artifact["change_id"],
                        "冻结版本新增成果物未先保存为草稿")
            frozen_submission = db.submit_artifact_for_review(
                frozen_artifact["artifact_id"], "自测", app.now_text(),
            )
            assert_true(frozen_submission["approval_status"] == "pending" and frozen_submission["change_id"],
                        "冻结版本成果物提交时未进入变更审批")
            db.review_change_request(
                frozen_submission["change_id"], "approved", "自测审批人", app.now_text(), "同意新增冻结版本成果物"
            )
            frozen_approved_row = db.one(
                "SELECT approval_status, reviewed_by, reviewed_at, review_note FROM artifacts WHERE id=?",
                (frozen_artifact["artifact_id"],),
            )
            assert_true(frozen_approved_row["approval_status"] == "pending" and not frozen_approved_row["reviewed_by"] and
                        db.one("SELECT approval_status FROM change_requests WHERE id=?",
                               (frozen_submission["change_id"],))["approval_status"] == "approved" and
                        db.one("SELECT MAX(snapshot_no) snapshot_no FROM version_baselines WHERE version_id=?",
                               (req["version_id"],))["snapshot_no"] == 2,
                        "成果物变更在批准阶段提前生效或生成基线")
            try:
                db.delete_artifact_record(frozen_artifact["artifact_id"], "管理自测", app.now_text(), can_manage=True)
                raise AssertionError("已批准待执行成果物可直接取消")
            except ValueError as exc:
                assert_true("等待执行" in str(exc), "待执行成果物取消拒绝提示异常")
            artifact_baseline = db.apply_change_request(
                frozen_submission["change_id"], "自测执行人", app.now_text()
            )
            frozen_approved_row = db.one(
                "SELECT approval_status, reviewed_by, reviewed_at, review_note FROM artifacts WHERE id=?",
                (frozen_artifact["artifact_id"],),
            )
            artifact_snapshot = db.one("""SELECT artifact_code FROM version_baseline_artifacts
                                           WHERE baseline_id=? AND artifact_id=?""",
                                       (artifact_baseline["baseline_id"], frozen_artifact["artifact_id"]))
            assert_true(frozen_approved_row["approval_status"] == "approved" and
                        frozen_approved_row["reviewed_by"] == "自测审批人" and frozen_approved_row["reviewed_at"] and
                        frozen_approved_row["review_note"] == "同意新增冻结版本成果物" and
                        artifact_baseline["snapshot_no"] == 3 and artifact_snapshot and
                        artifact_snapshot["artifact_code"] == "ART-FROZEN-PENDING",
                        "成果物变更执行后未生效或未进入递增基线")
            try:
                db.apply_change_request(conflict_change, "自测执行人", app.now_text())
                raise AssertionError("旧基线上的已批准变更仍可执行")
            except ValueError as exc:
                assert_true("基线已过期" in str(exc), "旧基线执行拦截提示异常")
            try:
                db.review_change_request(stale_change, "approved", "另一审批人", app.now_text(), "旧基线审批")
                raise AssertionError("旧基线上的待审批变更仍可批准")
            except ValueError as exc:
                assert_true("基线已过期" in str(exc), "旧基线审批拦截提示异常")
            db.review_change_request(stale_change, "rejected", "另一审批人", app.now_text(), "基线过期后驳回重提")
            try:
                db.delete_artifact_record(frozen_artifact["artifact_id"], "管理自测", app.now_text(), can_manage=True)
                raise AssertionError("冻结版本已通过成果物可直接删除")
            except ValueError as exc:
                assert_true("不能直接删除" in str(exc), "冻结成果物删除拒绝提示异常")
            assert_true(db.one("SELECT id FROM artifacts WHERE id=?", (frozen_artifact["artifact_id"],)) is not None,
                        "冻结版本已通过成果物在拒绝删除后丢失")

            pending_attachment = db.attachments_dir / "frozen-pending-cancel.txt"
            pending_attachment.write_text("pending", encoding="utf-8")
            pending_cancel = db.create_artifact_record({
                "artifact_code": "ART-FROZEN-CANCEL", "artifact_name": "待审批取消.txt",
                "artifact_type": "任务书方案", "file_path": str(pending_attachment.relative_to(db.data_dir)),
                "file_ext": ".txt", "file_size": pending_attachment.stat().st_size,
                "related_object_type": "版本", "related_object_id": req["version_id"],
                "version_no": "v2", "description": "待审批取消", "visibility": "内部",
                "project_id": req["project_id"],
            }, "自测", app.now_text())
            pending_cancel_submission = db.submit_artifact_for_review(
                pending_cancel["artifact_id"], "自测", app.now_text(),
            )
            pending_cancel_result = db.delete_artifact_record(
                pending_cancel["artifact_id"], "管理自测", app.now_text(), can_manage=True,
            )
            assert_true(pending_cancel_result["canceled"] and pending_cancel_result["attachment_deleted"] and
                        not pending_attachment.exists() and
                        db.one("SELECT id FROM artifacts WHERE id=?", (pending_cancel["artifact_id"],)) is None and
                        db.one("SELECT approval_status FROM change_requests WHERE id=?",
                               (pending_cancel_submission["change_id"],))["approval_status"] == "rejected",
                        "冻结范围待审批成果物未安全取消")

            rejected_attachment = db.attachments_dir / "frozen-rejected-cancel.txt"
            rejected_attachment.write_text("rejected", encoding="utf-8")
            rejected_artifact = db.create_artifact_record({
                "artifact_code": "ART-FROZEN-REJECTED", "artifact_name": "驳回成果物.txt",
                "artifact_type": "任务书方案", "file_path": str(rejected_attachment.relative_to(db.data_dir)),
                "file_ext": ".txt", "file_size": rejected_attachment.stat().st_size,
                "related_object_type": "版本", "related_object_id": req["version_id"],
                "version_no": "v3", "description": "审批驳回", "visibility": "内部",
                "project_id": req["project_id"],
            }, "自测", app.now_text())
            rejected_submission = db.submit_artifact_for_review(
                rejected_artifact["artifact_id"], "自测", app.now_text(),
            )
            db.review_change_request(
                rejected_submission["change_id"], "rejected", "自测审批人", app.now_text(), "成果物不符合交付要求"
            )
            assert_true(db.one("SELECT approval_status, reviewed_by, review_note FROM artifacts WHERE id=?",
                               (rejected_artifact["artifact_id"],))["approval_status"] == "rejected",
                        "成果物变更驳回后状态错误")
            rejected_cancel = db.delete_artifact_record(
                rejected_artifact["artifact_id"], "管理自测", app.now_text(), can_manage=True,
            )
            assert_true(rejected_cancel["canceled"] and rejected_cancel["attachment_deleted"] and
                        not rejected_attachment.exists(), "冻结范围已驳回成果物未允许取消")

            funding_id = db.execute("""INSERT INTO funding_applications(
                                         application_code, project_id, annual_plan_id, amount, status,
                                         applicant_name, description, created_at, updated_at)
                                       VALUES(?,?,?,?,?,?,?,?,?)""",
                                    ("FUND-SELFTEST", req["project_id"], req["annual_plan_id"], 50000, "草稿",
                                     "销售自测", "年度资金申报", t, t)).lastrowid
            for invalid_amount in (0, -1, 0.004, float("nan"), float("inf")):
                try:
                    db.update_funding_application(funding_id, invalid_amount, "非法金额", "销售自测", app.now_text())
                    raise AssertionError(f"非法资金申报金额可保存：{invalid_amount}")
                except ValueError:
                    pass
            try:
                db.update_funding_application(funding_id, 51000, "越权编辑", "其他销售", app.now_text())
                raise AssertionError("非申请人可编辑资金申报")
            except ValueError:
                pass
            assert_true(db.update_funding_application(
                funding_id, 52000.126, "草稿修改", "销售自测", app.now_text(),
            ), "草稿资金申报未允许编辑")
            funding_update_log = db.one("""SELECT before_value, after_value FROM operation_logs
                                           WHERE object_type='funding_application' AND object_id=?
                                             AND operation_type='update' ORDER BY id DESC LIMIT 1""", (funding_id,))
            funding_before = app.json.loads(funding_update_log["before_value"])
            funding_after = app.json.loads(funding_update_log["after_value"])
            assert_true(funding_before["amount"] == 50000 and funding_after["amount"] == 52000.13 and
                        funding_after["description"] == "草稿修改", "资金申报编辑审计未记录金额/说明前后值")

            assert_true(db.transition_funding_application(
                funding_id, "草稿", "已提交", "销售自测", app.now_text(),
            ), "资金申报草稿未提交")
            try:
                db.update_funding_application(funding_id, 53000, "提交后修改", "销售自测", app.now_text())
                raise AssertionError("已提交资金申报仍可编辑")
            except ValueError as exc:
                assert_true("只能编辑" in str(exc), "资金申报锁定状态错误提示异常")
            assert_true(db.transition_funding_application(
                funding_id, "已提交", "审批中", "审批自测", app.now_text(),
            ), "资金申报未进入审批")
            assert_true(db.transition_funding_application(
                funding_id, "审批中", "已驳回", "审批自测", app.now_text(),
            ), "资金申报未能驳回")
            assert_true(db.update_funding_application(
                funding_id, 53000, "驳回后补充说明", "销售自测", app.now_text(),
            ), "已驳回资金申报未允许编辑")
            try:
                db.transition_funding_application(funding_id, "已驳回", "已提交", "其他销售", app.now_text())
                raise AssertionError("非申请人可重新提交资金申报")
            except ValueError:
                pass
            assert_true(db.transition_funding_application(
                funding_id, "已驳回", "已提交", "销售自测", app.now_text(),
            ), "已驳回资金申报未允许重新提交")
            resubmitted_funding = db.one(
                "SELECT amount, description, status, submitted_at, reviewed_by, reviewed_at FROM funding_applications WHERE id=?",
                (funding_id,),
            )
            assert_true(float(resubmitted_funding["amount"]) == 53000 and
                        resubmitted_funding["description"] == "驳回后补充说明" and
                        resubmitted_funding["status"] == "已提交" and resubmitted_funding["submitted_at"] and
                        resubmitted_funding["reviewed_by"] is None and resubmitted_funding["reviewed_at"] is None,
                        "资金申报重提未保留编辑值或清空旧审批信息")
            funding_status = "已提交"
            for next_status in ["审批中", "已批复", "已拨付"]:
                assert_true(db.transition_funding_application(
                    funding_id, funding_status, next_status, "审批自测", app.now_text(),
                ), f"资金申报状态未流转到 {next_status}")
                funding_status = next_status
            assert_true(db.one("SELECT status FROM funding_applications WHERE id=?", (funding_id,))["status"] == "已拨付",
                        "资金申报未完成重提后的全流程")

            invalid_operation = {
                "record_code": "OPS-SELFTEST-MISMATCH", "project_id": req["project_id"],
                "version_id": req["version_id"], "requirement_id": movable_id,
                "record_type": "线上问题", "status": "已完成", "record_date": "2026-07-15",
                "owner_name": "运营自测", "description": "跨版本问题", "result": "不应保存",
            }
            try:
                db.create_operation_record(invalid_operation, "运营自测", t)
                raise AssertionError("运营记录允许关联其他版本需求")
            except ValueError as exc:
                assert_true("所选版本" in str(exc), "运营记录版本错配提示异常")
            assert_true(db.one("SELECT id FROM operation_records WHERE record_code='OPS-SELFTEST-MISMATCH'") is None,
                        "跨版本运营记录校验失败后未回滚")

            operation_id = db.create_operation_record({
                "record_code": "OPS-SELFTEST", "project_id": req["project_id"], "version_id": req["version_id"],
                "requirement_id": req["id"], "record_type": "功能建议", "status": "已完成",
                "record_date": "2026-07-15", "owner_name": "运营自测", "description": "功能建议说明", "result": "已评估",
            }, "运营自测", t)
            assert_true(db.one("SELECT id FROM operation_records WHERE id=?", (operation_id,)) is not None,
                        "运营服务记录未保存")
            mismatch_id = db.execute("""INSERT INTO operation_records(
                                            record_code, project_id, version_id, requirement_id, record_type, status,
                                            record_date, owner_name, description, result, created_by, created_at, updated_at)
                                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                     ("OPS-HEALTHCHECK-MISMATCH", req["project_id"], req["version_id"], movable_id,
                                      "线上问题", "待处理", "2026-07-15", "运营自测", "错配巡检", "", "运营自测", t, t)).lastrowid
            try:
                db.healthcheck()
                raise AssertionError("健康检查未发现运营记录版本错配")
            except RuntimeError as exc:
                assert_true("运营需求" in str(exc), "运营记录版本错配健康检查提示异常")
            db.execute("DELETE FROM operation_records WHERE id=?", (mismatch_id,))
            attachment_path = db.attachments_dir / "selftest.txt"
            attachment_path.write_text("attachment", encoding="utf-8")
            stored_path = attachment_path.relative_to(db.data_dir)
            assert_true((db.data_dir / stored_path).read_text(encoding="utf-8") == "attachment", "外部数据目录附件相对路径失败")
            assert_true(db.healthcheck()["free_bytes"] > 0, "数据库层健康检查失败")
            assert_log_files(db.logs_dir)
            audit_ids = {
                app.json.loads(line)["event_id"]
                for line in (db.logs_dir / "audit.log").read_text(encoding="utf-8").splitlines() if line.strip()
            }
            logged = db.one("SELECT event_id, result FROM operation_logs WHERE event_id IS NOT NULL ORDER BY id DESC LIMIT 1")
            assert_true(logged and logged["event_id"] in audit_ids and logged["result"] == "success", "数据库与文件审计无法按事件 ID 对账")
        finally:
            if db is not None:
                db.close()
            app.close_logging()


def run_checks():
    assert_true(app.business_key_text(" Foo  Bar ") == "foo bar", "业务标识规范化规则错误")
    assert_true(app.requirement_business_key({"business_key": "  ", "requirement_name": " Foo  Bar "}) == "foo bar",
                "空业务标识未回退规范化需求名称")
    assert_true(app.requirement_business_key_conflicts([
        {"id": 1, "project_id": 1, "version_id": 2, "business_key": "foo  bar", "requirement_name": "A"},
        {"id": 2, "project_id": 1, "version_id": 2, "business_key": " Foo Bar ", "requirement_name": "B"},
    ]), "同版本规范化业务标识冲突未被识别")
    assert_true(tuple(app.THEME_PALETTES) == ("专业蓝", "清雅绿", "暖灰橙", "深色模式") and
                len({palette["primary"] for palette in app.THEME_PALETTES.values()}) == 4,
                "四套主题名称或主色配置不完整")
    assert_true("功能建议" in app.OPERATION_TYPES, "运营服务缺少功能建议收口类型")
    dark_palette = app.THEME_PALETTES["深色模式"]
    for foreground_key, background_key in (
        ("text", "surface"), ("muted", "surface"), ("metric_primary", "surface"),
        ("role_text", "role_bg"), ("success", "success_bg"),
        ("warning", "warning_bg"), ("danger", "danger_bg"),
    ):
        assert_true(
            contrast_ratio(dark_palette[foreground_key], dark_palette[background_key]) >= 4.5,
            f"深色主题对比度不足：{foreground_key}/{background_key}",
        )
    for status, background in app.DARK_STATUS_COLORS.items():
        assert_true(contrast_ratio(dark_palette["text"], background) >= 4.5,
                    f"深色主题状态行对比度不足：{status}")
    for diff_type, background in app.DARK_DIFF_COLORS.items():
        assert_true(contrast_ratio(dark_palette["text"], background) >= 4.5,
                    f"深色主题差异行对比度不足：{diff_type}")
    compare_left = {
        "requirement_code": "REQ-CMP", "requirement_name": "统一需求", "requirement_description": "旧描述",
        "source_role": "客户", "proposer_name": "甲", "owner_name": "乙", "requirement_type": "功能优化",
        "tags": "重点,客户", "priority": "P1", "parent_requirement_business_key": "parent-a",
        "planned_finish_date": "2026-08-01", "actual_finish_date": "", "status": "规划中", "remark": "旧备注",
        "estimated_budget": 10, "allocated_budget": 8, "actual_cost": 3,
        "estimated_hours": 12, "actual_hours": 4,
    }
    compare_right = {
        **compare_left, "requirement_description": "新描述", "proposer_name": "丙",
        "tags": "客户,紧急", "priority": "P0",
    }
    visible_changes = set(app.requirement_compare_changes(compare_left, compare_right))
    assert_true({"需求描述", "提出人", "标签", "优先级"}.issubset(visible_changes),
                "跨版本比对未识别通用业务字段变更")
    tag_reordered = {**compare_left, "tags": "客户,重点"}
    assert_true(app.requirement_compare_changes(compare_left, tag_reordered) == [],
                "跨版本比对将标签顺序变化误判为内容变化")
    restricted_right = {**compare_left, "allocated_budget": 9, "actual_cost": 5, "actual_hours": 7}
    assert_true(app.requirement_compare_changes(compare_left, restricted_right) == [],
                "客户视角跨版本比对泄露资金或工时差异")
    assert_true(set(app.requirement_compare_changes(compare_left, restricted_right, True, True)) >=
                {"分配预算", "实际成本", "实际工时"}, "有权限角色跨版本比对缺少资金或工时差异")
    assert_true(not app.budget_limit_exceeded(0.1 + 0.2, 0.3) and app.budget_limit_exceeded(0.3001, 0.3),
                "资金浮点容差未正确区分等额与真实超额")

    reload_calls = []
    reload_harness = type("ReloadHarness", (), {})()
    reload_harness.show_version_baseline = lambda: reload_calls.append("baseline")
    reload_harness.reload_version_comparison = lambda: reload_calls.append("compare")
    reload_harness.current_page = "版本基线"
    app.App.reload_page(reload_harness)
    reload_harness.current_page = "跨版本比对"
    app.App.reload_page(reload_harness)
    assert_true(reload_calls == ["baseline", "compare"], "特殊版本页面换肤后跳转错误")
    test_legacy_audit_migration()
    test_legacy_approved_change_migration()
    test_legacy_business_key_conflict_migration()
    test_database_transactions()
    root = None
    try:
        root = app.App()
        assert_true(root.db.db_path.exists(), "数据库文件未创建")
        assert_true(root.db.attachments_dir.exists(), "附件目录未创建")
        assert_true(root.db.exports_dir.exists(), "导出目录未创建")
        assert_true(root.db.backups_dir.exists(), "备份目录未创建")
        assert_true(root.db.logs_dir.exists(), "日志目录未创建")
        for name in ["runtime.log", "error.log", "audit.log"]:
            assert_true((root.db.logs_dir / name).exists(), f"日志文件未创建：{name}")
        assert_true(root.current_project_id() is not None, "未加载默认项目")

        root.db.execute(
            """INSERT INTO dashboard_preferences(subject_key, layout_json, updated_at) VALUES(?,?,?)
               ON CONFLICT(subject_key) DO UPDATE SET layout_json=excluded.layout_json,
                                                      updated_at=excluded.updated_at""",
            (root.theme_preference_key(), app.json.dumps({"theme": []}), app.now_text()),
        )
        assert_true(root.load_theme_preference() == app.DEFAULT_THEME, "损坏的主题偏好未回退默认主题")
        root.show_projects()
        for theme_name, palette in app.THEME_PALETTES.items():
            root.theme_name.set(theme_name)
            root.apply_theme()
            root.update_idletasks()
            root.update()
            assert_true(root.colors == palette and root.current_page == "项目管理",
                        f"主题 {theme_name} 未完整应用或切换后丢失当前页面")
        persisted_theme = next(reversed(app.THEME_PALETTES))
        assert_true(root.load_theme_preference() == persisted_theme, "主题选择未持久化")
        root.theme_name.set(app.DEFAULT_THEME)
        root.apply_theme(persist=False)
        assert_true(app.ttk.Style(root).lookup("TLabel", "background") == root.colors["bg"],
                    "普通标签未跟随当前主题背景")

        original_project_option = root.selected_project.get()
        t = app.now_text()
        single_project_id = root.db.execute(
            """INSERT INTO planning_projects(project_code, project_name, total_budget, created_at, updated_at)
               VALUES(?,?,?,?,?)""",
            ("PRJ-SINGLE-VERSION", "单版本引导项目", 100000, t, t),
        ).lastrowid
        single_plan_id = root.db.execute(
            """INSERT INTO annual_plans(project_id, plan_year, plan_name, annual_budget, created_at, updated_at)
               VALUES(?,?,?,?,?,?)""",
            (single_project_id, 2097, "单版本年度", 100000, t, t),
        ).lastrowid
        root.db.execute(
            """INSERT INTO implementation_versions(project_id, annual_plan_id, version_code, version_name,
                                                      version_budget, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (single_project_id, single_plan_id, "V1.0", "唯一版本", 50000, t, t),
        )
        root.refresh_contexts()
        root.selected_project.set(next(option for option, project_id in root.projects.items()
                                       if project_id == single_project_id))
        root.on_project_change(refresh_only=True)
        root.compare_versions()
        empty_state_buttons = {
            child.cget("text") for frame in root.content.winfo_children()
            for child in frame.winfo_children() if isinstance(child, app.ttk.Button)
        }
        assert_true(root.current_page == "跨版本比对" and root.version_compare_ids is None and
                    {"新建第二个版本", "返回版本管理"}.issubset(empty_state_buttons),
                    "单版本比对未展示页面内引导和操作入口")
        root.selected_project.set(original_project_option)
        root.on_project_change(refresh_only=True)

        callback_errors = []
        root.report_callback_exception = lambda *error: callback_errors.append(error)
        root.show_dashboard()
        root.show_projects()
        root.update_idletasks()
        root.update()
        root.geometry("1100x680")
        root.show_dashboard()
        root.update_idletasks()
        root.update()
        visible_nav = [button for button in root.nav_buttons.values() if button.winfo_manager()]
        window_bottom = root.winfo_rooty() + root.winfo_height()
        assert_true(visible_nav and max(button.winfo_rooty() + button.winfo_height() for button in visible_nav) <= window_bottom,
                    "最小窗口高度下侧栏导航被裁切")
        for frame in root.content.winfo_children():
            cards = [child for child in frame.winfo_children() if child.grid_info()]
            for card in cards:
                assert_true(card.winfo_x() + card.winfo_width() <= frame.winfo_width() + 1,
                            "最小窗口宽度下指标卡超出内容区")
        root.current_role.set("管理员")
        root.show_requirements()
        root.update_idletasks()
        root.update()
        action_rows = root.requirement_action_rows
        assert_true(len(action_rows) == 2 and action_rows[0].winfo_y() < action_rows[1].winfo_y(),
                    "需求操作按钮未拆分为两行")
        for action_row in action_rows:
            for button in action_row.winfo_children():
                assert_true(button.winfo_x() + button.winfo_width() <= action_row.winfo_width() + 1,
                            "最小窗口宽度下需求操作按钮被裁切")
        root.show_budget()
        root.update_idletasks()
        root.update()
        content_right = root.content_canvas.winfo_rootx() + root.content_canvas.winfo_width()
        for frame in root.content.winfo_children():
            for card in [child for child in frame.winfo_children() if child.grid_info()]:
                assert_true(card.winfo_rootx() + card.winfo_width() <= content_right + 1,
                            "最小窗口宽度下资金指标卡超出内容区")
        canvas = root.budget_flow_canvas
        canvas_bounds = canvas.bbox("all")
        assert_true(canvas_bounds is not None and canvas_bounds[0] >= 0 and canvas_bounds[1] >= 0 and
                    canvas_bounds[2] <= canvas.winfo_width() and canvas_bounds[3] <= canvas.winfo_height(),
                    f"最小窗口宽度下资金图越界：{canvas_bounds} / {canvas.winfo_width()}x{canvas.winfo_height()}")
        for row in root.db.query("SELECT id FROM requirements WHERE version_id=? AND is_deleted=0", (root.current_version_id(),)):
            title_bounds = canvas.bbox(f"req_node_{row['id']}_title")
            detail_bounds = canvas.bbox(f"req_node_{row['id']}_details")
            assert_true(title_bounds and detail_bounds and title_bounds[3] < detail_bounds[1],
                        "资金图需求节点标题与金额明细发生重叠")
            rectangle = canvas.find_withtag(f"req_node_{row['id']}")[0]
            assert_true(canvas.itemcget(rectangle, "fill").upper() == "#E5F3EA", "正常预算资金节点未显示绿色")
        project_id = root.current_project_id()
        t = app.now_text()
        panorama_plan_id = root.db.execute("""INSERT INTO annual_plans(
                                                project_id, plan_year, plan_name, annual_budget, created_at, updated_at)
                                              VALUES(?,?,?,?,?,?)""",
                                           (project_id, 2098, "全景树年度", 100000, t, t)).lastrowid
        panorama_version_id = root.db.execute("""INSERT INTO implementation_versions(
                                                   project_id, annual_plan_id, version_code, version_name,
                                                   version_budget, created_at, updated_at)
                                                 VALUES(?,?,?,?,?,?,?)""",
                                              (project_id, panorama_plan_id, "V-PANO", "全景树版本", 100, t, t)).lastrowid
        zero_req_id = root.db.execute("""INSERT INTO requirements(
                                           requirement_code, requirement_name, requirement_description, business_key,
                                           source_role, project_id, annual_plan_id, version_id, allocated_budget,
                                           actual_cost, created_at, updated_at)
                                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                      ("REQ-PANO-ZERO", "零预算节点", "全景树灰色节点", "pano-zero", "咨询负责人",
                                       project_id, panorama_plan_id, panorama_version_id, 0, 0, t, t)).lastrowid
        over_req_id = root.db.execute("""INSERT INTO requirements(
                                           requirement_code, requirement_name, requirement_description, business_key,
                                           source_role, project_id, annual_plan_id, version_id, allocated_budget,
                                           actual_cost, created_at, updated_at)
                                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                      ("REQ-PANO-OVER", "超支节点", "全景树红色节点", "pano-over", "咨询负责人",
                                       project_id, panorama_plan_id, panorama_version_id, 10, 20, t, t)).lastrowid
        annual_unplanned_id = root.db.execute("""INSERT INTO requirements(
                                                   requirement_code, requirement_name, requirement_description, business_key,
                                                   source_role, project_id, annual_plan_id, version_id, allocated_budget,
                                                   actual_cost, created_at, updated_at)
                                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                              ("REQ-PANO-UNPLANNED-YEAR", "年度内待规划节点", "验证年度待规划分组",
                                               "pano-unplanned-year", "咨询负责人", project_id, panorama_plan_id,
                                               None, 7, 3, t, t)).lastrowid
        no_year_unplanned_id = root.db.execute("""INSERT INTO requirements(
                                                    requirement_code, requirement_name, requirement_description, business_key,
                                                    source_role, project_id, annual_plan_id, version_id, allocated_budget,
                                                    actual_cost, created_at, updated_at)
                                                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                               ("REQ-PANO-UNPLANNED-NONE", "无年度待规划节点", "验证无年度待规划池",
                                                "pano-unplanned-none", "咨询负责人", project_id, None, None,
                                                13, 5, t, t)).lastrowid
        root.show_budget()
        root.update_idletasks()
        root.update()
        canvas = root.budget_flow_canvas
        assert_true(canvas.find_withtag(f"plan_node_{panorama_plan_id}") and
                    canvas.find_withtag(f"version_node_{panorama_version_id}") and
                    canvas.find_withtag(f"req_node_{zero_req_id}"), "资金图未展示项目全部年度/版本/需求")
        assert_true(canvas.find_withtag(f"req_node_{annual_unplanned_id}") and
                    canvas.find_withtag(f"req_node_{no_year_unplanned_id}") and
                    canvas.find_withtag(f"unplanned_node_{panorama_plan_id}") and
                    canvas.find_withtag("unplanned_node_none") and canvas.find_withtag("unplanned_plan_node"),
                    "资金图未按年度和无年度池展示待规划需求")
        annual_pending_details = {
            canvas.itemcget(item, "text") for item in canvas.find_withtag(f"unplanned_node_{panorama_plan_id}_details")
        }
        no_year_pending_details = {
            canvas.itemcget(item, "text") for item in canvas.find_withtag("unplanned_node_none_details")
        }
        no_year_plan_details = {
            canvas.itemcget(item, "text") for item in canvas.find_withtag("unplanned_plan_node_details")
        }
        assert_true(f"分配 {app.money_text(7)}" in annual_pending_details and f"实际 {app.money_text(3)}" in annual_pending_details,
                    "年度待规划分组汇总金额与需求叶子不一致")
        assert_true(f"分配 {app.money_text(13)}" in no_year_pending_details and f"实际 {app.money_text(5)}" in no_year_pending_details and
                    f"分配 {app.money_text(13)}" in no_year_plan_details and f"实际 {app.money_text(5)}" in no_year_plan_details,
                    "无年度待规划池汇总金额与需求叶子不一致")
        assert_true(canvas.tag_bind(f"req_node_{annual_unplanned_id}", "<Double-Button-1>") and
                    canvas.tag_bind(f"req_node_{no_year_unplanned_id}", "<Double-Button-1>"),
                    "待规划需求节点缺少双击钻取绑定")
        canvas_bounds = canvas.bbox("all")
        assert_true(canvas_bounds is not None and canvas_bounds[0] >= 0 and canvas_bounds[1] >= 0 and
                    canvas_bounds[2] <= canvas.winfo_width() and canvas_bounds[3] <= canvas.winfo_height(),
                    f"待规划需求加入后资金图越界：{canvas_bounds} / {canvas.winfo_width()}x{canvas.winfo_height()}")
        zero_rectangle = canvas.find_withtag(f"req_node_{zero_req_id}")[0]
        over_rectangle = canvas.find_withtag(f"req_node_{over_req_id}")[0]
        assert_true(canvas.itemcget(zero_rectangle, "fill").upper() == root.colors["surface_alt"].upper(),
                    "零预算资金节点未使用当前主题的中性背景色")
        assert_true(canvas.itemcget(over_rectangle, "fill").upper() == "#FDE9E7", "超支资金节点未显示红色")
        root.geometry("1280x760")

        pages = [
            root.show_dashboard,
            root.show_projects,
            root.show_plans,
            root.show_versions,
            root.show_requirements,
            root.show_budget,
            root.show_funding_applications,
            root.show_artifacts,
            root.show_operation_records,
            root.show_search,
            root.show_milestones,
            root.show_exports,
            root.show_settings,
        ]
        for page in pages:
            page()
            root.update_idletasks()
            root.update()
            print(f"{page.__name__}: ok")

        req_count = root.db.one("SELECT COUNT(*) c FROM requirements WHERE is_deleted=0")["c"]
        assert_true(req_count >= 1, "默认需求数据缺失")

        log_count = root.db.one("SELECT COUNT(*) c FROM operation_logs")["c"]
        assert_true(log_count >= 1, "操作日志未初始化")
        root.operation_log_type_filter.set("system")
        assert_true(all(row["object_type"] == "system" for row in root.filtered_operation_logs(limit=20)), "操作日志类型过滤失败")
        root.operation_log_type_filter.set("全部")

        for table in ["requirement_status_history", "version_baselines", "version_baseline_requirements",
                      "version_baseline_artifacts", "task_effort_entries", "tag_definitions", "dashboard_preferences",
                      "funding_applications", "operation_records"]:
            exists = root.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            assert_true(exists is not None, f"升级表未创建：{table}")
        req_columns = {row[1] for row in root.db.query("PRAGMA table_info(requirements)")}
        assert_true({"business_key", "estimated_hours", "actual_hours", "parent_requirement_id"}.issubset(req_columns),
                    "需求增强字段未迁移")
        project_columns = {row[1] for row in root.db.query("PRAGMA table_info(planning_projects)")}
        assert_true("current_stage" in project_columns, "项目阶段字段未迁移")
        requirement_id = root.db.one("SELECT id FROM requirements WHERE is_deleted=0 ORDER BY id LIMIT 1")["id"]
        root.db.claim_requirement(requirement_id, root.current_user, app.now_text())
        root.db.record_effort(requirement_id, root.current_user, 1.5, "2026-07-10", "自测工时", app.now_text())
        assert_true(float(root.db.one("SELECT actual_hours FROM requirements WHERE id=?", (requirement_id,))["actual_hours"]) >= 1.5,
                    "任务工时未累计")
        layout = [{"key": "recent", "visible": True}, {"key": "status", "visible": False}]
        root.save_dashboard_layout("客户", layout)
        loaded_layout = root.load_dashboard_layout("客户")
        assert_true(loaded_layout[0]["key"] == "recent" and loaded_layout[1]["visible"] is False, "角色看板配置未持久化")

        assert_true("规划中" in app.STATUS_TRANSITIONS["草稿"], "需求状态机规则缺失")
        assert_true("requirement_create" in app.ROLE_ACTIONS["研发人员"], "研发人员缺少新建需求权限")
        assert_true("artifact" in app.ROLE_ACTIONS["销售"], "销售缺少成果物维护权限")
        assert_true({"funding_create", "funding_submit"}.issubset(app.ROLE_ACTIONS["销售"]),
                    "销售缺少资金申报创建或提交权限")
        assert_true("funding_review" in app.ROLE_ACTIONS["咨询负责人"], "咨询负责人缺少资金审批权限")
        assert_true({"artifact_review", "artifact_delete"}.issubset(app.ROLE_ACTIONS["项目经理"]) and
                    "artifact_review" not in app.ROLE_ACTIONS["销售"] and
                    "artifact_delete" not in app.ROLE_ACTIONS["运营人员"], "成果物审批或删除权限边界错误")
        assert_true("operation_record" in app.ROLE_ACTIONS["运营人员"], "运营人员缺少运营服务维护权限")
        assert_true(app.ARTIFACT_TARGET_TYPES["可研报告"] == {"项目"} and
                    app.ARTIFACT_TARGET_TYPES["分年任务申报书"] == {"年度"} and
                    app.ARTIFACT_TARGET_TYPES["任务清单"] == {"版本"}, "成果物挂载层级规则错误")
        assert_true({".exe", ".bat", ".ps1"}.issubset(app.DANGEROUS_ARTIFACT_EXTENSIONS),
                    "危险成果物扩展名拦截规则缺失")

        original_field_dialog = app.FieldDialog
        try:
            app.FieldDialog = lambda *_args, **_kwargs: type("StageDialog", (), {"result": {"current_stage": "建设落地"}})()
            root.current_role.set("管理员")
            root.update_project_stage()
        finally:
            app.FieldDialog = original_field_dialog
        assert_true(root.db.one("SELECT current_stage FROM planning_projects WHERE id=?", (root.current_project_id(),))["current_stage"] == "建设落地",
                    "项目当前阶段未更新")
        root.show_milestones()
        milestone_statuses = [root.milestone_tree.item(item)["values"][4] for item in root.milestone_tree.get_children("")]
        assert_true(milestone_statuses == ["已完成", "已完成", "当前阶段", "未开始", "未开始", "未开始"],
                    f"里程碑阶段状态错误：{milestone_statuses}")

        version_rows = root.db.query("""SELECT v.id, a.plan_year, v.version_code, v.version_name
                                        FROM implementation_versions v JOIN annual_plans a ON a.id=v.annual_plan_id
                                        WHERE v.project_id=? ORDER BY a.plan_year DESC, v.id DESC""", (root.current_project_id(),))
        compare_labels = [f"{row['id']} - {row['plan_year']} | {row['version_code']} {row['version_name']}" for row in version_rows]
        try:
            app.FieldDialog = lambda *_args, **_kwargs: type("CompareDialog", (), {"result": {"left": compare_labels[0], "right": compare_labels[-1]}})()
            root.compare_versions()
        finally:
            app.FieldDialog = original_field_dialog
        diff_tags = {tag for item in root.version_diff_tree.get_children("") for tag in root.version_diff_tree.item(item)["tags"]}
        assert_true("diff_新增" in diff_tags or "diff_移除" in diff_tags, "跨年度版本比对未标记新增/移除行")
        highlighted_rows = [root.version_diff_tree.item(item)["tags"]
                            for item in root.version_diff_tree.get_children("")
                            if any(tag.startswith("diff_") for tag in root.version_diff_tree.item(item)["tags"])]
        assert_true(highlighted_rows and all(len(row_tags) == 1 and row_tags[0].startswith("diff_")
                                             for row_tags in highlighted_rows),
                    "跨版本差异高亮被斑马纹或状态背景覆盖")
        selected_compare_ids = root.version_compare_ids
        root.theme_name.set("清雅绿")
        root.apply_theme(persist=False)
        assert_true(root.current_page == "跨版本比对" and root.version_compare_ids == selected_compare_ids and
                    root.version_diff_tree.get_children(""), "跨版本结果在换肤后未保留")
        assert_true(root.nav_buttons["版本管理"].cget("style") == "SideActive.TButton",
                    "跨版本比对页未保持版本管理导航高亮")
        root.theme_name.set(app.DEFAULT_THEME)
        root.apply_theme(persist=False)
        captured_details = {}
        original_detail_dialog = app.DetailDialog
        original_export_csv = root.export_csv
        try:
            app.DetailDialog = lambda _parent, _title, sections: captured_details.update({"sections": sections})
            for role in ("客户", "销售"):
                root.current_role.set(role)
                assert_true(not root.can_view_effort(), f"{role}不应查看工时投入")
                root.show_requirements()
                root.update_idletasks()
                root.update()
                assert_true("estimated_hours" not in root.req_tree["columns"] and "actual_hours" not in root.req_tree["columns"],
                            f"{role}需求列表泄露工时字段")
                assert_true("parent_requirement_code" in root.req_tree["columns"], "需求列表未展示原需求编号")
                captured_details.clear()
                root.open_requirement_detail(requirement_id)
                section_titles = [section[0] for section in captured_details["sections"]]
                assert_true("工时投入" not in section_titles and "工时明细" not in section_titles,
                            f"{role}需求详情泄露工时信息")

            root.db.execute("UPDATE requirements SET parent_requirement_id=? WHERE id=?", (requirement_id, zero_req_id))
            captured_details.clear()
            root.open_requirement_detail(zero_req_id)
            base_values = dict(captured_details["sections"][0][1])
            parent_code = root.db.one("SELECT requirement_code FROM requirements WHERE id=?", (requirement_id,))["requirement_code"]
            assert_true(base_values["原需求编号"] == parent_code, "需求详情未展示原需求编号")
            try:
                root.validate_parent_requirement(requirement_id, root.current_project_id(), requirement_id)
                raise AssertionError("需求允许关联自身作为原需求")
            except ValueError:
                pass

            exported = {}
            root.current_role.set("销售")
            root.export_csv = lambda name, rows: exported.update({"name": name, "rows": [dict(row) for row in rows]})
            root.export_requirements()
            assert_true(exported["rows"] and "estimated_hours" not in exported["rows"][0] and "actual_hours" not in exported["rows"][0],
                        "销售通用需求 CSV 泄露工时字段")
        finally:
            app.DetailDialog = original_detail_dialog
            root.export_csv = original_export_csv

        root.current_role.set("项目经理")
        assert_true(root.can_view_effort(), "项目经理应能查看工时投入")
        root.current_role.set("管理员")
        root.current_role.set("客户")
        assert_true(not root.can_action("budget"), "客户不应拥有预算写权限")
        root.current_role.set("管理员")
        assert_true(root.can_action("budget"), "管理员应拥有预算写权限")
        assert_true(root.can_view_money(), "管理员应能查看资金信息")

        project_id = root.current_project_id()
        plan_id = root.current_plan_id()
        version_id = root.current_version_id()
        t = app.now_text()
        operation_requirement_ids = {
            root.id_from_option(option) for option in root.requirement_options(include_unplanned=False)
            if root.id_from_option(option) is not None
        }
        assert_true(annual_unplanned_id not in operation_requirement_ids and no_year_unplanned_id not in operation_requirement_ids and
                    all(root.db.one("SELECT version_id FROM requirements WHERE id=?", (requirement_id,))["version_id"] == version_id
                        for requirement_id in operation_requirement_ids),
                    "新增运营记录的需求下拉未限定当前版本")
        root.db.execute("""INSERT INTO funding_applications(
                             application_code, project_id, annual_plan_id, amount, status,
                             applicant_name, description, created_at, updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        ("FUND-UI-SELFTEST", project_id, plan_id, 88000, "已提交", "销售自测", "界面申报", t, t))
        root.db.execute("""INSERT INTO funding_applications(
                             application_code, project_id, annual_plan_id, amount, status,
                             applicant_name, description, created_at, updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        ("FUND-UI-EDIT", project_id, plan_id, 66000, "草稿", root.current_user, "编辑前", t, t))
        root.current_role.set("销售")
        root.show_funding_applications()
        assert_true("编辑申报" in button_texts(root.content), "资金申报页面缺少编辑入口")
        assert_true(any("FUND-UI-SELFTEST" in root.funding_tree.item(item)["values"] for item in root.funding_tree.get_children("")),
                    "资金申报页面未展示当前项目申报")
        edit_item = next(item for item in root.funding_tree.get_children("")
                         if "FUND-UI-EDIT" in root.funding_tree.item(item)["values"])
        root.funding_tree.selection_set(edit_item)
        original_funding_dialog = app.FieldDialog
        try:
            app.FieldDialog = lambda *_args, **_kwargs: type(
                "FundingEditDialog", (), {"result": {"amount": "67000.55", "description": "界面编辑后"}}
            )()
            root.edit_funding_application()
        finally:
            app.FieldDialog = original_funding_dialog
        ui_funding = root.db.one("SELECT amount, description FROM funding_applications WHERE application_code='FUND-UI-EDIT'")
        assert_true(float(ui_funding["amount"]) == 67000.55 and ui_funding["description"] == "界面编辑后",
                    "资金申报编辑界面未保存金额或说明")
        root.db.execute("""INSERT INTO operation_records(
                             record_code, project_id, version_id, requirement_id, record_type, status,
                             record_date, owner_name, description, result, created_by, created_at, updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("OPS-UI-SELFTEST", project_id, version_id, requirement_id, "推广活动", "处理中",
                         "2026-07-15", "运营自测", "推广说明", "持续跟进", "运营自测", t, t))
        root.current_role.set("运营人员")
        root.show_operation_records()
        assert_true(any("OPS-UI-SELFTEST" in root.operation_tree.item(item)["values"] for item in root.operation_tree.get_children("")),
                    "运营服务页面未展示当前项目记录")
        root.current_role.set("管理员")
        root.db.execute("""INSERT INTO artifacts(artifact_code, artifact_name, artifact_type, file_path,
                                                  related_object_type, related_object_id, approval_status,
                                                  uploaded_by, uploaded_at, created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        ("ART-SELFTEST-YEAR", "年度成果物", "分年任务申报书", "attachments/year.txt",
                         "年度", plan_id, "approved", "自测", t, t))
        root.db.execute("""INSERT INTO artifacts(artifact_code, artifact_name, artifact_type, file_path,
                                                  related_object_type, related_object_id, approval_status,
                                                  uploaded_by, uploaded_at, created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        ("ART-SELFTEST-LEGACY-YEAR", "旧年度成果物", "分年任务申报书", "attachments/legacy-year.txt",
                         "年度计划", plan_id, "approved", "自测", t, t))
        assert_true(root.artifact_rows_for_context(None, plan_id, version_id) == [], "未选择项目时成果物列表泄露全局数据")
        context_artifact_codes = {row["artifact_code"] for row in root.artifact_rows_for_context(project_id, plan_id, version_id)}
        assert_true({"ART-SELFTEST-YEAR", "ART-SELFTEST-LEGACY-YEAR"}.issubset(context_artifact_codes),
                    "年度成果物列表未兼容新旧挂载类型")
        exported_artifacts = {}
        original_export_csv = root.export_csv
        try:
            root.export_csv = lambda name, rows: exported_artifacts.update({"name": name, "rows": [dict(row) for row in rows]})
            root.export_artifacts()
        finally:
            root.export_csv = original_export_csv
        exported_artifact_codes = {row["artifact_code"] for row in exported_artifacts["rows"]}
        assert_true({"ART-SELFTEST-YEAR", "ART-SELFTEST-LEGACY-YEAR"}.issubset(exported_artifact_codes),
                    "年度成果物导出未兼容“年度”和旧“年度计划”")

        baseline = root.db.freeze_version_with_baseline(version_id, "自测", app.now_text())
        assert_true(baseline is not None, "界面测试版本基线未生成")
        root.current_role.set("客户")
        root.show_version_baseline()
        customer_baseline_columns = set(root.baseline_requirement_tree["columns"])
        assert_true("estimated_budget" not in customer_baseline_columns and "allocated_budget" not in customer_baseline_columns and
                    "estimated_hours" not in customer_baseline_columns and "actual_hours" not in customer_baseline_columns,
                    "客户查看版本基线时泄露资金或工时")
        root.current_role.set("管理员")
        baseline_metrics = []
        original_metric_grid = root.metric_grid
        try:
            def capture_baseline_metrics(parent, cards, columns=4):
                baseline_metrics.extend(cards)
                return original_metric_grid(parent, cards, columns)

            root.metric_grid = capture_baseline_metrics
            root.show_version_baseline()
        finally:
            root.metric_grid = original_metric_grid
        admin_baseline_columns = set(root.baseline_requirement_tree["columns"])
        assert_true({"estimated_budget", "allocated_budget", "estimated_hours", "actual_hours"}.issubset(admin_baseline_columns),
                    "管理员版本基线缺少资金或工时快照")
        baseline_header = root.db.one("SELECT estimated_hours, actual_hours FROM version_baselines WHERE id=?",
                                      (baseline["baseline_id"],))
        effort_metric = next((card for card in baseline_metrics if card[0] == "工时投入"), None)
        assert_true(effort_metric is not None and effort_metric[1] == f"{baseline_header['actual_hours'] or 0} 小时" and
                    effort_metric[2] == f"预估 {baseline_header['estimated_hours'] or 0} 小时",
                    "管理员版本基线未正确展示工时汇总")
        pending_artifact = root.db.create_artifact_record({
            "artifact_code": "ART-UI-PENDING", "artifact_name": "冻结后任务清单.txt", "artifact_type": "任务清单",
            "file_path": "attachments/ui-pending.txt", "file_ext": ".txt", "file_size": 8,
            "related_object_type": "版本", "related_object_id": version_id, "version_no": "v2",
            "description": "验证普通列表只显示已批准成果物", "visibility": "客户可见",
            "project_id": project_id,
        }, "自测", app.now_text())
        assert_true(pending_artifact["approval_status"] == "draft", "冻结版本成果物未先保存为草稿")
        pending_submission = root.db.submit_artifact_for_review(
            pending_artifact["artifact_id"], "自测", app.now_text(),
        )
        assert_true(pending_submission["approval_status"] == "pending", "冻结版本成果物提交后未进入 pending")
        pending_codes = {row["artifact_code"] for row in root.artifact_rows_for_context(project_id, plan_id, version_id)}
        assert_true("ART-UI-PENDING" in pending_codes, "内部成果物列表未展示待审批状态")
        root.show_artifacts()
        assert_true("approval_status_label" in root.artifact_tree["columns"] and
                    any("变更待审批" in root.artifact_tree.item(item)["values"]
                        for item in root.artifact_tree.get_children("")), "成果物界面未展示审批状态")
        admin_artifact_actions = set(button_texts(root.content))
        assert_true({"提交审批", "审批通过", "审批驳回", "删除/取消"}.issubset(admin_artifact_actions),
                    "管理角色成果物操作入口不完整")
        assert_true("新增成果物" in root.change_payload_summary(pending_submission["change_id"]), "变更申请摘要未展示成果物")
        root.current_role.set("客户")
        root.show_artifacts()
        customer_artifact_actions = set(button_texts(root.content))
        assert_true(not ({"提交审批", "审批通过", "审批驳回", "删除/取消"} & customer_artifact_actions),
                    "客户成果物页面暴露了维护或审批入口")
        customer_pending_codes = {row["artifact_code"] for row in root.artifact_rows_for_context(project_id, plan_id, version_id)}
        assert_true("ART-UI-PENDING" not in customer_pending_codes, "客户看到了未审批成果物")
        root.current_role.set("管理员")
        root.db.review_change_request(
            pending_submission["change_id"], "approved", "审批自测", app.now_text(), "同意新增任务清单"
        )
        root.show_artifacts()
        assert_true(any("变更已批准，待执行" in root.artifact_tree.item(item)["values"]
                        for item in root.artifact_tree.get_children("")),
                    "成果物页面未明确显示变更已批准待执行状态")
        root.current_role.set("客户")
        customer_approved_not_applied = {
            row["artifact_code"] for row in root.artifact_rows_for_context(project_id, plan_id, version_id)
        }
        assert_true("ART-UI-PENDING" not in customer_approved_not_applied,
                    "成果物变更仅批准尚未执行时已对客户可见")
        root.current_role.set("管理员")
        root.show_settings()
        change_actions = set(button_texts(root.content))
        assert_true({"批准", "驳回", "执行变更"}.issubset(change_actions), "变更申请三段式操作入口不完整")
        assert_true({"approval_status_label", "baseline_context", "applied_by", "applied_at",
                     "applied_baseline_sequence"}.issubset(set(root.change_tree["columns"])),
                    "变更申请列表缺少状态、基线或执行信息")
        assert_true(any("已批准，待执行" in root.change_tree.item(item)["values"]
                        for item in root.change_tree.get_children("")),
                    "变更申请列表未明确显示已批准待执行状态")
        applied_ui_baseline = root.db.apply_change_request(
            pending_submission["change_id"], "执行自测", app.now_text()
        )
        assert_true(applied_ui_baseline["snapshot_no"] == 2, "界面场景成果物执行未生成递增基线")
        root.current_role.set("客户")
        customer_codes = {row["artifact_code"] for row in root.artifact_rows_for_context(project_id, plan_id, version_id)}
        assert_true("ART-UI-PENDING" in customer_codes and "ART-SELFTEST-YEAR" not in customer_codes,
                    "客户成果物可见范围过滤错误")
        root.current_role.set("管理员")

        other_project = root.db.execute(
            "INSERT INTO planning_projects(project_code, project_name, customer_name, total_budget, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            ("PRJ-OTHER", "其他项目", "其他客户", 1000, t, t),
        ).lastrowid
        assert_true(root.validate_artifact_target("项目", project_id), "当前项目不能作为成果物挂载对象")
        assert_true(not root.validate_artifact_target("项目", other_project), "项目成果物目标校验未限定当前项目")
        other_requirement = root.db.execute("""INSERT INTO requirements(requirement_code, requirement_name, requirement_description,
                                                      source_role, project_id, status, created_at, updated_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        ("REQ-OTHER-RECENT", "其他项目最新需求", "不应出现在当前项目", "客户", other_project, "草稿", t, "2099-12-31 23:59:59"))
        try:
            root.validate_parent_requirement(other_requirement.lastrowid, project_id)
            raise AssertionError("需求允许关联其他项目的原需求")
        except ValueError:
            pass
        recent_codes = {row["requirement_code"] for row in root.recent_requirement_rows(project_id, None)}
        assert_true("REQ-OTHER-RECENT" not in recent_codes, "最近需求未按当前项目隔离")
        assert_true(root.recent_requirement_rows(None, None) == [], "未选择项目时最近需求未返回空列表")
        for invalid in ["NaN", "Infinity", "-Infinity"]:
            try:
                root.parse_float(invalid, "测试金额")
                raise AssertionError("非有限金额未被拒绝")
            except ValueError:
                pass
        assert_true(app.csv_safe("=1+1").startswith("'"), "CSV 公式注入未处理")
        try:
            app.normalize_date("2026-02-30", "测试日期")
            raise AssertionError("无效日期未被拒绝")
        except ValueError:
            pass
        root.update_idletasks()
        root.update()
        for handler in app.LOGGER.handlers:
            handler.flush()
        error_text = (root.db.logs_dir / "error.log").read_text(encoding="utf-8")
        assert_true("bad window path name" not in error_text and "unhandled_tk_callback" not in error_text,
                    "页面切换后仍存在失效控件回调")
        assert_true(not callback_errors, f"页面切换触发 Tk 回调异常：{callback_errors}")
    finally:
        if root is not None:
            root.on_close()
        else:
            app.close_logging()


def main():
    test_fresh_database_initialization()
    test_packaged_database_never_seeds_demo_data()
    old_data_dir = os.environ.get("CRM_DATA_DIR")
    old_seed_demo = os.environ.get("CRM_SEED_DEMO_DATA")
    with tempfile.TemporaryDirectory(prefix="crm-ui-selftest-") as temp_dir:
        try:
            os.environ["CRM_DATA_DIR"] = str(Path(temp_dir) / "data")
            os.environ["CRM_SEED_DEMO_DATA"] = "1"
            assert_true(len({app.new_event_id() for _ in range(1000)}) == 1000, "审计事件 ID 出现重复")
            run_checks()
            assert_critical_error_routing()
        finally:
            if old_data_dir is None:
                os.environ.pop("CRM_DATA_DIR", None)
            else:
                os.environ["CRM_DATA_DIR"] = old_data_dir
            if old_seed_demo is None:
                os.environ.pop("CRM_SEED_DEMO_DATA", None)
            else:
                os.environ["CRM_SEED_DEMO_DATA"] = old_seed_demo


if __name__ == "__main__":
    try:
        main()
        print("selftest ok")
    except Exception:
        traceback.print_exc()
        raise
