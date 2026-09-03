"""主蓝图：所有页面路由"""
import os
import io
import json
import re
from urllib.parse import quote, urlencode
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort, send_from_directory, send_file
from flask_login import login_required, current_user
from app.extensions import db
from app.models import (
    User, Category, Brand, Product, Document, Comment,
    Post, Reply, Favorite, ReadingHistory, Contribution, Term
)
from config import Config
from app.search import search_products, decorate_results

main_bp = Blueprint('main', __name__)


# ============================================================
# 辅助函数
# ============================================================

DOC_TYPE_LABELS = {
    'quick_start': '快速入门',
    'manual': '使用手册',
    'faq': 'FAQ 常见问题',
    'troubleshooting': '故障排查',
    'feature': '功能说明',
    'other': '其他',
}

# 产品分组内文档的展示顺序：快速入门 → 使用手册 → FAQ → 故障排查 → 其他
DOC_TYPE_ORDER = {
    'quick_start': 0, 'manual': 1, 'faq': 2,
    'troubleshooting': 3, 'feature': 4, 'other': 5,
}

BOARD_LABELS = {
    'qa': '产品问答',
    'tips': '使用技巧',
    'help': '故障求助',
    'feedback': '建议反馈',
    'chat': '闲聊灌水',
}


def doc_type_label(doc_type):
    return DOC_TYPE_LABELS.get(doc_type, '其他')


def detect_locked_brand(q):
    """搜索词本身就是某品牌（网站）全称时，锁定到该品牌：
    其他品牌在侧栏不可再勾选（一篇文档不可能同时属于多个品牌）。
    返回 Brand 实例或 None。"""
    if not q:
        return None
    # 优先精确相等，其次查询词包含品牌全称（如「小米路由器」锁定「小米」）
    exact = Brand.query.filter_by(status='active').filter(Brand.name == q).first()
    if exact:
        return exact
    for brand in Brand.query.filter_by(status='active').all():
        if brand.name and brand.name in q:
            return brand
    return None


def board_label(board):
    return BOARD_LABELS.get(board, '其他')


def escape_html(text):
    """转义 HTML 特殊字符，防止存储型 XSS"""
    if not text:
        return ''
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def is_safe_url(url):
    """检查 URL 是否使用安全协议，拦截 javascript: / data: 等危险链接"""
    if not url:
        return False
    url = url.strip().lower()
    return url.startswith(('http://', 'https://', 'ftp://', '/', '#', 'mailto:', 'tel:'))


def highlight(text, keyword):
    """在文本中高亮关键词"""
    if not keyword or not text:
        return text or ''
    try:
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        return pattern.sub(lambda m: f'<mark>{m.group()}</mark>', text)
    except Exception:
        return text


