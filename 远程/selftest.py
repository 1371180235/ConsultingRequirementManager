import traceback
import os
import json
import tempfile
from pathlib import Path

import app


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def check_log_files():
    with tempfile.TemporaryDirectory(prefix="crm-log-selftest-") as temp_dir:
        logs_dir = app.configure_logging(Path(temp_dir), "offline selftest")
        try:
            app.LOGGER.info("selftest_runtime_marker")
            app.LOGGER.error("selftest_error_marker")
            app.audit_event("selftest", "logging", None, "selftest", "log routing")
            for logger in (app.LOGGER, app.AUDIT_LOGGER):
                for handler in logger.handlers:
                    handler.flush()
            assert_true("selftest_runtime_marker" in (logs_dir / "runtime.log").read_text(encoding="utf-8"), "运行日志未写入")
            assert_true("selftest_error_marker" in (logs_dir / "error.log").read_text(encoding="utf-8"), "错误日志未分流")
            audit_lines = [line for line in (logs_dir / "audit.log").read_text(encoding="utf-8").splitlines() if line.strip()]
            assert_true(audit_lines and json.loads(audit_lines[-1])["operation_type"] == "selftest", "审计日志不是有效 JSON Lines")
        finally:
            app.close_logging()
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


def offline_checks():
    check_log_files()
    assert_true(len({app.new_event_id() for _ in range(1000)}) == 1000, "审计事件 ID 出现重复")
    with tempfile.TemporaryDirectory(prefix="crm-attachment-selftest-") as temp_dir:
        database = object.__new__(app.Database)
        database.attachments_dir = Path(temp_dir) / "attachments"
        database.attachments_dir.mkdir()
        database.attachment_storage = "server"
        source = database.attachments_dir / "source.txt"
        source.write_text("attachment", encoding="utf-8")
        stored = database.store_attachment(source, "ART-SELFTEST")
        assert_true(stored == "ART-SELFTEST.txt", "服务器附件应保存相对对象键")
        destination = Path(temp_dir) / "download.txt"
        database.download_attachment(stored, destination)
        assert_true(destination.read_text(encoding="utf-8") == "attachment", "服务器附件下载失败")
        outside = Path(temp_dir) / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        try:
            database.download_attachment(str(outside), Path(temp_dir) / "blocked.txt")
            raise AssertionError("服务器附件目录越界未被拒绝")
        except RuntimeError:
            pass
        database.config = {"oss_bucket": "crm-bucket", "oss_prefix": "crm-prefix"}
        assert_true(database.oss_key_from_path("oss://crm-bucket/crm-prefix/2026/file.txt") == "crm-prefix/2026/file.txt",
                    "OSS 对象键解析失败")
        try:
            database.oss_key_from_path("oss://crm-bucket/other-system/file.txt")
            raise AssertionError("OSS 业务前缀越界未被拒绝")
        except RuntimeError:
            pass
    encoded = app.hash_password("Selftest-Password-2026")
    assert_true(app.verify_password("Selftest-Password-2026", encoded), "密码哈希校验失败")
    assert_true(not app.verify_password("wrong-password", encoded), "错误密码被接受")
    assert_true(app.csv_safe("=1+1").startswith("'"), "CSV 公式注入未处理")
    try:
        app.normalize_date("2026-02-30", "测试日期")
        raise AssertionError("无效日期未被拒绝")
    except ValueError:
        pass
    assert_true("规划中" in app.STATUS_TRANSITIONS["草稿"], "需求状态机规则缺失")
    assert_true("budget" not in app.ROLE_ACTIONS["客户"], "客户不应拥有预算写权限")
    old_tls_flag = os.environ.get("CRM_REQUIRE_TLS")
    os.environ["CRM_REQUIRE_TLS"] = "tru"
    try:
        try:
            app.env_bool("CRM_REQUIRE_TLS")
            raise AssertionError("拼写错误的 TLS 开关未被拒绝")
        except RuntimeError:
            pass
    finally:
        if old_tls_flag is None:
            os.environ.pop("CRM_REQUIRE_TLS", None)
        else:
            os.environ["CRM_REQUIRE_TLS"] = old_tls_flag
    with tempfile.TemporaryDirectory(prefix="crm-config-selftest-") as temp_dir:
        db = object.__new__(app.Database)
        db.config_path = Path(temp_dir) / "mysql_config.json"
        config = {
            "host": "127.0.0.1", "port": 3306, "user": "crm_user", "password": "",
            "password_env": "CRM_SELFTEST_DB_PASSWORD", "database": "crm_selftest",
            "attachment_storage": "server", "attachments_dir": "",
        }
        db.config_path.write_text(json.dumps(config), encoding="utf-8")
        old_value = os.environ.get("CRM_SELFTEST_DB_PASSWORD")
        os.environ["CRM_SELFTEST_DB_PASSWORD"] = "environment-secret"
        try:
            loaded = db.load_config()
            assert_true(loaded["password"] == "environment-secret", "数据库密码环境变量未生效")
        finally:
            if old_value is None:
                os.environ.pop("CRM_SELFTEST_DB_PASSWORD", None)
            else:
                os.environ["CRM_SELFTEST_DB_PASSWORD"] = old_value


