"""DocHub 数据库模型定义
严格按照 PRD v1.1 实体设计，共 12 个模型
"""
import hashlib
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db, login_manager

# 密码哈希算法：统一用 pbkdf2:sha256。
# 原因：Werkzeug 3.x 默认算法是 scrypt，而 macOS 自带 /usr/bin/python3(3.9) 的
# hashlib 基于 LibreSSL、未提供 hashlib.scrypt，会在校验密码时抛
# “module 'hashlib' has no attribute 'scrypt'”导致登录 500；pbkdf2 各平台均内置。
PASSWORD_HASH_METHOD = 'pbkdf2:sha256'


def scrypt_available():
    """当前解释器的 hashlib 是否支持 scrypt（用于旧哈希兼容判断）。"""
    return hasattr(hashlib, 'scrypt')


@login_manager.user_loader
def load_user(user_id):
    """Flask-Login 用户加载回调"""
    return User.query.get(int(user_id))


class User(UserMixin, db.Model):
    """用户表"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    avatar = db.Column(db.String(256), default='')  # 头像URL，空则用默认
    role = db.Column(db.String(20), default='user')  # user / admin
    bio = db.Column(db.Text, default='')  # 个人简介
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 关联
    comments = db.relationship('Comment', backref='user', lazy='dynamic')
    posts = db.relationship('Post', backref='user', lazy='dynamic')
    replies = db.relationship('Reply', backref='user', lazy='dynamic')
    favorites = db.relationship('Favorite', backref='user', lazy='dynamic')
    reading_histories = db.relationship('ReadingHistory', backref='user', lazy='dynamic')
    contributions = db.relationship('Contribution', backref='user', lazy='dynamic')

    def set_password(self, password):
        """设置密码（加密存储，统一 pbkdf2:sha256 以兼容无 scrypt 的解释器）"""
        self.password_hash = generate_password_hash(password, method=PASSWORD_HASH_METHOD)

    def check_password(self, password):
        """校验密码；当存量哈希算法在当前解释器不可用（如无 scrypt）时返回 False 而非抛异常"""
        try:
            return check_password_hash(self.password_hash, password)
        except (AttributeError, ValueError):
            return False

    @property
    def is_admin(self):
        return self.role == 'admin'

    def __repr__(self):
        return f'<User {self.username}>'


class Category(db.Model):
    """分类表（支持二级分类）"""
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    icon = db.Column(db.String(256), default='')  # SVG路径或class
    sort_order = db.Column(db.Integer, default=0)
    parent_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)

    # 自关联：子分类
    children = db.relationship('Category', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')

    def __repr__(self):
        return f'<Category {self.name}>'


class Brand(db.Model):
    """品牌/网站表"""
    __tablename__ = 'brands'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, index=True)
    logo = db.Column(db.String(256), default='')
    website_url = db.Column(db.String(512), default='')
    description = db.Column(db.Text, default='')
    status = db.Column(db.String(20), default='active')  # active / inactive / pending
    category_ids = db.Column(db.String(256), default='')  # 逗号分隔的分类ID字符串
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 关联
    products = db.relationship('Product', backref='brand', lazy='dynamic')

    def __repr__(self):
        return f'<Brand {self.name}>'


class Product(db.Model):
    """产品表"""
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    brand_id = db.Column(db.Integer, db.ForeignKey('brands.id'), nullable=False)
    name = db.Column(db.String(128), nullable=False, index=True)
    category = db.Column(db.String(64), default='')  # 产品品类
    description = db.Column(db.Text, default='')
    cover_image = db.Column(db.String(256), default='')
    status = db.Column(db.String(20), default='active')  # active / inactive
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 关联
    documents = db.relationship('Document', backref='product', lazy='dynamic')
    posts = db.relationship('Post', backref='product', lazy='dynamic')

    def __repr__(self):
        return f'<Product {self.name}>'


class Document(db.Model):
    """文档表"""
    __tablename__ = 'documents'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    title = db.Column(db.String(256), nullable=False, index=True)
    doc_type = db.Column(db.String(32), default='other')
    # quick_start / manual / faq / troubleshooting / feature / other
    description = db.Column(db.Text, default='')
    file_path = db.Column(db.String(512), default='')  # 上传文件路径
    original_url = db.Column(db.String(512), default='')  # 原文链接
    content = db.Column(db.Text, default='')  # Markdown 文本内容
    toc_json = db.Column(db.Text, default='')  # 目录结构 JSON 字符串
    status = db.Column(db.String(20), default='published')  # draft / published / pending
    view_count = db.Column(db.Integer, default=0)
    download_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关联
    comments = db.relationship('Comment', backref='document', lazy='dynamic')
    favorites = db.relationship('Favorite', backref='document', lazy='dynamic')
    reading_histories = db.relationship('ReadingHistory', backref='document', lazy='dynamic')

    def __repr__(self):
        return f'<Document {self.title}>'


class Comment(db.Model):
    """文档评论表（支持回复）"""
    __tablename__ = 'comments'

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('comments.id'), nullable=True)  # 支持回复
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='approved')  # pending / approved / rejected
    like_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 自关联：回复
    replies = db.relationship('Comment', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')

    def __repr__(self):
        return f'<Comment id={self.id} doc={self.document_id}>'


class Post(db.Model):
    """社区帖子表"""
    __tablename__ = 'posts'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    board = db.Column(db.String(20), default='chat')  # qa / tips / help / feedback / chat
    title = db.Column(db.String(256), nullable=False)
    content = db.Column(db.Text, default='')
    view_count = db.Column(db.Integer, default=0)
    reply_count = db.Column(db.Integer, default=0)
    is_solved = db.Column(db.Boolean, default=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)  # 关联目标产品
    related_document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=True)
    status = db.Column(db.String(20), default='approved')  # pending / approved / rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 关联
    replies = db.relationship('Reply', backref='post', lazy='dynamic')
    related_document = db.relationship('Document', foreign_keys=[related_document_id], lazy='joined')

    def __repr__(self):
        return f'<Post {self.title}>'


class Reply(db.Model):
    """帖子回复表"""
    __tablename__ = 'replies'

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('replies.id'), nullable=True)  # 楼中楼
    content = db.Column(db.Text, nullable=False)
    is_best_answer = db.Column(db.Boolean, default=False)
    like_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='approved')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 自关联
    children = db.relationship('Reply', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')

    def __repr__(self):
        return f'<Reply id={self.id} post={self.post_id}>'


class Favorite(db.Model):
    """收藏表"""
    __tablename__ = 'favorites'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'document_id', name='uq_user_document_favorite'),)

    def __repr__(self):
        return f'<Favorite user={self.user_id} doc={self.document_id}>'


class ReadingHistory(db.Model):
    """阅读历史表"""
    __tablename__ = 'reading_histories'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=False)
    progress = db.Column(db.Float, default=0.0)  # 阅读进度百分比
    last_read_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'document_id', name='uq_user_document_history'),)

    def __repr__(self):
        return f'<ReadingHistory user={self.user_id} doc={self.document_id} progress={self.progress}%>'


class Contribution(db.Model):
    """贡献记录表（用户提交的网站/产品/文档）"""
    __tablename__ = 'contributions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    contribution_type = db.Column(db.String(20), nullable=False)  # brand / product / document
    target_id = db.Column(db.Integer, nullable=True)  # 审核通过后关联的实体ID
    title = db.Column(db.String(256), nullable=False)
    payload_json = db.Column(db.Text, default='')  # 提交的完整数据 JSON
    status = db.Column(db.String(20), default='pending')
    # pending / approved / rejected / withdrawn
    review_note = db.Column(db.Text, default='')  # 审核意见
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f'<Contribution {self.contribution_type}: {self.title} [{self.status}]>'


class Term(db.Model):
    """术语库表（用于阅读页术语解释卡片）"""
    __tablename__ = 'terms'

    id = db.Column(db.Integer, primary_key=True)
    term = db.Column(db.String(128), unique=True, nullable=False, index=True)
    explanation = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Term {self.term}>'
