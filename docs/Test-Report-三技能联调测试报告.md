# DocHub 三技能联调测试报告

> 联调对象：
>
> `doubao-coding-analyze-codebase`
>
> （代码库理解与架构梳理）＋ 
>
> `doubao-coding-optimize-performance`
>
> （性能分析与优化）＋ 
>
> `doubao-coding-develop-unit-tests`
>
> （单元测试与回归验证）
> 被测代码库：
>
> `dochub-app`
>
> （Flask 官方文档聚合阅读平台）
> 测试日期：2026-09-03



***

## 0. 结论摘要（TL;DR）



| 联调项  | 技能                   | 结果                                              |
| ---- | -------------------- | ----------------------------------------------- |
| 架构梳理 | analyze-codebase     | ✅ 三条主链路闭环，4 张责任闭合卡，1 项 HEAD 潜伏缺陷（seed.py）       |
| 性能分析 | optimize-performance | ✅ 基线已建立、热点已定位（无词全表扫描路径 1k→20k 延迟 ×32）；**未授权优化** |
| 单元测试 | develop-unit-tests   | ✅ 11/11 GREEN，mutant 3/3 RED，探针敏感度成立            |
| 回归验证 | 既有 regression\_v2    | ✅ 50/50 通过                                      |
| 遗留风险处置 | R1–R4（追加执行）       | ✅ **全部已解决**，性能热点 2.1–2.4× 提速（见第 5 章）           |

**环境**：Python 3.14.7 · Flask 3.1.3 · Flask-SQLAlchemy 3.1.1 · Flask-Login 0.6.3 · jieba 0.42.1 · reportlab 5.0.1 · git HEAD `0f06d1d`（worktree 含 1 处未提交改动 `app/seed.py`，非本次任务产生）

**三个技能协同结论**：各技能均按其 SKILL.md 的交付契约闭环，且存在真实交接点 ——analyze-codebase 输出的源码事实直接支撑了 optimize-performance 的 Evidence card 与热点机制假设，develop-unit-tests 的契约表反过来验证了 analyze-codebase 对 `safe_next` / 限流 / 聚合逻辑的行为推断。三技能在 `dochub-app` 上可串联执行，无技能间冲突。



***

## 1. 技能一：代码库理解与架构梳理（analyze-codebase）

### 1.1 身份与范围（SCOPE）



* **revision/worktree**：HEAD `0f06d1d`；worktree 仅 `app/seed.py` 有未提交改动（+1 行：为 `Post` 补 import）。

* **已读范围**：`app.py`、`config.py`、`app/__init__.py`、`app/models.py`、`app/search.py`、`app/security.py`、`app/pdf_export.py`、`app/blueprints/{main,auth,api}.py`、`app/seed.py`、`tests/{integration_test,regression_v2,repro_baseline,repro_matrix}.py`。

* **未读范围（声明）**：`templates/*`、`static/*`、`deploy/*`、`migrate_v2.py`、`rebuild_index.py`。

* **数据库实况（只读证据）**：users=2、categories=5、brands=3、products=5、documents=8、posts=4、terms=15、FTS 行 = 8（与 published 文档数一致）。

### 1.2 架构概览

工厂模式单实例 Flask 应用：`app.py → create_app()`（`app/__init__.py:63`）注册 3 个蓝图 ——



* `main_bp`（`/`）：首页、搜索、分类、文档阅读 / 下载、社区、个人中心、贡献、管理审核后台；

* `auth_bp`（`/auth`）：注册 / 登录 / 登出；

* `api_bp`（`/api`）：搜索联想、产品文档级联、分类、收藏、阅读进度。

12 个 SQLAlchemy 模型；检索为 SQLite FTS5（jieba 分词，**仅品牌 / 产品全称列参与匹配**，BM25 权重 (1,0,0,2,3)，结果按产品聚合）；安全为自研 CSRF + CSP nonce + 会话轮换 + 进程内滑动窗口限流与登录指数退避 + 生产 ProxyFix。

