# DocHub 容器镜像（可选部署方式）
FROM python:3.11-slim

# 中文字体：reportlab 导出中文 PDF 需要
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-wqy-zenhei libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 数据目录（SQLite + 上传文件）通过卷持久化，避免随容器销毁
RUN mkdir -p /app/instance /app/app/static/uploads
VOLUME ["/app/instance", "/app/app/static/uploads"]

ENV APP_ENV=production \
    FLASK_DEBUG=0 \
    PYTHONUNBUFFERED=1
# SECRET_KEY 必须在运行时通过 -e 或 compose 注入，不写死在镜像里

EXPOSE 8000
CMD ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:8000", "--timeout", "60", "wsgi:app"]