def render_markdown(content):
    """简易 Markdown 渲染（标题、段落、列表、代码块、引用、表格、粗体、链接）"""
    if not content:
        return ''
    lines = content.split('\n')
    html = []
    in_code = False
    in_table = False
    table_rows = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # 代码块
        if line.strip().startswith('```'):
            if in_code:
                html.append('</code></pre>')
                in_code = False
            else:
                html.append('<pre><code>')
                in_code = True
            i += 1
            continue

        if in_code:
            html.append(line.replace('<', '&lt;').replace('>', '&gt;'))
            i += 1
            continue

        # 空行
        if not line.strip():
            if in_table:
                # 结束表格
                if table_rows:
                    html.append('<table>')
                    for idx, row in enumerate(table_rows):
                        tag = 'th' if idx == 0 else 'td'
                        cells = [escape_html(c.strip()) for c in row.split('|') if c.strip()]
                        html.append('<tr>' + ''.join(f'<{tag}>{c}</{tag}>' for c in cells) + '</tr>')
                    html.append('</table>')
                table_rows = []
                in_table = False
            i += 1
            continue

        # 表格行
        if '|' in line and line.strip().startswith('|'):
            if not in_table:
                in_table = True
                table_rows = []
            # 跳过分隔行 |---|---|
            if not re.match(r'^\|[\s\-:|]+\|$', line.strip()):
                table_rows.append(line)
            i += 1
            continue

        # 标题
        if line.startswith('### '):
            html.append(f'<h3 id="section-{i}">{inline_format(escape_html(line[4:].strip()))}</h3>')
        elif line.startswith('## '):
            html.append(f'<h2 id="section-{i}">{inline_format(escape_html(line[3:].strip()))}</h2>')
        elif line.startswith('# '):
            html.append(f'<h1 id="section-{i}">{inline_format(escape_html(line[2:].strip()))}</h1>')
        # 引用
        elif line.startswith('> '):
            html.append(f'<blockquote>{inline_format(escape_html(line[2:].strip()))}</blockquote>')
        # 无序列表
        elif line.strip().startswith(('- ', '* ')):
            items = []
            while i < len(lines) and lines[i].strip().startswith(('- ', '* ')):
                items.append(f'<li>{inline_format(escape_html(lines[i].strip()[2:]))}</li>')
                i += 1
            html.append('<ul>' + ''.join(items) + '</ul>')
            continue
        # 有序列表
        elif re.match(r'^\d+\.\s', line.strip()):
            items = []
            while i < len(lines) and re.match(r'^\d+\.\s', lines[i].strip()):
                _item_text = re.sub(r'^\d+\.\s', '', lines[i].strip())
                items.append(f'<li>{inline_format(escape_html(_item_text))}</li>')
                i += 1
            html.append('<ol>' + ''.join(items) + '</ol>')
            continue
        # 普通段落
        else:
            # 合并连续非空行
            para_lines = [line]
            while i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].startswith(('#', '>', '- ', '* ', '```')) and not re.match(r'^\d+\.\s', lines[i + 1].strip()) and '|' not in lines[i + 1]:
                i += 1
                para_lines.append(lines[i])
            html.append(f'<p>{inline_format(escape_html(" ".join(l.strip() for l in para_lines)))}</p>')
        i += 1

    # 处理未结束的表格
    if in_table and table_rows:
        html.append('<table>')
        for idx, row in enumerate(table_rows):
            tag = 'th' if idx == 0 else 'td'
            cells = [c.strip() for c in row.split('|') if c.strip()]
            html.append('<tr>' + ''.join(f'<{tag}>{c}</{tag}>' for c in cells) + '</tr>')
        html.append('</table>')

    return '\n'.join(html)


def inline_format(text):
    """行内格式：粗体、行内代码、链接（已做 XSS 防护，过滤危险 URL）"""
    # 行内代码
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # 粗体
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    # 链接 [text](url) - 仅允许安全协议，危险 URL 降级为纯文本
    def _replace_link(m):
        link_text = m.group(1)
        url = m.group(2)
        if not is_safe_url(url):
            return link_text
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{link_text}</a>'
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _replace_link, text)
    return text


def extract_toc(content):
    """从 Markdown 提取目录"""
    toc = []
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('### '):
            toc.append({'level': 3, 'text': line[4:].strip()})
        elif line.startswith('## '):
            toc.append({'level': 2, 'text': line[3:].strip()})
        elif line.startswith('# '):
            toc.append({'level': 1, 'text': line[2:].strip()})
    return toc


# ============================================================
# 页面路由
# ============================================================

@main_bp.route('/')
def index():
    """首页"""
    recent_docs = Document.query.filter_by(status='published').order_by(
        Document.created_at.desc()
    ).limit(6).all()
    return render_template('index.html',
                           recent_docs=recent_docs,
                           doc_type_label=doc_type_label,
                           active_page='home')


