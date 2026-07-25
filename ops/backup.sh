#!/usr/bin/env bash
#
# Open Relay backup. Produces ONE encrypted archive containing a Postgres dump
# plus the uploads volume, keeps a local retention window, and optionally copies
# the archive offsite over SSH. Run this on the VPS where the prod stack lives.
#
# Restore with ops/restore.sh. See ops/BACKUP.md for setup, cron, and (please)
# a tested restore.
#
# Config comes from environment variables; sensible defaults match the prod
# compose file. The only thing you MUST provide is the encryption passphrase.
#
#   BACKUP_PASSPHRASE_FILE   path to a file holding the encryption passphrase
#                            (chmod 600, keep a copy OFFSITE: lose it and the
#                            backups are unrecoverable)
#
# Optional:
#   PROJECT_DIR      dir containing docker-compose.prod.yml (default: this repo)
#   COMPOSE_FILE     default: docker-compose.prod.yml
#   ENV_FILE         default: .env.prod (read for POSTGRES_USER / POSTGRES_DB)
#   BACKUP_DIR       where archives are written (default: /var/backups/openrelay)
#   RETENTION_DAYS   local archives to keep (default: 14)
#   OFFSITE_HOST     if set, rsync the archive here over SSH
#   OFFSITE_USER / OFFSITE_PORT / OFFSITE_PATH / OFFSITE_KEY
#
set -euo pipefail

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/openrelay}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

[ -n "${BACKUP_PASSPHRASE_FILE:-}" ] || die "set BACKUP_PASSPHRASE_FILE"
[ -r "$BACKUP_PASSPHRASE_FILE" ] || die "cannot read BACKUP_PASSPHRASE_FILE ($BACKUP_PASSPHRASE_FILE)"
command -v gpg >/dev/null || die "gpg is required (apt install gnupg)"
command -v docker >/dev/null || die "docker is required"

cd "$PROJECT_DIR"
[ -f "$COMPOSE_FILE" ] || die "no $COMPOSE_FILE in $PROJECT_DIR"
[ -f "$ENV_FILE" ] || die "no $ENV_FILE in $PROJECT_DIR"

# Pull the DB credentials out of the env file without sourcing the whole thing.
PG_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
PG_DB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
[ -n "$PG_USER" ] && [ -n "$PG_DB" ] || die "POSTGRES_USER / POSTGRES_DB missing from $ENV_FILE"

DC="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE"
STAMP="$(date '+%Y%m%d-%H%M%S')"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$BACKUP_DIR"

log "backup starting ($STAMP)"

# 1. Postgres dump (plain SQL; restores with psql).
log "dumping database '$PG_DB'"
$DC exec -T db pg_dump -U "$PG_USER" "$PG_DB" > "$WORK/db.sql" \
  || die "pg_dump failed"

# 2. Uploads volume (streamed out of the backend container).
log "archiving uploads"
$DC exec -T backend tar czf - -C /app/uploads . > "$WORK/uploads.tar.gz" \
  || die "uploads archive failed"

# 3. Bundle + encrypt (AES-256, symmetric).
ARCHIVE="$BACKUP_DIR/openrelay-$STAMP.tar.gz.gpg"
log "encrypting -> $ARCHIVE"
tar czf - -C "$WORK" db.sql uploads.tar.gz \
  | gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase-file "$BACKUP_PASSPHRASE_FILE" \
        -o "$ARCHIVE" \
  || die "encryption failed"

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
log "wrote $ARCHIVE ($SIZE)"

# 4. Prune old local archives.
find "$BACKUP_DIR" -name 'openrelay-*.tar.gz.gpg' -mtime "+$RETENTION_DAYS" -print -delete \
  | sed 's/^/pruned /' || true

# 5. Offsite copy (optional).
if [ -n "${OFFSITE_HOST:-}" ]; then
  log "copying offsite to ${OFFSITE_USER:-$USER}@$OFFSITE_HOST"
  ssh_opts="-p ${OFFSITE_PORT:-22} -o StrictHostKeyChecking=accept-new"
  [ -n "${OFFSITE_KEY:-}" ] && ssh_opts="$ssh_opts -i $OFFSITE_KEY"
  rsync -az -e "ssh $ssh_opts" "$ARCHIVE" \
    "${OFFSITE_USER:-$USER}@$OFFSITE_HOST:${OFFSITE_PATH:-openrelay-backups}/" \
    || die "offsite rsync failed"
  log "offsite copy done"
fi

log "backup complete"
