FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

EXPOSE 8000

# The entrypoint runs `alembic upgrade head` and then launches gunicorn, which
# manages N uvicorn workers. WebSocket fan-out works across all workers because
# messages are published through Redis, not held in-process.
CMD ["./entrypoint.sh"]
