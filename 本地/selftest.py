import traceback

import app


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    root = app.App()
    try:
        assert_true(root.db.db_path.exists(), "数据库文件未创建")
        assert_true(root.db.attachments_dir.exists(), "附件目录未创建")
        assert_true(root.db.exports_dir.exists(), "导出目录未创建")
        assert_true(root.db.backups_dir.exists(), "备份目录未创建")
        assert_true(root.current_project_id() is not None, "未加载默认项目")

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
    finally:
        root.destroy()


if __name__ == "__main__":
    try:
        main()
        print("selftest ok")
    except Exception:
        traceback.print_exc()
        raise
