"""DocHub 种子数据初始化脚本（根目录入口）
运行方式：python3 seed.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.seed import seed

if __name__ == '__main__':
    seed()
