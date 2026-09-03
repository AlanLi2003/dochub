"""认证蓝图：注册、登录、登出"""
import re
import secrets
from urllib.parse import urlparse
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db
from app.models import User
from app.security import check_ip_rate, login_guard

auth_bp = Blueprint('auth', __name__)

# 限流参数：登录每 IP 每分钟最多 12 次尝试；注册每 IP 每分钟最多 5 次
LOGIN_IP_MAX, LOGIN_IP_WINDOW = 12, 60
REGISTER_IP_MAX, REGISTER_IP_WINDOW = 5, 60

# 邮箱格式正则
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def _rotate_session():
    """登录/注册成功后清空匿名期会话并重建 CSRF 令牌，防会话固定攻击（#5）。
    必须在 login_user 之前调用：login_user 随后写入新的身份信息。"""
    from flask import session
    session.clear()
    session['csrf_token'] = secrets.token_hex(32)
    session.permanent = False


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
        # 注册接口按 IP 限流，防批量注册/撞库
        allowed, retry_after = check_ip_rate('register', REGISTER_IP_MAX, REGISTER_IP_WINDOW)
        if not allowed:
            flash(f'操作过于频繁，请 {retry_after} 秒后再试', 'error')
            return render_template('auth/register.html'), 429

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

        # 自动登录（先轮换会话，防会话固定 #5）
        _rotate_session()
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

        # 按 IP 限流，缓解撞库/爆破
        allowed, retry_after = check_ip_rate('login', LOGIN_IP_MAX, LOGIN_IP_WINDOW)
        if not allowed:
            flash(f'尝试过于频繁，请 {retry_after} 秒后再登录', 'error')
            return render_template('auth/login.html', identifier=identifier), 429

        # 按账号的连续失败退避（指数锁定）
        guard = login_guard()
        lock_left = guard.locked_for(identifier)
        if lock_left > 0:
            flash(f'该账号失败次数过多，已临时锁定，请 {lock_left} 秒后再试', 'error')
            return render_template('auth/login.html', identifier=identifier), 429

        # 任何数据库 / 会话异常都回滚并回到登录页，绝不向用户抛裸 500
        try:
            # 支持用户名或邮箱登录
            user = User.query.filter(
                (User.username == identifier) | (User.email == identifier)
            ).first()

            if user is None or not user.check_password(password):
                # 记录失败并在达到阈值后锁定该账号
                lock_secs = guard.record_failure(identifier)
                if lock_secs:
                    flash(f'密码连续错误，账号已锁定 {lock_secs} 秒', 'error')
                else:
                    flash('用户名/邮箱或密码错误', 'error')
                return render_template('auth/login.html', identifier=identifier), 401

            # 登录成功：清除失败计数；先轮换会话与 CSRF 令牌（防会话固定 #5），再写入身份
            guard.clear(identifier)
            _rotate_session()
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
