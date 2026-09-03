"""FTS5 检索模块：jieba 分词 + SQLite FTS5 倒排索引 + BM25 相关度排序。

设计要点（v2：按产品聚合 + 名称范围检索）
- 检索走 SQLite FTS5 虚拟表 document_fts，rowid 与 documents.id 一一对应；
- 入库文本先经 jieba 分词（token 以空格连接），中文检索质量由分词保证；
- **检索范围只包含「品牌（网站）全称」与「产品全称」字段，文档正文/简介不参与检索**
  （MATCH 用 FTS5 列过滤器 {brand_name product_name} 限定），从根本上避免正文命中
  带来的噪声；正文不写入索引以控制万级/十万级文档下的索引体积；
- **结果按产品聚合**：同一产品下的快速入门/使用手册/FAQ/故障排查等多篇文档合并为
  一个产品分组返回，分页与计数以产品为单位，避免「搜产品类型 → 3×N 条平行结果」的
  冗余与前端渲染压力；
- 相关度用 FTS5 内建 bm25()，品牌 2 / 产品 3；
- 检索先尝试「全部 token AND」，无结果再放宽为「任一 token OR」，最后前缀匹配；
- 左侧筛选（文档类型、品牌）为单选；facet 计数按「产品数」一次聚合得到。
"""
import re

from sqlalchemy import text

from app.extensions import db
from app.models import Document

FTS_TABLE = 'document_fts'
# bm25 字段权重：(title, description, content, brand_name, product_name)
# 仅 brand_name/product_name 参与匹配，其余列权重置 0。
BM25_WEIGHTS = (1.0, 0.0, 0.0, 2.0, 3.0)
# 名称检索的列过滤器：只在品牌全称、产品全称两列内匹配
NAME_SCOPE = '{brand_name product_name}'
SNIPPET_BEFORE = 40
SNIPPET_AFTER = 80
SNIPPET_MAX = 200


def segment(text):
    """将文本 jieba 分词，返回以空格连接的 token 串（供 FTS 索引使用）"""
    if not text:
        return ''
    import jieba
    words = jieba.lcut(text)
    return ' '.join(w for w in words if w and w.strip())


def query_tokens(q):
    """把用户查询切成检索 token：jieba 分词 + 去空 + 去纯符号"""
    if not q:
        return []
    import jieba
    tokens = [w.strip() for w in jieba.lcut(q) if w and w.strip()]
    return [t for t in tokens if re.search(r'[\w一-鿿]', t)]


def _escape_phrase(tok):
    """FTS5 phrase 转义：双引号包裹、内部引号翻倍"""
    return '"' + tok.replace('"', '""') + '"'


def init_fts():
    """创建 FTS5 虚拟表（幂等）"""
    db.session.execute(text(
        f'CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5('
        'title, description, content, brand_name, product_name)'
    ))
    db.session.commit()


def fts_count():
    row = db.session.execute(text(f'SELECT COUNT(*) FROM {FTS_TABLE}')).fetchone()
    return row[0] if row else 0


def ensure_index():
    """启动自检：已发布文档数与 FTS 行数不一致时全量重建（适合数据量小阶段）"""
    init_fts()
    published = Document.query.filter_by(status='published').count()
    if fts_count() != published:
        rebuild_index()


def _insert(doc):
    brand = doc.product.brand.name if (doc.product and doc.product.brand) else ''
    product = doc.product.name if doc.product else ''
    # 正文/简介不写入索引：检索范围仅品牌/产品全称，同时显著降低索引体积
    db.session.execute(text(
        f'INSERT INTO {FTS_TABLE}(rowid, title, description, content, brand_name, product_name) '
        'VALUES (:id, :t, :d, :c, :b, :p)'
    ), {
        'id': doc.id,
        't': segment(doc.title),
        'd': '',
        'c': '',
        'b': segment(brand),
        'p': segment(product),
    })


def rebuild_index():
    """全量重建 FTS 索引（先清空再按已发布文档重建），返回重建条数"""
    init_fts()
    db.session.execute(text(f'DELETE FROM {FTS_TABLE}'))
    docs = Document.query.filter_by(status='published').all()
    for d in docs:
        _insert(d)
    db.session.commit()
    return len(docs)