### 1.3 主链路与关键旁路（ROUTE）

**链路 A｜搜索（入口&#x20;**`/search`**）**

`main.py:253` → 参数校验（doc\_type/brand 单选、sort 白名单）→ `detect_locked_brand`（品牌锁定）→ `search_products`（`search.py:174`）→ `query_tokens`（jieba）→ 按 `and→or→prefix` 三级生成 `_name_match_clause` → `_flat_matches`（FTS MATCH + bm25 JOIN documents/products）→ `_aggregate`（产品聚合）→ 排序 → 两次 facet `_flat_matches`（类型 / 品牌）→ `main.py` 回查产品与文档 → `decorate_results` 高亮 → 渲染 `search.html`。

*关键旁路*：无 q 且无筛选 → 返回空态；无 q 但有品牌 / 类型筛选 → `use_match=False`，走**全表扫描**（见技能二热点）。

**链路 B｜认证（入口 POST&#x20;**`/auth/login`**）**

`auth.py:100` → `check_ip_rate`（IP 12 次 / 分）→ `login_guard.locked_for`（账号退避）→ `User` 查询（用户名或邮箱）→ `check_password`（含无 scrypt 解释器兼容）→ 失败 `record_failure`（第 5 次起指数锁定）→ 成功 `guard.clear` + `_rotate_session`（会话固定防护）+ `login_user` → `safe_next` 防开放重定向 → 302。

**链路 C｜下载（入口 GET&#x20;**`/doc/<id>/download`**）**

`main.py:480` → `download_count+1` → 有上传原文件则 `send_from_directory`；否则 `pdf_export.build_pdf_bytes`（reportlab + 中文字体自动探测）→ `send_file`（RFC 5987 中文文件名）。

### 1.4 责任闭合卡（EFFECT，跨组件效果主张）



| 对象 / 范围        | 触发者                         | 当前装配 / 选择                                                                                              | 实际执行者                    | 成功副作用与观察点                         | 失败是否返回且被检查                                                               | 重试 / 重放来源                | 不能覆盖的对象                       | status                                             |
| -------------- | --------------------------- | ------------------------------------------------------------------------------------------------------ | ------------------------ | --------------------------------- | ------------------------------------------------------------------------ | ------------------------ | ----------------------------- | -------------------------------------------------- |
| FTS 索引与已发布文档一致 | 启动 `create_app()`、审核通过、seed | `ensure_index` 启动比对计数、不一致全量重建（`__init__.py:170`、`search.py:71`）；`sync_document` 仅挂在审核通过（`main.py:876`） | `app/search.py`          | FTS 行数 = published 文档数（实测 8=8）    | rebuild 异常冒泡至启动失败；`sync_document` 无 try                                  | 无；下次启动 `ensure_index` 兜底 | 非 seed / 审核路径的文档增删改（当前无其他写路径） | **conditional**                                    |
| PDF 下载不中断      | GET `/doc/<id>/download`    | 有原文件→原样下发；否则 reportlab                                                                                 | `app/pdf_export.py`      | application/pdf + 中文文件名（回归 #5 通过） | 仅捕获 `PdfExportError`→降级 text/plain；`ImportError`**（缺 reportlab）未捕获→500** | 无                        | reportlab 缺失场景                | **conditional**（requirements 已锁定 reportlab==5.0.1） |
| 登录失败指数锁定       | POST `/auth/login`          | 进程内单例 `LoginGuard`，阈值 5、基 30s、上限 900s                                                                  | `app/security.py`        | 5 连败锁定、第 6 次 429（回归 #1a 通过）       | `locked_for>0`→429；`record_failure` 返回锁定时长                               | 无                        | 多 worker 下跨进程强一致（已注释为保守下限）    | **conditional**（单 worker 强一致）                      |
| 搜索词为品牌时锁定品牌    | q 含品牌全称                     | `detect_locked_brand` 精确→子串（`main.py:54`）                                                              | `app/blueprints/main.py` | 其他品牌侧栏置灰（回归 #9b 通过）               | 无匹配→None 不锁定                                                             | 无                        | 非 active 品牌                   | **confirmed**                                      |

