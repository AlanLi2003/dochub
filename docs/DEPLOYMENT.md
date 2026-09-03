# DocHub 公网部署方案

> 目标：将 `dochub-app`（Flask 文档聚合平台）发布到公网。
> 本方案基于仓库 `deploy/` 现成工件编写，路径、端口、域名与现有配置保持一致。
> 适用环境：单台 Linux 服务器（Ubuntu/Debian/CentOS 均可）。已给出「systemd 裸机」与「Docker」两条路线。

---

## 0. 部署架构（总览）

```
                        ┌───────────────────────────── 单机 VPS ─────────────────────────────┐
用户浏览器 ──HTTPS/443──►  nginx ──127.0.0.1:8000──►  gunicorn(4 worker) ──► Flask(wsgi:app)
                          │ 反代+静态缓存+TLS              │                        │
                          │                               │                        ├─ app/static/uploads（上传文件，卷/目录持久化）
                          │                               └────────────────────────┴─ SQLite instance/dochub.db（FTS5 全文索引）
                          └── 备份定时器（systemd timer 或 cron）→ /var/backups/dochub（14 天保留）
```

**核心约束（必须先读）**：检索依赖 **SQLite FTS5**（`app/search.py` 使用 `document_fts` 虚拟表与 `MATCH`）。**生产数据库必须保持 SQLite**，不得切换到 PostgreSQL/MySQL，否则全文搜索整体失效。`DATABASE_URL` 仅用于指向**另一个 SQLite 文件路径**。

---

## 1. 部署前提

| 项 | 要求 |
| --- | --- |
| 服务器 | 1 核 1G 起步（建议 2C2G）；Ubuntu 22.04 / Debian 12 / CentOS 7+ |
| 域名 | 一个域名（如 `docs.example.com`），A 记录解析到服务器公网 IP |
| 安全组 | 放行 `80`、`443`（SSH 22 保持）；**不要**公网放行 8000 |
| 依赖 | Python ≥ 3.10（建议 3.11/3.12，与 `Dockerfile` 的 3.11 对齐）、`git`、`sqlite3` CLI |
| 账号 | 建议专用系统用户 `dochub`，不用 root 跑应用 |

---

## 2. 方案 A：systemd 裸机部署（推荐，直接复用 deploy/ 工件）

### A1. 拉取代码并准备环境

```bash
# 1) 拉取代码（把 <你的仓库> 换成 GitHub 仓库地址）
sudo useradd -r -m -d /opt/dochub dochub || true
sudo mkdir -p /opt/dochub
sudo git clone <你的GitHub仓库URL> /opt/dochub
sudo chown -R dochub:dochub /opt/dochub

# 2) 建虚拟环境并安装依赖（以 dochub 用户）
sudo -u dochub bash -c '
  cd /opt/dochub
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
'

# 3) 初始化数据库与种子数据（首次部署；正式数据可跳过 seed 只建表）
sudo -u dochub bash -c 'cd /opt/dochub && ./.venv/bin/python seed.py'
#   说明：seed.py 会清空并写入演示数据；如需空库，改为仅执行
#   ./.venv/bin/python -c "from app import create_app; from app.search import init_fts; a=create_app(); a.app_context().push(); init_fts()"
```

### A2. 配置生产环境变量（`.env`）

```bash
# 生成强随机密钥并写入 .env（权限 600，禁止入库）
sudo -u dochub bash -c '
  cd /opt/dochub
  umask 077
  cat > .env <<EOF
APP_ENV=production
SECRET_KEY='$(python3 -c "import secrets;print(secrets.token_hex(32))")'
# 默认即 SQLite: instance/dochub.db；如需换 SQLite 路径才设置：
# DATABASE_URL=sqlite:////opt/dochub/instance/dochub.db
FLASK_DEBUG=0
EOF
'
```

> `ProductionConfig` 强制要求 `SECRET_KEY`，缺失时应用会拒绝启动（快速失败，防止会话伪造）。

### A3. 注册 systemd 服务

```bash
# 1) 安装服务单元（默认路径 /opt/dochub 已在单元内写死，与上文一致）
sudo cp /opt/dochub/deploy/dochub.service /etc/systemd/system/dochub.service

# 2) 启动并设开机自启
sudo systemctl daemon-reload
sudo systemctl enable --now dochub
systemctl status dochub            # active (running)
journalctl -u dochub -f            # 观察启动日志
```

### A4. nginx 反代 + HTTPS

```bash
# 1) 安装 nginx
sudo apt-get install -y nginx

# 2) 拷贝并修改配置（把 server_name 改成真实域名）
sudo cp /opt/dochub/deploy/nginx-dochub.conf /etc/nginx/conf.d/dochub.conf
sudo sed -i 's/your-domain.example.com/你的域名/g' /etc/nginx/conf.d/dochub.conf

# 3) 语法检查并重载
sudo nginx -t && sudo systemctl reload nginx

# 4) 签发免费 HTTPS 证书（生产 Cookie Secure 标记依赖 HTTPS，必做）
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名
#    完成后：80 → 443 自动跳转；证书 90 天自动续期（certbot renew --dry-run 验证）
```

### A5. 配置定时备份