@main_bp.route('/search')
def search():
    """搜索结果页（按产品聚合：FTS5 仅匹配品牌/产品全称，同产品文档合并为一个分组）"""
    q = request.args.get('q', '').strip()
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 8
    # 侧栏为单选语义：doc_type / brand 各只取一个合法值
    selected_type = request.args.get('doc_type', '')
    if selected_type not in DOC_TYPE_LABELS:
        selected_type = ''
    brand_arg = request.args.get('brand', '')
    selected_brand_id = int(brand_arg) if brand_arg.isdigit() else None
    sort = request.args.get('sort', 'relevance')
    if sort not in ('relevance', 'latest', 'views'):
        sort = 'relevance'

    # 搜索词本身是品牌全称时锁定品牌（其他品牌不可再选）
    locked_brand = detect_locked_brand(q)
    effective_brand_id = locked_brand.id if locked_brand else selected_brand_id

    result = search_products(
        q=q, doc_type=selected_type or None,
        brand_id=effective_brand_id, sort=sort,
        page=page, per_page=per_page,
    )
    tokens = result['tokens']
    mode = result['mode']
    product_ids = result['product_ids']

    # 按聚合顺序回查产品，并为每个产品挂载其文档（受文档类型筛选约束）
    products = Product.query.filter(Product.id.in_(product_ids)).all() if product_ids else []
    p_map = {p.id: p for p in products}
    groups = []
    total_docs = 0
    if product_ids:
        docs = Document.query.filter(
            Document.status == 'published',
            Document.product_id.in_(product_ids)
        ).all()
        by_product = {}
        for d in docs:
            if selected_type and d.doc_type != selected_type:
                continue
            by_product.setdefault(d.product_id, []).append(d)
        for pid in product_ids:
            prod = p_map.get(pid)
            if not prod:
                continue
            pdocs = by_product.get(pid, [])
            pdocs.sort(key=lambda d: (DOC_TYPE_ORDER.get(d.doc_type, 9), d.id))
            decorate_results(pdocs, tokens)
            prod.group_docs = pdocs
            prod.group_doc_count = result['doc_counts'].get(pid, len(pdocs))
            total_docs += len(pdocs)
            groups.append(prod)

    # 侧栏 facet（按命中产品数计数，一次聚合得到，避免万级数据下的 N+1 count）
    type_count = dict(result['facets']['types'])
    doc_types = []
    for key, label in DOC_TYPE_LABELS.items():
        cnt = type_count.get(key, 0)
        if cnt > 0:
            doc_types.append((key, label, cnt))

    brand_count = dict(result['facets']['brands'])
    brands = []
    for brand in Brand.query.filter_by(status='active').order_by(Brand.id).all():
        cnt = brand_count.get(brand.id, 0)
        if cnt > 0 or (locked_brand and brand.id == locked_brand.id):
            brand.count = cnt
            brand.locked_disabled = bool(
                locked_brand and brand.id != locked_brand.id
            )
            brands.append(brand)

    # 分页 URL：完整保留 q / sort / 单选筛选；overrides 可覆盖类型/品牌/排序
    def _search_href(page_num, **overrides):
        params = []
        if q:
            params.append(('q', q))
        use_sort = overrides.get('sort', sort)
        if use_sort != 'relevance':
            params.append(('sort', use_sort))
        dt = overrides.get('doc_type', selected_type)
        bd = overrides.get('brand', effective_brand_id)
        if dt:
            params.append(('doc_type', dt))
        if bd:
            params.append(('brand', bd))
        if page_num and page_num > 1:
            params.append(('page', page_num))
        return '/search?' + urlencode(params)

    pagination = None
    if result['pages'] > 1:
        pages = result['pages']
        pagination = {
            'page': page,
            'pages': pages,
            'prev_url': _search_href(page - 1) if page > 1 else None,
            'next_url': _search_href(page + 1) if page < pages else None,
            'items': [(p, _search_href(p)) for p in range(1, pages + 1)],
        }

    return render_template(
        'search.html',
        q=q, groups=groups, total=result['total'], total_docs=total_docs,
        doc_types=doc_types, brands=brands,
        selected_type=selected_type,
        selected_brand_id=effective_brand_id,
        locked_brand=locked_brand,
        current_sort=sort, mode=mode, tokens=tokens,
        pagination=pagination, search_href=_search_href,
        doc_type_label=doc_type_label,
        active_page='search',
    )


@main_bp.route('/categories')
def categories():
    """分类浏览页：默认展示品类入口（各品类独立图标）；
    带 category 参数时展示该品类下「品牌 → 产品 → 文档」两级浏览。"""
    category_name = request.args.get('category', '').strip()
    cats = Category.query.filter_by(parent_id=None).order_by(Category.sort_order).all()
    for cat in cats:
        cat.doc_count = Document.query.join(Product).filter(
            Document.status == 'published',
            Product.category == cat.name
        ).count()
        cat.product_count = Product.query.filter(
            Product.status == 'active', Product.category == cat.name
        ).count()

    # 品类详情：按品牌分组列出产品及其文档
    brand_groups = []
    current_category = None
    if category_name:
        current_category = Category.query.filter_by(name=category_name).first()
        products = Product.query.filter(
            Product.status == 'active', Product.category == category_name
        ).order_by(Product.id).all()
        docs = Document.query.filter(
            Document.status == 'published',
            Document.product_id.in_([p.id for p in products])
        ).all() if products else []
        by_pid = {}
        for d in docs:
            by_pid.setdefault(d.product_id, []).append(d)
        # 注意：Brand.products 是 lazy='dynamic' 关系，不能直接赋值，否则 SQLAlchemy
        # 会尝试把原产品的 brand_id 置空并 flush，触发 NOT NULL 约束错误。用普通字典聚合。
        brand_cache = {}
        for prod in products:
            pdocs = by_pid.get(prod.id, [])
            pdocs.sort(key=lambda d: (DOC_TYPE_ORDER.get(d.doc_type, 9), d.id))
            prod.group_docs = pdocs
            brand = prod.brand
            if brand.id not in brand_cache:
                bucket = {'brand': brand, 'products': []}
                brand_cache[brand.id] = bucket
                brand_groups.append(bucket)
            brand_cache[brand.id]['products'].append(prod)

    return render_template('categories.html',
                           categories=cats,
                           current_category=current_category,
                           brand_groups=brand_groups,
                           doc_type_label=doc_type_label,
                           active_page='categories')


