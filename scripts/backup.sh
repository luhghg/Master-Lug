#!/bin/bash
# Run via cron: 0 3 * * * /opt/masterlug/scripts/backup.sh
set -e

BACKUP_DIR="/opt/masterlug/backups"
DATE=$(date +%Y%m%d_%H%M%S)
FILE="$BACKUP_DIR/masterlug_$DATE.sql.gz"

mkdir -p "$BACKUP_DIR"

docker exec masterlug-db-1 pg_dump -U postgres masterlug_db | gzip > "$FILE"

# Keep only last 7 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +7 -delete

echo "Backup saved: $FILE"
