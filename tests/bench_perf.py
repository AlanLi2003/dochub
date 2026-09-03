#!/usr/bin/env python3
"""DocHub 性能基线测量脚本（establish-baseline，配合三技能联调测试报告使用）。

边界说明
--------
- 使用 Flask 测试客户端（进程内），测得的是「应用处理时间」，不含网络/生产 WSGI 开销；
- 第一部分：种子数据规模（8 文档 / 5 产品 / 3 品牌）下关键端点延迟；
- 第二部分：合成数据库容量探针，测量 search_products 在 1k / 5k / 20k 文档规模下的延迟，
  覆盖 命中查询 / 无命中查询（and→or→prefix 三级回退）/ 仅品牌筛选（无词全表扫描）三类 workload。
- 所有合成数据写入临时 SQLite（mktemp），脚本结束自动清理，不触碰仓库内 instance/dochub.db。

复跑方式：python3 tests/bench_perf.py [--scale 1000,5000,20000] [--samples 20] [--warmup 3]
"""
import os
import sys
import time
import json
import math
import tempfile
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WARMUP = 3
SAMPLES = 20
SCALES = [1000, 5000, 20000]


def _make_config(db_path, testing=True):
    """构造指向临时数据库的配置类（不改仓库内 config.py）。"""
    from config import Config

    class _BenchConfig(Config):
        TESTING = testing
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + db_path
        SECRET_KEY = 'bench'
        WTF_CSRF_ENABLED = False

    return _BenchConfig


def _timer(fn):
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def _stats(samples):
    s = sorted(samples)
    mean = sum(s) / len(s)
    p50 = s[len(s) // 2]
    p95 = s[int(len(s) * 0.95) - 1]
    return {'mean_ms': round(mean * 1000, 3), 'p50_ms': round(p50 * 1000, 3),
            'p95_ms': round(p95 * 1000, 3), 'n': len(s)}


def run_endpoint_baseline(app, samples=SAMPLES, warmup=WARMUP):
    """第一部分：种子数据规模下的端点延迟基线。"""
    results = {}
    with app.test_client() as c:
        paths = [
            ('GET /', 'GET', '/'),
            ('GET /search?q=小米', 'GET', '/search?q=%E5%B0%8F%E7%B1%B3'),
            ('GET /search?q=小米&doc_type=manual', 'GET',
             '/search?q=%E5%B0%8F%E7%B1%B3&doc_type=manual'),
            ('GET /search?q=WiFi(正文词不命中)', 'GET', '/search?q=WiFi'),
            ('GET /search?brand=1(无词仅筛选)', 'GET', '/search?brand=1'),
            ('GET /api/search/suggest?q=小米', 'GET', '/api/search/suggest?q=%E5%B0%8F%E7%B1%B3'),
            ('GET /doc/1(阅读+markdown渲染)', 'GET', '/doc/1'),
            ('GET /categories', 'GET', '/categories'),
        ]
        # warmup
        for _ in range(warmup):
            for _, m, p in paths:
                c.open(p, method=m)
        # measure
        for name, m, p in paths:
            samples_raw = []
            for _ in range(samples):
                samples_raw.append(_timer(lambda: c.open(p, method=m)))
            results[name] = _stats(samples_raw)
    return results


def _seed_synthetic(db_path, n_docs):
    """在临时数据库写入合成数据并重建 FTS 索引，返回 (brands, products_per_brand, docs_per_product)。"""
    from app import create_app
    from app.extensions import db
    from app.models import Brand, Product, Document
    from app.search import rebuild_index

    cfg = _make_config(db_path)
    app = create_app(cfg)
    with app.app_context():
        db.drop_all()
        db.create_all()
        # 50 个品牌，每个品牌下 (n_docs/50/20) 个产品，每产品 20 篇文档
        n_brands = 50
        per_brand = max(1, n_docs // n_brands // 20)
        brands = []
        for b in range(n_brands):
            brand = Brand(name=f'测试品牌{b:02d}', status='active')
            db.session.add(brand)
            brands.append(brand)
        db.session.commit()
        products = []
        for bi, brand in enumerate(brands):
            for pj in range(per_brand):
                prod = Product(brand_id=brand.id, name=f'测试产品{bi:02d}-{pj:03d}', status='active')
                db.session.add(prod)
                products.append(prod)
        db.session.commit()
        cnt = 0
        for pi, prod in enumerate(products):
            for k in range(20):
                if cnt >= n_docs:
                    break
                db.session.add(Document(
                    product_id=prod.id, title=f'文档{pi}-{k} 测试标题',
                    doc_type='manual', content='正文内容 ' * 50,
                    status='published', view_count=k * 10,
                ))
                cnt += 1
            if cnt >= n_docs:
                break
        db.session.commit()
        n = rebuild_index()
        return app, n


def run_search_scale_probe(scales):
    """第二部分：search_products 在多个文档规模下的延迟（容量边界探针）。"""
    from app.search import search_products
    workloads = {
        'q=品牌全称(命中)': lambda: search_products(q='测试品牌00'),
        'q=无命中词(and→or→prefix回退)': lambda: search_products(q='zzzz不存在的词'),
        '仅品牌筛选(无词全表扫描)': lambda: search_products(q='', brand_id=1),
        'q=产品全称(命中)': lambda: search_products(q='测试产品00-000'),
    }
    out = {}
    for scale in scales:
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        tmp.close()
        try:
            app, n = _seed_synthetic(tmp.name, scale)
            with app.app_context():
                # warmup（含 jieba 首次加载）
                for fn in workloads.values():
                    for _ in range(WARMUP):
                        fn()
                row = {'docs': n}
                for name, fn in workloads.items():
                    raw = [_timer(fn) for _ in range(SAMPLES)]
                    row[name] = _stats(raw)
                out[f'@{scale}'] = row
        finally:
            for suffix in (tmp.name, tmp.name + '-journal', tmp.name + '-wal'):
                if os.path.exists(suffix):
                    os.remove(suffix)
    return out


def main():
    global SAMPLES, WARMUP, SCALES
    parser = argparse.ArgumentParser()
    parser.add_argument('--scale', default=','.join(str(x) for x in SCALES))
    parser.add_argument('--samples', type=int, default=SAMPLES)
    parser.add_argument('--warmup', type=int, default=WARMUP)
    args = parser.parse_args()
    SCALES = [int(x) for x in args.scale.split(',')]
    SAMPLES, WARMUP = args.samples, args.warmup

    from app import create_app
    import shutil
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_db = os.path.join(base_dir, 'instance', 'dochub.db')
    # Part 1 使用「种子库副本」，避免 /doc/1 等写操作污染真实库
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    shutil.copy2(src_db, tmp.name)
    cfg = _make_config(tmp.name)
    app = create_app(cfg)
    app.config['TESTING'] = True

    print('==== Part 1: 种子数据规模端点延迟 ====')
    baseline = run_endpoint_baseline(app)
    for suffix in (tmp.name, tmp.name + '-journal', tmp.name + '-wal'):
        if os.path.exists(suffix):
            os.remove(suffix)
    for k, v in baseline.items():
        print(f"{k:45s} {v}")

    print('\n==== Part 2: 搜索容量探针（search_products 延迟） ====')
    probe = run_search_scale_probe(SCALES)
    for scale, row in probe.items():
        print(f"\n--- {scale} (docs={row['docs']}) ---")
        for name, v in row.items():
            if name == 'docs':
                continue
            print(f"{name:40s} {v}")

    print('\n==== 原始样本留存 ====')
    print(json.dumps({'endpoints': baseline, 'scale_probe': probe},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