```bash
# 复用 deploy/ 备份脚本 + systemd timer（每天备份，保留 14 天）
sudo cp /opt/dochub/deploy/dochub-backup.service /etc/systemd/system/
sudo cp /opt/dochub/deploy/dochub-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dochub-backup.timer
sudo systemctl list-timers | grep dochub      # 确认定时器生效
# 手动验证一次：
sudo -u dochub bash -c 'APP_DIR=/opt/dochub BACKUP_DIR=/var/backups/dochub bash /opt/dochub/deploy/backup.sh'
ls -la /var/backups/dochub/
```

---

## 3. 方案 B：Docker 部署（可选）

```bash
# 前提：服务器已装 Docker + docker compose 插件
cd /opt/dochub
echo "SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')" > .env
docker compose -f deploy/docker-compose.yml up -d --build

# 镜像内含中文字体（fonts-wqy-zenhei），reportlab 中文 PDF 开箱可用；
# 数据经卷持久化：instance/（SQLite）与 uploads/（上传文件）。
# 注意：compose 绑定 127.0.0.1:8000，仍需 nginx 反代 + certbot HTTPS（同 A4）。
```

---

## 4. 上线验证清单（对应测试用例）

部署完成后，按顺序冒烟验证（可用 curl 或浏览器）：

| # | 验证项 | 期望 | 对应测试 |
| --- | --- | --- | --- |
| 1 | `curl -I https://你的域名/` | `200`，无报错 | 回归 #首页 |
| 2 | `curl -s 'https://你的域名/search?q=小米'` | 返回结果，无 500 | regression_v2 + 单元 |
| 3 | 无词筛选 `?brand=1` | 返回品牌 1 产品，facet 计数正确 | test_search_facets（R2） |
| 4 | 文档下载 `/doc/1/download` | PDF（或纯文本降级）非 500 | test_pdf_fallback（R3） |
| 5 | 注册→登录→个人中心 | 302→200 流程通 | integration_test 2 |
| 6 | 提交贡献→管理员审核通过 | 新文档**立即可检索** | test_fts_sync（R4） |
| 7 | `/doc/99999` | 404（非 500） | integration_test 4 |
| 8 | 未登录访问 `/profile` | 302 跳登录 | integration_test 2 |
| 9 | 连错 5 次密码 | 第 6 次 429/锁定 | test_unit_core（LoginGuard） |
| 10 | `curl -I http://你的域名/` | 301/308 跳 https | certbot 配置 |

> 全量自动化：可先在**临时库**上跑 `python3 tests/integration_test.py https://你的域名`（需把临时库服务器改为公网域名），或复跑各 `tests/*.py`。

---

## 5. 关键约束与风险

| 风险/约束 | 说明 | 处置 |
| --- | --- | --- |
| **FTS5 强依赖 SQLite** | 换 Postgres/MySQL 全文检索失效 | 生产保持 SQLite；单库即可，配合备份 |
| 登录限流/指数退避为**进程内**状态 | gunicorn 4 worker 各独立计数，属保守下限 | 单机单实例部署即满足设计；不做多机横向扩容 |
| `SESSION_COOKIE_SECURE=True` | HTTP 下 Cookie 不发送，登录会“失效” | 必须 HTTPS（certbot 已覆盖） |
| 上传体积 50MB | 与 nginx `client_max_body_size 55m` 对齐 | 已配置，勿改小 |
| 首次部署需初始化 FTS | `document_fts` 表由 `create_app()` 自动建 | 见 A1 第 3 步 |
| 静态资源 | nginx 直发 `/static/`，`expires 7d` | 已配置 |
| 密钥泄露 | SECRET_KEY 泄露可伪造会话 | `.env` 权限 600 + 轮换 SECRET_KEY 即全体会话失效 |
| 管理员账号 | 种子默认 admin/admin123 | 上线后必须改密（个人中心/数据库） |

---

## 6. 升级与回滚

**升级**（拉新代码 + 重启）：

```bash
cd /opt/dochub && sudo -u dochub git pull --ff-only
sudo -u dochub ./.venv/bin/pip install -r requirements.txt   # 依赖有变化才需要
sudo systemctl restart dochub
```

**回滚**（回到上一稳定提交）：

```bash
cd /opt/dochub && sudo -u dochub git checkout <上一提交SHA> -- . && sudo systemctl restart dochub
# 数据库与上传文件不受影响（在 instance/ 与 uploads/，不入 git）
```

---

## 7. 上线前安全清单（自检）

- [ ] `SECRET_KEY` 已设置为强随机值，`.env` 权限 600
- [ ] 服务器安全组仅放行 22/80/443；gunicorn 只绑 127.0.0.1
- [ ] HTTPS 已签发并强制跳转；`curl -I http://…` 返回 3xx
- [ ] 管理员/演示账号密码已修改
- [ ] 定时备份已启用且手动跑通一次，`/var/backups/dochub` 有产物
- [ ] 数据库为 SQLite（未误配 Postgres/MySQL），`/search` 检索正常
- [ ] 上传目录与 SQLite 落在持久卷/磁盘（容器场景）
- [ ] `git status` 干净、`.env`/`instance/`/`*.db` 未入库（已由 .gitignore 保证）

---

*本方案命令基于仓库 `deploy/` 工件与 `config.py`/`requirements.txt` 实际内容编写；本地开发 Python 3.14.7 与仓库锁定的 3.0.3/3.11 有版本差，生产以仓库锁定版本为准（双环境回归已验证）。*