@main_bp.route('/doc/<int:doc_id>')
def doc_detail(doc_id):
    """文档阅读页"""
    doc = Document.query.get_or_404(doc_id)
    if doc.status != 'published':
        abort(404)

    # 增加阅读量
    doc.view_count += 1
    db.session.commit()

    # 记录阅读历史
    reading_progress = 0
    if current_user.is_authenticated:
        history = ReadingHistory.query.filter_by(
            user_id=current_user.id, document_id=doc_id
        ).first()
        if history:
            reading_progress = history.progress
        else:
            history = ReadingHistory(user_id=current_user.id, document_id=doc_id, progress=0)
            db.session.add(history)
            db.session.commit()

    # 是否收藏
    is_favorited = False
    if current_user.is_authenticated:
        is_favorited = Favorite.query.filter_by(
            user_id=current_user.id, document_id=doc_id
        ).first() is not None

    # 目录
    toc = extract_toc(doc.content)
    # 渲染内容
    rendered_content = render_markdown(doc.content)
    # 评论
    comments = Comment.query.filter_by(document_id=doc_id, parent_id=None).order_by(
        Comment.created_at.desc()
    ).all()

    # 上一篇/下一篇
    prev_doc = Document.query.filter(
        Document.id < doc_id, Document.status == 'published'
    ).order_by(Document.id.desc()).first()
    next_doc = Document.query.filter(
        Document.id > doc_id, Document.status == 'published'
    ).order_by(Document.id.asc()).first()

    return render_template('doc.html',
                           doc=doc, toc=toc, rendered_content=rendered_content,
                           comments=comments, is_favorited=is_favorited,
                           reading_progress=reading_progress,
                           prev_doc=prev_doc, next_doc=next_doc,
                           doc_type_label=doc_type_label,
                           active_page='doc')


@main_bp.route('/doc/<int:doc_id>/download')
def download_doc(doc_id):
    """下载文档：统一导出为 PDF（PRD AC-P4-03，文件名：品牌-产品名-文档类型.pdf）。
    若该文档是用户上传的原始文件，则原样下发上传文件。"""
    doc = Document.query.get_or_404(doc_id)
    if doc.status != 'published':
        abort(404)
    doc.download_count += 1
    db.session.commit()

    # 用户上传的原始文件：原样下发
    if doc.file_path and os.path.exists(doc.file_path):
        return send_from_directory(
            os.path.dirname(doc.file_path),
            os.path.basename(doc.file_path),
            as_attachment=True
        )

    # Markdown 正文 → PDF
    from app.pdf_export import build_pdf_bytes, PdfExportError
    brand_name = doc.product.brand.name if doc.product and doc.product.brand else 'DocHub'
    product_name = doc.product.name if doc.product else ''
    type_label = doc_type_label(doc.doc_type)
    try:
        pdf_bytes = build_pdf_bytes(doc, type_label=type_label)
    except PdfExportError:
        # PDF 引擎/中文字体不可用时的最终降级：下发纯文本，保证下载不中断
        from flask import Response
        content = doc.content or f'# {doc.title}\n\n{doc.description}'
        return Response(content, mimetype='text/plain; charset=utf-8',
                        headers={'Content-Disposition': "attachment; filename=doc.txt"})

    raw_name = f'{brand_name}-{product_name}-{type_label}.pdf'
    ascii_name = raw_name.encode('ascii', 'replace').decode('ascii').replace('?', '_')
    utf8_name = quote(raw_name)
    resp = send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=ascii_name,
    )
    # RFC 5987：filename* 承载 UTF-8 中文文件名
    resp.headers['Content-Disposition'] = (
        f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
    )
    return resp