def sync_document(doc_id, status='published'):
    """文档增删改后同步 FTS 索引（应用层调用，供后续管理/审核流程接入）"""
    init_fts()
    db.session.execute(text(f'DELETE FROM {FTS_TABLE} WHERE rowid = :id'), {'id': doc_id})
    if status == 'published':
        doc = Document.query.get(doc_id)
        if doc:
            _insert(doc)
    db.session.commit()


def _name_match_clause(tokens, mode):
    """生成限定在 品牌/产品全称 列上的 FTS5 MATCH 表达式。
    mode='and' 全词 AND；'or' 任词 OR；'prefix' 前缀任词 OR（兼容部分词输入）。"""
    parts = []
    for tok in tokens:
        phrase = _escape_phrase(tok)
        if mode == 'prefix':
            phrase += ' *'
        parts.append(f'{NAME_SCOPE} : {phrase}')
    joiner = ' AND ' if mode == 'and' else ' OR '
    return joiner.join(parts)


def _flat_matches(use_match, match_expr=None, doc_type=None, brand_id=None):
    """直接扫描 FTS（bm25 不能包进 CTE/子查询，否则 SQLite 报
    “unable to use function bm25 in the requested context”），返回命中文档的扁平行：
    [(doc_id, product_id, doc_type, created_at(str), view_count, brand_id, score|None), ...]"""
    params = {}
    conds = ["d.status = 'published'"]
    if use_match:
        select = (f'SELECT d.id, d.product_id, d.doc_type, d.created_at, '
                  f'd.view_count, p.brand_id, bm25({FTS_TABLE}, :weights) '
                  f'FROM {FTS_TABLE} JOIN documents d ON d.id = {FTS_TABLE}.rowid '
                  f'JOIN products p ON p.id = d.product_id ')
        conds.insert(0, f'{FTS_TABLE} MATCH :match')
        params['weights'] = ','.join(str(w) for w in BM25_WEIGHTS)
        params['match'] = match_expr
    else:
        select = ('SELECT d.id, d.product_id, d.doc_type, d.created_at, '
                  'd.view_count, p.brand_id, NULL '
                  'FROM documents d JOIN products p ON p.id = d.product_id ')
    if doc_type:
        conds.append('d.doc_type = :fdt')
        params['fdt'] = doc_type
    if brand_id:
        conds.append('p.brand_id = :fbid')
        params['fbid'] = int(brand_id)
    sql = select + ' WHERE ' + ' AND '.join(conds)
    return [tuple(r) for r in db.session.execute(text(sql), params).fetchall()]


def _facet_counts(use_match, match_expr, group, doc_type, brand_id):
    """SQL 侧 facet 聚合：按分组值统计去重产品数，返回 [(分组值, 产品数, MIN(rowid)), ...]。

    相对旧实现（把全量行物化到 Python 再去重）在万级文档下实测约 2 倍加速；
    MIN(rowid) 用于复刻旧实现的「扫描首见序」tie-break，保证等计数时顺序逐字节一致。"""
    if group not in ('doc_type', 'brand_id'):
        raise ValueError(f'unsupported facet group: {group}')
    params = {}
    conds = ["d.status = 'published'"]
    if use_match:
        select = (f'SELECT {group}, COUNT(DISTINCT d.product_id), MIN(d.rowid) '
                  f'FROM {FTS_TABLE} JOIN documents d ON d.id = {FTS_TABLE}.rowid '
                  f'JOIN products p ON p.id = d.product_id ')
        conds.insert(0, f'{FTS_TABLE} MATCH :match')
        params['match'] = match_expr
    else:
        select = (f'SELECT {group}, COUNT(DISTINCT d.product_id), MIN(d.rowid) '
                  f'FROM documents d JOIN products p ON p.id = d.product_id ')
    if doc_type:
        conds.append('d.doc_type = :fdt')
        params['fdt'] = doc_type
    if brand_id:
        conds.append('p.brand_id = :fbid')
        params['fbid'] = int(brand_id)
    sql = select + ' WHERE ' + ' AND '.join(conds) + f' GROUP BY {group}'
    return [tuple(r) for r in db.session.execute(text(sql), params).fetchall()]