def integration_transaction_checks(db):
    stamp = app.datetime.now().strftime("%Y%m%d%H%M%S%f")
    t = app.now_text()
    project_id = plan_id = version_a = version_b = requirement_id = unplanned_id = change_id = artifact_id = None
    try:
        project_id = db.execute("""INSERT INTO planning_projects(project_code, project_name, total_budget, created_at, updated_at)
                                   VALUES(?,?,?,?,?)""", (f"PRJ-ST-{stamp}", "集成自测项目", 100000, t, t)).lastrowid
        plan_id = db.execute("""INSERT INTO annual_plans(project_id, plan_year, plan_name, annual_budget, created_at, updated_at)
                                VALUES(?,?,?,?,?,?)""", (project_id, 2026, "集成自测年度", 80000, t, t)).lastrowid
        version_a = db.execute("""INSERT INTO implementation_versions(project_id, annual_plan_id, version_code, version_name, version_budget, created_at, updated_at)
                                  VALUES(?,?,?,?,?,?,?)""", (project_id, plan_id, f"STA-{stamp[-8:]}", "自测版本A", 40000, t, t)).lastrowid
        version_b = db.execute("""INSERT INTO implementation_versions(project_id, annual_plan_id, version_code, version_name, version_budget, created_at, updated_at)
                                  VALUES(?,?,?,?,?,?,?)""", (project_id, plan_id, f"STB-{stamp[-8:]}", "自测版本B", 40000, t, t)).lastrowid
        record = {
            "requirement_code": f"REQ-ST-{stamp}", "requirement_name": "事务集成自测需求",
            "requirement_description": "验证远程需求事务、冻结锁和变更申请。", "source_role": "咨询负责人",
            "proposer_name": "自测", "owner_name": "自测", "project_id": project_id, "annual_plan_id": plan_id,
            "version_id": version_a, "requirement_type": "功能优化", "tags": "selftest", "priority": "P1",
            "status": "草稿", "estimated_budget": 1000, "allocated_budget": 0, "actual_cost": 0,
            "planned_finish_date": "2026-12-31", "remark": "", "created_at": t, "updated_at": t,
        }
        requirement_id = db.create_requirement(record, "集成自测", t)
        updated = {**record, "requirement_name": "事务集成自测需求-已更新", "estimated_budget": 1200}
        db.update_requirement(requirement_id, updated, "集成自测", app.now_text())
        db.assign_requirement(requirement_id, version_b, plan_id, "集成自测", app.now_text())
        assert_true(db.one("SELECT version_id FROM requirements WHERE id=?", (requirement_id,))["version_id"] == version_b,
                    "需求事务分配未生效")
        db.freeze_version_with_baseline(version_b, "集成自测", app.now_text())
        try:
            db.update_requirement(requirement_id, updated, "集成自测", app.now_text())
            raise AssertionError("冻结版本仍允许直接更新需求")
        except ValueError as exc:
            assert_true(str(exc) == "VERSION_FROZEN", "冻结版本拒绝原因异常")
        change_id = db.create_change_request_record(
            requirement_id, "update", {**updated, "requirement_name": "事务集成自测需求-已审批"},
            {"change_title": "集成自测变更", "change_reason": "验证 payload 原子提交", "impact_scope": "自测"},
            "集成自测", app.now_text(),
        )
        assert_true(db.one("SELECT change_type FROM change_request_payloads WHERE change_request_id=?", (change_id,)),
                    "变更申请 payload 未原子提交")
        db.review_change_request(change_id, "approved", "集成自测", app.now_text())

        unplanned = {**record, "requirement_code": f"REQ-ST-U-{stamp}", "version_id": None, "annual_plan_id": None}
        unplanned_id = db.create_requirement(unplanned, "集成自测", app.now_text())
        db.soft_delete_requirement(unplanned_id, "集成自测", app.now_text())
        assert_true(db.one("SELECT is_deleted FROM requirements WHERE id=?", (unplanned_id,))["is_deleted"] == 1,
                    "需求软删除事务未生效")

        artifact = {
            "artifact_code": f"ART-ST-{stamp}", "artifact_name": "selftest.txt", "artifact_type": "其他",
            "file_path": f"ART-ST-{stamp}.txt", "file_ext": ".txt", "file_size": 8,
            "related_object_type": "项目", "related_object_id": project_id, "version_no": "v1",
            "description": "事务自测", "uploaded_by": "集成自测", "uploaded_at": t, "created_at": t,
        }
        artifact_id = db.create_artifact_record(artifact, "集成自测", app.now_text())
        invalid = {**artifact, "artifact_code": f"ART-ST-X-{stamp}", "related_object_id": -1}
        try:
            db.create_artifact_record(invalid, "集成自测", app.now_text())
            raise AssertionError("不存在的成果物挂载对象未被拒绝")
        except ValueError:
            pass
    finally:
        if artifact_id:
            db.execute("DELETE FROM artifacts WHERE id=?", (artifact_id,))
        if change_id:
            db.execute("DELETE FROM change_request_payloads WHERE change_request_id=?", (change_id,))
            db.execute("DELETE FROM change_requests WHERE id=?", (change_id,))
        for req_id in [requirement_id, unplanned_id]:
            if req_id:
                db.execute("DELETE FROM requirement_status_history WHERE requirement_id=?", (req_id,))
                db.execute("DELETE FROM requirements WHERE id=?", (req_id,))
        for version_id in [version_a, version_b]:
            if version_id:
                baselines = db.query("SELECT id FROM version_baselines WHERE version_id=?", (version_id,))
                for baseline in baselines:
                    db.execute("DELETE FROM version_baseline_requirements WHERE baseline_id=?", (baseline["id"],))
                db.execute("DELETE FROM version_baselines WHERE version_id=?", (version_id,))
                db.execute("DELETE FROM implementation_versions WHERE id=?", (version_id,))
        if plan_id:
            db.execute("DELETE FROM annual_plans WHERE id=?", (plan_id,))
        if project_id:
            db.execute("DELETE FROM planning_projects WHERE id=?", (project_id,))


