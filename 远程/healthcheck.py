import json

import app


def main():
    database = None
    try:
        database = app.Database(app.app_base_dir())
        details = database.healthcheck()
        database.log("系统", "healthcheck", None, "check", "", "success", "MySQL 远程版部署健康检查通过")
        result = {
            "status": "ok",
            "variant": app.APP_VARIANT,
            "version": app.APP_VERSION,
            "host": app.HOST_NAME,
            **details,
            "logs": [str(database.logs_dir / name) for name in ["runtime.log", "error.log", "audit.log"]],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        app.LOGGER.exception("healthcheck_failed variant=mysql")
        if database is not None:
            try:
                database.log("系统", "healthcheck", None, "check", "", "failed",
                             "MySQL 远程版部署健康检查失败", result="failed")
            except Exception:
                app.audit_event("系统", "healthcheck", None, "check", "MySQL 远程版部署健康检查失败", result="failed")
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    finally:
        if database is not None:
            database.close()
        app.close_logging()


if __name__ == "__main__":
    raise SystemExit(main())
