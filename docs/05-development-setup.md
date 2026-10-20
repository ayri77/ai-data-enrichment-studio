# Development Setup

**Status:** Bootstrap baseline, v0.1.

## Requirements

- Python 3.12 (managed with `uv`).
- Git and `uv` available on PATH.

## Local setup

```powershell
uv python install 3.12
uv lock
uv sync --locked --group dev
uv run enrichment --help
uv run enrichment version
uv run pytest
```

`uv.lock` must be generated and committed before the first GitHub Actions run to ensure reproducible CI and local installations. Do not commit `.venv` or local credentials.

## Check Excel input safely

```powershell
uv run enrichment inspect samples/capitals.xlsx
uv run enrichment inspect samples/mountains.xlsx
```

The bootstrap inspector assumes the first worksheet row contains headers. It is read-only and intentionally does not perform enrichment.

## Quality gates

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest --cov=ai_data_enrichment --cov-report=term-missing
```

To apply formatting locally:

```powershell
uv run ruff format .
uv run ruff check . --fix
```

## CI

`.github/workflows/ci.yml` runs on pushes to `main`, pull requests, and manual dispatch. The workflow has no external LLM calls and does not require API keys.

## Deferred dependencies

LangGraph, LLM SDKs, HTTP clients, caching libraries and backend/UI dependencies are installed only when the corresponding milestone requires them.

## Notes

The initial package name is `ai_data_enrichment` and its CLI is `enrichment`. Domain contracts and actual enrichment execution will follow in M1/M2.
