#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

# Start tattoo demo bot in background if token is provided
if [ -n "$DEMO_BOT_TATTOO_TOKEN" ]; then
    echo "Starting tattoo demo bot in background..."
    cd /app/demos/tattoo
    BOT_TOKEN="$DEMO_BOT_TATTOO_TOKEN" python bot.py &
    cd /app
fi

echo "Starting server..."
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
