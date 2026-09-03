#!/usr/bin/env python3
"""更广的参数矩阵探测 + 数据库实况检查，定位崩溃触发条件。"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import User, Brand, Document, Product, Post
from sqlalchemy import text

app = create_app()
app.config['PROPAGATE_EXCEPTIONS'] = True

with app.app_context():
    print('--- users ---')
    for u in User.query.all():
        print(u.id, repr(u.username), repr(u.email), 'hash_len=', len(u.password_hash or ''), u.role)
    print('--- posts schema cols ---')
    cols = [r[1] for r in db.session.execute(text('PRAGMA table_info(posts)')).fetchall()]
    print(cols)
    print('--- docs count / brands ---')
    print('docs', Document.query.count(), 'brands', Brand.query.count())
    print('fts count', db.session.execute(text('SELECT COUNT(*) FROM document_fts')).scalar())
    print('--- 每品牌文档 ---')
    for b in Brand.query.all():
        print(b.id, b.name, b.website_url, Document.query.join(Product).filter(Product.brand_id==b.id).count())

cases = [
    '/search?q=%E5%B0%8F%E7%B1%B3',
    '/search?q=%E5%B0%8F%E7%B1%B3&brand=1',
    '/search?q=%E5%B0%8F%E7%B1%B3&brand=1&brand=3',
    '/search?q=%E8%B7%AF%E7%94%B1%E5%99%A8&doc_type=manual&doc_type=quick_start',
    '/search?brand=1',
    '/search?doc_type=faq',
    '/search?brand=abc',
    '/search?q=Excel&sort=views&page=99',
    '/search?q=',
    '/categories',
    '/categories?category=1',
    '/community',
]

with app.test_client() as c:
    c.get('/')
    for url in cases:
        try:
            r = c.get(url)
            print(f'{r.status_code}  {url}')
        except Exception:
            print(f'EXC  {url}')
            traceback.print_exc()

    # 登录矩阵
    for ident, pw in [('demo','demo123'), ('admin','admin123'), ('demo','wrong'), ('nobody','x123456')]:
        with c.session_transaction() as s:
            tok = s.get('csrf_token')
        try:
            r = c.post('/auth/login', data={'identifier':ident,'password':pw,'csrf_token':tok or ''},
                       follow_redirects=False)
            print(f'LOGIN {ident}/{pw} -> {r.status_code} {r.headers.get("Location")}')
        except Exception:
            print(f'LOGIN {ident}/{pw} -> EXC')
            traceback.print_exc()
