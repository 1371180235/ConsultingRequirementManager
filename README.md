# 咨询项目全流程需求管理系统 MVP

这是依据 `consulting_project_requirement_management.md` 生成的 Windows 桌面 EXE 首版实现。

## 已实现

- 本地 SQLite 数据库，首次启动自动创建 `data/app.db`
- 默认管理员账号与角色视图切换
- 项目、年度计划、落地版本、需求任务管理
- 需求状态流转与操作日志
- 版本隔离下的需求列表和资金统计
- 四级资金概览与资金流水登记
- 成果物本地文件挂载，文件复制到 `data/attachments`
- 全局搜索
- 需求、资金、成果物 CSV 导出
- 本地 ZIP 备份和恢复

## 开发运行

```powershell
python app.py
```

## 打包主程序

```powershell
pyinstaller --noconfirm --onefile --windowed --name ConsultingRequirementManager app.py
```

生成文件位于 `dist/ConsultingRequirementManager.exe`。

## 打包安装器

先打包主程序，然后把主程序作为资源打进安装器：

```powershell
pyinstaller --noconfirm --clean --onefile --windowed --name ConsultingRequirementManager app.py
pyinstaller --noconfirm --clean --onefile --windowed --name ConsultingRequirementManagerSetup --add-binary "dist\ConsultingRequirementManager.exe;." installer.py
```

生成的 `dist/ConsultingRequirementManagerSetup.exe` 可双击运行，并选择安装路径。