### 1.5 发现的问题与未知（BREAK / SHIP）



1. **【已证实・HEAD 潜伏缺陷】**`app/seed.py`**&#x20;在 HEAD&#x20;**`0f06d1d`**&#x20;会 NameError**：第 1090 行使用 `Post(...)` 但未 import；worktree 的未提交改动正是补上 `Post` 导入（`confirmed`，来源：`git show HEAD:app/seed.py` vs worktree diff）。既有回归 50/50 未覆盖 `seed()` 执行路径，故未暴露。**结论：HEAD 分支的&#x20;**`python3 seed.py`**&#x20;不可运行，需提交该修复或回退。**

2. `ensure_schema` 仅自愈 `posts.product_id` 一列；其余表的新增列不覆盖（`confirmed`，来源 `__init__.py:15-26`）。

3. 登录限流为进程内状态，多 worker 部署时各进程独立计数，属保守下限（`documented`，`security.py:5-6`）。

4. PDF 降级仅覆盖字体 / 引擎错误，不覆盖依赖缺失（见 1.4 卡片 2）。



***

## 2. 技能二：性能分析与优化（optimize-performance）

> 流程声明：
>
> **「使用性能优化流程：无测量，不结论；无同合同证据，不授权。」**
> 主模式：
>
> `establish-baseline`
>
> （建立可复跑基线 + 容量边界探针）。
>
> **未运行 optimize（只读，未授权改码）**
>
> 。

### 2.1 Evidence card



| 项                 | 内容                                                                                              |
| ----------------- | ----------------------------------------------------------------------------------------------- |
| revision/worktree | HEAD `0f06d1d`（测量不涉及未提交改动）                                                                      |
| runtime           | Python 3.14.7；Flask 3.1.3（测试客户端，进程内）                                                            |
| 点名入口              | `app/search.search_products`（`search.py:174`）＋ 关键端点 `/search`、`/doc/<id>`、`/api/search/suggest` |
| 边界                | 种子库 8 文档；合成库 1k/5k/20k 文档（50 品牌 × 每品牌 20 产品 × 每产品 20 篇）；进程内延迟不含网络 / 生产 WSGI                     |
| 测量边界              | 测试客户端请求全程；warmup 2、样本 15\~20；同进程配对顺序交替                                                          |

### 2.2 测量合同（可复跑）



```
cd dochub-app

python3 tests/bench\_perf.py --scale 1000,5000,20000 --samples 15 --warmup 2
```



* 第一部分用「种子库副本」（`tempfile` + `shutil.copy2`），避免 `/doc/1` 等写操作污染真实库；

* 第二部分合成库写入 `tempfile`，脚本退出自动清理，不触碰 `instance/dochub.db`。

### 2.3 基线数据（Part 1：种子数据规模，均值 ms）



| 端点                                    | mean  | p50   | p95   |
| ------------------------------------- | ----- | ----- | ----- |
| GET /                                 | 1.499 | 1.339 | 2.878 |
| GET /search?q = 小米                    | 1.260 | 1.132 | 1.522 |
| GET /search?q = 小米 & doc\_type=manual | 1.137 | 1.048 | 1.408 |
| GET /search?q=WiFi（正文词不命中）            | 0.916 | 0.889 | 1.022 |
| GET /search?brand=1（无词仅筛选）            | 1.079 | 0.955 | 1.886 |
| GET /api/search/suggest?q = 小米        | 0.751 | 0.704 | 0.886 |
| GET /doc/1（阅读 + markdown 渲染）          | 1.682 | 1.645 | 1.926 |
| GET /categories                       | 1.681 | 1.708 | 1.901 |

种子规模全部亚 2ms，无即时性能风险。

### 2.4 容量探针（Part 2：search\_products 平均延迟 ms）



