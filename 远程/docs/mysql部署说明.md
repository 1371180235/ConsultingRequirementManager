# MySQL 远程版部署说明

## 1. 安装依赖

```powershell
pip install -r requirements.txt
```

## 2. 准备 MySQL 账号

使用管理员账号登录 MySQL 后执行：

```sql
CREATE DATABASE IF NOT EXISTS consulting_requirement_manager
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'crm_user'@'%' IDENTIFIED BY '请替换成强密码';
GRANT ALL PRIVILEGES ON consulting_requirement_manager.* TO 'crm_user'@'%';
FLUSH PRIVILEGES;
```

如果只允许本机访问，把 `'crm_user'@'%'` 改成 `'crm_user'@'localhost'`。

## 3. 配置连接

复制配置模板：

```powershell
Copy-Item config\mysql_config.example.json config\mysql_config.json
```

编辑 `config/mysql_config.json`：

```json
{
  "host": "127.0.0.1",
  "port": 3306,
  "user": "crm_user",
  "password": "你的数据库密码",
  "database": "consulting_requirement_manager",
  "create_database": true
}
```

## 4. 启动

```powershell
python app.py
```

首次启动会自动创建业务表，并初始化示例项目、年度计划、版本和需求。

## 5. 远程访问注意事项

- MySQL 服务端需放行 3306 端口，或通过内网/VPN 访问。
- 生产环境不要使用 root 账号连接应用。
- `config/mysql_config.json` 包含密码，已被 `.gitignore` 忽略，不应提交到仓库。
- 附件仍保存在应用本机 `data/attachments`，数据库只保存附件路径。多电脑共同使用时，建议把附件目录放到共享盘或对象存储，并按需改造文件上传逻辑。
- 数据库备份建议使用 `mysqldump`，应用内 ZIP 备份只包含配置快照和附件。
