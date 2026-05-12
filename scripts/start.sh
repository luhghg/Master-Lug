#!/bin/sh
set -e

echo "Running database migrations..."
if ! alembic upgrade head 2>&1; then
    echo "Migration failed — stamping current state as head (existing DB)"
    alembic stamp head
fi

echo "Starting server..."
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
