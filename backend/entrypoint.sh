#!/bin/sh
set -e

echo "[entrypoint] applying database migrations..."
alembic upgrade head

echo "[entrypoint] starting server..."
exec gunicorn app.main:app \
	-k uvicorn.workers.UvicornWorker \
	-w 4 \
	-b 0.0.0.0:8000 \
	--access-logfile - \
	--timeout 120
