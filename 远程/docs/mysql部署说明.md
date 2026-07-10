# MySQL 远程版 Linux 部署说明

本文适用于 Linux MySQL 8.x 服务器和 Windows/Linux 桌面客户端。远程版是桌面程序，不是部署在 Linux 上的 Web 服务；Linux 主要承载 MySQL，以及可选的共享附件目录。

## 1. Linux 安装 MySQL

以 Ubuntu/Debian 为例：

```bash
sudo apt update
sudo apt install mysql-server
sudo systemctl enable --now mysql
sudo mysql_secure_installation
```

确认字符集使用 `utf8mb4`，服务器时区与业务要求一致。3306 只允许应用网段或 VPN，不对公网开放。

## 2. 创建数据库和最小权限账号

使用数据库管理员账号执行：

```sql
CREATE DATABASE IF NOT EXISTS consulting_requirement_manager
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'crm_user'@'10.0.%'
  IDENTIFIED BY '替换为随机强密码';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE
  ON consulting_requirement_manager.* TO 'crm_user'@'10.0.%';
FLUSH PRIVILEGES;
```

把 `10.0.%` 改为真实客户端网段或固定来源。不要使用 root，不要默认使用 `'%'`。当前程序启动时执行 `CREATE TABLE IF NOT EXISTS`，因此账号需要 `CREATE`；不需要 `DROP`、`GRANT OPTION` 或全库权限。

## 3. 网络和 TLS

在 MySQL 中绑定内网地址，并用主机防火墙限制来源。生产环境配置服务端证书后，把 CA 文件安全分发到客户端。

客户端配置：

```json
{
  "host": "10.0.0.10",
  "port": 3306,
  "ssl_ca": "C:/secure/mysql-ca.pem"
}
```

配置 `ssl_ca` 后，客户端会启用证书和主机身份校验。证书中的主机名必须与 `host` 匹配。无法立即完成 TLS 时只能在隔离内网短期试运行，不应作为公网补偿措施。

## 4. 客户端安装

```powershell
cd 远程
python -m pip install -r requirements.txt
Copy-Item config\mysql_config.example.json config\mysql_config.json
```

编辑 `config/mysql_config.json`：

```json
{
  "host": "10.0.0.10",
  "port": 3306,
  "user": "crm_user",
  "password": "",
  "password_env": "CRM_DB_PASSWORD",
  "database": "consulting_requirement_manager",
  "create_database": false,
  "seed_demo_data": false,
  "connect_timeout": 10,
  "ssl_ca": "C:/secure/mysql-ca.pem",
  "attachment_storage": "oss",
  "attachments_dir": "",
  "oss_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
  "oss_bucket": "your-private-bucket",
  "oss_prefix": "consulting-requirement-manager"
}
```

真实配置 `config/mysql_config.json` 被 Git 忽略。推荐不在其中填写密码。

PowerShell 当前会话设置密码：

```powershell
$env:CRM_DB_PASSWORD = "数据库强密码"
$env:CRM_INITIAL_ADMIN_PASSWORD = "首次管理员强密码"
python app.py
```

首次管理员成功初始化并登录后，移除 `CRM_INITIAL_ADMIN_PASSWORD`。生产终端可使用受控的凭据管理器或服务启动环境注入，不要写入共享脚本和 Git。

## 5. 附件存储二选一

### 5.1 Linux/文件服务器目录

```json
{
  "attachment_storage": "server",
  "attachments_dir": "/srv/consulting-requirement-manager/attachments"
}
```

只有应用本身也运行在可访问该路径的 Linux 桌面时，才能直接使用 `/srv/...`。如果桌面客户端运行在 Windows，应在 Linux 服务器配置 SMB/NFS，共享并挂载目录，然后把 `attachments_dir` 写成客户端实际可访问的 UNC/挂载路径。数据库服务器上的路径不会通过 MySQL 自动共享。

Linux 目录示例：

```bash
sudo install -d -m 2770 -o crmfiles -g crmfiles /srv/consulting-requirement-manager/attachments
```

共享账号只授予该目录读写权限。多客户端必须使用同一共享目录，并保证路径稳定。

### 5.2 阿里云 OSS

```json
{
  "attachment_storage": "oss",
  "oss_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
  "oss_bucket": "your-private-bucket",
  "oss_prefix": "consulting-requirement-manager"
}
```

设置凭据：

```powershell
$env:OSS_ACCESS_KEY_ID = "..."
$env:OSS_ACCESS_KEY_SECRET = "..."
$env:OSS_SECURITY_TOKEN = "..."  # 仅 STS 临时凭据需要
```

Bucket 使用私有读写，RAM 权限限制到指定 Bucket/前缀。启用版本控制、服务端加密、访问日志和生命周期。优先使用 STS 临时凭据，避免长期 AccessKey 分发到每台客户端。

## 6. 启动与检查

离线检查不连接 MySQL：

```powershell
$env:CRM_OFFLINE_SELFTEST = "1"
python selftest.py
Remove-Item Env:CRM_OFFLINE_SELFTEST
```

连接测试库后的全页面检查：

```powershell
python selftest.py
python app.py
```

必须在测试数据库执行，不要用生产数据库跑开发性测试。

## 7. 数据库备份与恢复

Linux 定时备份示例：

```bash
mysqldump --single-transaction --routines --triggers \
  -u backup_user -p consulting_requirement_manager \
  | gzip > /backup/crm_$(date +%F_%H%M%S).sql.gz
```

恢复到新测试库：

```bash
gunzip -c /backup/crm_2026-07-10_020000.sql.gz \
  | mysql -u restore_user -p consulting_requirement_manager_restore
```

服务器目录附件使用快照、`rsync` 或备份系统；OSS 使用版本控制和跨区域复制。应用内 ZIP 仅处理服务器目录附件，不包含 MySQL 数据，也不会批量备份 OSS。

## 8. 生产核对

- `create_database=false`、`seed_demo_data=false`。
- 数据库账号非 root，来源主机受限。
- 3306 仅内网/VPN可达，TLS CA 校验通过。
- 数据库密码和 OSS 凭据未写入仓库或备份 ZIP。
- 首个管理员已改密，角色账号权限已逐一核验。
- 两客户端并发预算、冻结和审批测试通过。
- MySQL 和附件联合恢复演练通过。
- 磁盘、连接数、慢查询、备份失败和 OSS 异常已有监控告警。

更完整的业务与验收边界见 `最终需求与部署验收文档.md` 和仓库根目录 `最终需求与部署说明.md`。
