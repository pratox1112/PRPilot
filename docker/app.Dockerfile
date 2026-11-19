# Cloud Run `api` and `executor` (same image, different command), and the migration job.
#
#   docker build -f docker/app.Dockerfile -t <registry>/app:<git sha> .
#
# api:        uvicorn services.api.main:app --host 0.0.0.0 --port 8080 (the default command)
# executor:   uvicorn services.executor.main:app --host 0.0.0.0 --port 8080
# migrations: alembic upgrade head
# Only the main dependencies from uv.lock: no screening models, no model client weights.

FROM ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv

FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY prpilot_core prpilot_core
COPY prpilot_data prpilot_data
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
RUN useradd --create-home --uid 10001 prpilot
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY services services
COPY alembic.ini alembic.ini
COPY migrations migrations
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
USER prpilot
EXPOSE 8080
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
