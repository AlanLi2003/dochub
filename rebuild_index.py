#!/usr/bin/env python3
"""重建全文检索索引脚本（FTS5 + jieba 分词）
用法：python3 rebuild_index.py
适用：数据导入/直接改库/索引异常后的全量重建。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.search import rebuild_index

app = create_app()
with app.app_context():
    n = rebuild_index()
    print(f'全文检索索引重建完成，共索引 {n} 篇已发布文档。')
