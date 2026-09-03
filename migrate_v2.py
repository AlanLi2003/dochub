#!/usr/bin/env python3
"""v2 问题修复的幂等数据迁移（不清空现有数据）：
1. posts 表补 product_id 列（与应用启动 ensure_schema 等价，这里显式执行一次）；
2. 为已收录文档补「官方原文链接」original_url；
3. 插入与产品关联的社区示例帖（按标题去重，可重复执行）；
4. 按新检索范围（仅品牌/产品全称）重建 FTS 索引。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app import create_app
from app.extensions import db
from app.models import Document, Product, Post, User
from app.search import rebuild_index

OFFICIAL_URL_RULES = [
    ('AX3600', 'https://www.mi.com/aiot/router/ax3600'),
    ('小爱', 'https://www.mi.com/aispeaker'),
    ('智能音箱', 'https://www.mi.com/aispeaker'),
    ('Excel', 'https://support.microsoft.com/zh-cn/excel'),
    ('小程序', 'https://developers.weixin.qq.com/miniprogram/dev/framework/'),
    ('多维表格', 'https://www.feishu.cn/product/base'),
]


def guess_official_url(title):
    for key, url in OFFICIAL_URL_RULES:
        if key in (title or ''):
            return url
    return None


SAMPLE_POSTS = [
    ('qa', '小米路由器 AX3600', 'manual',
     'AX3600 怎么设置 5G 和 2.4G 分开的 WiFi？',
     '想让智能家居设备连 2.4G、手机电脑连 5G，在管理后台哪里可以分开设置两个 SSID？'),
    ('help', '小米路由器 AX3600', None,
     '升级固件后管理后台 192.168.31.1 打不开了',
     '固件升级重启后手机连 WiFi 正常，但电脑浏览器打不开管理后台，已试过换浏览器和重启，求排查思路。'),
    ('tips', 'Microsoft Excel', 'faq',
     '数据透视表日期自动按月/季度分组的小技巧',
     '选中日期字段任一格 → 右键「组合」→ 步长勾选月和季度，就能一键生成月度/季度汇总，不用再写公式。'),
    ('qa', '微信小程序', None,
     '云开发数据库在真机上读不到数据怎么办？',
     '开发者工具里正常，真机预览返回空集合，权限已设为“所有用户可读”，是否还需要配置安全域名？'),
]


def main():
    app = create_app()
    with app.app_context():
        # 1. 补列
        cols = {r[1] for r in db.session.execute(text('PRAGMA table_info(posts)')).fetchall()}
        if 'product_id' not in cols:
            db.session.execute(text('ALTER TABLE posts ADD COLUMN product_id INTEGER REFERENCES products(id)'))
            db.session.commit()
            print('[1] posts.product_id 列已补充')
        else:
            print('[1] posts.product_id 已存在，跳过')

        # 2. 官方原文链接
        fixed = 0
        for doc in Document.query.all():
            if not doc.original_url:
                url = guess_official_url(doc.title)
                if url:
                    doc.original_url = url
                    fixed += 1
        db.session.commit()
        print(f'[2] 为 {fixed} 篇文档补充官方原文链接')

        # 3. 社区示例帖（幂等）
        demo = User.query.filter_by(username='demo').first() or User.query.first()
        added = 0
        for board, product_name, doc_type, title, content in SAMPLE_POSTS:
            if Post.query.filter_by(title=title).first():
                continue
            product = Product.query.filter_by(name=product_name).first()
            if not product:
                continue
            related_doc = None
            if doc_type:
                related_doc = Document.query.filter_by(
                    product_id=product.id, doc_type=doc_type).first()
            db.session.add(Post(
                user_id=demo.id, board=board, title=title, content=content,
                product_id=product.id,
                related_document_id=related_doc.id if related_doc else None,
                status='approved',
            ))
            added += 1
        db.session.commit()
        print(f'[3] 新增 {added} 条产品关联示例帖（已存在的自动跳过）')

        # 4. 重建索引
        n = rebuild_index()
        print(f'[4] FTS 索引重建完成，共 {n} 篇文档（仅索引品牌/产品全称）')

        print('\n迁移完成。')


if __name__ == '__main__':
    main()
