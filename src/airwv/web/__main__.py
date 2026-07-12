"""Run the dashboard: ``python -m airwv.web`` (needs the ``web`` extra)."""

import os

import uvicorn

try:  # load a local .env (e.g. AIRWV_ADMIN_TOKEN) when serving; optional in prod
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from airwv.web.app import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("AIRWV_WEB_HOST", "127.0.0.1"),
        port=int(os.environ.get("AIRWV_WEB_PORT", "8000")),
    )
