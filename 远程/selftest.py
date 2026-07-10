import traceback
import os
import json
import tempfile
from pathlib import Path

import app


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def offline_checks():
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


def main():
    offline_checks()
    if os.environ.get("CRM_OFFLINE_SELFTEST") == "1":
        print("offline selftest ok")
        return
    root = app.App(skip_login=True)
    try:
        assert_true(root.db.config_path.exists(), "MySQL 配置文件未创建")
        assert_true(root.db.storage_name == "MySQL", "当前不是 MySQL 存储")
        assert_true(root.db.attachments_dir.exists(), "附件目录未创建")
        assert_true(root.db.exports_dir.exists(), "导出目录未创建")
        assert_true(root.db.backups_dir.exists(), "备份目录未创建")

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

        for table in ["requirement_status_history", "version_baselines", "version_baseline_requirements", "user_project_access"]:
            root.db.one(f"SELECT COUNT(*) c FROM {table}")

        assert_true("规划中" in app.STATUS_TRANSITIONS["草稿"], "需求状态机规则缺失")
        root.current_role.set("客户")
        assert_true(not root.can_action("budget"), "客户不应拥有预算写权限")
        root.current_role.set("管理员")
        assert_true(root.can_action("budget"), "管理员应拥有预算写权限")
    finally:
        root.destroy()


if __name__ == "__main__":
    try:
        main()
        print("selftest ok")
    except Exception:
        traceback.print_exc()
        raise
