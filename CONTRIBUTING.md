# Contributing to AirWV

Thanks for helping monitor West Virginia's air. AirWV is a community project and
contributions of all kinds are welcome — code, docs, sensor data, and domain
expertise.

## Ways to help

- **Code** — ingestion, storage, analysis, alerts, API, or the web front end.
- **Sensors & data** — help identify WV sensors to include, or validate readings.
- **Docs** — clarify setup, architecture, or the science.
- **Issues** — report bugs or propose features.

## Development setup

```bash
git clone git@github.com:createwv/airwv.git
cd airwv
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then add your PurpleAir API key
```

## Ground rules

- **Never commit secrets.** API keys and credentials live in `.env` (gitignored)
  or a secret manager. If you leak one, rotate it immediately.
- **Never lose raw data.** Handle bad readings by flagging, not deleting.
- Keep the diff focused; match the style of surrounding code.
- Add or update tests for behavior changes.
- Run checks before opening a PR:

```bash
ruff check .
pytest
```

## Pull requests

1. Fork and branch from `main`.
2. Make your change with tests.
3. Open a PR describing the what and why, linking any related issue.

By contributing you agree your contributions are licensed under the project's
[MIT License](LICENSE).
