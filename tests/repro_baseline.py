#!/usr/bin/env python3
"""基线复现脚本：复现用户报告的两个崩溃，抓取真实堆栈。只读探测，不改数据。"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import Brand

app = create_app()
app.config['PROPAGATE_EXCEPTIONS'] = True
app.testing = True


def show(title, fn):
    print('\n' + '=' * 70)
    print(title)
    print('=' * 70)
    try:
        fn()
        print('[OK] 未出现异常')
    except Exception:
        traceback.print_exc()


with app.test_client() as c:
    # 拿一次首页以建立 session / csrf
    c.get('/')

    # 问题1：勾选品牌“小米”后点搜索。先找小米 id
    with app.app_context():
        xm = Brand.query.filter_by(name='小米').first()
        xm_id = xm.id if xm else 1
        print('小米 brand id =', xm_id, '| 全部品牌:',
              [(b.id, b.name) for b in Brand.query.all()])

    def case_brand_filter():
        resp = c.get(f'/search?q=%E5%B0%8F%E7%B1%B3&brand={xm_id}')
        print('status:', resp.status_code)
        if resp.status_code >= 500:
            raise RuntimeError(f'HTTP {resp.status_code}')

    show('问题1：搜索词=小米 且勾选品牌小米', case_brand_filter)

    def case_brand_only_no_q():
        # 截图1：q 为空、仅筛选
        resp = c.get(f'/search?brand={xm_id}')
        print('status:', resp.status_code)
        if resp.status_code >= 500:
            raise RuntimeError(f'HTTP {resp.status_code}')

    show('问题1b：无 q 仅勾选品牌', case_brand_only_no_q)

    # 问题8：登录提交
    def case_login():
        with c.session_transaction() as sess:
            token = sess.get('csrf_token')
        print('csrf token present:', bool(token))
        resp = c.post('/auth/login', data={
            'identifier': 'demo',
            'password': 'demo123',
            'csrf_token': token or '',
        }, follow_redirects=False)
        print('status:', resp.status_code, 'location:', resp.headers.get('Location'))
        if resp.status_code >= 500:
            raise RuntimeError(f'HTTP {resp.status_code}')

    show('问题8：demo 账号登录 POST', case_login)
