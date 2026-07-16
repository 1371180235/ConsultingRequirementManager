import traceback
import os
import json
import inspect
import tempfile
import threading
from pathlib import Path

import app


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


class StubVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class CredentialHarness:
    def __init__(self, username, password, setup=False, confirm=""):
        self.username = StubVar(username)
        self.password = StubVar(password)
        self.confirm = StubVar(confirm)
        self.setup = setup
        self.result = None
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class LiveSessionDB:
    def __init__(self, user, access=()):
        self.user = user
        self.access = set(access)

    def one(self, sql, params=()):
        if "FROM users" in sql:
            return dict(self.user) if self.user else None
        if "FROM user_project_access" in sql:
            return {"user_id": params[0]} if tuple(params) in self.access else None
        raise AssertionError(f"未预期的实时授权查询：{sql}")


def live_session_harness(role="管理员", access=()):
    harness = type("LiveSessionHarness", (), {
        "ensure_live_session": app.App.ensure_live_session,
        "can_access_project_now": app.App.can_access_project_now,
    })()
    harness.current_user_id = 7
    harness.current_username = "live-user"
    harness.current_role = StubVar(role)
    harness.session_token = "selftest-session-token"
    harness.session_invalid = False
    harness.session_invalid_reason = ""
    harness.db = LiveSessionDB({
        "id": 7, "username": "live-user", "display_name": "实时用户",
        "role_name": role, "is_active": 1, "session_token": harness.session_token,
    }, access)
    return harness


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


def check_hidden_parent_credential_dialog():
    try:
        root = app.tk.Tk()
    except app.tk.TclError:
        return
    observed = {}
    timed_out = {"value": False}
    root.withdraw()

    def visible_dialog():
        dialogs = [child for child in root.winfo_children() if isinstance(child, app.tk.Toplevel)]
        if not dialogs or not dialogs[0].winfo_viewable():
            root.after(25, visible_dialog)
            return
        dialog = dialogs[0]
        observed["viewable"] = bool(dialog.winfo_viewable())
        observed["transient"] = dialog.transient()
        dialog.destroy()

    def force_close():
        if observed:
            return
        timed_out["value"] = True
        for child in root.winfo_children():
            if isinstance(child, app.tk.Toplevel):
                child.destroy()

    root.after(25, visible_dialog)
    root.after(3000, force_close)
    try:
        app.CredentialDialog(root)
    except app.tk.TclError as exc:
        if not timed_out["value"]:
            raise
        raise AssertionError("隐藏父窗下登录对话框映射超时") from exc
    finally:
        try:
            root.destroy()
        except app.tk.TclError:
            pass
    assert_true(not timed_out["value"], "隐藏父窗下登录对话框未能在 3 秒内映射")
    assert_true(observed.get("viewable"), "隐藏父窗下登录对话框不可见")
    assert_true(not observed.get("transient"), "隐藏父窗下登录对话框不应绑定为 transient")


