"""DocHub 应用入口
运行方式：python3 app.py
生产环境请使用 wsgi.py（gunicorn/uwsgi），勿用本开发服务器。
"""
import os
from app import create_app

app = create_app()

if __name__ == '__main__':
    # debug 模式仅在显式设置 FLASK_DEBUG=1 时开启，生产环境默认关闭
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', '5000'))
    app.run(host=host, port=port, debug=debug)
