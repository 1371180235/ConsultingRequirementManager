# 版本变更记录

## v1.1.0 - 2026-06-08

### 新增

- 新增 `ConsultingRequirementManagerSetup.exe` 安装器。
- 双击安装器后可选择安装路径。
- 安装器会把主程序复制到目标目录，并创建 `data`、`attachments`、`backups`、`exports` 等运行数据目录。
- 安装完成后可选择立即启动主程序。
- 源码新增 `installer.py` 和 `ConsultingRequirementManagerSetup.spec`。

### 调整

- `exe` 目录调整为只保留安装器，便于用户直接双击安装。
- `source` 目录保留源码、README、打包配置和 Git 仓库。
- README 增加安装器打包说明。

### 说明

- 主程序仍为 `ConsultingRequirementManager.exe`，由安装器复制到用户选择的安装目录。
- 运行数据默认保存在安装目录下的 `data` 文件夹。

## v1.0.0 - 2026-06-08

### 新增

- 依据需求文档生成 Windows 桌面 MVP。
- 支持本地 SQLite 数据库首次启动初始化。
- 支持项目、年度计划、落地版本、需求任务、资金流水、成果物管理。
- 支持需求状态流转、角色视图、全局搜索、CSV 导出、本地备份恢复和操作日志。
- 生成单文件主程序 `ConsultingRequirementManager.exe`。
