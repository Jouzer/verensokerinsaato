"""Dash application entrypoint for local use and Railway deployment."""

from __future__ import annotations

import os

from src.dashboard import create_app


app = create_app()
server = app.server


@server.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8050"))
    debug = os.environ.get("DASH_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