def offline_checks():
    check_log_files()
    check_hidden_parent_credential_dialog()
    assert_true(app.SESSION_HEARTBEAT_MS == 10_000, "远程账号会话巡检间隔不是 10 秒")
    assert_true(app.validated_display_name("  示例负责人  ") == "示例负责人",
                "用户显示名称未正确清理首尾空白")
    for invalid_display_name in ("包含\n换行", "X" * (app.USER_DISPLAY_NAME_MAX_LENGTH + 1)):
        try:
            app.validated_display_name(invalid_display_name)
            raise AssertionError("无效用户显示名称未被拒绝")
        except ValueError:
            pass
    compact_identity = app.topbar_identity_text("超长显示名称用于验证顶栏稳定布局", "管理员")
    assert_true("…" in compact_identity and "超长显示名称用于验证顶栏稳定布局" not in compact_identity,
                "顶栏超长显示名称未截断")
    assert_true("validated_display_name" in inspect.getsource(app.App.add_user) and
                "topbar_identity_text" in inspect.getsource(app.App.build_layout),
                "显示名称校验或顶栏截断未接入实际账号界面")
    assert_true(app.business_key_text(" Foo  Bar ") == "foo bar", "业务标识规范化规则错误")
    assert_true(app.requirement_business_key({"business_key": "  ", "requirement_name": " Foo  Bar "}) == "foo bar",
                "空业务标识未回退规范化需求名称")
    assert_true(app.requirement_business_key_conflicts([
        {"id": 1, "project_id": 1, "version_id": 2, "business_key": "foo  bar", "requirement_name": "A"},
        {"id": 2, "project_id": 1, "version_id": 2, "business_key": " Foo Bar ", "requirement_name": "B"},
    ]), "同版本规范化业务标识冲突未被识别")
    assert_true(tuple(app.THEME_PALETTES) == ("云岚蓝", "松石青", "靛夜紫", "石墨灰") and
                len({palette["primary"] for palette in app.THEME_PALETTES.values()}) == 4,
                "四套主题名称或主色配置不完整")
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
    assert_true(app.requirement_compare_changes(compare_left, {**compare_left, "tags": "客户,重点"}) == [],
                "跨版本比对将标签顺序变化误判为内容变化")
    restricted_right = {**compare_left, "allocated_budget": 9, "actual_cost": 5, "actual_hours": 7}
    assert_true(app.requirement_compare_changes(compare_left, restricted_right) == [],
                "客户视角跨版本比对泄露资金或工时差异")
    assert_true(set(app.requirement_compare_changes(compare_left, restricted_right, True, True)) >=
                {"分配预算", "实际成本", "实际工时"}, "有权限角色跨版本比对缺少资金或工时差异")
    assert_true(not app.budget_limit_exceeded(0.1 + 0.2, 0.3) and app.budget_limit_exceeded(0.3001, 0.3),
                "资金浮点容差未正确区分等额与真实超额")

    theme_harness = type("ThemeHarness", (), {})()
    theme_harness.current_user_id = 7
    theme_harness.current_username = "selftest"
    theme_harness.theme_preference_key = lambda: "ui-theme:user:7"
    theme_harness.db = type("ThemeDB", (), {
        "one": lambda _self, _sql, _params=(): {"layout_json": json.dumps({"theme": []})},
    })()
    assert_true(app.App.load_theme_preference(theme_harness) == app.DEFAULT_THEME,
                "损坏的远程主题偏好未回退默认主题")

    reload_calls = []
    reload_harness = type("ReloadHarness", (), {})()
    reload_harness.show_version_baseline = lambda: reload_calls.append("baseline")
    reload_harness.reload_version_comparison = lambda: reload_calls.append("compare")
    reload_harness.current_page = "版本基线"
    app.App.reload_page(reload_harness)
    reload_harness.current_page = "跨版本比对"
    app.App.reload_page(reload_harness)
    assert_true(reload_calls == ["baseline", "compare"], "特殊版本页面换肤后跳转错误")
    assert_true("submit" in app.CredentialDialog.__dict__, "登录提交方法未定义在 CredentialDialog")
    assert_true("submit" not in app.DashboardLayoutDialog.__dict__, "登录提交方法被错误定义在看板配置对话框")
    assert_true(callable(app.App.authenticate), "登录认证方法缺失")
    login = CredentialHarness("operator", "Strong-Password-2026")
    app.CredentialDialog.submit(login)
    assert_true(login.result == ("operator", "Strong-Password-2026") and login.destroyed, "普通登录提交失败")
    setup = CredentialHarness("admin", "Admin-Password-2026", setup=True, confirm="Admin-Password-2026")
    app.CredentialDialog.submit(setup)
    assert_true(setup.result == ("admin", "Admin-Password-2026") and setup.destroyed, "管理员初始化提交失败")

    permission = type("PermissionHarness", (), {
        "can_view_effort": app.App.can_view_effort,
        "can_view_money": app.App.can_view_money,
    })()
    permission.current_role = StubVar("销售")
    assert_true(not app.App.can_view_effort(permission), "销售不应查看工时")
    assert_true(app.App.can_view_money(permission), "销售应可查看资金")
    assert_true(app.App.can_action(permission, "artifact"), "销售应可挂载成果物")
    assert_true(app.App.visible_export_columns(permission, ["requirement_code", "estimated_hours", "actual_hours", "actual_cost"])
                == ["requirement_code", "actual_cost"], "销售通用导出未过滤工时字段")
    permission.current_role.set("客户")
    assert_true(not app.App.can_view_effort(permission), "客户不应查看工时")
    assert_true(not app.App.can_view_money(permission), "客户不应查看资金成本")
    assert_true(app.App.visible_export_columns(permission, ["requirement_code", "estimated_hours", "actual_cost"])
                == ["requirement_code"], "客户通用导出未过滤工时和资金字段")
    permission.current_role.set("研发人员")
    assert_true(app.App.can_action(permission, "requirement_create"), "研发人员应可新建需求")
    assert_true(app.App.can_view_effort(permission), "研发人员应可查看工时")

    assert_true(app.PROJECT_STAGES == ["宏观规划", "规划细化", "建设落地", "招投标", "项目交付验收", "运维运营"],
                "六阶段业务流程定义不完整")
    assert_true(app.FUNDING_TRANSITIONS["草稿"] == ["已提交"] and app.FUNDING_TRANSITIONS["审批中"] == ["已批复", "已驳回"],
                "资金申报状态机定义错误")
    assert_true({"推广活动", "线上问题", "维护记录", "问题解答"} == set(app.OPERATION_TYPES),
                "运营服务类型定义不完整")
    assert_true(app.ROLE_ACTIONS["销售"] >= {"funding_create", "funding_submit"}, "销售资金申报权限缺失")
    assert_true("funding_review" in app.ROLE_ACTIONS["咨询负责人"], "咨询负责人资金审批权限缺失")
    assert_true("operation_record" in app.ROLE_ACTIONS["运营人员"], "运营人员服务记录权限缺失")
    assert_true(app.ARTIFACT_TARGET_TYPES["可研报告"] == {"项目"}, "可研报告挂载规则错误")
    assert_true(app.ARTIFACT_TARGET_TYPES["分年任务申报书"] == {"年度"}, "年度申报书挂载规则错误")
    assert_true(app.ARTIFACT_TARGET_TYPES["验收报告"] == {"版本"}, "验收报告挂载规则错误")
    assert_true(app.ARTIFACT_TARGET_TYPES["运维反馈"] == {"需求"}, "运维反馈挂载规则错误")
    assert_true({".exe", ".ps1", ".js", ".lnk"}.issubset(app.DANGEROUS_ARTIFACT_EXTENSIONS),
                "危险附件扩展名拦截规则缺失")
    assert_true(set(app.DIFF_COLORS) == {"新增", "移除", "变更"}, "跨版本差异高亮规则缺失")
    assert_true("diff_" in inspect.getsource(app.App.add_table), "表格未应用跨版本差异行标签")
    reload_source = inspect.getsource(app.App.reload_page)
    assert_true("show_funding_applications" in reload_source and "show_operation_records" in reload_source,
                "资金申报或运营服务页面未接入上下文重载")
    project_source = inspect.getsource(app.App.show_projects)
    search_source = inspect.getsource(app.App.show_search)
    assert_true("user_project_access" in project_source and "self.projects.values" not in project_source,
                "客户项目列表仍依赖可能过期的授权缓存")
    assert_true("user_project_access" in search_source and "self.projects.values" not in search_source,
                "客户全局搜索仍依赖可能过期的授权缓存")
    assert_true("UNION ALL" not in inspect.getsource(app.App.validate_parent_requirement),
                "父需求循环检查仍可能在遗留环数据上无限递归")
    assignment_source = inspect.getsource(app.Database.assign_requirement)
    assert_true(assignment_source.count("FOR UPDATE") >= 3 and
                "WHERE project_id=%s AND version_id=%s" in assignment_source and
                "requirement_business_key" in assignment_source and "is_deleted=0" in assignment_source,
                "需求分配事务未锁定目标版本、需求及业务标识候选行")
    for method_name in ["create_requirement", "update_requirement", "review_change_request"]:
        method_source = inspect.getsource(getattr(app.Database, method_name))
        assert_true("FOR UPDATE" in method_source and "requirement_business_key" in method_source,
                    f"{method_name} 未在事务锁内按规范业务标识检查冲突")
    credential_source = inspect.getsource(app.CredentialDialog.__init__)
    assert_true('parent.state() == "withdrawn"' in credential_source and
                "if not parent_is_withdrawn" in credential_source and
                all(call in credential_source for call in ["deiconify()", "lift()", "focus_force()"]),
                "隐藏父窗登录对话框映射保护缺失")
    healthcheck_source = inspect.getsource(app.Database.healthcheck)
    assert_true("需求年度" in healthcheck_source and "需求版本" in healthcheck_source and "IS NULL" in healthcheck_source,
                "部署健康检查未覆盖跨项目关联或空枚举值")

    live = live_session_harness()
    assert_true(live.ensure_live_session(notify=False), "有效账号的实时会话被错误拒绝")
    live.db.user["role_name"] = "销售"
    assert_true(not live.ensure_live_session(notify=False) and live.session_invalid,
                "账号角色变化后缓存会话未失效")
    replaced = live_session_harness()
    replaced.db.user["session_token"] = "newer-client-session"
    assert_true(not replaced.ensure_live_session(notify=False) and replaced.session_invalid and
                "另一台设备" in replaced.session_invalid_reason,
                "同账号后登录未使旧客户端会话失效")
    terminated = live_session_harness()
    terminated.db.user["session_token"] = None
    assert_true(not terminated.ensure_live_session(notify=False) and terminated.session_invalid and
                "管理员" in terminated.session_invalid_reason and "另一台设备" not in terminated.session_invalid_reason,
                "管理员下线或密码重置后的会话失效原因不准确")
    customer = live_session_harness("客户", {(7, 101), (7, 102)})
    assert_true(customer.ensure_live_session(101, notify=False), "已授权客户无法访问项目")
    customer.db.access.remove((7, 102))
    assert_true(customer.ensure_live_session(101, notify=False), "撤销其他项目后当前有效项目会话被错误拒绝")
    assert_true(not customer.ensure_live_session(102, notify=False), "客户非当前项目授权撤销后仍可访问")
    customer.db.access.clear()
    assert_true(not customer.ensure_live_session(101, notify=False), "客户项目授权撤销后仍可访问")
    disabled = live_session_harness()
    disabled.db.user["is_active"] = 0
    assert_true(not disabled.ensure_live_session(notify=False) and disabled.session_invalid,
                "账号停用后缓存会话未失效")

    race_state = {
        "id": 9, "is_active": 1, "password_hash": "new-password-hash",
        "role_name": "销售", "session_token": None,
    }
    race_database = object.__new__(app.Database)

    def race_execute(sql, params=()):
        assert_true("password_hash" in sql and "role_name" in sql,
                    "会话占用未绑定刚完成认证的密码与角色快照")
        token, _started_at, user_id, expected_hash, expected_role = params
        matches = bool(
            user_id == race_state["id"] and race_state["is_active"]
            and expected_hash == race_state["password_hash"]
            and expected_role == race_state["role_name"]
        )
        if matches:
            race_state["session_token"] = token
        return app.ExecutionResult(rowcount=1 if matches else 0)

    race_database.execute = race_execute
    assert_true(not app.Database.claim_user_session(
                    race_database, 9, "stale-login-token", app.now_text(), "old-password-hash", "销售"
                ) and race_state["session_token"] is None,
                "密码重置与登录并发时旧密码仍可重新占用会话")
    assert_true(app.Database.claim_user_session(
                    race_database, 9, "current-login-token", app.now_text(), "new-password-hash", "销售"
                ) and race_state["session_token"] == "current-login-token",
                "有效密码与角色快照无法占用会话")

    class SameRoleCursor:
        def __init__(self):
            self.closed = False

        def execute(self, sql, params=()):
            assert_true("FOR UPDATE" in sql and params == (11,), "同角色事务未锁定目标用户")

        def fetchone(self):
            return {"username": "same-role", "role_name": "客户"}

        def close(self):
            self.closed = True

    class SameRoleConnection:
        def __init__(self):
            self.rollbacks = 0

        def rollback(self):
            self.rollbacks += 1

    same_role_cursor = SameRoleCursor()
    same_role_database = object.__new__(app.Database)
    same_role_database.conn = SameRoleConnection()
    same_role_database.begin_transaction = lambda: same_role_cursor
    same_role_database.end_transaction = lambda cursor: cursor.close()
    assert_true(app.Database.update_user_role(
                    same_role_database, 11, "客户", "selftest", app.now_text()
                ) == "客户" and same_role_database.conn.rollbacks == 1 and same_role_cursor.closed,
                "同角色竞态路径未显式结束事务并释放用户锁")

    reset_state = {"refreshed": 0, "sql": ""}
    reset_database = type("ResetDatabase", (), {
        "one": lambda _self, _sql, _params=(): {"username": "reset-user", "display_name": "重置用户"},
        "execute": lambda _self, sql, _params=(): reset_state.update({"sql": sql}) or app.ExecutionResult(rowcount=1),
        "log": lambda *_args, **_kwargs: None,
    })()
    reset_harness = type("ResetHarness", (), {})()
    reset_harness.ensure_live_session = lambda _project_id=None: True
    reset_harness.current_project_id = lambda: 1
    reset_harness.current_role = StubVar("管理员")
    reset_harness.current_user_id = 7
    reset_harness.current_user = "自测管理员"
    reset_harness.selected_user_id = lambda: 8
    reset_harness.db = reset_database
    reset_harness.show_settings = lambda: reset_state.update({"refreshed": reset_state["refreshed"] + 1})
    original_field_dialog = app.FieldDialog
    original_showinfo = app.messagebox.showinfo
    try:
        app.FieldDialog = lambda *_args, **_kwargs: type("ResetDialog", (), {
            "result": {"new_password": "New-Password-2026", "confirm_password": "New-Password-2026"}
        })()
        app.messagebox.showinfo = lambda *_args, **_kwargs: None
        app.App.reset_user_password(reset_harness)
    finally:
        app.FieldDialog = original_field_dialog
        app.messagebox.showinfo = original_showinfo
    assert_true(reset_state["refreshed"] == 1 and "session_token=NULL" in reset_state["sql"],
                "管理员重置密码后用户会话表格未刷新或旧会话未清理")

    mysql_sql = app.Database.mysql_sql(None, "SELECT ? AS probe, v.version_code || ' ' || v.version_name AS label")
    assert_true("%s" in mysql_sql and "CONCAT(v.version_code, ' ', v.version_name)" in mysql_sql,
                "MySQL 占位符或字符串拼接适配失败")

    assert_true(app.validate_cumulative_budget(40, 60, 100, "年度预算", "项目总预算") == 100,
                "累计预算边界校验错误")
    try:
        app.validate_cumulative_budget(40.01, 60, 100, "年度预算", "项目总预算")
        raise AssertionError("累计预算超出上级预算未被拒绝")
    except ValueError:
        pass
    no_query = type("NoQuery", (), {"query": lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("无项目时不应查询"))})()
    scoped = type("ScopeHarness", (), {"db": no_query})()
    assert_true(app.App.recent_requirement_rows(scoped, None, None) == [], "无项目时最近需求不应返回数据")
    assert_true(app.App.artifact_rows_for_context(scoped, None, None, None) == [], "无项目时成果物不应返回数据")

    lock_calls = []
    lock_state = {"fetches": 0, "cursor_closes": 0, "connection_closes": 0}

    class LockCursor:
        def execute(self, sql, params):
            lock_calls.append((sql, params))

        def fetchone(self):
            lock_state["fetches"] += 1
            return (1,)

        def close(self):
            lock_state["cursor_closes"] += 1

    class LockConnection:
        def cursor(self):
            return LockCursor()

        def close(self):
            lock_state["connection_closes"] += 1

    lock_database = object.__new__(app.Database)
    lock_database.config = {"database": "consulting_requirement_manager_selftest"}
    lock_database.attachment_storage = "server"
    lock_connection = LockConnection()
    lock_database.connect = lambda: lock_connection
    held_connection = lock_database.acquire_attachment_maintenance_lock(timeout=7)
    lock_database.release_attachment_maintenance_lock(held_connection)
    assert_true(held_connection is lock_connection and len(lock_calls) == 2 and
                "GET_LOCK" in lock_calls[0][0] and "RELEASE_LOCK" in lock_calls[1][0] and
                lock_calls[0][1][0] == lock_calls[1][1][0] and lock_calls[0][1][1] == 7 and
                lock_state == {"fetches": 2, "cursor_closes": 2, "connection_closes": 1},
                "MySQL 附件维护锁获取或释放契约错误")

    assert_true(len({app.new_event_id() for _ in range(1000)}) == 1000, "审计事件 ID 出现重复")
    with tempfile.TemporaryDirectory(prefix="crm-attachment-selftest-") as temp_dir:
        database = object.__new__(app.Database)
        database.attachments_dir = Path(temp_dir) / "attachments"
        database.attachments_dir.mkdir()
        database.attachment_storage = "server"
        source = database.attachments_dir / "source.txt"
        source.write_text("attachment", encoding="utf-8")
        stored = database.store_attachment(source, "ART-SELFTEST", lock_held=True)
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
            "attachment_storage": "server", "attachments_dir": str(Path(temp_dir) / "shared" / "attachments"),
        }
        db.config_path.write_text(json.dumps(config), encoding="utf-8")
        old_value = os.environ.get("CRM_SELFTEST_DB_PASSWORD")
        os.environ["CRM_SELFTEST_DB_PASSWORD"] = "environment-secret"
        try:
            loaded = db.load_config()
            assert_true(loaded["password"] == "environment-secret", "数据库密码环境变量未生效")
            config["create_database"] = "false"
            db.config_path.write_text(json.dumps(config), encoding="utf-8")
            try:
                db.load_config()
                raise AssertionError("字符串 false 被错误接受为布尔配置")
            except RuntimeError as exc:
                assert_true("JSON 布尔值" in str(exc), "布尔配置错误提示不明确")
        finally:
            if old_value is None:
                os.environ.pop("CRM_SELFTEST_DB_PASSWORD", None)
            else:
                os.environ["CRM_SELFTEST_DB_PASSWORD"] = old_value
    with tempfile.TemporaryDirectory(prefix="crm-integration-guard-") as temp_dir:
        config_dir = Path(temp_dir)
        config_path = config_dir / app.MYSQL_CONFIG_FILE
        old_config_dir = os.environ.get("CRM_CONFIG_DIR")
        old_expected = os.environ.get("CRM_INTEGRATION_TEST_DATABASE")
        try:
            os.environ["CRM_CONFIG_DIR"] = str(config_dir)
            config_path.write_text(json.dumps({"database": "consulting_production"}), encoding="utf-8")
            os.environ["CRM_INTEGRATION_TEST_DATABASE"] = "consulting_production"
            try:
                require_integration_test_database()
                raise AssertionError("疑似生产库名称未被在线自测安全门禁拒绝")
            except RuntimeError as exc:
                assert_true("test 或 selftest" in str(exc), "在线自测生产库拒绝原因不明确")
            config_path.write_text(json.dumps({"database": "consulting_selftest"}), encoding="utf-8")
            os.environ["CRM_INTEGRATION_TEST_DATABASE"] = "consulting_selftest"
            assert_true(require_integration_test_database() == "consulting_selftest", "显式测试库被错误拒绝")
        finally:
            if old_config_dir is None:
                os.environ.pop("CRM_CONFIG_DIR", None)
            else:
                os.environ["CRM_CONFIG_DIR"] = old_config_dir
            if old_expected is None:
                os.environ.pop("CRM_INTEGRATION_TEST_DATABASE", None)
            else:
                os.environ["CRM_INTEGRATION_TEST_DATABASE"] = old_expected


