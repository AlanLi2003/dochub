# DocHub - 官方文档聚合阅读平台

> 把散落官网各处的产品说明书，变成一本你能直接翻开的电子书。

DocHub 是一个收录各大官网官方产品使用说明文档的聚合阅读平台，为非技术用户提供"找得到、看得懂、能下载、可交流"的文档查阅体验。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3 + Flask |
| ORM | Flask-SQLAlchemy |
| 认证 | Flask-Login + Werkzeug Security |
| 数据库 | SQLite（单文件，自动创建） |
| 前端 | 原生 HTML/CSS/JS + Jinja2 模板 |
| 设计系统 | 浅蓝白学术风格，Noto Serif SC + Noto Sans SC |

## 项目结构

```
dochub-app/
├── app.py                    # 应用入口
├── config.py                 # 配置类
├── requirements.txt          # 依赖清单
├── README.md                 # 项目说明
├── instance/
│   └── dochub.db             # SQLite 数据库（运行时自动创建）
└── app/
    ├── __init__.py           # app 工厂函数
    ├── models.py             # 12 个数据库模型
    ├── extensions.py         # Flask 扩展实例
    ├── seed.py               # 种子数据初始化脚本
    ├── templates/
    │   ├── base.html         # 通用布局
    │   ├── index.html        # 首页占位
    │   ├── auth/
    │   │   ├── login.html
    │   │   └── register.html
    │   └── errors/
    │       ├── 404.html
    │       └── 500.html
    ├── static/
    │   ├── css/main.css      # 全局样式
    │   ├── js/main.js        # 全局 JS
    │   └── uploads/          # 用户上传目录
    └── blueprints/
        ├── main.py           # 主蓝图（首页）
        ├── auth.py           # 认证蓝图（注册/登录/登出）
        └── api.py            # API 蓝图（搜索联想/分类列表）
```

## 快速开始

### 1. 安装依赖

```bash
pip3 install flask flask-sqlalchemy flask-login
```

或使用 requirements.txt：

```bash
pip3 install -r requirements.txt
```

### 2. 初始化数据库和种子数据

```bash
python3 seed.py
```

种子数据包含：
- **2 个用户**：admin/admin123（管理员），demo/demo123（普通用户）
- **5 个分类**：办公软件、智能家居、网络设备、开发工具、手机应用
- **3 个品牌**：小米、微软、腾讯
- **5 个产品**：小米路由器 AX3600、小米智能音箱、Microsoft Excel、微信小程序、飞书多维表格
- **8 篇文档**：覆盖快速入门/使用手册/FAQ/故障排查等类型，含真实 Markdown 内容
- **15 条术语**：SSID、WiFi 6、端口转发、数据透视表等常见技术术语解释

### 3. 启动应用

```bash
python3 app.py
```

访问 http://127.0.0.1:5000

## 默认账号

| 角色 | 用户名 | 密码 |
|------|--------|------|
| 管理员 | admin | admin123 |
| 普通用户 | demo | demo123 |

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/search/suggest?q=xxx` | GET | 搜索联想，返回匹配的文档标题列表（最多8条） |
| `/api/categories` | GET | 返回所有分类列表（含子分类） |

## 数据库模型

共 12 个模型：User、Brand、Product、Document、Category、Comment、Post、Reply、Favorite、ReadingHistory、Contribution、Term。

关系：Brand 1→N Product，Product 1→N Document，User 1→N Comment/Post/Favorite/History/Contribution。

## 设计规范

- 主色 `#2563EB`，背景 `#F8FAFC`，卡片 `#FFFFFF`，边框 `#E2E8F0`
- 标题字体 Noto Serif SC（衬线），正文字体 Noto Sans SC（无衬线）
- 8px 栅格系统，卡片圆角 8px，按钮圆角 6px
- 禁止：渐变背景、玻璃拟态、emoji 图标
- 所有图标使用内联 SVG（feather icons 风格，2px 描边，圆角端点）

## 开发说明

- 应用使用工厂模式 `create_app()`，扩展在 `extensions.py` 中实例化
- 蓝图按功能模块划分：main（首页）、auth（认证）、api（接口）
- 密码使用 `werkzeug.security` 的 `generate_password_hash` / `check_password_hash`
- 首次启动自动创建数据库表，运行 `seed.py` 初始化种子数据
- 生产环境请修改 `config.py` 中的 `SECRET_KEY`

## 许可证

本项目仅收录官方公开文档，所有文档版权归原作者所有。
