"""认证蓝图：注册、登录、登出"""
import re
from urllib.parse import urlparse
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db
from app.models import User

auth_bp = Blueprint('auth', __name__)

# 邮箱格式正则
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def safe_next(target):
    """仅允许站内相对路径跳转，拦截 //host、/\\host 等协议相对 URL（防开放重定向）"""
    if not target:
        return None
    if not target.startswith('/'):
        return None
    if target.startswith('//') or target.startswith('/\\'):
        return None
    parsed = urlparse(target)
    if parsed.netloc or parsed.scheme:
        return None
    return target


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """用户注册"""
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        # 表单验证
        errors = []
        if not username or len(username) < 2:
            errors.append('用户名至少2个字符')
        if not email or not EMAIL_REGEX.match(email):
            errors.append('请输入有效的邮箱地址')
        if not password or len(password) < 6:
            errors.append('密码至少6位')
        if password != confirm_password:
            errors.append('两次输入的密码不一致')

        # 检查用户名和邮箱唯一性
        if User.query.filter_by(username=username).first():
            errors.append('该用户名已被注册')
        if User.query.filter_by(email=email).first():
            errors.append('该邮箱已被注册')

        if errors:
            for err in errors:
                flash(err, 'error')
            return render_template('auth/register.html',
                                   username=username, email=email), 400

        # 创建用户
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        # 自动登录
        login_user(user)
        flash('注册成功，欢迎加入 DocHub！', 'success')
        return redirect(url_for('main.index'))

    return render_template('auth/register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """用户登录"""
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()  # 用户名或邮箱
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'

        if not identifier or not password:
            flash('请输入用户名/邮箱和密码', 'error')
            return render_template('auth/login.html', identifier=identifier), 400

        # 任何数据库 / 会话异常都回滚并回到登录页，绝不向用户抛裸 500
        try:
            # 支持用户名或邮箱登录
            user = User.query.filter(
                (User.username == identifier) | (User.email == identifier)
            ).first()

            if user is None or not user.check_password(password):
                flash('用户名/邮箱或密码错误', 'error')
                return render_template('auth/login.html', identifier=identifier), 401

            login_user(user, remember=remember)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('登录处理失败: %s', exc)
            flash('登录服务暂时异常，请稍后重试', 'error')
            return render_template('auth/login.html', identifier=identifier), 500

        flash('登录成功', 'success')
        # 跳转 next 参数或首页（兼容 query / hidden form 两种携带方式）
        next_page = safe_next(request.args.get('next') or request.form.get('next'))
        return redirect(next_page or url_for('main.index'))

    return render_template('auth/login.html')


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    """用户登出"""
    logout_user()
    flash('已退出登录', 'info')
    return redirect(url_for('main.index'))
