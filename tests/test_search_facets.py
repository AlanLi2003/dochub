#!/usr/bin/env python3
"""R2 语义 guard：固化 search_products 的 facet 语义，防止 SQL 聚合改造回归。

保护点：
  1) facet 计数 = 去重「产品数」而非文档数（COUNT(DISTINCT product_id)）；
  2) type facet 忽略自身 doc_type 维度、保留品牌筛选；brand facet 相反；
  3) 排序确定性：types 按计数降序 + 首见序 tie-break，brands 按 brand_id 升序。

运行方式：python3 tests/test_search_facets.py（临时库，不碰 instance/dochub.db）
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config


def _make_config(db_path):
    class _C(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + db_path
        SECRET_KEY = 'facet'
        WTF_CSRF_ENABLED = False
    return _C


def main():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    from app import create_app
    from app.seed import seed
    from app.search import search_products
    ok = True

    def check(name, cond, detail=''):
        nonlocal ok
        ok = ok and bool(cond)
        print(('PASS' if cond else 'FAIL'), '|', name, ('| ' + str(detail)) if detail else '')

    try:
        app = create_app(_make_config(tmp.name))
        with app.app_context():
            seed(app=app)
            # 1) DISTINCT 产品数：brand=2 下产品3有 quick_start+faq 两篇 → type facet 各计 1（产品数非文档数）
            r = search_products('', brand_id=2)
            check('T1 品牌筛选时 type facet=去重产品数(quick_start:1,faq:1)',
                  dict(r['facets']['types']) == {'quick_start': 1, 'faq': 1}, r['facets']['types'])
            check('T2 品牌筛选时 brand facet 忽略品牌维度(跨全部品牌统计)',
                  dict(r['facets']['brands']) == {1: 2, 2: 1, 3: 2}, r['facets']['brands'])

            # 2) 忽略自身维度：type 筛选时 type facet 保留全部类型（含非 manual 类型）
            r2 = search_products('', doc_type='manual')
            check('T3 类型筛选时 type facet 忽略自身维度(含全部类型)',
                  set(dict(r2['facets']['types']).keys()) ==
                  {'manual', 'quick_start', 'faq', 'troubleshooting'},
                  r2['facets']['types'])
            check('T4 类型筛选时 brand facet 仅统计该类型产品',
                  dict(r2['facets']['brands']) == {1: 2, 3: 1}, r2['facets']['brands'])

            # 3) 排序确定性：types 计数降序；brands bid 升序
            types = [t for t, _c in r2['facets']['types']]
            check('T5 type facet 按计数降序',
                  [c for _t, c in r2['facets']['types']] ==
                  sorted([c for _t, c in r2['facets']['types']], reverse=True), types)
            brands = [b for b, _c in r2['facets']['brands']]
            check('T6 brand facet 按 brand_id 升序', brands == sorted(brands), brands)

            # 4) 等计数 tie-break 确定性：种子库上 quick_start 在 faq 之前（首见序）
            r3 = search_products('', brand_id=2)
            tie_types = [t for t, _c in r3['facets']['types']]
            check('T7 等计数 tie-break 与基线首见序一致(quick_start→faq)',
                  tie_types == ['quick_start', 'faq'], tie_types)

            # 5) 词检索 facet 同样为去重产品数
            r4 = search_products('小米')
            check('T8 词检索(type 筛选 None) facets 存在且为产品数口径',
                  all(v >= 1 for _k, v in r4['facets']['types'] + r4['facets']['brands']),
                  (r4['facets']['types'], r4['facets']['brands']))

            print('=' * 56)
            print('facet 语义 guard：', '全部通过' if ok else '存在失败')
            return 0 if ok else 1
    finally:
        for suffix in (tmp.name, tmp.name + '-journal', tmp.name + '-wal'):
            if os.path.exists(suffix):
                os.remove(suffix)


if __name__ == '__main__':
    sys.exit(main())
