#!/usr/bin/env python3
"""R1 冒烟测试：app/seed.py 可完整执行（修复 HEAD 的 Post NameError），并在临时库产生预期种子数据。

运行方式：python3 tests/test_seed_smoke.py
安全边界：使用 tempfile 临时 SQLite 库，绝不触碰 instance/dochub.db。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config


def _make_config(db_path):
    class _SmokeConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + db_path
        SECRET_KEY = 'smoke'
        WTF_CSRF_ENABLED = False
    return _SmokeConfig


def main():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    from app import create_app
    from app.seed import seed
    from app.extensions import db
    from app.models import User, Category, Brand, Product, Document, Post, Term
    from sqlalchemy import text

    try:
        app = create_app(_make_config(tmp.name))
        with app.app_context():
            seed(app=app)   # 注入临时库应用
            counts = {
                'users': User.query.count(),
                'categories': Category.query.count(),
                'brands': Brand.query.count(),
                'products': Product.query.count(),
                'documents': Document.query.count(),
                'posts': Post.query.count(),
                'terms': Term.query.count(),
                'fts': db.session.execute(
                    text('SELECT COUNT(*) FROM document_fts')).scalar(),
            }
            expected = {'users': 2, 'categories': 5, 'brands': 3,
                        'products': 5, 'documents': 8, 'posts': 4,
                        'terms': 15, 'fts': 8}
            ok = True
            for k, want in expected.items():
                got = counts[k]
                mark = 'PASS' if got == want else 'FAIL'
                if got != want:
                    ok = False
                print(f'{mark} | {k}: {got} (期望 {want})')
            # 附加：默认账号存在
            admin = User.query.filter_by(username='admin').first()
            demo = User.query.filter_by(username='demo').first()
            for name, u in (('admin', admin), ('demo', demo)):
                m = 'PASS' if u is not None else 'FAIL'
                if u is None:
                    ok = False
                print(f'{m} | 默认账号 {name}')
            print('=' * 52)
            print('seed 冒烟测试：', '全部通过' if ok else '存在失败')
            return 0 if ok else 1
    finally:
        for suffix in (tmp.name, tmp.name + '-journal', tmp.name + '-wal'):
            if os.path.exists(suffix):
                os.remove(suffix)


if __name__ == '__main__':
    sys.exit(main())
