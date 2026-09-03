"""API 蓝图：搜索联想、收藏、阅读进度等接口"""
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Document, Category, Favorite, ReadingHistory, Product
from app.search import suggest_products

api_bp = Blueprint('api', __name__)


@api_bp.after_app_request
def add_security_headers(response):
    """API 响应安全头。

    前端与后端同源部署，不再下发 Access-Control-Allow-Origin: *——
    通配符会对收藏/进度等登录态接口造成不必要的跨域读开放。
    若将来确需跨域调用，应改为白名单回显具体 Origin 并显式开启凭证。"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    return response


@api_bp.route('/search/suggest')
def search_suggest():
    """搜索联想接口（FTS5 相关度 Top8）"""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'results': [], 'total': 0})

    # 联想按产品聚合：同一产品只出现一次，跳转到该产品首篇文档
    pairs = suggest_products(q, limit=8)
    pids = [p[0] for p in pairs]
    doc_ids = [p[1] for p in pairs if p[1]]
    prod_map = {p.id: p for p in Product.query.filter(Product.id.in_(pids)).all()} if pids else {}
    doc_map = {d.id: d for d in Document.query.filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}

    results = []
    for pid, doc_id in pairs:
        prod = prod_map.get(pid)
        if not prod:
            continue
        rep = doc_map.get(doc_id)
        results.append({
            'id': rep.id if rep else None,
            'title': prod.name,
            'description': (rep.title if rep else (prod.description or ''))[:100],
            'doc_type': rep.doc_type if rep else '',
            'product_name': prod.name,
            'brand_name': prod.brand.name if prod.brand else '',
        })

    return jsonify({'results': results, 'total': len(results), 'query': q})


@api_bp.route('/product/<int:product_id>/docs')
def product_docs(product_id):
    """返回某产品下已发布文档（发帖时级联选择关联文档用）"""
    product = Product.query.get_or_404(product_id)
    docs = Document.query.filter_by(
        product_id=product_id, status='published'
    ).order_by(Document.id).all()
    from app.blueprints.main import doc_type_label
    return jsonify({
        'product': product.name,
        'docs': [{
            'id': d.id, 'title': d.title,
            'doc_type': d.doc_type, 'doc_type_label': doc_type_label(d.doc_type),
        } for d in docs],
    })


@api_bp.route('/categories')
def get_categories():
    """返回所有分类列表"""
    top_categories = Category.query.filter_by(parent_id=None).order_by(Category.sort_order).all()
    result = []
    for cat in top_categories:
        children = Category.query.filter_by(parent_id=cat.id).order_by(Category.sort_order).all()
        result.append({
            'id': cat.id,
            'name': cat.name,
            'icon': cat.icon,
            'sort_order': cat.sort_order,
            'children': [{'id': c.id, 'name': c.name, 'icon': c.icon} for c in children],
        })
    return jsonify({'categories': result, 'total': len(result)})


@api_bp.route('/favorite/add', methods=['POST'])
@login_required
def favorite_add():
    """添加收藏"""
    data = request.get_json(silent=True) or request.form
    doc_id = data.get('document_id')
    if not doc_id:
        return jsonify({'success': False, 'error': '缺少文档ID'}), 400

    existing = Favorite.query.filter_by(user_id=current_user.id, document_id=doc_id).first()
    if not existing:
        fav = Favorite(user_id=current_user.id, document_id=doc_id)
        db.session.add(fav)
        db.session.commit()
    return jsonify({'success': True})


@api_bp.route('/favorite/remove', methods=['POST'])
@login_required
def favorite_remove():
    """取消收藏"""
    data = request.get_json(silent=True) or request.form
    doc_id = data.get('document_id')
    if not doc_id:
        return jsonify({'success': False, 'error': '缺少文档ID'}), 400

    Favorite.query.filter_by(user_id=current_user.id, document_id=doc_id).delete()
    db.session.commit()
    return jsonify({'success': True})


@api_bp.route('/reading/progress', methods=['POST'])
@login_required
def reading_progress():
    """保存阅读进度"""
    data = request.get_json(silent=True) or request.form
    doc_id = data.get('document_id')
    progress = data.get('progress', 0)
    if not doc_id:
        return jsonify({'success': False, 'error': '缺少文档ID'}), 400

    history = ReadingHistory.query.filter_by(user_id=current_user.id, document_id=doc_id).first()
    if history:
        history.progress = float(progress)
    else:
        history = ReadingHistory(user_id=current_user.id, document_id=doc_id, progress=float(progress))
        db.session.add(history)
    db.session.commit()
    return jsonify({'success': True})
