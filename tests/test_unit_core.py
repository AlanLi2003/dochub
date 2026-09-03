#!/usr/bin/env python3
"""DocHub 核心逻辑单元测试（配合三技能联调测试报告）。

运行方式：python3 tests/test_unit_core.py
约定：沿用 tests/regression_v2.py 的 check() 运行器与 PASS/FAIL 输出；仅用标准库。
范围：开放重定向防护、滑动窗口限流、登录指数退避、检索 token/聚合/高亮、安全 URL、目录提取。
通过公开入口与调用者可观察结果取证；从模块属性运行时取值，便于变异探针注入。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.blueprints import auth as auth_mod      # safe_next
from app import security as security_mod          # SlidingWindowLimiter / LoginGuard
from app import search as search_mod              # query_tokens / _aggregate / highlight_tokens
from app.blueprints import main as main_mod       # is_safe_url / extract_toc

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), str(detail)))
    print(('PASS' if ok else 'FAIL'), '|', name, ('| ' + str(detail)) if detail else '')


# ============================================================
# 契约 → 反例 编号表（每条对应一个唯一错误实现）
#   T1  safe_next('/profile') 应放行      -> 错误实现：一律返回 None
#   T2  safe_next('//evil.com') 应拒绝    -> 错误实现：仅拒绝带 scheme
#   T3  safe_next('https://evil.com') 拒  -> 错误实现：不检查 scheme/netloc
#   T4  safe_next('/\\evil.com') 应拒绝   -> 错误实现：仅拒绝 '//'
#   T5  safe_next('/a/../b') 应放行(不过度拦截) -> 错误实现：拒绝含 '..'
#   T6  限流 window 内 max_hits 放行，第 max_hits+1 次拒 -> 错误实现：计数不增/永放行
#   T7  滑动窗口过期后恢复放行           -> 错误实现：不清除过期命中
#   T8  登录第5次失败触发锁定             -> 错误实现：阈值错位
#   T9  第6次失败锁定时长指数增长(~60s)   -> 错误实现：恒定30s
#   T10 clear() 复位失败计数              -> 错误实现：clear 不重置
#   T11 identifier 大小写/空白归一        -> 错误实现：区分大小写
#   T12 highlight_tokens XSS 转义         -> 错误实现：不转义原文
#   T13 highlight_tokens 重叠 span 合并   -> 错误实现：不合并
#   T14 highlight_tokens 大小写不敏感     -> 错误实现：区分大小写
#   T15 _aggregate 视图求和/字段映射      -> 错误实现：不求和/错位
#   T16 _aggregate best=min(bm25)         -> 错误实现：取 max
#   T17 _aggregate latest=max(created_at) -> 错误实现：不更新 latest
#   T18 query_tokens 过滤纯符号/空 token  -> 错误实现：保留符号/空串
#   T19 is_safe_url 危险协议拦截          -> 错误实现：不拦截 javascript:
#   T20 extract_toc 级别映射/忽略正文     -> 错误实现：级别错乱/含正文行
# ============================================================

# ---------- T1（探针）：safe_next 放行站内相对路径 ----------
def test_safe_next_allow():
    assert auth_mod.safe_next('/profile') == '/profile'
    assert auth_mod.safe_next('/a/../b') == '/a/../b'   # 不过度拦截


# ---------- T2/T3/T4：拒绝外跳 ----------
def test_safe_next_reject_proto_relative():
    assert auth_mod.safe_next('//evil.com') is None      # 协议相对
    assert auth_mod.safe_next('/\\evil.com') is None     # 反斜杠变体
    assert auth_mod.safe_next('https://evil.com') is None
    assert auth_mod.safe_next('http://evil.com/path') is None
    assert auth_mod.safe_next('') is None
    assert auth_mod.safe_next(None) is None


# ---------- T6/T7：滑动窗口限流 ----------
def test_sliding_window_limit():
    lim = security_mod.SlidingWindowLimiter()
    base = [0.0]
    orig = security_mod.time.monotonic
    security_mod.time.monotonic = lambda: base[0]
    try:
        # window=10, max_hits=3：前3次放行，第4次拒绝且 retry_after>0
        for i in range(3):
            ok, remaining, retry = lim.check('k', 3, 10)
            assert ok and remaining == 3 - (i + 1), (i, remaining)
        ok, remaining, retry = lim.check('k', 3, 10)
        assert not ok and retry > 0, (ok, retry)
        # T7：窗口滑动后过期命中清除，恢复放行
        base[0] = 10.1
        ok, _, _ = lim.check('k', 3, 10)
        assert ok, '窗口过期后应恢复放行'
    finally:
        security_mod.time.monotonic = orig


# ---------- T8/T9/T10/T11：登录指数退避 ----------
def test_login_guard_lock():
    guard = security_mod.LoginGuard()
    base = [0.0]
    orig = security_mod.time.monotonic
    security_mod.time.monotonic = lambda: base[0]
    try:
        # 前4次失败不锁
        for i in range(4):
            assert guard.record_failure('admin') == 0, i
            assert guard.locked_for('admin') == 0, i
        # 第5次触发锁定
        lock = guard.record_failure('admin')
        assert lock == security_mod.LoginGuard.BASE_LOCK_SECONDS, lock
        assert guard.locked_for('admin') > 0
        # T9：第6次指数增长 ≈ 60s
        lock6 = guard.record_failure('admin')
        assert lock6 == security_mod.LoginGuard.BASE_LOCK_SECONDS * 2, lock6
        # T11：大小写/空白归一（'  ADMIN  ' 与 'admin' 同 key）
        assert guard.locked_for('  ADMIN  ') > 0, 'identifier 应大小写/空白归一'
        # T10：clear 复位
        guard.clear('admin')
        assert guard.locked_for('admin') == 0
        assert guard.record_failure('admin') == 0
    finally:
        security_mod.time.monotonic = orig


# ---------- T12/T13/T14：highlight_tokens ----------
def test_highlight_xss_safe():
    out = search_mod.highlight_tokens('<script>alert(1)</script>', ['script'])
    assert '<script>' not in out, out          # 原文已转义
    assert '<mark>script</mark>' in out, out   # token 仍被高亮
    # 空输入
    assert search_mod.highlight_tokens('', []) == ''
    assert search_mod.highlight_tokens(None, ['x']) == ''


def test_highlight_overlap_merge():
    # 不同 token 的命中区间重叠（'ab' 覆盖 0-2，'abc' 覆盖 0-3），应合并为单个 mark
    out = search_mod.highlight_tokens('abc', ['ab', 'abc'])
    assert out.count('<mark>') == 1 and '<mark>abc</mark>' in out, out


def test_highlight_case_insensitive():
    out = search_mod.highlight_tokens('Hello WORLD', ['world'])
    assert '<mark>WORLD</mark>' in out, out


# ---------- T15/T16/T17：_aggregate ----------
def test_aggregate_fields():
    rows = [
        (1, 100, 'manual', '2026-01-01 00:00:00', 10, 1, 3.0),
        (2, 100, 'faq', '2026-01-02 00:00:00', 5, 1, 2.0),
        (3, 200, 'manual', '2026-01-01 00:00:00', 7, 2, 1.0),
    ]
    agg = search_mod._aggregate(rows)
    g100, g200 = agg[100], agg[200]
    assert g100['doccnt'] == 2 and g100['views'] == 15, g100      # 求和
    assert g100['bid'] == 1
    assert g200['doccnt'] == 1 and g200['views'] == 7
    # T16：best = min(bm25)（bm25 越小越相关）
    assert g100['best'] == 2.0, g100
    # T17：latest = max(created_at)
    assert g100['latest'] == '2026-01-02 00:00:00', g100


# ---------- T18：query_tokens ----------
def test_query_tokens():
    toks = search_mod.query_tokens('小米 路由器')
    assert toks and all(t.strip() for t in toks), toks
    assert search_mod.query_tokens('!!!') == []
    assert search_mod.query_tokens('') == []
    assert search_mod.query_tokens(None) == []


# ---------- T19：is_safe_url ----------
def test_is_safe_url():
    assert not main_mod.is_safe_url('javascript:alert(1)')
    assert not main_mod.is_safe_url('data:text/html,<script>')
    assert main_mod.is_safe_url('https://example.com')
    assert main_mod.is_safe_url('/relative/path')
    assert main_mod.is_safe_url('#anchor')
    assert not main_mod.is_safe_url('')
    assert not main_mod.is_safe_url(None)


# ---------- T20：extract_toc ----------
def test_extract_toc():
    md = ('# 标题一\n正文行\n## 标题二\n### 标题三\n普通段落')
    toc = main_mod.extract_toc(md)
    assert [t['level'] for t in toc] == [1, 2, 3], toc
    assert [t['text'] for t in toc] == ['标题一', '标题二', '标题三'], toc


# ============================================================
# 运行
# ============================================================
def run_all():
    # 探针先行
    try:
        test_safe_next_allow()
        check('T1(探针) safe_next 放行站内相对路径', True)
    except AssertionError as e:
        check('T1(探针) safe_next 放行站内相对路径', False, e)

    for fn in (test_safe_next_reject_proto_relative,
               test_sliding_window_limit,
               test_login_guard_lock,
               test_highlight_xss_safe,
               test_highlight_overlap_merge,
               test_highlight_case_insensitive,
               test_aggregate_fields,
               test_query_tokens,
               test_is_safe_url,
               test_extract_toc):
        name = fn.__name__
        try:
            fn()
            check(name, True)
        except AssertionError as e:
            check(name, False, e)

    print('\n' + '=' * 64)
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f'单元测试结果：{passed}/{total} 通过')
    for name, ok, detail in results:
        if not ok:
            print('  FAIL -', name, detail)
    print('=' * 64)
    return passed == total


if __name__ == '__main__':
    sys.exit(0 if run_all() else 1)