def integration_transaction_checks(db):
    stamp = app.datetime.now().strftime("%Y%m%d%H%M%S%f")
    t = app.now_text()
    project_id = plan_id = version_a = version_b = None
    requirement_id = parent_id = unplanned_id = change_id = funding_id = operation_id = None
    conflict_id = None
    concurrent_requirement_ids = []
    normalization_requirement_ids = []
    artifact_ids = []
    change_ids = []
    try:
        project_id = db.execute("""INSERT INTO planning_projects(project_code, project_name, total_budget, created_at, updated_at)
                                   VALUES(?,?,?,?,?)""", (f"PRJ-ST-{stamp}", "集成自测项目", 100000, t, t)).lastrowid
        plan_id = db.execute("""INSERT INTO annual_plans(project_id, plan_year, plan_name, annual_budget, created_at, updated_at)
                                VALUES(?,?,?,?,?,?)""", (project_id, 2026, "集成自测年度", 80000, t, t)).lastrowid
        version_a = db.execute("""INSERT INTO implementation_versions(project_id, annual_plan_id, version_code, version_name, version_budget, created_at, updated_at)
                                  VALUES(?,?,?,?,?,?,?)""", (project_id, plan_id, f"STA-{stamp[-8:]}", "自测版本A", 40000, t, t)).lastrowid
        version_b = db.execute("""INSERT INTO implementation_versions(project_id, annual_plan_id, version_code, version_name, version_budget, created_at, updated_at)
                                  VALUES(?,?,?,?,?,?,?)""", (project_id, plan_id, f"STB-{stamp[-8:]}", "自测版本B", 40000, t, t)).lastrowid
        for table in ["version_baseline_artifacts", "funding_applications", "operation_records"]:
            db.one(f"SELECT COUNT(*) c FROM {table}")
        assert_true(db.one("SHOW COLUMNS FROM planning_projects LIKE 'current_stage'"), "项目阶段迁移字段缺失")
        assert_true(db.one("SHOW COLUMNS FROM requirements LIKE 'parent_requirement_id'"), "原需求关联字段缺失")
        assert_true(db.one("SHOW COLUMNS FROM artifacts LIKE 'approval_status'"), "成果物审批字段缺失")
        assert_true(db.one("SHOW COLUMNS FROM change_requests LIKE 'pending_requirement_id'"), "待审批唯一生成列缺失")
        assert_true(db.one("SHOW INDEX FROM change_requests WHERE Key_name='uk_change_pending_requirement'"),
                    "同一需求待审批唯一索引缺失")
        db.update_project_stage(project_id, "建设落地", "集成自测", app.now_text())
        assert_true(db.one("SELECT current_stage FROM planning_projects WHERE id=?", (project_id,))["current_stage"] == "建设落地",
                    "项目阶段事务未生效")
        try:
            db.update_project_stage(project_id, "不存在阶段", "集成自测", app.now_text())
            raise AssertionError("非法项目阶段未被拒绝")
        except ValueError:
            pass
        try:
            db.create_annual_plan({
                "project_id": project_id, "plan_year": 2027, "plan_name": "超额年度",
                "annual_budget": 20001, "business_pain_points": "", "plan_description": "",
            }, "集成自测", app.now_text())
            raise AssertionError("年度累计预算超出项目总预算未被拒绝")
        except ValueError as exc:
            assert_true("累计预算" in str(exc), "年度累计预算错误提示异常")
        try:
            db.create_implementation_version({
                "project_id": project_id, "annual_plan_id": plan_id,
                "version_code": f"STX-{stamp[-8:]}", "version_name": "超额版本",
                "version_goal": "", "version_scope": "", "version_budget": 1,
                "planned_start_date": "", "planned_end_date": "",
            }, "集成自测", app.now_text())
            raise AssertionError("版本累计预算超出年度预算未被拒绝")
        except ValueError as exc:
            assert_true("累计预算" in str(exc), "版本累计预算错误提示异常")
        record = {
            "requirement_code": f"REQ-ST-{stamp}", "requirement_name": "事务集成自测需求",
            "requirement_description": "验证远程需求事务、冻结锁和变更申请。", "source_role": "咨询负责人",
            "proposer_name": "自测", "owner_name": "自测", "project_id": project_id, "annual_plan_id": plan_id,
            "version_id": version_a, "requirement_type": "功能优化", "tags": "selftest", "priority": "P1",
            "status": "草稿", "estimated_budget": 1000, "allocated_budget": 0, "actual_cost": 0,
            "estimated_hours": 16, "planned_finish_date": "2026-12-31",
            "remark": "原始业务备注", "created_at": t, "updated_at": t,
        }
        normalized_record = {
            **record, "requirement_code": f"REQ-ST-NORM-A-{stamp}",
            "requirement_name": "业务标识规范化 A", "business_key": "foo  bar",
            "parent_requirement_id": None,
        }
        normalized_id = db.create_requirement(normalized_record, "集成自测", app.now_text())
        normalization_requirement_ids.append(normalized_id)
        assert_true(db.one("SELECT business_key FROM requirements WHERE id=?", (normalized_id,))["business_key"] == "foo bar",
                    "新建需求未持久化规范业务标识")
        try:
            db.create_requirement({
                **normalized_record, "requirement_code": f"REQ-ST-NORM-B-{stamp}",
                "requirement_name": "业务标识规范化 B", "business_key": " Foo Bar ",
            }, "集成自测", app.now_text())
            raise AssertionError("foo  bar 与 Foo Bar 被允许写入同一版本")
        except ValueError as exc:
            assert_true("业务需求标识不能重复" in str(exc), "规范业务标识冲突提示异常")

        db.execute("UPDATE requirements SET business_key=? WHERE id=?", (" Foo   Bar ", normalized_id))
        try:
            db.healthcheck()
            raise AssertionError("健康检查未发现未规范化业务标识")
        except RuntimeError as exc:
            assert_true("业务标识未规范化" in str(exc), "未规范化业务标识健康检查提示异常")
        db.init_schema()
        assert_true(db.one("SELECT business_key FROM requirements WHERE id=?", (normalized_id,))["business_key"] == "foo bar",
                    "旧业务标识规范差异未被迁移")

        migration_conflict = db.create_requirement({
            **normalized_record, "requirement_code": f"REQ-ST-NORM-C-{stamp}",
            "requirement_name": "迁移冲突占位需求", "business_key": "migration-placeholder",
        }, "集成自测", app.now_text())
        normalization_requirement_ids.append(migration_conflict)
        db.execute("UPDATE requirements SET business_key=? WHERE id=?", ("foo  bar", normalized_id))
        db.execute("UPDATE requirements SET business_key=? WHERE id=?", (" Foo Bar ", migration_conflict))
        try:
            db.healthcheck()
            raise AssertionError("健康检查未发现同版本规范业务标识冲突")
        except RuntimeError as exc:
            assert_true("同版本业务标识冲突" in str(exc), "同版本规范冲突健康检查提示异常")
        try:
            db.init_schema()
            raise AssertionError("迁移静默覆盖了同版本规范业务标识冲突")
        except RuntimeError as exc:
            assert_true("规范化后存在同版本业务标识冲突" in str(exc), "迁移冲突提示异常")
        unchanged = db.query("SELECT id, business_key FROM requirements WHERE id IN (?,?) ORDER BY id",
                             (normalized_id, migration_conflict))
        assert_true([row["business_key"] for row in unchanged] == ["foo  bar", " Foo Bar "],
                    "迁移冲突失败后旧业务标识被部分覆盖")
        db.execute("UPDATE requirements SET business_key='foo bar' WHERE id=?", (normalized_id,))
        db.execute("UPDATE requirements SET business_key='migration-placeholder' WHERE id=?", (migration_conflict,))

        parent_record = {
            **record, "requirement_code": f"REQ-ST-P-{stamp}", "requirement_name": "集成自测原需求",
            "requirement_description": "用于验证原需求关系和循环保护。", "estimated_hours": 8,
        }
        parent_id = db.create_requirement(parent_record, "集成自测", t)
        record["parent_requirement_id"] = parent_id
        requirement_id = db.create_requirement(record, "集成自测", t)
        assert_true(db.transition_requirement_status(requirement_id, "草稿", "规划中", "进入规划", "集成自测", app.now_text()),
                    "需求状态流转未生效")
        transitioned = db.one("SELECT status, remark FROM requirements WHERE id=?", (requirement_id,))
        assert_true(transitioned["status"] == "规划中" and transitioned["remark"] == "原始业务备注",
                    "状态流转覆盖了需求业务备注")
        updated = {**record, "requirement_name": "事务集成自测需求-已更新", "estimated_budget": 1200}
        db.update_requirement(requirement_id, updated, "集成自测", app.now_text())
        try:
            db.update_requirement(parent_id, {**parent_record, "parent_requirement_id": requirement_id},
                                  "集成自测", app.now_text())
            raise AssertionError("原需求循环关系未被拒绝")
        except ValueError as exc:
            assert_true("循环" in str(exc), "原需求循环校验错误提示异常")

        conflict_record = {
            **record,
            "requirement_code": f"REQ-ST-DUP-{stamp}",
            "requirement_name": updated["requirement_name"],
            "business_key": updated["requirement_name"],
            "parent_requirement_id": None,
            "version_id": version_b,
        }
        conflict_id = db.create_requirement(conflict_record, "集成自测", app.now_text())
        db.execute("UPDATE requirements SET business_key=NULL WHERE id=?", (requirement_id,))
        db.execute("UPDATE requirements SET business_key='   ' WHERE id=?", (conflict_id,))
        try:
            db.assign_requirement(requirement_id, version_b, plan_id, "集成自测", app.now_text())
            raise AssertionError("目标版本已有相同业务标识时仍允许分配需求")
        except ValueError as exc:
            assert_true("同一版本内业务需求标识不能重复" in str(exc), "需求分配业务标识冲突提示异常")
        assert_true(db.one("SELECT version_id FROM requirements WHERE id=?", (requirement_id,))["version_id"] == version_a,
                    "业务标识冲突后需求所属版本发生变化")
        db.soft_delete_requirement(conflict_id, "集成自测", app.now_text())
        db.assign_requirement(requirement_id, version_b, plan_id, "集成自测", app.now_text())
        assert_true(db.one("SELECT version_id FROM requirements WHERE id=?", (requirement_id,))["version_id"] == version_b,
                    "需求事务分配未生效")

        concurrent_keys = [f" Assignment   Race {stamp} ", f"assignment race {stamp}"]
        concurrent_records = [
            {
                **record,
                "requirement_code": f"REQ-ST-RACE-A-{stamp}",
                "requirement_name": f"并发分配需求 {stamp}",
                "business_key": concurrent_keys[0],
                "parent_requirement_id": None,
                "version_id": version_a,
                "annual_plan_id": plan_id,
            },
            {
                **record,
                "requirement_code": f"REQ-ST-RACE-B-{stamp}",
                "requirement_name": f"并发分配需求 {stamp}",
                "business_key": concurrent_keys[1],
                "parent_requirement_id": None,
                "version_id": None,
                "annual_plan_id": None,
            },
        ]
        concurrent_requirement_ids.extend(
            db.create_requirement(item, "集成自测", app.now_text()) for item in concurrent_records
        )
        assignment_clients = []
        assignment_barrier = threading.Barrier(3)
        assignment_results = []
        assignment_result_lock = threading.Lock()

        def assign_from_client(index):
            try:
                assignment_barrier.wait(timeout=30)
                assignment_clients[index].assign_requirement(
                    concurrent_requirement_ids[index], version_b, plan_id, "集成自测", app.now_text()
                )
                result = (index, "success", "")
            except Exception as exc:
                result = (index, "error", f"{type(exc).__name__}: {exc}")
            with assignment_result_lock:
                assignment_results.append(result)

        assignment_workers = []
        try:
            assignment_clients.extend(app.Database(db.base_dir, data_dir=db.data_dir, config_dir=db.config_dir)
                                      for _ in range(2))
            assignment_workers = [threading.Thread(target=assign_from_client, args=(index,), daemon=True)
                                  for index in range(2)]
            for worker in assignment_workers:
                worker.start()
            assignment_barrier.wait(timeout=30)
            for worker in assignment_workers:
                worker.join(timeout=45)
            assert_true(not any(worker.is_alive() for worker in assignment_workers),
                        "双客户端需求分配并发测试超时")
        finally:
            for client in assignment_clients:
                client.close()
        assignment_successes = [result for result in assignment_results if result[1] == "success"]
        assignment_conflicts = [result for result in assignment_results
                                if result[1] == "error" and "同一版本内业务需求标识不能重复" in result[2]]
        assert_true(len(assignment_successes) == 1 and len(assignment_conflicts) == 1,
                    f"双客户端同业务标识需求分配未正确串行化：{assignment_results}")
        assigned_rows = db.query("SELECT id, version_id FROM requirements WHERE id IN (?,?)",
                                 tuple(concurrent_requirement_ids))
        assert_true(sum(row["version_id"] == version_b for row in assigned_rows) == 1,
                    "并发需求分配后目标版本存在重复业务需求")

        for index, amount in enumerate((0.1, 0.2), start=1):
            db.record_budget_flow(f"BF-ST-PRECISION-A{index}-{stamp}", project_id, plan_id, version_b,
                                  requirement_id, "已分配预算", amount, "小数分配", "集成自测", app.now_text())
            db.record_budget_flow(f"BF-ST-PRECISION-C{index}-{stamp}", project_id, plan_id, version_b,
                                  requirement_id, "实际消耗", amount, "小数消耗", "集成自测", app.now_text())
        precision_row = db.one("SELECT allocated_budget, actual_cost FROM requirements WHERE id=?", (requirement_id,))
        assert_true(float(precision_row["allocated_budget"]) == 0.3 and float(precision_row["actual_cost"]) == 0.3,
                    "MySQL 0.1 + 0.2 资金累计未规范为两位小数")
        try:
            db.record_budget_flow(f"BF-ST-PRECISION-OVER-{stamp}", project_id, plan_id, version_b,
                                  requirement_id, "实际消耗", 0.01, "真实超额", "集成自测", app.now_text())
            raise AssertionError("MySQL 真实超额实际消耗未被拒绝")
        except ValueError as exc:
            assert_true(str(exc) == "ACTUAL_OVERRUN", "MySQL 实际消耗超额提示异常")
        for index, amount in enumerate((-0.1, -0.2), start=1):
            db.record_budget_flow(f"BF-ST-PRECISION-R{index}-{stamp}", project_id, plan_id, version_b,
                                  requirement_id, "调整金额", amount, "负向归零", "集成自测", app.now_text())
        assert_true(float(db.one("SELECT allocated_budget FROM requirements WHERE id=?", (requirement_id,))["allocated_budget"]) == 0,
                    "MySQL 负向预算调整归零后残留浮点误差")

        clients = []
        barrier = threading.Barrier(3)
        concurrency_results = []
        result_lock = threading.Lock()

        def allocate_from_client(index):
            flow_code = f"BF-ST-C{index}-{stamp}"
            try:
                barrier.wait(timeout=30)
                clients[index].record_budget_flow(
                    flow_code, project_id, plan_id, version_b, requirement_id,
                    "已分配预算", 25000, "双客户端预算冲突自测", "集成自测", app.now_text(),
                )
                result = (flow_code, "success", "")
            except Exception as exc:
                result = (flow_code, "error", f"{type(exc).__name__}: {exc}")
            with result_lock:
                concurrency_results.append(result)

        workers = []
        try:
            clients.extend(app.Database(db.base_dir, data_dir=db.data_dir, config_dir=db.config_dir)
                           for _ in range(2))
            workers = [threading.Thread(target=allocate_from_client, args=(index,), daemon=True)
                       for index in range(2)]
            for worker in workers:
                worker.start()
            barrier.wait(timeout=30)
            for worker in workers:
                worker.join(timeout=45)
            assert_true(not any(worker.is_alive() for worker in workers), "双客户端预算并发测试超时")
        finally:
            for client in clients:
                client.close()
        successes = [result for result in concurrency_results if result[1] == "success"]
        conflicts = [result for result in concurrency_results if result[1] == "error" and "超过版本预算" in result[2]]
        assert_true(len(successes) == 1 and len(conflicts) == 1,
                    f"双客户端并发预算冲突未被正确串行化：{concurrency_results}")
        allocated = db.one("SELECT allocated_budget FROM requirements WHERE id=?", (requirement_id,))
        assert_true(float(allocated["allocated_budget"] or 0) == 25000,
                    "并发预算冲突后需求分配金额不正确")
        audit = db.one("""SELECT before_value, after_value FROM operation_logs
                          WHERE object_type='budget_flow' AND description LIKE ? ORDER BY id DESC LIMIT 1""",
                       (f"%{successes[0][0]}",))
        before_value = json.loads(audit["before_value"]) if audit else {}
        after_value = json.loads(audit["after_value"]) if audit else {}
        assert_true(before_value.get("allocated_budget") == 0 and
                    after_value.get("allocated_budget") == 25000,
                    "资金流水审计未记录分配预算前后值")

        baseline_artifact = {
            "artifact_code": f"ART-ST-B-{stamp}", "artifact_name": "baseline.txt", "artifact_type": "任务书方案",
            "file_path": f"ART-ST-B-{stamp}.txt", "file_ext": ".txt", "file_size": 8,
            "related_object_type": "版本", "related_object_id": version_b, "project_id": project_id,
            "version_no": "v1", "visibility": "客户可见", "description": "基线成果物",
            "uploaded_by": "集成自测", "uploaded_at": t, "created_at": t,
        }
        artifact_result = db.create_artifact_record(baseline_artifact, "集成自测", app.now_text())
        artifact_ids.append(artifact_result["artifact_id"])
        assert_true(artifact_result["approval_status"] == "approved", "未冻结版本成果物不应进入审批")

        funding_id = db.create_funding_application({
            "application_code": f"FUND-ST-{stamp}", "project_id": project_id, "annual_plan_id": plan_id,
            "amount": 10000, "applicant_name": "集成自测", "description": "资金状态链自测",
        }, "集成自测", app.now_text())
        assert_true(db.transition_funding_application(funding_id, "草稿", "已提交", "集成自测", app.now_text()),
                    "资金申报提交失败")
        assert_true(not db.transition_funding_application(funding_id, "草稿", "已提交", "集成自测", app.now_text()),
                    "过期资金申报状态仍被重复提交")
        for before, after in [("已提交", "审批中"), ("审批中", "已批复"), ("已批复", "已拨付")]:
            assert_true(db.transition_funding_application(funding_id, before, after, "集成自测", app.now_text()),
                        f"资金申报状态流转失败：{before}->{after}")
        assert_true(db.one("SELECT status FROM funding_applications WHERE id=?", (funding_id,))["status"] == "已拨付",
                    "资金申报未完成拨付闭环")

        invalid_operation = {
            "record_code": f"OPS-ST-X-{stamp}", "project_id": project_id, "version_id": version_a,
            "requirement_id": requirement_id, "record_type": "线上问题", "status": "待处理",
            "record_date": "2026-07-15", "owner_name": "集成自测", "description": "错误版本关联",
        }
        try:
            db.create_operation_record(invalid_operation, "集成自测", app.now_text())
            raise AssertionError("运营记录允许关联其他版本需求")
        except ValueError as exc:
            assert_true("所选版本" in str(exc), "运营记录版本校验错误提示异常")
        operation_id = db.create_operation_record({
            **invalid_operation, "record_code": f"OPS-ST-{stamp}", "version_id": version_b,
            "description": "有效运营服务记录",
        }, "集成自测", app.now_text())
        db.update_operation_record(operation_id, {
            "record_type": "维护记录", "status": "已完成", "record_date": "2026-07-16",
            "owner_name": "集成自测", "description": "问题已闭环", "result": "已恢复",
        }, "集成自测", app.now_text())
        assert_true(db.one("SELECT status FROM operation_records WHERE id=?", (operation_id,))["status"] == "已完成",
                    "运营服务记录更新未生效")

        db.freeze_version_with_baseline(version_b, "集成自测", app.now_text())
        baseline_item = db.one("""SELECT br.estimated_hours, br.actual_hours, br.requirement_description,
                                         br.parent_requirement_code, br.remark
                                  FROM version_baseline_requirements br
                                  INNER JOIN version_baselines b ON b.id=br.baseline_id
                                  WHERE b.version_id=? AND br.requirement_id=?""", (version_b, requirement_id))
        assert_true(float(baseline_item["estimated_hours"] or 0) == 16, "版本基线未固化工时")
        assert_true(baseline_item["requirement_description"] == record["requirement_description"] and
                    baseline_item["parent_requirement_code"] == parent_record["requirement_code"] and
                    baseline_item["remark"] == "原始业务备注", "版本基线未完整固化需求内容")
        baseline_artifact_row = db.one("""SELECT ba.artifact_code FROM version_baseline_artifacts ba
                                            INNER JOIN version_baselines b ON b.id=ba.baseline_id
                                            WHERE b.version_id=? AND ba.artifact_id=?""",
                                       (version_b, artifact_result["artifact_id"]))
        assert_true(baseline_artifact_row, "版本基线未固化已审批成果物")
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
        change_ids.append(change_id)
        assert_true(db.one("SELECT change_type FROM change_request_payloads WHERE change_request_id=?", (change_id,)),
                    "变更申请 payload 未原子提交")
        try:
            db.create_change_request_record(
                requirement_id, "delete", {},
                {"change_title": "重复待审批", "change_reason": "验证唯一约束", "impact_scope": "自测"},
                "集成自测", app.now_text(),
            )
            raise AssertionError("同一需求允许创建多个待审批变更")
        except ValueError as exc:
            assert_true("待审批变更" in str(exc), "重复待审批变更错误提示异常")
        db.review_change_request(change_id, "approved", "集成自测", app.now_text())

        pending_artifact = {
            **baseline_artifact, "artifact_code": f"ART-ST-P-{stamp}", "artifact_name": "acceptance.txt",
            "artifact_type": "验收报告", "file_path": f"ART-ST-P-{stamp}.txt", "version_no": "v2",
        }
        pending_result = db.create_artifact_record(pending_artifact, "集成自测", app.now_text())
        artifact_ids.append(pending_result["artifact_id"])
        change_ids.append(pending_result["change_id"])
        assert_true(pending_result["approval_status"] == "pending" and pending_result["change_id"],
                    "冻结版本新增成果物未进入变更审批")
        db.review_change_request(pending_result["change_id"], "approved", "集成自测", app.now_text())
        assert_true(db.one("SELECT approval_status FROM artifacts WHERE id=?", (pending_result["artifact_id"],))["approval_status"] == "approved",
                    "成果物变更审批未生效")

        unplanned = {**record, "requirement_code": f"REQ-ST-U-{stamp}", "version_id": None, "annual_plan_id": None}
        unplanned_id = db.create_requirement(unplanned, "集成自测", app.now_text())
        db.soft_delete_requirement(unplanned_id, "集成自测", app.now_text())
        assert_true(db.one("SELECT is_deleted FROM requirements WHERE id=?", (unplanned_id,))["is_deleted"] == 1,
                    "需求软删除事务未生效")

        artifact = {
            "artifact_code": f"ART-ST-{stamp}", "artifact_name": "selftest.txt", "artifact_type": "其他",
            "file_path": f"ART-ST-{stamp}.txt", "file_ext": ".txt", "file_size": 8,
            "related_object_type": "项目", "related_object_id": project_id, "version_no": "v1",
            "project_id": project_id, "visibility": "内部", "description": "事务自测",
            "uploaded_by": "集成自测", "uploaded_at": t, "created_at": t,
        }
        project_artifact_result = db.create_artifact_record(artifact, "集成自测", app.now_text())
        artifact_ids.append(project_artifact_result["artifact_id"])
        invalid = {**artifact, "artifact_code": f"ART-ST-X-{stamp}", "related_object_id": -1}
        try:
            db.create_artifact_record(invalid, "集成自测", app.now_text())
            raise AssertionError("不存在的成果物挂载对象未被拒绝")
        except ValueError:
            pass
        invalid_target = {
            **baseline_artifact, "artifact_code": f"ART-ST-T-{stamp}", "artifact_type": "可研报告",
        }
        try:
            db.create_artifact_record(invalid_target, "集成自测", app.now_text())
            raise AssertionError("成果物类型允许挂载到错误对象")
        except ValueError:
            pass
        dangerous = {**artifact, "artifact_code": f"ART-ST-D-{stamp}", "file_ext": ".exe"}
        try:
            db.create_artifact_record(dangerous, "集成自测", app.now_text())
            raise AssertionError("危险附件扩展名未被数据库事务拒绝")
        except ValueError:
            pass
    finally:
        for version_id in [version_a, version_b]:
            if version_id:
                baselines = db.query("SELECT id FROM version_baselines WHERE version_id=?", (version_id,))
                for baseline in baselines:
                    db.execute("DELETE FROM version_baseline_artifacts WHERE baseline_id=?", (baseline["id"],))
                    db.execute("DELETE FROM version_baseline_requirements WHERE baseline_id=?", (baseline["id"],))
                db.execute("DELETE FROM version_baselines WHERE version_id=?", (version_id,))
        for pending_change_id in dict.fromkeys(change_ids):
            if pending_change_id:
                db.execute("DELETE FROM change_request_payloads WHERE change_request_id=?", (pending_change_id,))
                db.execute("DELETE FROM change_requests WHERE id=?", (pending_change_id,))
        if operation_id:
            db.execute("DELETE FROM operation_records WHERE id=?", (operation_id,))
        if funding_id:
            db.execute("DELETE FROM funding_applications WHERE id=?", (funding_id,))
        for stored_artifact_id in artifact_ids:
            db.execute("DELETE FROM artifacts WHERE id=?", (stored_artifact_id,))
        if project_id:
            db.execute("DELETE FROM budget_flows WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM operation_records WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM funding_applications WHERE project_id=?", (project_id,))
        for req_id in [requirement_id, unplanned_id, parent_id, conflict_id,
                       *concurrent_requirement_ids, *normalization_requirement_ids]:
            if req_id:
                db.execute("DELETE FROM requirement_status_history WHERE requirement_id=?", (req_id,))
                db.execute("DELETE FROM task_effort_entries WHERE requirement_id=?", (req_id,))
                db.execute("DELETE FROM requirements WHERE id=?", (req_id,))
        for version_id in [version_a, version_b]:
            if version_id:
                db.execute("DELETE FROM implementation_versions WHERE id=?", (version_id,))
        if plan_id:
            db.execute("DELETE FROM annual_plans WHERE id=?", (plan_id,))
        if project_id:
            db.execute("DELETE FROM planning_projects WHERE id=?", (project_id,))
        db.execute("DELETE FROM operation_logs WHERE operator_name='集成自测' AND operation_time>=?", (t,))