@main_bp.route('/contribute')
@login_required
def contribute():
    """内容贡献中心"""
    tab = request.args.get('tab', 'document')
    brands = Brand.query.filter_by(status='active').all()
    products = Product.query.filter_by(status='active').all()
    contributions = Contribution.query.filter_by(user_id=current_user.id).order_by(
        Contribution.submitted_at.desc()
    ).all()
    return render_template('contribute.html',
                           active_tab=tab, brands=brands, products=products,
                           contributions=contributions,
                           active_page='contribute')


@main_bp.route('/contribute/submit/<contrib_type>', methods=['POST'])
@login_required
def contribute_submit(contrib_type):
    """提交贡献"""
    if contrib_type not in ('document', 'brand', 'product'):
        abort(400)

    title = request.form.get('title', request.form.get('name', ''))
    payload = {}
    for key, value in request.form.items():
        if key not in ('title', 'name'):
            payload[key] = value

    # 处理文件上传
    if contrib_type == 'document' and 'file' in request.files:
        file = request.files['file']
        if file and file.filename:
            # 校验文件扩展名白名单，防止任意文件上传/存储型 XSS
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
            if ext not in Config.ALLOWED_DOC_EXTENSIONS:
                flash(
                    f'不支持的文件类型 ".{ext}"，仅允许: '
                    f'{", ".join(sorted(Config.ALLOWED_DOC_EXTENSIONS))}',
                    'error'
                )
                return redirect(url_for('main.contribute', tab='document'))
            from werkzeug.utils import secure_filename
            import uuid
            filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
            filepath = os.path.join(Config.UPLOAD_FOLDER, filename)
            file.save(filepath)
            payload['file_path'] = filepath

    contribution = Contribution(
        user_id=current_user.id,
        contribution_type=contrib_type,
        title=title,
        payload_json=json.dumps(payload, ensure_ascii=False),
        status='pending'
    )
    db.session.add(contribution)
    db.session.commit()
    flash('提交成功，将在 1-3 个工作日内完成审核', 'success')
    return redirect(url_for('main.contribute', tab='my'))


@main_bp.route('/community')
def community():
    """社区讨论页：支持板块 + 目标产品 + 社区内关键词的定向查询"""
    board = request.args.get('board', '')
    if board not in BOARD_LABELS:
        board = ''
    product_id = request.args.get('product', type=int)
    kw = request.args.get('kw', '').strip()
    sort = request.args.get('sort', 'latest')
    if sort not in ('latest', 'replies', 'views'):
        sort = 'latest'

    query = Post.query.filter_by(status='approved')
    if board:
        query = query.filter_by(board=board)
    if product_id:
        query = query.filter_by(product_id=product_id)
    if kw:
        like = f'%{kw}%'
        query = query.filter(db.or_(Post.title.like(like), Post.content.like(like)))

    order = {
        'replies': Post.reply_count.desc(),
        'views': Post.view_count.desc(),
    }.get(sort, Post.created_at.desc())
    posts = query.order_by(order, Post.id.desc()).limit(50).all()

    boards = []
    for key, label in BOARD_LABELS.items():
        count = Post.query.filter_by(board=key, status='approved').count()
        boards.append((key, label, count))

    # 侧栏产品筛选（只列出有帖子的产品，附品牌名）
    products = Product.query.filter_by(status='active').order_by(Product.id).all()
    product_post_count = dict(db.session.query(
        Post.product_id, db.func.count(Post.id)
    ).filter(Post.status == 'approved', Post.product_id.isnot(None)).group_by(
        Post.product_id).all())
    products = [p for p in products if product_post_count.get(p.id)]
    selected_product = Product.query.get(product_id) if product_id else None

    return render_template('community.html',
                           posts=posts, boards=boards,
                           active_board=board, products=products,
                           product_post_count=product_post_count,
                           selected_product=selected_product,
                           community_kw=kw, current_sort=sort,
                           board_label=board_label,
                           doc_type_label=doc_type_label,
                           active_page='community')


@main_bp.route('/community/post/<int:post_id>')
def post_detail(post_id):
    """帖子详情页"""
    post = Post.query.get_or_404(post_id)
    post.view_count += 1
    db.session.commit()
    replies = Reply.query.filter_by(post_id=post_id, status='approved').order_by(
        Reply.created_at.asc()
    ).all()
    return render_template('post_detail.html',
                           post=post, replies=replies,
                           board_label=board_label,
                           active_page='community')


