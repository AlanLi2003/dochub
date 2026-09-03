"""DocHub 生产 WSGI 入口
用于 gunicorn / uWSGI 等生产 WSGI 服务器启动应用。

使用示例：
  gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app
  uwsgi --http :8000 --module wsgi:app

生产环境必须设置以下环境变量：
  SECRET_KEY=your-strong-secret-key
  FLASK_DEBUG=0
"""
from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run()