| workload                   | @1,000    | @5,000    | @20,000    | 1k→20k 增幅 |
| -------------------------- | --------- | --------- | ---------- | --------- |
| **仅品牌筛选（无词全表扫描）**          | **0.988** | **7.149** | **31.948** | **≈×32**  |
| q = 品牌全称（命中）               | 0.379     | 1.181     | 4.363      | ≈×11.5    |
| q = 产品全称（命中）               | 0.382     | 0.901     | 3.268      | ≈×8.6     |
| q = 无命中词（and→or→prefix 回退） | 0.262     | 0.284     | 0.345      | ≈×1.3     |

### 2.5 热点定位（机制，静态阅读 + 运行证据）



* **热点**：`search_products(q='', brand_id=...)` 无词分支走 `_flat_matches(use_match=False)`（`search.py:145-148`），执行

  `SELECT d.id, d.product_id, d.doc_type, d.created_at, d.view_count, p.brand_id, NULL FROM documents d JOIN products p ON p.id = d.product_id WHERE d.status='published' [AND p.brand_id=...]`

  **无 LIMIT，全量物化行到 Python**；主查询 + 2 次 facet 共 3 次全表扫描，20k 文档 ≈ 6 万行物化。`@20k` p50=29.1ms、p95=43.3ms；外推 100k 文档可达百毫秒级。

* 命中路径走 FTS5 索引，`bm25` 相关性稳定亚毫秒；无命中词因 FTS 快速返回空集，三级回退不产生额外放大（与源注释一致）。

### 2.6 结论（verdict）



* **verdict**：`baseline established`（基线闭合：workload、命令、原始样本、增长曲线齐全）；热点机制已用「源码事实 + 实测增长」双重证据命中。

* **未授权**：未运行 A/B，不提供收益承诺、参数或实施步骤（按技能约束，`diagnose`/`establish-baseline` 的热点不得写成 `optimize` 已授权）。

* **下一判别动作（若需继续）**：对「无词品牌筛选」候选方案（如对 `products` 建 `(brand_id)` 索引或改用聚合计数、限制扫描列）做同 fingerprint 单变量 A/B，并用语义 oracle + mutant 保护筛选语义后再裁决。



***

## 3. 技能三：单元测试与回归验证（develop-unit-tests）

### 3.1 契约 → 反例表（每条对应唯一错误实现）



| 编号      | 目标（符号）                       | 输入轨迹                                              | 唯一错误实现                 |
| ------- | ---------------------------- | ------------------------------------------------- | ---------------------- |
| T1（探针）  | `auth.safe_next`             | `/profile`、`/a/../b` 应放行                          | 一律返回 None              |
| T2–T4   | `auth.safe_next`             | `//evil.com`、`/\\evil.com`、`https://evil.com` 应拒绝 | 仅拒绝带 scheme / 仅拒绝 `//` |
| T6–T7   | `SlidingWindowLimiter.check` | window 内 max\_hits 放行、第 max\_hits+1 拒、过期后恢复       | 计数不增 / 不清理过期命中         |
| T8–T11  | `LoginGuard`                 | 第 5 次锁定、第 6 次指数增长、大小写归一、clear 复位                  | 阈值错位 / 恒定 30s / 区分大小写  |
| T12–T14 | `search.highlight_tokens`    | `<script>` 转义、重叠 span 合并、大小写不敏感                   | 不转义 / 不合并 / 区分大小写      |
| T15–T17 | `search._aggregate`          | views 求和、best=min (bm25)、latest=max               | 不求和 / 取 max / 不更新      |
| T18     | `search.query_tokens`        | 有效 token、纯符号→空                                    | 保留符号 / 空串              |
| T19     | `main.is_safe_url`           | `javascript:` 拒、http / 相对路径放行                     | 不拦截危险协议                |
| T20     | `main.extract_toc`           | 级别映射、忽略正文行                                        | 级别错乱 / 含正文             |

### 3.2 证据



* **GREEN**：`python3 tests/test_unit_core.py` → **11/11 通过**（探针 T1 首先执行）。