@main_bp.route('/community/create', methods=['GET', 'POST'])
@login_required
def create_post():
    """发布帖子：可关联目标产品（用于产品定向问答）与该产品下的具体文档"""
    products = Product.query.filter_by(status='active').order_by(Product.id).all()

    if request.method == 'POST':
        board = request.form.get('board', 'qa')
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        product_id = request.form.get('product_id', type=int)
        related_document_id = request.form.get('related_document_id', type=int)
        if not title or not content:
            flash('标题和内容不能为空', 'error')
            return redirect(url_for('main.create_post', product_id=product_id))
        # 校验关联产品 / 文档真实存在且归属一致
        target_product = Product.query.get(product_id) if product_id else None
        related_doc = None
        if related_document_id:
            related_doc = Document.query.filter_by(
                id=related_document_id, status='published').first()
            if related_doc and target_product and \
                    related_doc.product_id != target_product.id:
                related_doc = None
        post = Post(
            user_id=current_user.id,
            board=board if board in BOARD_LABELS else 'qa',
            title=title, content=content,
            product_id=target_product.id if target_product else None,
            related_document_id=related_doc.id if related_doc else None,
        )
        db.session.add(post)
        db.session.commit()
        flash('发布成功', 'success')
        return redirect(url_for('main.post_detail', post_id=post.id))

    # GET：支持从文档页 / 社区产品筛选带参预选
    preselect_product = request.args.get('product_id', type=int)
    preselect_doc = request.args.get('doc_id', type=int)
    product_docs = []
    if preselect_product:
        product_docs = Document.query.filter_by(
            product_id=preselect_product, status='published'
        ).order_by(Document.id).all()
    return render_template('create_post.html',
                           products=products, product_docs=product_docs,
                           preselect_product=preselect_product,
                           preselect_doc=preselect_doc,
                           active_page='community')


@main_bp.route('/community/post/<int:post_id>/reply', methods=['POST'])
@login_required
def add_reply(post_id):
    """发表回复"""
    post = Post.query.get_or_404(post_id)
    content = request.form.get('content', '').strip()
    if content:
        reply = Reply(post_id=post_id, user_id=current_user.id, content=content)
        db.session.add(reply)
        post.reply_count += 1
        db.session.commit()
        flash('回复成功', 'success')
    return redirect(url_for('main.post_detail', post_id=post_id))


@main_bp.route('/profile')
@login_required
def profile():
    """个人中心"""
    tab = request.args.get('tab', 'history')

    stats = {
        'documents_read': ReadingHistory.query.filter_by(user_id=current_user.id).count(),
        'favorites': Favorite.query.filter_by(user_id=current_user.id).count(),
        'comments': Comment.query.filter_by(user_id=current_user.id).count(),
        'posts': Post.query.filter_by(user_id=current_user.id).count(),
        'contributions': Contribution.query.filter_by(user_id=current_user.id).count(),
    }

    reading_history = []
    favorites = []
    comments = []
    posts = []
    contributions = []

    if tab == 'history':
        reading_history = ReadingHistory.query.filter_by(user_id=current_user.id).order_by(
            ReadingHistory.last_read_at.desc()
        ).limit(20).all()
    elif tab == 'favorites':
        favorites = Favorite.query.filter_by(user_id=current_user.id).order_by(
            Favorite.created_at.desc()
        ).limit(20).all()
    elif tab == 'comments':
        comments = Comment.query.filter_by(user_id=current_user.id).order_by(
            Comment.created_at.desc()
        ).limit(20).all()
    elif tab == 'posts':
        posts = Post.query.filter_by(user_id=current_user.id).order_by(
            Post.created_at.desc()
        ).limit(20).all()
    elif tab == 'contributions':
        contributions = Contribution.query.filter_by(user_id=current_user.id).order_by(
            Contribution.submitted_at.desc()
        ).all()

    return render_template('profile.html',
                           active_tab=tab, stats=stats,
                           reading_history=reading_history,
                           favorites=favorites, comments=comments,
                           posts=posts, contributions=contributions,
                           active_page='profile')


@main_bp.route('/doc/<int:doc_id>/comment', methods=['POST'])
@login_required
def add_comment(doc_id):
    """发表文档评论"""
    doc = Document.query.get_or_404(doc_id)
    if doc.status != 'published':
        abort(404)
    content = request.form.get('content', '').strip()
    parent_id = request.form.get('parent_id', type=int)
    if content:
        comment = Comment(
            document_id=doc_id, user_id=current_user.id,
            content=content, parent_id=parent_id
        )
        db.session.add(comment)
        db.session.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True})
        flash('评论成功', 'success')
    return redirect(url_for('main.doc_detail', doc_id=doc_id))
