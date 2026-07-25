# Uptime monitoring

`ops/uptime-check.sh` polls the app's health endpoint(s) and alerts you the
moment a target goes down (and again when it recovers). It only fires on a
**state change**, so you get one "down" message and one "recovered" message,
not a page every two minutes.

> **Run it on a different box than the one it watches.** A monitor living on the
> VPS can't tell you when the VPS is down. The Hostinger box that already holds
> your offsite backups is a natural home; any always-on machine with `bash` and
> `curl` works.

## What it checks

By default, both hostnames' health endpoints:

- `https://orc.openrelay.pl/api/health`
- `https://chat.openrelay.pl/api/health`

A target is **up** when the request returns HTTP 200 and the body contains `ok`.
A connection failure or any non-200 is **down**, confirmed across `RETRIES`
probes (default 2) so a single blip doesn't page you.

> Note: `/api/health` reports that the web + app process is answering. It does
> not deep-check Postgres/Redis, so it catches "the box or app is down", which
> is the outage you most need to hear about.

## One-time setup

1. **Get an alert channel.** The simplest is a Discord webhook (you already use
   Discord): in a server, *Channel → Edit → Integrations → Webhooks → New
   Webhook → Copy URL*. Any Discord/Slack-style webhook that accepts
   `{"content": "..."}` works. (Prefer email or ntfy? Use `ALERT_CMD` instead.)

2. **Create the env file** (root-only), e.g. `/etc/openrelay-uptime.env`:
   ```
   ALERT_WEBHOOK=https://discord.com/api/webhooks/XXXX/YYYY
   # Optional overrides:
   # CHECK_URLS=https://orc.openrelay.pl/api/health
   # RETRIES=2
   # STATE_DIR=/var/lib/openrelay-uptime
   # ALERT_CMD=/usr/local/bin/notify-me   # gets the message as $1
   ```
   ```bash
   sudo chmod 600 /etc/openrelay-uptime.env
   ```

3. **Smoke test it** before scheduling:
   ```bash
   sudo env $(grep -v '^#' /etc/openrelay-uptime.env | xargs) ops/uptime-check.sh
   ```
   You should see `... -> up` lines and no alert (nothing changed). To prove the
   alert path works, point it at a dead URL once:
   ```bash
   sudo env ALERT_WEBHOOK=... CHECK_URLS=https://orc.openrelay.pl/nope \
        STATE_DIR=/tmp/uptime-test ops/uptime-check.sh
   ```
   That transitions "up"→"down" and should deliver a message.

## Schedule it: systemd (a box you control)

```bash
sudo cp ops/systemd/openrelay-uptime.service ops/systemd/openrelay-uptime.timer \
        /etc/systemd/system/
sudo sed -i "s#/home/YOUR_USER/chat-app#$PWD#" /etc/systemd/system/openrelay-uptime.service
sudo systemctl daemon-reload
sudo systemctl enable --now openrelay-uptime.timer
```
Check it:
```bash
systemctl list-timers openrelay-uptime.timer     # next run
sudo systemctl start openrelay-uptime.service     # run once now
journalctl -u openrelay-uptime.service -n 30      # see the results
```

## Schedule it: cron (shared hosting, e.g. Hostinger)

Shared hosting usually has cron but not systemd. Put the secrets inline and use
a writable `STATE_DIR` in your home:

```cron
*/2 * * * * ALERT_WEBHOOK='https://discord.com/api/webhooks/XXXX/YYYY' STATE_DIR="$HOME/.openrelay-uptime" /home/YOUR_USER/chat-app/ops/uptime-check.sh >> "$HOME/openrelay-uptime.log" 2>&1
```

Make the script executable once: `chmod +x ops/uptime-check.sh`.

## Want a dashboard too?

This script is deliberately tiny and alert-only. If you'd like a status page and
history, run [Uptime Kuma](https://github.com/louislam/uptime-kuma) (one Docker
container) on the same off-site box and point a monitor at
`https://orc.openrelay.pl/api/health`. The two aren't mutually exclusive.