* **mutant RED（探针敏感度证明，仓库外临时副本）**：`prove_mutant.py` 注入 3 个错误实现 ——`safe_next` 一律返回 None（T1 红）、漏拦 `//evil.com`（T2 红）、`_aggregate` best 取 max（T16 红）→ **3/3 全部变红**，证明测试对错误实现敏感。

* **回归**：既有 `python3 tests/regression_v2.py` → **50/50 通过**；新增单元测试与既有回归同时运行无冲突。

### 3.3 新增文件



| 文件                        | 说明                                         |
| ------------------------- | ------------------------------------------ |
| `tests/test_unit_core.py` | 核心逻辑单元测试（11 用例，`check()` 运行器，仅标准库）         |
| `tests/bench_perf.py`     | 性能基线 / 容量探针测量脚本（`establish-baseline` 合同实现） |

> 未改动任何生产代码；
>
> `app/seed.py`
>
>  的未提交改动为任务前已存在，未触碰。



***

## 4. 联调总评与遗留风险

### 4.1 技能协同验证



1. **交接成立**：analyze-codebase 输出的「搜索链路 A」与「无词全表扫描旁路」被 optimize-performance 实测证实（Part 2 热点）；analyze-codebase 对 `safe_next`/ 限流 / 聚合的行为推断被 develop-unit-tests 以契约表 + mutant 反证闭环。

2. **各技能契约自洽**：三个技能的交付结构（证据矩阵 / 责任闭合卡、Evidence card+verdict、契约→反例→证据）均按其 SKILL.md 执行并留痕。

### 4.2 遗留风险（按优先级）—— 全部已处置，处置明细见第 5 章



| #  | 风险                                                         | 证据                               | 建议动作                                |
| -- | ---------------------------------------------------------- | -------------------------------- | ----------------------------------- |
| R1 | **HEAD&#x20;**`seed.py`**&#x20;无法运行（NameError: Post 未导入）** | `git show HEAD:app/seed.py:1090` | 提交 worktree 修复或回退，补一条 `seed()` 冒烟测试 |
| R2 | 「无词品牌 / 类型筛选」全表扫描随文档量线性恶化（20k→≈32ms p50）                   | bench\_perf Part 2               | 量级 ≥1 万篇时评估建索引 / 聚合改造（需 A/B）        |
| R3 | PDF 降级不覆盖 reportlab 缺失（ImportError→500）                    | `main.py:499-510` 导入在 try 外      | 将导入纳入降级或启动依赖检查                      |
| R4 | FTS 同步仅挂审核路径与启动自愈                                          | 责任闭合卡 1（conditional）             | 若新增文档编辑 / 删除入口，需接入 `sync_document`  |

### 4.3 复现命令



```
\# 既有回归

python3 tests/regression\_v2.py

\# 新增单元测试

python3 tests/test\_unit\_core.py

\# 性能基线（可选 --scale）

python3 tests/bench\_perf.py --scale 1000,5000,20000
```



***

## 5. 遗留风险处置（2026-09-03 追加执行，按风险等级）

> 处置流程：对 R2 走完整 optimize 证据链（语义 oracle + mutant → 配对 A/B → ACCEPT → 落地 → guard）；
> R1/R3/R4 为定向修复 + 回归测试。**生产代码改动 3 处**：`app/search.py`（R2）、`app/blueprints/main.py`（R3）、`app/seed.py`（R1）。

### 5.1 R1（最高优先级）：HEAD `seed.py` NameError —— 已解决

- **根因**：HEAD `0f06d1d` 的 `app/seed.py:1090` 使用 `Post(...)` 但未导入（`git show HEAD:app/seed.py` 证实）；worktree 未提交改动即补 import。
- **处置**：① 保留 import 修复；② `seed()` 重构为可注入 `app`（`def seed(app=None)`），移除模块级 `create_app()`，避免导入即连真实库；③ 新增 `tests/test_seed_smoke.py` 在**临时库**跑通 `seed()`，断言 2 用户 / 5 分类 / 3 品牌 / 5 产品 / 8 文档 / 4 帖 / 15 术语 / FTS 8 行 → **全部通过**；真实 `instance/dochub.db` 未被触碰（docs=8/fts=8 复检）。
- **验证**：`python3 tests/test_seed_smoke.py` ✅；`python3 seed.py` 路径保留可用（未在真实库执行，避免清库）。

