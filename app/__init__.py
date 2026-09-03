"""DocHub Flask 应用工厂"""
import os
import secrets
import logging
from flask import Flask, render_template, session, request, abort, g
from sqlalchemy import text
from config import Config, select_config
from app.extensions import db, login_manager


def ensure_schema():
    """轻量级 schema 自愈：db.create_all() 不会给已存在的表补列，
    旧版本数据库缺少新增列时查询会直接抛 500。这里用 PRAGMA 检查并 ALTER 补列。"""
    # 表名 -> {列名: 列定义}
    needed = {
        'posts': {
            'product_id': 'INTEGER REFERENCES products(id)',
        },
    }
    for table, columns in needed.items():
        rows = db.session.execute(text(f'PRAGMA table_info({table})')).fetchall()
        existing = {r[1] for r in rows}
        for col, ddl in columns.items():
            if rows and col not in existing:
                db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}'))
        db.session.commit()


def normalize_password_hashes():
    """把旧版本用 scrypt 生成的种子账号密码哈希迁移为 pbkdf2:sha256。

    背景：Werkzeug 3.x 默认 scrypt，而 macOS 自带 /usr/bin/python3(3.9) 的
    hashlib 无 scrypt，旧库中的 scrypt 哈希在该解释器下永远无法校验通过。
    种子账号口令以 app/seed.py 为准（仅在哈希不是 pbkdf2 时重写，幂等）。"""
    from app.models import User, PASSWORD_HASH_METHOD
    seed_credentials = {'admin': 'admin123', 'demo': 'demo123'}
    changed = False
    for username, plaintext in seed_credentials.items():
        user = User.query.filter_by(username=username).first()
        if user and not (user.password_hash or '').startswith('pbkdf2:'):
            user.set_password(plaintext)
            changed = True
    if changed:
        db.session.commit()


def ensure_admin_role():
    """确保种子管理员账号具有 admin 角色（旧库可能在引入审核后台前已创建，幂等修正 #8）。"""
    from app.models import User
    admin = User.query.filter_by(username='admin').first()
    if admin and admin.role != 'admin':
        admin.role = 'admin'
        db.session.commit()


def get_csrf_token():
    """生成或获取当前会话的 CSRF 令牌"""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def create_app(config_class=None):
    """创建并配置 Flask 应用。

    config_class 显式传入时优先使用；否则按环境变量 APP_ENV
    （development/production，默认 development）选择配置。"""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class or select_config())

    # 生产环境位于 nginx 等反向代理之后：信任 1 跳 X-Forwarded-* ，
    # 使 request.scheme/remote_addr 正确，Secure Cookie 与 HSTS 才能按 HTTPS 生效（#2）
    if os.environ.get('APP_ENV', 'development').lower() == 'production':
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # 确保 instance 目录和上传目录存在
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # 初始化扩展
    db.init_app(app)
    login_manager.init_app(app)

    # 注册蓝图
    from app.blueprints.main import main_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.api import api_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(api_bp, url_prefix='/api')

    # 注册错误处理器
    @app.errorhandler(404)
    def not_found(error):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def internal_error(error):
        # 记录完整堆栈，避免登录等接口 500 时无任何可定位信息
        app.logger.exception('未处理的服务器错误: %s', error)
        db.session.rollback()
        return render_template('errors/500.html'), 500

    # 每请求生成 CSP nonce，供内联 <style>/<script> 白名单使用（#4）
    @app.before_request
    def _assign_csp_nonce():
        g.csp_nonce = secrets.token_hex(16)

    # CSRF 防护：对所有非安全方法校验令牌
    @app.before_request
    def csrf_protect():
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            expected = session.get('csrf_token')
            if not expected:
                abort(400, 'CSRF token missing in session')
            submitted = (
                request.form.get('csrf_token')
                or request.headers.get('X-CSRF-Token')
            )
            if not submitted:
                data = request.get_json(silent=True)
                if data:
                    submitted = data.get('csrf_token')
            if not submitted or submitted != expected:
                abort(400, 'CSRF token validation failed')

    # 全局上下文处理器：注入当前年份、CSRF 令牌等通用变量
    @app.context_processor
    def inject_globals():
        from datetime import datetime
        return {
            'current_year': datetime.now().year,
            'csrf_token': get_csrf_token(),
            'csp_nonce': getattr(g, 'csp_nonce', None) or secrets.token_hex(16),
        }

    # 统一安全响应头。CSP 用每请求 nonce 白名单内联 <style>/<script>，
    # 不再使用 'unsafe-inline'（#4）；外部脚本/样式仅允许同源。
    @app.after_request
    def set_security_headers(response):
        nonce = getattr(g, 'csp_nonce', None) or secrets.token_hex(16)
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            f"style-src 'self' 'nonce-{nonce}'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "object-src 'none'; "
            "frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
        )
        # 仅在显式启用 HTTPS（生产配置）时下发 HSTS
        if app.config.get('HSTS_ENABLED'):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

    # 启动时自动建表、补列、校验全文索引（旧库自愈，避免 schema 漂移导致 500）
    with app.app_context():
        db.create_all()
        ensure_schema()
        normalize_password_hashes()
        ensure_admin_role()
        # 初始化全文检索索引，并在文档数与索引行数不一致时自动重建（自愈）
        from app.search import ensure_index
        ensure_index()

    if not app.debug:
        logging.basicConfig(level=logging.INFO)

    return app
