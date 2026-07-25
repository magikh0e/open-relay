#!/usr/bin/env bash
#
# Restore an Open Relay backup produced by ops/backup.sh. DESTRUCTIVE: it drops
# and recreates the database and replaces the uploads volume with the archive's
# contents. Run on the VPS.
#
#   ops/restore.sh /var/backups/openrelay/openrelay-YYYYMMDD-HHMMSS.tar.gz.gpg
#
# Needs the same BACKUP_PASSPHRASE_FILE that produced the archive. Set FORCE=1
# to skip the confirmation prompt (for scripted DR drills).
#
set -euo pipefail

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

ARCHIVE="${1:-}"
[ -n "$ARCHIVE" ] || die "usage: restore.sh <archive.tar.gz.gpg>"
[ -r "$ARCHIVE" ] || die "cannot read archive: $ARCHIVE"
[ -n "${BACKUP_PASSPHRASE_FILE:-}" ] && [ -r "$BACKUP_PASSPHRASE_FILE" ] \
  || die "set BACKUP_PASSPHRASE_FILE to the passphrase used for this archive"

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"
cd "$PROJECT_DIR"

PG_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
PG_DB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
[ -n "$PG_USER" ] && [ -n "$PG_DB" ] || die "POSTGRES_USER / POSTGRES_DB missing from $ENV_FILE"
DC="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE"

if [ "${FORCE:-0}" != "1" ]; then
  echo "This will REPLACE the '$PG_DB' database and all uploads with:"
  echo "  $ARCHIVE"
  read -r -p "Type 'restore' to continue: " ans
  [ "$ans" = "restore" ] || die "aborted"
fi

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
log "decrypting archive"
gpg --batch --yes --decrypt --passphrase-file "$BACKUP_PASSPHRASE_FILE" "$ARCHIVE" \
  | tar xzf - -C "$WORK" || die "decrypt/extract failed"
[ -f "$WORK/db.sql" ] && [ -f "$WORK/uploads.tar.gz" ] || die "archive missing db.sql or uploads.tar.gz"

log "stopping backend"
$DC stop backend

log "recreating database '$PG_DB'"
$DC exec -T db psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 <<SQL || die "db recreate failed"
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$PG_DB' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "$PG_DB";
CREATE DATABASE "$PG_DB" OWNER "$PG_USER";
SQL

log "loading dump"
$DC exec -T db psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 < "$WORK/db.sql" >/dev/null \
  || die "psql restore failed"

log "restoring uploads"
$DC run --rm -T backend sh -c 'rm -rf /app/uploads/* /app/uploads/.[!.]* 2>/dev/null; tar xzf - -C /app/uploads' \
  < "$WORK/uploads.tar.gz" || die "uploads restore failed"

log "starting backend"
$DC up -d backend

log "restore complete; verify at /api/health and by signing in"
