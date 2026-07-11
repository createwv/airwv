# Deploying AirWV collection

The collector needs to run on a schedule so history accumulates continuously.
Pick whichever fits your host. All options read the PurpleAir API key from the
environment (`.env` or `EnvironmentFile`) — never bake it into these files.

Before scheduling, resolve sensor indices once:

```bash
python -m airwv.ingest resolve
```

## Option A — systemd timer (recommended for a Linux server)

A timer fires a one-shot collection every 5 minutes. More robust than a
long-lived process: each run is independent and restarts cleanly.

```bash
sudo cp deploy/systemd/airwv-collect.* /etc/systemd/system/
# edit User / WorkingDirectory / venv path in the .service file
sudo systemctl daemon-reload
sudo systemctl enable --now airwv-collect.timer
systemctl list-timers airwv-collect.timer      # confirm it's scheduled
journalctl -u airwv-collect.service -f          # watch runs
```

## Option B — built-in loop (simple / cross-platform)

Long-running process that collects every `AIRWV_POLL_INTERVAL_SECONDS` (default
hourly) with retry + exponential backoff, and **delivers alerts after each
collection**:

```bash
python -m airwv.ingest run              # collect + evaluate/send alerts
python -m airwv.ingest run --no-alerts  # collect only
```

> Using the timer/cron approach (Option A/C) instead? Those run `collect` only —
> add a matching `python -m airwv.ingest alerts --send` step to also deliver alerts.

Good for a quick start, a container `CMD`, or running under a process manager
(supervisor, pm2, tmux). Under systemd you'd use `Type=simple` + `Restart=always`
with `ExecStart=… -m airwv.ingest run`.

## Option C — cron

```cron
# every 5 minutes; adjust paths
*/5 * * * * cd /opt/airwv && .venv/bin/python -m airwv.ingest collect >> /var/log/airwv.log 2>&1
```

## Dashboard

### Local (take a look)

```bash
pip install -e ".[web]"
python -m airwv.web            # http://127.0.0.1:8000
```

### Public (e.g. air.createwv.org)

The dashboard is read-only over public sensor data. Run it bound to localhost and
put a reverse proxy in front for HTTPS.

1. **DNS:** point `air.createwv.org` (A record) at the server's IP.
2. **App:** on the server, clone to `/opt/airwv`, create the venv, install:
   ```bash
   git clone git@github.com:createwv/airwv.git /opt/airwv
   cd /opt/airwv && python3.12 -m venv .venv && . .venv/bin/activate
   pip install -e ".[web]"
   ```
   Bring the data: either copy your local `airwv.sqlite` up, or run collection on
   the server (`resolve` + `backfill`/`run`) once points are available.
3. **Service:** install `deploy/systemd/airwv-web.service` (binds 127.0.0.1:8000):
   ```bash
   sudo cp deploy/systemd/airwv-web.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now airwv-web
   ```
4. **Reverse proxy + TLS** — Caddy gives automatic HTTPS. `/etc/caddy/Caddyfile`:
   ```
   air.createwv.org {
       reverse_proxy 127.0.0.1:8000
   }
   ```
   `sudo systemctl reload caddy`. (nginx + certbot works too.)

Notes: the dashboard has no auth (it shows public data); add HTTP basic-auth at the
proxy if you want it private during preview. Sensor coordinates shown are the same
ones PurpleAir already publishes.

## Notes

- **Resolve occasionally, not every run.** Re-run `resolve` after deploying or
  renaming sensors; `collect` reuses the cached index map.
- **Storage.** SQLite is fine to start; point `AIRWV_DATABASE_URL` at Postgres
  for production and durability.
- **GitHub Actions is a poor fit** for the collector — runners are ephemeral, so
  a local SQLite DB wouldn't persist. Use a host with durable storage.

## Schema migrations (Alembic)

Schema is defined by the models in `airwv/storage/models.py`. `create_schema()`
(create_all) creates any **missing tables** — but can't **alter** existing ones, so
column changes go through **Alembic** (batch mode is on for SQLite ALTER support).

```bash
# fresh database — create everything:
alembic upgrade head

# EXISTING database that already has tables (created earlier by create_all):
python -c "from airwv.storage import Store; import os; \
  Store(os.environ.get('AIRWV_DATABASE_URL') or 'sqlite:///airwv.sqlite').create_schema()"
alembic stamp head          # mark it at the baseline; future `alembic upgrade head` applies changes

# after editing a model, generate + apply a migration:
alembic revision --autogenerate -m "add column X"
alembic upgrade head
```

Alembic reads `AIRWV_DATABASE_URL` (same as the app), so run these from the repo root
with the env loaded (`set -a; . ./.env; set +a`).