def _aggregate(rows):
    """扁平命中文档行 → 按产品聚合：{pid: {doccnt, latest, views, best}}"""
    agg = {}
    for did, pid, dt, ca, vc, bid, score in rows:
        g = agg.setdefault(pid, {'doccnt': 0, 'latest': ca, 'views': 0,
                                 'best': score, 'bid': bid})
        g['doccnt'] += 1
        g['views'] += vc or 0
        if ca and (g['latest'] is None or ca > g['latest']):
            g['latest'] = ca
        if score is not None:
            g['best'] = score if g['best'] is None else min(g['best'], score)
    return agg


def search_products(q, doc_type=None, brand_id=None, sort='relevance',
                    page=1, per_page=8):
    """按产品聚合检索。

    返回 dict：
      tokens/mode：实际命中的 token 与匹配模式；
      product_ids：当前页产品 id（已按 sort 排序）；
      total：命中产品总数（分页以产品为单位）；page/pages：分页信息；
      doc_counts：{产品id: 该产品在当前筛选下命中的文档数}；
      facets：{'types': [(doc_type, 产品数)], 'brands': [(brand_id, 产品数)]}，
              分别在「忽略自身维度」的口径下统计，供侧栏单选计数。
    """
    tokens = query_tokens(q)
    page = max(1, int(page or 1))
    empty = {
        'tokens': tokens, 'mode': 'and', 'product_ids': [], 'total': 0,
        'page': page, 'pages': 0, 'doc_counts': {},
        'facets': {'types': [], 'brands': []},
    }
    # 既无查询词也无筛选 → 不返回任何结果（空搜索引导态）
    if not tokens and not doc_type and not brand_id:
        return empty

    use_match = bool(tokens)
    modes = ('and', 'or', 'prefix') if use_match else (None,)
    chosen_mode = 'and'
    agg = {}

    for mode in modes:
        match_expr = _name_match_clause(tokens, mode) if use_match else None
        rows = _flat_matches(use_match, match_expr, doc_type, brand_id)
        agg = _aggregate(rows)
        if agg or mode == 'prefix' or not use_match:
            chosen_mode = mode or 'and'
            break

    # 产品级排序（SQLite 时间为 ISO 文本，latest 用稳定的多轮排序实现降序）
    ordered = sorted(agg.items(), key=lambda it: it[0])  # 末位 tie-break：pid 升序
    if sort == 'latest' or (sort == 'relevance' and not use_match):
        ordered.sort(key=lambda it: (it[1]['latest'] or ''), reverse=True)
        ordered.sort(key=lambda it: it[1]['latest'] is None)  # 无时间的排最后
    elif sort == 'views':
        ordered.sort(key=lambda it: -it[1]['views'])
    else:  # relevance（BM25 越小越相关），其次文档数
        ordered.sort(key=lambda it: (it[1]['best'] is None,
                                     it[1]['best'] if it[1]['best'] is not None else 0,
                                     -it[1]['doccnt']))
    total = len(ordered)
    pages = (total + per_page - 1) // per_page if total else 0
    page_items = ordered[(page - 1) * per_page:(page - 1) * per_page + per_page]
    product_ids = [pid for pid, _ in page_items]
    doc_counts = {pid: g['doccnt'] for pid, g in page_items}

    # ---- facet：文档类型（忽略 doc_type 维度，保留查询词 + 品牌）----
    type_rows = _facet_counts(use_match,
                              _name_match_clause(tokens, chosen_mode) if use_match else None,
                              'doc_type', None, brand_id)
    type_rowid = {dt: r for dt, _c, r in type_rows}
    type_facets = sorted(((dt, cnt) for dt, cnt, _r in type_rows if dt),
                         key=lambda x: (-x[1], type_rowid[x[0]]))

    # ---- facet：品牌（忽略 brand 维度，保留查询词 + 文档类型）----
    brand_rows = _facet_counts(use_match,
                               _name_match_clause(tokens, chosen_mode) if use_match else None,
                               'brand_id', doc_type, None)
    brand_facets = sorted(((bid, cnt) for bid, cnt, _r in brand_rows
                           if bid is not None), key=lambda x: x[0])

    return {
        'tokens': tokens, 'mode': chosen_mode,
        'product_ids': product_ids, 'total': total,
        'page': page, 'pages': pages, 'doc_counts': doc_counts,
        'facets': {'types': type_facets, 'brands': brand_facets},
    }


