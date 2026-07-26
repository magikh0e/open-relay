#!/bin/sh
set -e

echo "[entrypoint] applying database migrations..."
alembic upgrade head

# Worker count. These are async (UvicornWorker): one process already multiplexes
# many concurrent requests on its event loop, so the sync-era "(2 x cores) + 1"
# rule badly over-provisions. Extra workers mostly duplicate the app in memory
# without adding throughput, since the CPU is the ceiling either way. Two is a
# sensible floor (one keeps serving while the other restarts). Raise it on a
# bigger box: GUNICORN_WORKERS=8.
#
# Workers share no state, so this is safe to change: WebSocket fan-out goes
# through Redis pub/sub precisely so any worker can serve any connection.
WORKERS="${GUNICORN_WORKERS:-2}"

echo "[entrypoint] starting server ($WORKERS workers)..."
exec gunicorn app.main:app \
	-k uvicorn.workers.UvicornWorker \
	-w "$WORKERS" \
	-b 0.0.0.0:8000 \
	--access-logfile - \
	--timeout 120
