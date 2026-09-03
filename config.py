"""DocHub 应用配置类"""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def load_env_file(path=None):
    """极简 .env 加载器（不引入 python-dotenv 依赖）。

    仅填充当前进程尚未设置的变量（真实环境变量优先），支持 # 注释与 export 前缀。
    .env 已被 .gitignore 忽略，适合在服务器上注入 SECRET_KEY 等生产密钥。"""
    env_path = path or os.path.join(BASE_DIR, '.env')
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding='utf-8') as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                if line.startswith('export '):
                    line = line[len('export '):].strip()
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        # .env 读取失败不应阻断启动（真实环境变量仍可生效）
        pass


load_env_file()


class Config:
    """基础配置"""
    # 安全密钥：生产环境必须通过 SECRET_KEY 环境变量设置，否则使用开发回退值
    SECRET_KEY = os.environ.get(
        'SECRET_KEY',
        'dochub-dev-secret-key-2026-change-in-production'
    )

    # 数据库配置 - SQLite 单文件
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'dochub.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 文件上传配置
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'app', 'static', 'uploads')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    # 注意：刻意不包含 svg——SVG 可内嵌脚本，同源托管会形成存储型 XSS 面
    ALLOWED_EXTENSIONS = {'pdf', 'docx', 'md', 'txt', 'png', 'jpg', 'jpeg'}

    # 文档上传允许的文件类型
    ALLOWED_DOC_EXTENSIONS = {'pdf', 'docx', 'md', 'txt'}
    # 图片上传允许的文件类型（位图；SVG 需独立域名或强制下载，故不放行）
    ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg'}

    # Flask-Login 配置
    LOGIN_VIEW = 'auth.login'
    LOGIN_MESSAGE = '请先登录后再访问该页面'
    LOGIN_MESSAGE_CATEGORY = 'warning'

    # 分页配置
    ITEMS_PER_PAGE = 20

    @staticmethod
    def init_app(app):
        """初始化应用时调用，可用于注册扩展等"""
        pass


class ProductionConfig(Config):
    """生产环境配置：强制外部密钥、HTTPS-only Cookie、安全传输。"""

    # 生产环境不允许使用内置开发回退密钥（缺失时的快速失败在 select_config 中触发，
    # 避免类定义期抛错导致开发环境也无法导入本模块）
    SECRET_KEY = os.environ.get('SECRET_KEY')

    # 数据库允许用 DATABASE_URL 覆盖（默认仍为 SQLite，便于单机部署）
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'dochub.db'),
    )

    # Cookie 仅在 HTTPS 下回传，缓解会话劫持；SameSite=Lax 防跨站携带
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'

    # 开启 HSTS 响应头（由应用工厂读取）
    HSTS_ENABLED = True


class DevelopmentConfig(Config):
    """开发环境配置：允许回退密钥、不强制 HTTPS Cookie。"""
    HSTS_ENABLED = False


_CONFIGS = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
}


def select_config():
    """按 APP_ENV 选择配置类，默认 development。"""
    env = os.environ.get('APP_ENV', 'development').lower()
    if env == 'production' and not os.environ.get('SECRET_KEY'):
        # 仅在真正启用生产配置时快速失败，避免会话被开发回退密钥伪造
        raise RuntimeError(
            '生产环境必须通过环境变量 SECRET_KEY 设置强随机密钥，例如：'
            'SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))")'
        )
    return _CONFIGS.get(env, DevelopmentConfig)
