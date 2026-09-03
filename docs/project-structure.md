# DocHub 项目目录标准化说明

> 依据 `doubao-coding-analyze-codebase` 技能流程（SCOPE → ROUTE → EFFECT → BREAK → SHIP）对最终项目目录做标准化与路径规范化。
> 本文件只描述**现状**，不包含任何伪代码或未落地的规划。

## 1. 身份与范围（SCOPE）

- **revision**：`1f27887`（main，`test: R1-R4 联调修复与回归测试固化`），继承 `0f06d1d`（DocHub 闭环上线前 9 项残留）
- **仓库路径**：`dochub-app/`（Flask 文档聚合阅读平台）
- **运行环境**：Python 3.14.7 · Flask 3.1.3 · Flask-SQLAlchemy 3.1.1 · jieba 0.42.1 · reportlab 5.0.1 · SQLite FTS5
- **路径规范化结论**：全仓代码 `grep` 扫描**无任何绝对路径**（`/Users/...`、`/home/`、`/root/`、`/var/www` 均无命中）；路径一律以 `BASE_DIR`（`config.py`）或项目相对约定承载，可直接跨机器迁移。

## 2. 标准化目录树

```
dochub-app/
├── app.py                  # 开发入口：python3 app.py（FLASK_PORT/HOST/DEBUG 环境变量控制）
├── wsgi.py                 # 生产 WSGI 入口（gunicorn/uwsgi 使用）
├── config.py               # 配置：DevelopmentConfig / ProductionConfig，DATABASE_URL/SECRET_KEY 环境变量
├── seed.py                 # 顶层种子入口（委托 app/seed.py 的 seed()）
├── migrate_v2.py           # 数据迁移脚本（v1→v2）
├── rebuild_index.py        # FTS 索引重建脚本
├── requirements.txt        # 依赖锁定（pip freeze 级）
├── .env.example            # 环境变量模板（不含真实密钥；真实 .env 被 .gitignore 忽略）
├── README.md               # 项目说明
├── docs/                   # 【本轮新增】标准化文档目录
│   ├── project-structure.md           # 本文件：目录标准化说明与路径规范
│   ├── Test-Report-三技能联调测试报告.md  # 联调测试报告（R1-R4 处置 + 第二轮复测）
│   └── DEPLOYMENT.md                  # 公网部署方案
├── app/                    # 应用包（工厂模式）
│   ├── __init__.py         # create_app()：蓝图注册、CSRF/CSP、限流、FTS 初始化、ensure_schema
│   ├── extensions.py       # 扩展实例（db、login_manager 等）
│   ├── models.py           # 12 个 SQLAlchemy 模型
│   ├── search.py           # 检索：FTS5 + jieba、search_products、_facet_counts、suggest
│   ├── security.py         # CSRF/CSP nonce、滑动窗口限流、登录指数退避
│   ├── pdf_export.py       # reportlab PDF 导出（缺依赖时由调用方降级）
│   ├── seed.py             # 种子数据：seed(app=None) 支持注入（测试友好）
│   ├── blueprints/
│   │   ├── main.py         # 首页/搜索/文档/社区/贡献/管理审核/下载
│   │   ├── auth.py         # 注册/登录/登出/会话轮换
│   │   └── api.py          # 联想/级联/收藏/阅读进度
│   ├── static/
│   │   ├── css/  js/       # 前端资源
│   │   └── uploads/        # 用户上传（.gitignore 忽略，仅保留 .gitkeep）
│   └── templates/          # Jinja2 模板（含 admin/、auth/、errors/）
├── deploy/                 # 部署工件
│   ├── dochub.service      # systemd 服务单元（gunicorn）
│   ├── dochub-backup.service/.timer  # SQLite 定时备份
│   ├── backup.sh           # 备份脚本
│   ├── nginx-dochub.conf   # nginx 反代 + HTTPS 配置骨架
│   ├── Dockerfile          # 容器镜像
│   └── docker-compose.yml  # 容器编排
├── tests/                  # 测试套件（独立脚本，非 pytest）
│   ├── regression_v2.py            # 既有回归 50 项（双解释器基线）
│   ├── test_unit_core.py           # 核心逻辑单元测试 11 项（探针+mutant）
│   ├── test_seed_smoke.py          # seed() 冒烟 10 项（临时库，R1）
│   ├── test_search_facets.py       # facet 语义 guard 8 项（R2）
│   ├── test_pdf_fallback.py        # PDF 降级链 3 项（含失败注入，R3）
│   ├── test_fts_sync.py            # FTS 同步端到端 5 项（R4）
│   ├── bench_perf.py               # 性能基线/容量探针（--scale 可复跑）
│   ├── integration_test.py         # 外部 HTTP 端到端 40 项（需临时库起服务）
│   ├── repro_baseline.py / repro_matrix.py  # 历史复现脚本（保留）
└── instance/               # 运行时 SQLite 库（.gitignore 忽略，不入库）
```

## 3. 路径与目录规范（规范化约定）

| 规则 | 约定 | 依据 |
| --- | --- | --- |
| 生产代码 | 一律位于 `app/` 包内，蓝图按模块拆分 | `app/blueprints/*` |
| 入口 | `app.py`（开发）/ `wsgi.py`（生产） | 两者均调用 `create_app()` |
| 数据库 | 默认 `instance/dochub.db`；生产可用 `DATABASE_URL` 覆盖 | `config.py` |
| 绝对路径 | 禁止；一律经 `BASE_DIR` 或相对引用 | 全仓扫描 0 命中 |
| 敏感文件 | `.env`、`instance/`、`*.db`、`uploads/`、`__pycache__` 全部忽略 | `.gitignore` |
| 测试 | 独立脚本 `python3 tests/<name>.py`，临时库隔离，不触碰 `instance/` | 本轮全部测试 |
| 部署工件 | 统一 `deploy/`（systemd/nginx/docker/备份） | `deploy/` |
| 文档 | 统一 `docs/`（结构说明/测试报告/部署方案） | 本轮新增 |

## 4. 关键路径-符号映射（供快速定位）

| 符号 | 路径:行 |
| --- | --- |
| `create_app()` | `app/__init__.py:63` |
| `search_products()` | `app/search.py:202` |
| `_facet_counts()` | `app/search.py:159`（R2 SQL 聚合） |
| `seed(app=None)` | `app/seed.py:842`（R1 可注入） |
| `download_doc()` | `app/blueprints/main.py:478`（R3 降级链 499-516） |
| `sync_document()` | 审核通过路径 `app/blueprints/main.py:876`（R4） |

## 5. 责任边界（EFFECT，仅列跨组件效果主张）

| 对象/范围 | 触发者 | 实际执行者 | 观察点 | status |
| --- | --- | --- | --- | --- |
| FTS 索引与已发布文档一致 | 启动 `create_app()`、审核通过、seed | `app/search.py` | FTS 行数 = published 文档数（实测 8=8） | **conditional**（文档写路径仅 2 处，均已同步） |
| 下载不中断 | GET `/doc/<id>/download` | `app/blueprints/main.py` | PDF 引擎/字体/reportlab 缺失统一降级 text/plain | **confirmed**（R3 后含 ImportError） |

> 未读边界（声明）：第三方依赖源码、`instance/` 运行时数据、`.git/` 内部结构。