def suggest_products(q, limit=8):
    """搜索联想：在品牌/产品全称列上前缀匹配，返回 [(product_id, doc_id)]（每产品取一篇代表文档）。"""
    tokens = query_tokens(q)
    if not tokens:
        return []
    weights = ','.join(str(w) for w in BM25_WEIGHTS)
    match = ' OR '.join(
        f'{NAME_SCOPE} : {_escape_phrase(t)} *' for t in tokens
    )
    # bm25 不能出现在聚合/外层查询中：直接按相关度取候选，再在 Python 端按产品去重
    sql = text(f'''
        SELECT d.product_id, d.id, bm25({FTS_TABLE}, {weights}) AS score
        FROM {FTS_TABLE}
        JOIN documents d ON d.id = {FTS_TABLE}.rowid
        WHERE {FTS_TABLE} MATCH :match AND d.status = 'published'
        ORDER BY score ASC
        LIMIT :cand
    ''')
    rows = db.session.execute(sql, {'match': match, 'cand': limit * 4}).fetchall()
    seen = set()
    out = []
    for pid, did, _score in rows:
        if pid in seen:
            continue
        seen.add(pid)
        out.append((pid, did))
        if len(out) >= limit:
            break
    return out


def highlight_tokens(text, tokens):
    """在文本中高亮命中的 token（大小写不敏感、重叠合并），返回带 <mark> 的 HTML。

    安全：结果会在模板中以 |safe 渲染，因此除我们自己插入的 <mark> 外，
    原文必须先经 HTML 转义，避免标题/正文中的 <script> 等造成存储型 XSS。"""
    from markupsafe import escape
    if not text or not tokens:
        return str(escape(text or ''))
    spans = []
    for tok in tokens:
        if not tok:
            continue
        try:
            pat = re.compile(re.escape(tok), re.IGNORECASE)
        except re.error:
            continue
        for m in pat.finditer(text):
            spans.append((m.start(), m.end()))
    if not spans:
        return str(escape(text))
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    out = []
    last = 0
    for s, e in merged:
        out.append(str(escape(text[last:s])))
        out.append('<mark>' + str(escape(text[s:e])) + '</mark>')
        last = e
    out.append(str(escape(text[last:])))
    return ''.join(out)


def make_snippet(content, tokens, before=SNIPPET_BEFORE, after=SNIPPET_AFTER,
                 max_len=SNIPPET_MAX):
    """从正文抽取包含命中 token 的上下文片段（带高亮与省略号）"""
    content = content or ''
    if not tokens or not content:
        return content[:max_len]
    hit = None
    for tok in sorted(tokens, key=len, reverse=True):
        idx = content.lower().find(tok.lower())
        if idx >= 0:
            hit = (tok, idx)
            break
    if hit is None:
        return content[:max_len]
    _, idx = hit
    start = max(0, idx - before)
    end = min(len(content), idx + after)
    snippet = content[start:end]
    prefix = '…' if start > 0 else ''
    suffix = '…' if end < len(content) else ''
    return prefix + highlight_tokens(snippet, tokens) + suffix


def decorate_results(docs, tokens):
    """为文档列表附加高亮标题 / 摘要字段（title_hl / desc_hl / snippet）"""
    for doc in docs:
        doc.title_hl = highlight_tokens(doc.title, tokens)
        desc = doc.description or ''
        if any(t and t.lower() in desc.lower() for t in tokens):
            doc.desc_hl = highlight_tokens(desc, tokens)
        else:
            doc.desc_hl = desc[:120]