def main():
    offline_checks()
    if os.environ.get("CRM_MYSQL_INTEGRATION_SELFTEST") != "1":
        print("offline selftest ok")
        return
    root = app.App(skip_login=True)
    try:
        assert_true(root.db.config_path.exists(), "MySQL 配置文件未创建")
        assert_true(root.db.storage_name == "MySQL", "当前不是 MySQL 存储")
        assert_true(root.db.attachments_dir.exists(), "附件目录未创建")
        assert_true(root.db.exports_dir.exists(), "导出目录未创建")
        assert_true(root.db.backups_dir.exists(), "备份目录未创建")
        assert_true(root.db.logs_dir.exists(), "日志目录未创建")
        for name in ["runtime.log", "error.log", "audit.log"]:
            assert_true((root.db.logs_dir / name).exists(), f"日志文件未创建：{name}")

        integration_transaction_checks(root.db)

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

        log_count = root.db.one("SELECT COUNT(*) c FROM operation_logs")["c"]
        assert_true(log_count >= 1, "操作日志未初始化")
        correlated = root.db.one("SELECT event_id, result FROM operation_logs WHERE event_id IS NOT NULL ORDER BY id DESC LIMIT 1")
        assert_true(correlated and correlated["result"] in {"success", "failed", "denied"}, "数据库审计事件字段缺失")
        root.operation_log_type_filter.set("system")
        assert_true(all(row["object_type"] == "system" for row in root.filtered_operation_logs(limit=20)), "操作日志类型过滤失败")
        root.operation_log_type_filter.set("全部")

        for table in ["requirement_status_history", "version_baselines", "version_baseline_requirements", "user_project_access"]:
            root.db.one(f"SELECT COUNT(*) c FROM {table}")

        assert_true(root.db.healthcheck()["free_bytes"] > 0, "远程部署健康检查失败")

        assert_true("规划中" in app.STATUS_TRANSITIONS["草稿"], "需求状态机规则缺失")
        root.current_role.set("客户")
        assert_true(not root.can_action("budget"), "客户不应拥有预算写权限")
        root.current_role.set("管理员")
        assert_true(root.can_action("budget"), "管理员应拥有预算写权限")
    finally:
        root.on_close()


if __name__ == "__main__":
    try:
        main()
        print("selftest ok")
    except Exception:
        traceback.print_exc()
        raise
