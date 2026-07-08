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

## Notes

- **Resolve occasionally, not every run.** Re-run `resolve` after deploying or
  renaming sensors; `collect` reuses the cached index map.
- **Storage.** SQLite is fine to start; point `AIRWV_DATABASE_URL` at Postgres
  for production and durability.
- **GitHub Actions is a poor fit** for the collector — runners are ephemeral, so
  a local SQLite DB wouldn't persist. Use a host with durable storage.
