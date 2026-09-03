"""Flask 扩展实例集中管理，避免循环导入"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

# 数据库实例
db = SQLAlchemy()

# 登录管理实例
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录后再访问该页面'
login_manager.login_message_category = 'warning'
