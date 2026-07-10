import traceback
import tempfile
from pathlib import Path

import app


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def test_database_transactions():
    with tempfile.TemporaryDirectory(prefix="crm-selftest-") as temp_dir:
        db = app.Database(Path(temp_dir))
        try:
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
        finally:
            db.conn.close()


def main():
    test_database_transactions()
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
    finally:
        root.destroy()


if __name__ == "__main__":
    try:
        main()
        print("selftest ok")
    except Exception:
        traceback.print_exc()
        raise
