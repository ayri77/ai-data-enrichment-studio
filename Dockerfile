# One image for the hosted demo: the API and the built frontend on one origin.
# Build: docker build -t enrichment-studio .
# Run:   docker run -p 10000:10000 -e ENRICHMENT_ACCESS_CODE=... -e OPENAI_API_KEY=... \
#            -e ENRICHMENT_ALLOW_NETWORK=1 enrichment-studio

FROM node:24-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1
RUN pip install --no-cache-dir uv==0.12.15
WORKDIR /app
# Runtime dependencies only; the dev group (tests, linters) stays out of the image.
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --locked --no-dev
# Example workbooks and their prepared plans, offered on the first screen.
COPY samples/capitals.xlsx samples/mountains.xlsx samples/capitals_roads.xlsx ./samples/
COPY demo/capitals_api_plan.json demo/mountains_api_plan.json \
     demo/capitals_roads_api_plan.json ./demo/
COPY --from=frontend /build/frontend/dist ./frontend/dist
ENV ENRICHMENT_STATIC_DIR=/app/frontend/dist \
    PORT=10000
# Uploads and results are written to the system temporary directory only.
RUN useradd --create-home --uid 10001 studio
USER studio
EXPOSE 10000
# One process: plans and jobs live in memory. The factory refuses to start
# without ENRICHMENT_ACCESS_CODE, so the demo cannot be open by accident.
CMD ["sh", "-c", "exec /app/.venv/bin/uvicorn --factory ai_data_enrichment.hosted:create_hosted_app --host 0.0.0.0 --port \"$PORT\""]
