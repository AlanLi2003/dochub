#!/usr/bin/env python3
"""v2 十项问题修复的回归测试（Flask 测试客户端，不依赖外部端口）。"""
import sys, os, io, re, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import Brand, Product, Document, Post, User

app = create_app()
app.config['PROPAGATE_EXCEPTIONS'] = True
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(('PASS' if ok else 'FAIL'), '|', name, ('| ' + str(detail)) if detail else '')


def csrf(client):
    with client.session_transaction() as s:
        return s.get('csrf_token')


with app.test_client() as c:
    # ---------- 公开页面渲染冒烟 ----------
    for path in ['/', '/search', '/categories', '/community', '/doc/1', '/auth/login']:
        r = c.get(path)
        check(f'页面渲染 {path}', r.status_code == 200, f'status={r.status_code}')

    html_home = c.get('/').get_data(as_text=True)

    # ---------- 问题2：首页删除“按分类浏览” ----------
    check('问题2 首页不再出现“按分类浏览”板块', '按分类浏览' not in html_home)

    # ---------- 问题1：品牌筛选 / 空 q 不再崩溃且侧栏完整 ----------
    r = c.get('/search?brand=1')
    h = r.get_data(as_text=True)
    check('问题1a 仅勾选品牌(无q)返回200', r.status_code == 200, r.status_code)
    check('问题1b 无q时侧栏仍渲染文档类型/品牌', '文档类型' in h and '全部品牌' in h)
    r = c.get('/search?q=%E5%B0%8F%E7%B1%B3&brand=1')
    check('问题1c 搜索词+品牌筛选返回200', r.status_code == 200, r.status_code)

    # ---------- 问题10：仅匹配产品/品牌全称，正文不参与 ----------
    r = c.get('/search?q=WiFi')  # WiFi 大量出现在正文，但不是产品/品牌全称
    h_wifi = r.get_data(as_text=True)
    m = re.search(r'找到 <strong[^>]*>(\d+)</strong> 个相关产品', h_wifi)
    wifi_products = int(m.group(1)) if m else -1
    check('问题10 正文词 WiFi 不命中（产品数=0）', wifi_products == 0, f'products={wifi_products}')

    r = c.get('/search?q=%E5%B0%8F%E7%B1%B3')  # 小米：品牌全称
    h_xm = r.get_data(as_text=True)
    m = re.search(r'找到 <strong[^>]*>(\d+)</strong> 个相关产品，\s*共 (\d+) 篇文档', h_xm)
    xm_p, xm_d = (int(m.group(1)), int(m.group(2))) if m else (-1, -1)
    check('问题10/3 品牌“小米”聚合为2个产品、4篇文档', xm_p == 2 and xm_d == 4,
          f'products={xm_p} docs={xm_d}')

    # ---------- 问题3：同产品多文档聚合为一个分组 ----------
    group_cards = h_xm.count('product-group-card')
    check('问题3 结果按产品聚合（2 张产品分组卡）', group_cards == 2, f'cards={group_cards}')
    # AX3600 产品下 3 篇文档在同一张卡内（按分组卡片切分统计）
    cards = re.findall(r'<div class="card product-group-card">(.*?)(?=<div class="card product-group-card">|$)',
                       h_xm, re.S)
    ax_card = [c for c in cards if 'AX3600' in c]
    rows_in_router = ax_card[0].count('product-doc-row') if ax_card else -1
    check('问题3 路由器产品下3篇文档合并展示', rows_in_router == 3, f'rows={rows_in_router}')

    # ---------- 问题9：单选 + 品牌锁定 ----------
    check('问题9a 侧栏无多选 checkbox（改为单选链接）',
          'type="checkbox"' not in h_xm and 'filter-item' in h_xm)
    check('问题9b 搜索词为品牌时锁定，其他品牌置灰',
          'is-disabled' in h_xm and '已自动锁定' in h_xm)
    # 多品牌参数也只生效一个（单选语义）
    r = c.get('/search?q=%E5%B0%8F%E7%B1%B3&brand=1&brand=3')
    check('问题9c 多品牌参数不报错且仍200', r.status_code == 200, r.status_code)

    # ---------- 问题7：分类页图标区分 + 两级浏览 ----------
    h_cat = c.get('/categories').get_data(as_text=True)
    icon_kinds = [k for k in ['icon-home', 'icon-wifi', 'icon-code', 'icon-smartphone']
                  if k in h_cat]
    check('问题7a 分类页不同品类使用不同图标类', len(icon_kinds) >= 3, f'{icon_kinds}')
    r = c.get('/categories?category=%E7%BD%91%E7%BB%9C%E8%AE%BE%E5%A4%87')
    h_cd = r.get_data(as_text=True)
    check('问题7b 品类详情展示品牌→产品→文档', r.status_code == 200 and
          'brand-section' in h_cd and '小米路由器 AX3600' in h_cd, r.status_code)

    # ---------- 问题6：官方原文 / 品牌官网跳转 ----------
    h_doc = c.get('/doc/1').get_data(as_text=True)
    check('问题6a 文档页提供“官方原文”链接', '官方原文' in h_doc and 'mi.com' in h_doc)
    check('问题6b 文档页提供“品牌官网”链接', '品牌官网' in h_doc)

    # ---------- 问题5：PDF 下载 ----------
    r = c.get('/doc/1/download')
    body = r.data
    cd = r.headers.get('Content-Disposition', '')
    is_pdf = r.headers.get('Content-Type', '').startswith('application/pdf') and body[:4] == b'%PDF'
    check('问题5a 下载为 application/pdf 且文件头正确', is_pdf,
          f"{r.headers.get('Content-Type')} head={body[:4]}")
    check('问题5b 文件名规范为 品牌-产品-类型.pdf', '.pdf' in cd and '%E5%B0%8F%E7%B1%B3' in cd, cd[:120])

    # ---------- 问题4：社区产品定向 ----------
    with app.app_context():
        router_pid = Product.query.filter_by(name='小米路由器 AX3600').first().id
    r = c.get(f'/community?product={router_pid}')
    h_cm = r.get_data(as_text=True)
    check('问题4a 社区可按产品定向筛选', r.status_code == 200 and '按产品定向' in h_cm)
    post_hits = h_cm.count('AX3600')
    check('问题4b 产品筛选下出现该产品相关帖', post_hits >= 1, f'hits={post_hits}')
    rj = c.get(f'/api/product/{router_pid}/docs')
    data = json.loads(rj.get_data(as_text=True))
    check('问题4c 产品文档级联接接口返回该产品文档',
          rj.status_code == 200 and len(data.get('docs', [])) == 3,
          f"docs={len(data.get('docs', []))}")

    # ---------- 问题8：登录健壮性（单会话，贴近真实浏览器） ----------
    def fresh_token(client):
        client.get('/auth/login')
        return csrf(client)

    tok = fresh_token(c)
    r = c.post('/auth/login', data={'identifier': 'demo', 'password': 'wrong',
                                    'csrf_token': tok})
    check('问题8a 错误密码返回401而非500', r.status_code == 401, r.status_code)

    tok = fresh_token(c)
    r = c.post('/auth/login?next=/profile',
               data={'identifier': 'demo', 'password': 'demo123',
                     'csrf_token': tok, 'next': '/profile'})
    check('问题8b 正确密码302且next跳转保留', r.status_code == 302 and
          r.headers.get('Location', '').endswith('/profile'),
          f"{r.status_code} {r.headers.get('Location')}")

    # 登出后再测开放重定向拦截
    with c.session_transaction() as s:
        s.clear()
    tok = fresh_token(c)
    r = c.post('/auth/login?next=//evil.com',
               data={'identifier': 'demo', 'password': 'demo123',
                     'csrf_token': tok, 'next': '//evil.com'})
    loc = r.headers.get('Location', '')
    check('问题8c 开放重定向被拦截（不跳外域）', r.status_code == 302 and 'evil.com' not in loc, loc)

    # ---------- 问题4d-f：登录态下发帖关联产品（复用当前已登录会话） ----------
    r = c.get('/community/create')
    h_cp = r.get_data(as_text=True)
    check('问题4d 发帖页含关联产品选择器', r.status_code == 200 and 'id="productSelect"' in h_cp)
    with app.app_context():
        excel = Product.query.filter_by(name='Microsoft Excel').first()
        excel_pid, excel_docid = excel.id, Document.query.filter_by(product_id=excel.id).first().id
    tok = csrf(c)
    r = c.post('/community/create', data={
        'board': 'qa', 'product_id': str(excel_pid),
        'related_document_id': str(excel_docid),
        'title': '回归测试：Excel 关联发帖', 'content': '测试内容', 'csrf_token': tok})
    # 302 必须跳到新建帖子页 /community/post/<id>，而不是未登录被踢回 /auth/login
    loc4e = r.headers.get('Location', '')
    ok4e = r.status_code == 302 and loc4e.startswith('/community/post/')
    check('问题4e 带产品/文档关联发帖成功', ok4e, f'{r.status_code} {loc4e}')
    with app.app_context():
        p = Post.query.filter_by(title='回归测试：Excel 关联发帖').first()
        ok = p and p.product_id == excel_pid and p.related_document_id == excel_docid
        check('问题4f 帖子正确关联产品与文档', bool(ok))
        if p:
            db.session.delete(p); db.session.commit()

    # ---------- 搜索联想按产品聚合 ----------
    r = c.get('/api/search/suggest?q=%E5%B0%8F%E7%B1%B3')
    data = json.loads(r.get_data(as_text=True))
    names = [x['product_name'] for x in data['results']]
    check('联想接口按产品去重', len(names) == len(set(names)), str(names))

# ---------- 汇总 ----------
print('\n' + '=' * 64)
total = len(results); passed = sum(1 for _, ok, _ in results if ok)
print(f'回归结果：{passed}/{total} 通过')
for name, ok, detail in results:
    if not ok:
        print('  FAIL -', name, detail)
print('=' * 64)
sys.exit(0 if passed == total else 1)
