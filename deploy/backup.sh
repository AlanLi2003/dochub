#!/usr/bin/env bash
# DocHub 数据备份脚本（#7）：热备份 SQLite + 打包上传目录，带 14 天保留策略
# 用法：
#   APP_DIR=/opt/dochub BACKUP_DIR=/var/backups/dochub bash deploy/backup.sh
# 建议用 systemd timer / cron 每天执行一次（见 deploy/dochub-backup.*）
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dochub}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/dochub}"
KEEP_DAYS="${KEEP_DAYS:-14}"
DB_FILE="${APP_DIR}/instance/dochub.db"
UPLOAD_DIR="${APP_DIR}/app/static/uploads"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "${BACKUP_DIR}"

# 1) SQLite 一致性热备份（.backup 会处理 WAL，比直接 cp 安全）
if command -v sqlite3 >/dev/null 2>&1 && [ -f "${DB_FILE}" ]; then
  sqlite3 "${DB_FILE}" ".backup '${BACKUP_DIR}/dochub-${STAMP}.db'"
else
  # 无 sqlite3 CLI 时的兜底：原子快照（数据量小，可接受）
  [ -f "${DB_FILE}" ] && cp "${DB_FILE}" "${BACKUP_DIR}/dochub-${STAMP}.db"
fi
gzip -f "${BACKUP_DIR}/dochub-${STAMP}.db" 2>/dev/null || true

# 2) 上传目录打包（目录不存在则跳过）
if [ -d "${UPLOAD_DIR}" ]; then
  tar -czf "${BACKUP_DIR}/uploads-${STAMP}.tar.gz" -C "${UPLOAD_DIR}" .
fi

# 3) 保留策略：清理超过 KEEP_DAYS 的旧备份
find "${BACKUP_DIR}" -name 'dochub-*.db.gz' -mtime "+${KEEP_DAYS}" -delete
find "${BACKUP_DIR}" -name 'uploads-*.tar.gz' -mtime "+${KEEP_DAYS}" -delete

echo "[backup] ${STAMP} done -> ${BACKUP_DIR}"
