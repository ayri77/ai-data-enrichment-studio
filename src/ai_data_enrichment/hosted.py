"""Entry point for a publicly reachable demo: one process serves API and frontend.

Start with:

    uvicorn --factory ai_data_enrichment.hosted:create_hosted_app --host 0.0.0.0 --port 10000

Everything is configured from the environment, never from the browser:

- `ENRICHMENT_ACCESS_CODE` (required, at least 8 characters): shared code every
  visitor must enter. Without it the app refuses to start, so a deployment can
  never be open by accident.
- `OPENAI_API_KEY`, `OPENAI_MODEL`: planning provider (model optional).
- `ENRICHMENT_ALLOW_NETWORK=1`: server network gate for live requests.
- `ENRICHMENT_STATIC_DIR` (default `frontend/dist`): the built frontend.

State is in memory and in a temporary directory, as in the local launcher: run
exactly one process, and expect a restart to clear uploads, plans and jobs.
"""

import os
from pathlib import Path

from fastapi import FastAPI

from ai_data_enrichment.api import create_app

MIN_ACCESS_CODE_LENGTH = 8


def create_hosted_app() -> FastAPI:
    code = os.getenv("ENRICHMENT_ACCESS_CODE", "")
    if len(code) < MIN_ACCESS_CODE_LENGTH or not code.isascii() or any(c.isspace() for c in code):
        raise RuntimeError(
            "Set ENRICHMENT_ACCESS_CODE to at least 8 ASCII characters without spaces."
        )
    static_dir = Path(os.getenv("ENRICHMENT_STATIC_DIR", "frontend/dist"))
    if not (static_dir / "index.html").is_file():
        raise RuntimeError(f"Built frontend not found in {static_dir}. Run `npm run build` first.")
    # Browser configuration of the provider stays disabled: this is not the local launcher.
    return create_app(access_code=code, static_dir=static_dir)
