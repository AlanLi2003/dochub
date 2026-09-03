#!/usr/bin/env python3
"""R3 测试：PDF 导出降级链（reportlab 缺失 ImportError / PdfExportError → 纯文本 200）。

运行方式：python3 tests/test_pdf_fallback.py（临时库，不碰 instance/dochub.db）
"""
import builtins
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config


def _make_config(db_path):
    class _C(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + db_path
        SECRET_KEY = 'pdf'
        WTF_CSRF_ENABLED = False
    return _C


def main():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    from app import create_app
    from app.seed import seed
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
            # 1) 正常 PDF 导出（seed 文档无 file_path → 走 PDF 分支）
            r = client.get('/doc/1/download')
            check('T1 正常 PDF 导出 application/pdf 200',
                  r.status_code == 200 and r.mimetype == 'application/pdf',
                  (r.status_code, r.mimetype))
            # 2) reportlab 缺失：注入 __import__ ImportError → 纯文本降级
            real_import = builtins.__import__
            def fake_import(name, *a, **k):
                if name == 'app.pdf_export':
                    raise ImportError('reportlab missing (simulated)')
                return real_import(name, *a, **k)
            builtins.__import__ = fake_import
            try:
                r2 = client.get('/doc/1/download')
            finally:
                builtins.__import__ = real_import
            check('T2 reportlab 缺失 → 纯文本降级 200',
                  r2.status_code == 200 and r2.mimetype.startswith('text/plain'),
                  (r2.status_code, r2.mimetype))
            # 3) PdfExportError（中文字体不可用）→ 纯文本降级
            import app.pdf_export as pe
            orig = pe.build_pdf_bytes
            pe.build_pdf_bytes = lambda *a, **k: (_ for _ in ()).throw(
                __import__('app.pdf_export', fromlist=['PdfExportError']).PdfExportError('no font'))
            try:
                r3 = client.get('/doc/1/download')
            finally:
                pe.build_pdf_bytes = orig
            check('T3 PdfExportError → 纯文本降级 200',
                  r3.status_code == 200 and r3.mimetype.startswith('text/plain'),
                  (r3.status_code, r3.mimetype))

            print('=' * 56)
            print('PDF 降级链测试：', '全部通过' if ok else '存在失败')
            return 0 if ok else 1
    finally:
        for suffix in (tmp.name, tmp.name + '-journal', tmp.name + '-wal'):
            if os.path.exists(suffix):
                os.remove(suffix)


if __name__ == '__main__':
    sys.exit(main())