### 5.2 R2：无词全表扫描随数据量线性恶化 —— 已解决（optimize 全流程）

- **delta**：facet 计算由「Python 全量行物化 + 去重」改为 **SQL 侧 `GROUP BY COUNT(DISTINCT product_id)`**，并用 `MIN(rowid)` 复刻基线的「扫描首见序」tie-break；**零 schema 变更**（无需索引）。
- **语义 oracle**：种子库 162 组（q × doc_type × brand_id × sort）基线 vs 候选 **162/162 逐字节一致**；mutant 2 类错误实现（丢 DISTINCT、facet 漏品牌筛选）**全部被 oracle 捕获**。
- **配对 A/B @20k 文档（同 fingerprint、交替测序、n=15）**：

| workload | 基线 mean | 候选 mean | 加速 |
| --- | --- | --- | --- |
| 仅品牌筛选（无词） | 31.875 ms | 13.285 ms | **2.40×** |
| 仅类型筛选（无词） | 87.035 ms | 42.087 ms | **2.07×** |
| 词命中（品牌全称） | 4.872 ms | 2.009 ms | **2.43×** |

- **落地与验证**：`app/search.py` 应用后与已验证候选 **162/162 等价**；修复后 `bench_perf.py` 复测 @20k：仅品牌筛选 13.83ms（-57%）、词命中 2.10ms、产品命中 1.22ms；既有回归 **50/50** + 单元 **11/11** 全通过。
- **guard**：新增 `tests/test_search_facets.py`（8 项）固化「facet=去重产品数、忽略自身维度、排序确定性」语义。

### 5.3 R3：PDF 降级不覆盖 reportlab 缺失 —— 已解决

- **处置**：`main.py` 下载路由将 `from app.pdf_export import ...` 移入 try，`ImportError`（reportlab 缺失）与 `PdfExportError`（无中文字体）统一走纯文本降级；通过「先置空再判空」规避异常未定义名。
- **验证**：新增 `tests/test_pdf_fallback.py`（3 项）——正常 PDF 200、`__import__` 注入 reportlab 缺失 → text/plain 200、`PdfExportError` → text/plain 200，**全部通过**。

### 5.4 R4：FTS 同步仅挂审核路径与启动自愈 —— 已解决（审计 + 端到端固化）

- **审计**：`app/` 全库检索确认文档写路径仅两处——admin 审核通过（`main.py:869`→`sync_document`）与 seed（→`rebuild_index`），两处均已覆盖 FTS；无其他未受保护写路径。
- **验证**：新增 `tests/test_fts_sync.py`（5 项）固化端到端保证——审核通过后新文档发布、FTS 行数 +1、新 rowid 在索引中、按产品名立即可检索且 doc_count +1，**全部通过**。未来新增写路径若漏接 `sync_document`，该测试将失败。

### 5.5 处置后全量回归

| 套件 | 结果 |
| --- | --- |
| `regression_v2` | 50/50 ✅ |
| `test_unit_core` | 11/11 ✅ |
| `test_seed_smoke` | 全部通过 ✅ |
| `test_search_facets`（R2 guard） | 全部通过 ✅ |
| `test_pdf_fallback`（R3） | 全部通过 ✅ |
| `test_fts_sync`（R4） | 全部通过 ✅ |

**变更文件**：`app/search.py`、`app/blueprints/main.py`、`app/seed.py`；新增测试 5 个。`app/seed.py` 的 import 修复（任务前已存在于 worktree）随 R1 一并保留，建议提交。

***

