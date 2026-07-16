# MySQL 8.x LTS 远程版 Linux 部署说明

适用版本：应用 v1.6.3；数据库 MySQL 8.x（推荐 8.4 LTS）

基线日期：2026-07-16

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

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER
  ON consulting_requirement_manager.* TO 'crm_user'@'10.0.%';
FLUSH PRIVILEGES;
```

把 `10.0.%` 改为真实客户端网段或固定来源。不要使用 root，不要默认使用 `'%'`。当前程序启动时执行 `CREATE TABLE IF NOT EXISTS`，因此账号需要 `CREATE`。1.6.3-mysql 从旧库升级时会在 MySQL `GET_LOCK` 保护下补充兼容字段和索引，需要一次性 `ALTER`；测试库升级验证完成后执行 `REVOKE ALTER ON consulting_requirement_manager.* FROM 'crm_user'@'10.0.%';`。不需要 `DROP`、`GRANT OPTION` 或全库权限。

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

远程版要求 `mysql-connector-python>=9.2.0`，旧版 Connector 不支持当前读写超时参数。部署机应使用锁定后的依赖清单构建并保存安装制品。

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
  "read_timeout": 30,
  "write_timeout": 30,
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
$env:CRM_DATA_DIR = "D:\ConsultingRequirementManagerData"
$env:CRM_CONFIG_DIR = "D:\ConsultingRequirementManagerConfig"
$env:CRM_LOG_LEVEL = "INFO"
$env:CRM_LOG_MAX_BYTES = "10485760"
$env:CRM_LOG_BACKUP_COUNT = "30"
$env:CRM_REQUIRE_TLS = "1"
python app.py
```

首次管理员成功初始化并登录后，移除 `CRM_INITIAL_ADMIN_PASSWORD`。生产终端可使用受控的凭据管理器或服务启动环境注入，不要写入共享脚本和 Git。

`CRM_DATA_DIR` 应指向业务用户可写、其他普通用户不可访问的目录；应用日志、导出和客户端备份均位于其中。`CRM_CONFIG_DIR` 保存 `mysql_config.json`，应单独限制读取权限。

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

共享账号只授予该目录读写权限。目录必须为专用两级子目录，不能使用磁盘根、共享根或用户主目录。多客户端必须访问同一共享目录；新上传文件以相对对象键入库，因此允许各客户端使用不同挂载盘符或挂载点。

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

Endpoint 必须使用 HTTPS。Bucket 使用私有读写，RAM 权限限制到指定 Bucket/前缀，应用也会拒绝当前 `oss_prefix` 之外的下载和删除。启用版本控制、服务端加密、访问日志和生命周期。优先使用 STS 临时凭据，环境凭据轮换后客户端会重建 OSS 连接。

## 6. 启动与检查

离线检查默认不连接 MySQL：

```powershell
python selftest.py
```

连接测试库后的全页面检查，禁止指向生产库：

```powershell
$env:CRM_MYSQL_INTEGRATION_SELFTEST = "1"
python selftest.py
$env:CRM_REQUIRE_TLS = "1"
python healthcheck.py
python app.py
Remove-Item Env:CRM_MYSQL_INTEGRATION_SELFTEST
```

`selftest.py` 默认仅执行离线测试；只有显式设置 `CRM_MYSQL_INTEGRATION_SELFTEST=1` 才连接数据库，该模式必须使用测试库。`healthcheck.py` 面向真实部署环境，会检查 MySQL 8、`utf8mb4`、严格 SQL mode、TLS、表结构、客户端目录和磁盘，并对服务器目录或 OSS 写入后立即删除一个健康探测文件；上线前确认该行为符合运维策略。不要让同终端 GUI 与健康检查并发写同一日志目录。

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
- `CRM_REQUIRE_TLS=1`，健康检查返回非空 `tls_cipher`。
- 数据库密码和 OSS 凭据未写入仓库或备份 ZIP。
- 至少两个管理员已改密，账号预建、角色调整、忘记密码重置和强制下线流程已核验。
- 两个客户端登录同一账号时，后登录能替换前会话，且旧客户端退出不影响新会话。
- 两客户端并发预算、冻结和审批测试通过。
- MySQL 和附件联合恢复演练通过。
- 磁盘、连接数、慢查询、备份失败和 OSS 异常已有监控告警。
- 每台客户端的 `runtime.log`、`error.log`、`audit.log` 已集中采集，轮转和目录权限验证通过。
- MySQL error log、slow query log、binlog 和操作系统日志已纳入监控。

更完整的业务与验收边界见 `最终需求与部署验收文档.md` 和仓库根目录 `最终需求与部署说明.md`；日志与告警规则见仓库根目录 `日志与运维说明.md`。
