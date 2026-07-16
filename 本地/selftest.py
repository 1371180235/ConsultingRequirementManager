import traceback
import tempfile
import os
import sqlite3
from pathlib import Path

import app


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


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
            assert_true("current_stage" in project_columns, "旧库未迁移项目当前阶段")
            assert_true(db.one("SELECT current_stage FROM planning_projects LIMIT 1")["current_stage"] == "宏观规划",
                        "旧库项目阶段默认值错误")
            assert_true("parent_requirement_id" in requirement_columns, "旧库未迁移原需求关联")
            assert_true(db.one("SELECT business_key FROM requirements WHERE id=1")["business_key"] == "legacy key",
                        "旧库业务标识未按 trim/lower/折叠空白规则迁移")
            assert_true({"visibility", "approval_status", "change_request_id"}.issubset(artifact_columns),
                        "旧库未迁移成果物可见性或审批字段")
            assert_true({"requirement_description", "business_key", "estimated_budget", "estimated_hours",
                         "parent_requirement_id"}.issubset(baseline_columns), "旧库未迁移完整基线字段")
            assert_true({"estimated_hours", "actual_hours"}.issubset(baseline_header_columns),
                        "旧库未迁移基线工时汇总字段")
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
            assert_true(approved_artifact["approval_status"] == "approved", "未冻结版本成果物不应进入审批")

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
            proposed = dict(req)
            proposed["requirement_name"] = "统一需求池（已审批）"
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
            payload = db.one("SELECT change_type FROM change_request_payloads WHERE change_request_id=?", (change_id,))
            assert_true(payload and payload["change_type"] == "update", "变更申请与载荷未在同一事务创建")
            try:
                db.create_change_request_record(
                    req["version_id"], req["id"], "重复变更", "验证单一待审批", "同一需求",
                    "update", proposed, "自测", t,
                )
                raise AssertionError("同一需求允许创建多条待审批变更")
            except ValueError:
                pass
            db.review_change_request(change_id, "approved", "自测审批人", app.now_text())
            changed = db.one("SELECT requirement_name, status FROM requirements WHERE id=?", (req["id"],))
            assert_true(changed["requirement_name"] == "统一需求池（已审批）" and changed["status"] == "变更中", "审批内容未应用")

            conflicting = dict(db.one("SELECT * FROM requirements WHERE id=?", (child_id,)))
            conflicting["business_key"] = req["business_key"]
            conflict_change = db.create_change_request_record(
                req["version_id"], child_id, "业务标识冲突", "验证审批复核", "业务标识",
                "update", conflicting, "自测", app.now_text(),
            )
            try:
                db.review_change_request(conflict_change, "approved", "自测审批人", app.now_text())
                raise AssertionError("审批未复核同版本业务标识唯一性")
            except ValueError:
                pass
            assert_true(db.one("SELECT approval_status FROM change_requests WHERE id=?", (conflict_change,))["approval_status"] == "pending",
                        "业务标识冲突审批失败后事务未回滚")
            db.review_change_request(conflict_change, "rejected", "自测审批人", app.now_text())

            missing_payload = db.execute("""INSERT INTO change_requests(
                                              version_id, change_title, change_reason, approval_status, requested_by, requested_at)
                                            VALUES(?,?,?,'pending',?,?)""",
                                         (req["version_id"], "缺少载荷", "验证载荷必填", "自测", app.now_text())).lastrowid
            try:
                db.review_change_request(missing_payload, "rejected", "自测审批人", app.now_text())
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
            assert_true(frozen_artifact["approval_status"] == "pending" and frozen_artifact["change_id"],
                        "冻结版本新增成果物未进入变更审批")
            db.review_change_request(frozen_artifact["change_id"], "approved", "自测审批人", app.now_text())
            assert_true(db.one("SELECT approval_status FROM artifacts WHERE id=?", (frozen_artifact["artifact_id"],))["approval_status"] == "approved",
                        "成果物变更审批通过后未生效")
            rejected_artifact = db.create_artifact_record({
                "artifact_code": "ART-FROZEN-REJECTED", "artifact_name": "驳回成果物.txt",
                "artifact_type": "任务书方案", "file_path": "attachments/rejected.txt", "file_ext": ".txt",
                "file_size": 8, "related_object_type": "版本", "related_object_id": req["version_id"],
                "version_no": "v3", "description": "审批驳回", "visibility": "内部",
                "project_id": req["project_id"],
            }, "自测", app.now_text())
            db.review_change_request(rejected_artifact["change_id"], "rejected", "自测审批人", app.now_text())
            assert_true(db.one("SELECT approval_status FROM artifacts WHERE id=?", (rejected_artifact["artifact_id"],))["approval_status"] == "rejected",
                        "成果物变更驳回后状态错误")

            funding_id = db.execute("""INSERT INTO funding_applications(
                                         application_code, project_id, annual_plan_id, amount, status,
                                         applicant_name, description, created_at, updated_at)
                                       VALUES(?,?,?,?,?,?,?,?,?)""",
                                    ("FUND-SELFTEST", req["project_id"], req["annual_plan_id"], 50000, "草稿",
                                     "销售自测", "年度资金申报", t, t)).lastrowid
            funding_status = "草稿"
            for next_status in ["已提交", "审批中", "已批复", "已拨付"]:
                assert_true(db.transition_funding_application(funding_id, funding_status, next_status, "自测", app.now_text()),
                            f"资金申报状态未流转到 {next_status}")
                funding_status = next_status
            assert_true(db.one("SELECT status, submitted_at, reviewed_by FROM funding_applications WHERE id=?", (funding_id,))["status"] == "已拨付",
                        "资金申报未完成全流程")

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
        root.current_role.set("销售")
        root.show_funding_applications()
        assert_true(any("FUND-UI-SELFTEST" in root.funding_tree.item(item)["values"] for item in root.funding_tree.get_children("")),
                    "资金申报页面未展示当前项目申报")
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
                                                  related_object_type, related_object_id, uploaded_by, uploaded_at, created_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        ("ART-SELFTEST-YEAR", "年度成果物", "分年任务申报书", "attachments/year.txt",
                         "年度", plan_id, "自测", t, t))
        root.db.execute("""INSERT INTO artifacts(artifact_code, artifact_name, artifact_type, file_path,
                                                  related_object_type, related_object_id, uploaded_by, uploaded_at, created_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        ("ART-SELFTEST-LEGACY-YEAR", "旧年度成果物", "分年任务申报书", "attachments/legacy-year.txt",
                         "年度计划", plan_id, "自测", t, t))
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
        assert_true(pending_artifact["approval_status"] == "pending", "冻结版本成果物未进入 pending")
        pending_codes = {row["artifact_code"] for row in root.artifact_rows_for_context(project_id, plan_id, version_id)}
        assert_true("ART-UI-PENDING" not in pending_codes, "普通成果物列表显示了 pending 成果物")
        assert_true("新增成果物" in root.change_payload_summary(pending_artifact["change_id"]), "变更申请摘要未展示成果物")
        root.db.review_change_request(pending_artifact["change_id"], "approved", "审批自测", app.now_text())
        approved_codes = {row["artifact_code"] for row in root.artifact_rows_for_context(project_id, plan_id, version_id)}
        assert_true("ART-UI-PENDING" in approved_codes, "成果物审批通过后普通列表仍不可见")
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
