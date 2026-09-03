"""轻量安全组件：进程内滑动窗口限流 + 登录失败退避（无第三方依赖）。

设计说明：
- 不依赖 Flask-Limiter/Redis，保证在任意标准 Python 解释器下可直接运行；
- 计数器为进程内、线程安全；多 worker(gunicorn) 部署时各进程独立计数，
  属于保守下限，跨进程的强一致限流应由前置 nginx limit_req 补充（见部署文档）。
"""
import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    """固定键的滑动窗口限流器：window 秒内最多 max_hits 次。"""

    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key, max_hits, window):
        """返回 (allowed: bool, remaining: int, retry_after: int 秒)。"""
        now = time.monotonic()
        with self._lock:
            dq = self._hits[key]
            while dq and now - dq[0] > window:
                dq.popleft()
            if len(dq) >= max_hits:
                retry_after = max(1, int(window - (now - dq[0])) + 1)
                return False, 0, retry_after
            dq.append(now)
            return True, max_hits - len(dq), 0

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)


# 全局单例
_ip_limiter = SlidingWindowLimiter()


class LoginGuard:
    """按账号记录连续失败次数，触发指数退避，登录成功后清零。"""

    # 连续失败达到阈值后开始锁定；第 n 次锁定时长 = BASE * 2**(fails-阈值)
    FAIL_THRESHOLD = 5
    BASE_LOCK_SECONDS = 30
    MAX_LOCK_SECONDS = 15 * 60

    def __init__(self):
        self._fails = defaultdict(int)
        self._locked_until = {}
        self._lock = threading.Lock()

    def locked_for(self, identifier):
        """账号当前剩余锁定秒数；未锁定返回 0。"""
        key = (identifier or '').strip().lower()
        with self._lock:
            until = self._locked_until.get(key, 0)
            remain = until - time.monotonic()
            return int(remain) + 1 if remain > 0 else 0

    def record_failure(self, identifier):
        key = (identifier or '').strip().lower()
        with self._lock:
            self._fails[key] += 1
            fails = self._fails[key]
            if fails >= self.FAIL_THRESHOLD:
                extra = fails - self.FAIL_THRESHOLD
                lock = min(self.MAX_LOCK_SECONDS,
                           self.BASE_LOCK_SECONDS * (2 ** extra))
                self._locked_until[key] = time.monotonic() + lock
                return lock
            return 0

    def clear(self, identifier):
        key = (identifier or '').strip().lower()
        with self._lock:
            self._fails.pop(key, None)
            self._locked_until.pop(key, None)


_login_guard = LoginGuard()


def client_ip():
    """获取客户端 IP；反代后取 X-Forwarded-For 首段（nginx 已覆盖该头）。"""
    from flask import request
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def check_ip_rate(scope, max_hits, window):
    """对某 IP 在指定作用域上限流，返回 (allowed, retry_after)。"""
    allowed, _, retry_after = _ip_limiter.check(
        f'{scope}:{client_ip()}', max_hits, window)
    return allowed, retry_after


def login_guard():
    """暴露登录失败退避单例。"""
    return _login_guard


def admin_required(view):
    """管理员专属路由装饰器：未登录跳登录页，已登录但非管理员返回 403。"""
    import functools
    from flask import abort, current_app
    from flask_login import current_user

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return current_app.login_manager.unauthorized()
        if not getattr(current_user, 'is_admin', False):
            abort(403)
        return view(*args, **kwargs)

    return wrapped
