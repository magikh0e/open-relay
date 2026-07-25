# Uptime monitoring

Whatever you use, the rule that matters is: **it has to run somewhere other than
the machine it watches.** A monitor living on the VPS cannot tell you the VPS is
down, which is the outage you most need to hear about.

There are two sensible ways to do this. Pick one.

---

## Option 1: a hosted monitor (recommended)

For most people this is the right answer, and it is what
`orc.openrelay.pl` uses. A third-party service polls your health endpoint from
outside your infrastructure, so it survives your server, your network and your
hosting provider all failing at once. Free tiers cover this comfortably and
there is nothing to install, patch or monitor in turn.

Services that do HTTP checks on a free tier include
[UptimeRobot](https://uptimerobot.com) and
[Better Stack](https://betterstack.com/uptime). Prefer to keep it self-hosted?
[Uptime Kuma](https://github.com/louislam/uptime-kuma) is one Docker container
and gives you a status page too, but it still needs to live on a *different*
box.

Configure the monitor with:

| Setting | Value |
|---|---|
| URL | `https://your-domain/api/health` (add one per hostname you serve) |
| Method | `GET` |
| Expect | HTTP `200`, body contains `ok` |
| Interval | 1 to 5 minutes |
| Alert | email, or a Discord/Slack webhook |

That is the whole setup. The rest of this document is only needed if you would
rather not involve a third party.

---

## Option 2: self-hosted, with `ops/uptime-check.sh`

`ops/uptime-check.sh` polls the app's health endpoint(s) and alerts you the
moment a target goes down (and again when it recovers). It only fires on a
**state change**, so you get one "down" message and one "recovered" message,
not a page every two minutes.

> Same rule as above: **run it on a different box than the one it watches.** An
> off-site machine that already holds your backups is a natural home; any
> always-on machine with `bash` and `curl` works. The systemd units in
> `ops/systemd/` ship with a placeholder `ExecStart` path deliberately, because
> only you know where this repo lives on that machine.

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

This script is deliberately tiny and alert-only: it tells you when something
changed, and keeps no history. If you want a status page and uptime figures,
that is what the hosted services in Option 1 give you, and running both is
fine. They are not mutually exclusive.
