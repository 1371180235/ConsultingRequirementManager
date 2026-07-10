import traceback
import tempfile
import os
import sqlite3
from pathlib import Path

import app


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


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
            before_allocated = float(req["allocated_budget"] or 0)
            db.record_budget_flow("BF-SELFTEST-1", req["project_id"], req["annual_plan_id"], req["version_id"], req["id"],
                                  "已分配预算", 1000, "selftest", "自测", app.now_text())
            after = db.one("SELECT allocated_budget FROM requirements WHERE id=?", (req["id"],))
            assert_true(float(after["allocated_budget"]) == before_allocated + 1000, "资金流水未原子更新需求预算")

            try:
                db.record_budget_flow("BF-SELFTEST-INVALID", req["project_id"], req["annual_plan_id"], None, req["id"],
                                      "已分配预算", 1000, "invalid", "自测", app.now_text())
                raise AssertionError("版本不一致的资金流水未被拒绝")
            except ValueError:
                pass
            assert_true(db.one("SELECT id FROM budget_flows WHERE flow_code='BF-SELFTEST-INVALID'") is None, "非法资金流水未回滚")

            transitioned = db.transition_requirement_status(req["id"], req["status"], "已排期", "selftest transition", "自测", app.now_text())
            assert_true(transitioned, "合法状态流转未提交")
            state = db.one("SELECT status FROM requirements WHERE id=?", (req["id"],))
            history = db.one("SELECT id FROM requirement_status_history WHERE requirement_id=? AND from_status=? AND to_status='已排期'",
                             (req["id"], req["status"]))
            assert_true(state["status"] == "已排期" and history is not None, "状态与历史未原子写入")

            baseline = db.freeze_version_with_baseline(req["version_id"], "自测", app.now_text())
            assert_true(baseline and baseline["snapshot_no"] == 1, "版本基线未生成")
            version = db.one("SELECT is_frozen FROM implementation_versions WHERE id=?", (req["version_id"],))
            assert_true(version["is_frozen"] == 1, "版本冻结状态未提交")
            snapshot = db.one("SELECT requirement_name FROM version_baseline_requirements WHERE baseline_id=? AND requirement_id=?",
                              (baseline["baseline_id"], req["id"]))
            assert_true(snapshot is not None, "版本基线缺少需求快照")
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
            cur = db.execute("""INSERT INTO change_requests(version_id, requirement_id, change_title, change_reason,
                                                              approval_status, requested_by, requested_at)
                              VALUES(?,?,?,?,?,?,?)""",
                             (req["version_id"], req["id"], "自测变更", "验证事务", "pending", "自测", t))
            change_id = cur.lastrowid
            proposed = dict(req)
            proposed["requirement_name"] = "统一需求池（已审批）"
            db.execute("INSERT INTO change_request_payloads(change_request_id, change_type, proposed_value) VALUES(?,?,?)",
                       (change_id, "update", app.json.dumps(proposed, ensure_ascii=False)))
            db.review_change_request(change_id, "approved", "自测审批人", app.now_text())
            changed = db.one("SELECT requirement_name, status FROM requirements WHERE id=?", (req["id"],))
            assert_true(changed["requirement_name"] == "统一需求池（已审批）" and changed["status"] == "变更中", "审批内容未应用")
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
    test_legacy_audit_migration()
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

        callback_errors = []
        root.report_callback_exception = lambda *error: callback_errors.append(error)
        root.show_dashboard()
        root.show_projects()
        root.update_idletasks()
        root.update()

        pages = [
            root.show_dashboard,
            root.show_projects,
            root.show_plans,
            root.show_versions,
            root.show_requirements,
            root.show_budget,
            root.show_artifacts,
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

        for table in ["requirement_status_history", "version_baselines", "version_baseline_requirements"]:
            exists = root.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            assert_true(exists is not None, f"升级表未创建：{table}")

        assert_true("规划中" in app.STATUS_TRANSITIONS["草稿"], "需求状态机规则缺失")
        root.current_role.set("客户")
        assert_true(not root.can_action("budget"), "客户不应拥有预算写权限")
        root.current_role.set("管理员")
        assert_true(root.can_action("budget"), "管理员应拥有预算写权限")
        assert_true(root.can_view_money(), "管理员应能查看资金信息")
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