## 6. 第二轮联调（2026-09-03 复测，修复后代码库）

> 本轮对象：R1–R4 已落地后的工作树（git HEAD 仍为 `0f06d1d`，工作树含 3 处修改 + 6 个测试文件）。
> 目的：验证修复未引入回归、优化收益在更大规模下成立、新 guard 对错误实现敏感。

### 6.1 三技能复测

| 技能 | 复测内容 | 结果 |
| --- | --- | --- |
| analyze-codebase | 重读三处修改代码，核对 R1–R4 架构主张 | ✅ `_facet_counts`(search.py:159) SQL 聚合、PDF 降级(main.py:499-516)、`seed(app=None)`(seed.py:842) 均与报告一致；全仓 `Document(` 写路径仅 2 处（main.py:869、seed.py:1028），FTS 同步覆盖闭环 |
| optimize-performance | 修复后全规模再基线（1k/5k/20k） | ✅ 无词品牌筛选 @20k **31.9→10.0ms（3.19×）**；命中路径 2.9–3.1× 提速；无命中路径持平 |
| develop-unit-tests | 全量重跑 + 外部 HTTP 端到端 + 新 guard mutant 验证 | ✅ 87 项进程内全过；外部 HTTP **40/40**；facet guard mutant 2/2 变红 |

### 6.2 性能前后对比（仅品牌筛选·无词路径，mean ms）

| 规模 | 第一轮（修复前） | 第二轮（修复后） | 加速 |
| --- | --- | --- | --- |
| @1,000 | 0.988 | 0.365 | 2.71× |
| @5,000 | 7.149 | 2.512 | 2.85× |
| @20,000 | 31.948 | 10.011 | 3.19× |

**@20,000 全 workload 对比**：品牌命中 4.363→1.522ms（2.87×）· 产品命中 3.268→1.056ms（3.09×）· 无命中词 0.345→0.366ms（持平）· 仅品牌筛选 31.948→10.011ms（3.19×）。

> **残余线性说明**：无词分支的主结果查询（无 LIMIT 列出全部已发布文档）仍是线性全扫描，facet 聚合已全部下沉 SQL 侧；@20k 10ms 属可接受，若需进一步压规模需对主查询本身改造（超出本轮授权范围）。

### 6.3 测试全景（第二轮）

| 套件 | 项数 | 结果 |
| --- | --- | --- |
| 既有回归 `regression_v2` | 50 | ✅ |
| 单元 `test_unit_core` | 11 | ✅ |
| `test_seed_smoke`（R1） | 10 | ✅ |
| `test_search_facets`（R2 guard） | 8 | ✅ |
| `test_pdf_fallback`（R3，含失败注入） | 3 | ✅ |
| `test_fts_sync`（R4） | 5 | ✅ |
| **外部 HTTP 端到端 `integration_test.py`** | **40** | ✅（临时库独立服务器 127.0.0.1:5055，覆盖真实 HTTP/WSGI/模板/静态资源；测试后服务器与临时库已清理） |

**新增 guard 敏感度（mutant 2/2 变红）**：注入「丢 DISTINCT（`COUNT(*)`）」→ T2 变红；注入「type facet 漏品牌筛选」→ T1/T7 变红；sanity 8/8 GREEN。证明 `test_search_facets` 对两类错误实现均可击穿。

**真实库完整性**：测试全程使用临时库，`instance/dochub.db` 复检 docs=8 / FTS=8，未受影响。

### 6.4 结论

修复后代码库通过三技能第二轮联调：架构主张与改动后源码逐一对齐、性能优化收益在 1k–20k 全规模复现（3.2×）、87 项进程内测试 + 40 项真实 HTTP 端到端全部通过、新 guard 对错误实现敏感。**未发现新引入的回归或风险，建议将 3 处修改与 5 个新增测试一并提交 git。**

***

*本报告所有数字均来自命令行实测输出（raw samples 已留存于 `tests/bench_perf.py` 运行结果与对话记录），代码定位基于工作树实际源码阅读。*