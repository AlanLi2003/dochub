#!/usr/bin/env python3
"""R4 测试：文档写路径 → FTS 索引同步的端到端保证。

审计结论（app/ 全库检索）：
  - 文档写入仅两处：admin 审核通过（main.py:869 → sync_document）与 seed（→ rebuild_index）；
  - 两处均已覆盖 FTS；本测试把「审核通过后新文档立即可被检索」固化为可观察断言，
    防止未来新增写路径时漏接 sync_document。

运行方式：python3 tests/test_fts_sync.py（临时库，不碰 instance/dochub.db）
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config


def _make_config(db_path):
    class _C(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + db_path
        SECRET_KEY = 'fts'
        WTF_CSRF_ENABLED = False
    return _C


def csrf(client):
    with client.session_transaction() as s:
        return s.get('csrf_token')


def main():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    from app import create_app
    from app.seed import seed
    from app.extensions import db
    from app.models import Product, Contribution, Document
    from app.search import fts_count, search_products
    from sqlalchemy import text
    ok = True

    def check(name, cond, detail=''):
        nonlocal ok
        ok = ok and bool(cond)
        print(('PASS' if cond else 'FAIL'), '|', name, ('| ' + str(detail)) if detail else '')

    try:
        app = create_app(_make_config(tmp.name))
        with app.app_context():
            seed(app=app)
            client = app.test_client()
            c = client.get('/auth/login'); tok = csrf(client)
            r = client.post('/auth/login', data={
                'identifier': 'admin', 'password': 'admin123', 'csrf_token': tok})
            check('T0 管理员登录', r.status_code == 302, r.status_code)

            with app.app_context():
                prod = Product.query.filter_by(name='小米路由器 AX3600').first()
                prod_id = prod.id
                prod_name = prod.name
                before_fts = fts_count()
                before_docs = Document.query.filter_by(product_id=prod_id).count()
                contrib = Contribution(
                    user_id=1, contribution_type='document', title='R4同步测试临时文档XQ9',
                    payload_json=json.dumps({'product_id': prod_id, 'doc_type': 'manual',
                                             'description': 'r4-sync', 'content': 'r4 sync body',
                                             'original_url': 'https://example.com'}),
                    status='pending')
                db.session.add(contrib); db.session.commit()
                contrib_id = contrib.id

            tok = csrf(client)
            rr = client.post(f'/admin/contribution/{contrib_id}/approve', data={'csrf_token': tok})

            with app.app_context():
                new_doc = Document.query.filter_by(title='R4同步测试临时文档XQ9').first()
                check('T1 审核通过后新文档已发布', bool(new_doc) and new_doc.status == 'published')
                # FTS 行数 +1（sync_document 生效）
                after_fts = fts_count()
                check('T2 FTS 索引行数 +1', after_fts == before_fts + 1,
                      (before_fts, after_fts))
                # 新文档 rowid 确实在 FTS 中
                in_fts = db.session.execute(text(
                    'SELECT COUNT(*) FROM document_fts WHERE rowid = :id'),
                    {'id': new_doc.id}).scalar()
                check('T3 新文档 rowid 存在于 FTS', in_fts == 1, in_fts)
                # 立即可被检索：按产品名搜索，该产品 doc_count +1
                res = search_products(prod_name)
                check('T4 审核后按产品名立即可检索到且 doc_count+1',
                      res['total'] >= 1 and res['doc_counts'].get(prod_id, 0) == before_docs + 1,
                      (before_docs, res['doc_counts'].get(prod_id)))

            print('=' * 56)
            print('FTS 同步测试：', '全部通过' if ok else '存在失败')
            return 0 if ok else 1
    finally:
        for suffix in (tmp.name, tmp.name + '-journal', tmp.name + '-wal'):
            if os.path.exists(suffix):
                os.remove(suffix)


if __name__ == '__main__':
    sys.exit(main())