def require_integration_test_database():
    config_dir = Path(os.environ.get("CRM_CONFIG_DIR") or (app.app_base_dir() / "config"))
    config_path = config_dir / app.MYSQL_CONFIG_FILE
    if not config_path.is_file():
        raise RuntimeError(f"在线自测配置不存在：{config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    database = str(config.get("database", "")).strip()
    expected = os.environ.get("CRM_INTEGRATION_TEST_DATABASE", "").strip()
    if not expected or database != expected:
        raise RuntimeError(
            "在线自测仅允许显式测试库：CRM_INTEGRATION_TEST_DATABASE 必须与配置文件 database 完全一致。"
        )
    if not any(token in database.lower() for token in ("test", "selftest")):
        raise RuntimeError("在线自测数据库名称必须包含 test 或 selftest，拒绝连接疑似生产库。")
    return database


def integration_user_session_checks(db):
    username = f"session-selftest-{app.new_event_id()[:12]}"
    user_id = None
    t = app.now_text()
    try:
        initial_password_hash = app.hash_password("Session-Selftest-2026")
        user_id = db.execute(
            """INSERT INTO users(username, display_name, password_hash, role_name, is_active, created_at, updated_at)
               VALUES(?,?,?,?,1,?,?)""",
            (username, "会话自测用户", initial_password_hash, "销售", t, t),
        ).lastrowid
        first_token = app.new_session_token()
        second_token = app.new_session_token()
        assert_true(db.claim_user_session(user_id, first_token, t, initial_password_hash, "销售"), "首次用户会话占用失败")
        assert_true(db.one("SELECT session_token FROM users WHERE id=?", (user_id,))["session_token"] == first_token,
                    "首次用户会话令牌未保存")
        assert_true(db.claim_user_session(user_id, second_token, app.now_text(), initial_password_hash, "销售"), "第二客户端登录失败")
        assert_true(not db.release_user_session(user_id, first_token) and
                    db.one("SELECT session_token FROM users WHERE id=?", (user_id,))["session_token"] == second_token,
                    "旧客户端退出错误清除了新客户端会话")
        reset_password_hash = app.hash_password("Reset-Session-Selftest-2026")
        db.execute("UPDATE users SET password_hash=?, session_token=NULL, session_started_at=NULL WHERE id=?",
                   (reset_password_hash, user_id))
        assert_true(not db.claim_user_session(
                        user_id, app.new_session_token(), app.now_text(), initial_password_hash, "销售"
                    ) and db.one("SELECT session_token FROM users WHERE id=?", (user_id,))["session_token"] is None,
                    "密码重置后旧密码认证快照重新占用了会话")
        assert_true(db.claim_user_session(
                        user_id, app.new_session_token(), app.now_text(), reset_password_hash, "销售"
                    ), "重置后的新密码认证快照无法占用会话")
        old_role = db.update_user_role(user_id, "客户", "集成自测", app.now_text())
        updated = db.one("SELECT role_name, session_token FROM users WHERE id=?", (user_id,))
        assert_true(old_role == "销售" and updated["role_name"] == "客户" and updated["session_token"] is None,
                    "角色调整未更新角色或未使旧会话下线")
        assert_true(not db.claim_user_session(
                        user_id, app.new_session_token(), app.now_text(), reset_password_hash, "销售"
                    ), "角色调整后旧角色认证快照重新占用了会话")
    finally:
        if user_id:
            db.execute("DELETE FROM user_project_access WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM operation_logs WHERE object_type='user' AND object_id=?", (user_id,))
            db.execute("DELETE FROM users WHERE id=?", (user_id,))


def main():
    offline_checks()
    if os.environ.get("CRM_MYSQL_INTEGRATION_SELFTEST") != "1":
        print("offline selftest ok")
        return
    print(f"integration database guard ok: {require_integration_test_database()}")
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

        root.show_projects()
        for theme_name, palette in app.THEME_PALETTES.items():
            root.theme_name.set(theme_name)
            root.apply_theme()
            root.update_idletasks()
            root.update()
            assert_true(root.colors == palette and root.current_page == "项目管理",
                        f"远程主题 {theme_name} 未完整应用或切换后丢失当前页面")
        persisted_theme = next(reversed(app.THEME_PALETTES))
        assert_true(root.load_theme_preference() == persisted_theme, "远程主题未按登录账号持久化")
        root.theme_name.set(app.DEFAULT_THEME)
        root.apply_theme(persist=False)

        integration_user_session_checks(root.db)
        integration_transaction_checks(root.db)

        root.geometry("1100x680")
        root.identity_label.configure(text=app.topbar_identity_text("超长显示名称" * 20, root.current_role.get()))
        root.show_dashboard()
        root.update_idletasks()
        root.update()
        visible_nav = [button for button in root.nav_buttons.values() if button.winfo_manager()]
        window_bottom = root.winfo_rooty() + root.winfo_height()
        assert_true(visible_nav and max(button.winfo_rooty() + button.winfo_height() for button in visible_nav) <= window_bottom,
                    "最小窗口高度下侧栏导航被裁切")
        window_right = root.winfo_rootx() + root.winfo_width()
        assert_true(root.theme_box.winfo_ismapped() and
                    root.theme_box.winfo_rootx() + root.theme_box.winfo_width() <= window_right,
                    "超长显示名称导致最小窗口宽度下主题控件被裁切")
        root.show_requirements()
        root.update_idletasks()
        root.update()
        assert_true(len(root.requirement_action_rows) == 2, "需求操作区未拆分为两行")
        for action_row in root.requirement_action_rows:
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
        canvas_bounds = root.budget_flow_canvas.bbox("all")
        assert_true(canvas_bounds is not None and canvas_bounds[0] >= 0 and canvas_bounds[2] <= root.budget_flow_canvas.winfo_width(),
                    f"最小窗口宽度下资金全景图横向越界：{canvas_bounds} / "
                    f"{root.budget_flow_canvas.winfo_width()}x{root.budget_flow_canvas.winfo_height()}")
        root.show_settings()
        root.update_idletasks()
        root.update()
        assert_true(len(root.user_action_rows) == 2 and
                    root.user_action_rows[0].winfo_y() < root.user_action_rows[1].winfo_y(),
                    "用户与角色操作按钮未拆分为两行")
        for action_row in root.user_action_rows:
            for button in action_row.winfo_children():
                assert_true(button.winfo_x() + button.winfo_width() <= action_row.winfo_width() + 1,
                            "最小窗口宽度下用户管理按钮被裁切")

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

        log_count = root.db.one("SELECT COUNT(*) c FROM operation_logs")["c"]
        assert_true(log_count >= 1, "操作日志未初始化")
        correlated = root.db.one("SELECT event_id, result FROM operation_logs WHERE event_id IS NOT NULL ORDER BY id DESC LIMIT 1")
        assert_true(correlated and correlated["result"] in {"success", "failed", "denied"}, "数据库审计事件字段缺失")
        root.operation_log_type_filter.set("system")
        assert_true(all(row["object_type"] == "system" for row in root.filtered_operation_logs(limit=20)), "操作日志类型过滤失败")
        root.operation_log_type_filter.set("全部")

        for table in ["requirement_status_history", "version_baselines", "version_baseline_requirements",
                      "version_baseline_artifacts", "funding_applications", "operation_records", "user_project_access",
                      "task_effort_entries", "tag_definitions", "dashboard_preferences"]:
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
