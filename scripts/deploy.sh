#!/bin/bash
# Manual deploy: pull new image and restart
set -e

cd /opt/masterlug

echo "Pulling latest image..."
docker compose -f docker-compose.prod.yml pull app

echo "Restarting app..."
docker compose -f docker-compose.prod.yml up -d app

echo "Waiting for health check..."
sleep 5
docker compose -f docker-compose.prod.yml ps app

echo "Done. Logs:"
docker compose -f docker-compose.prod.yml logs --tail=20 app
