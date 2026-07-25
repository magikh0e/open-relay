# Backups

`ops/backup.sh` writes a single **encrypted** archive holding a Postgres dump
plus the uploads volume, keeps a local retention window, and (optionally) copies
each archive **offsite** over SSH. `ops/restore.sh` puts it all back.

Run both on the VPS, from the repo directory that holds `docker-compose.prod.yml`
and `.env.prod`.

## One-time setup

1. **Tools.** The VPS needs `gpg`, `rsync`, and `docker`:
   ```bash
   sudo apt-get install -y gnupg rsync
   ```

2. **Encryption passphrase.** Generate a strong one and store it in a
   root-only file:
   ```bash
   openssl rand -base64 48 | sudo tee /root/.openrelay-backup-pass >/dev/null
   sudo chmod 600 /root/.openrelay-backup-pass
   ```
   > **Keep a copy of this passphrase somewhere OFF the server** (a password
   > manager). If the VPS dies and you only had the passphrase on it, every
   > backup is unrecoverable. This is the single most important step.

3. **Offsite target (recommended).** Backups on the same box are not backups.
   Send them to a different host (a second VPS, a shared host, or any box you
   can reach over SSH):
   - Put the VPS's public key (`~/.ssh/id_*.pub`, or generate one) into the
     offsite host's `~/.ssh/authorized_keys`.
   - Make a destination dir on the offsite host, e.g. `~/openrelay-backups`.

## Run a backup

```bash
sudo BACKUP_PASSPHRASE_FILE=/root/.openrelay-backup-pass \
     OFFSITE_HOST=your-offsite-host OFFSITE_USER=backup-user OFFSITE_PORT=22 \
     OFFSITE_PATH=openrelay-backups \
     ops/backup.sh
```

Drop the `OFFSITE_*` vars to keep backups local only. Other knobs:
`BACKUP_DIR` (default `/var/backups/openrelay`), `RETENTION_DAYS` (default 14).

## Automate it (cron)

Daily at 03:30, as root:

```cron
30 3 * * *  cd /path/to/chat-app && BACKUP_PASSPHRASE_FILE=/root/.openrelay-backup-pass OFFSITE_HOST=your-offsite-host OFFSITE_USER=backup-user OFFSITE_PORT=22 OFFSITE_PATH=openrelay-backups ops/backup.sh >> /var/log/openrelay-backup.log 2>&1
```

## Restore

Restoring is **destructive**: it drops and recreates the database and replaces
the uploads volume with the archive's contents.

```bash
sudo BACKUP_PASSPHRASE_FILE=/root/.openrelay-backup-pass \
     ops/restore.sh /var/backups/openrelay/openrelay-YYYYMMDD-HHMMSS.tar.gz.gpg
```

It stops the backend, recreates the DB from the dump, restores uploads, and
starts the backend again. Then check `/api/health` and sign in to confirm.

## Test your restore (do this)

A backup you have never restored is a guess, not a backup. At least once, run a
restore drill: copy a recent archive to a throwaway box (or a second copy of the
stack), restore into it, and confirm you can sign in and read history. The most
common failure is a lost or wrong passphrase; find that out on a drill, not
during a real outage.

## What is and isn't covered

- **Covered:** all channel and DM data (encrypted DMs restore as the same
  ciphertext), users, keys, settings, and every uploaded file.
- **Not covered:** Redis (presence/rate-limit state, all ephemeral and
  self-healing) and Caddy's issued TLS certs (re-issued automatically on start).
  Neither needs backing up.
- E2EE note: restoring gives users back their **wrapped** private keys, so they
  keep working with their existing passphrases. Nothing about encryption changes.
