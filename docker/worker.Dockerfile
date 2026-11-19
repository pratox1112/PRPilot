# Cloud Run `worker`: the app plus the screening group (CPU torch, PromptGuard 2, Presidio
# with en_core_web_lg, detect-secrets) and the pinned model files, fetched at build time.
#
#   docker build -f docker/worker.Dockerfile --secret id=hf_token,src=<file with HF_TOKEN> \
#     -t <registry>/worker:<git sha> .
#
# The Hugging Face token is a build secret: it is read during one RUN step and is not in any
# image layer. PromptGuard is gated under the Llama 4 Community License (its LICENSE file is in
# the copied snapshot); the image stays in the project's private Artifact Registry.

FROM ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv

FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv HF_HOME=/opt/hf
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --group screening --no-install-project
COPY prpilot_core prpilot_core
COPY prpilot_data prpilot_data
RUN uv sync --locked --no-dev --group screening --no-editable
COPY scripts/fetch_worker_models.py scripts/fetch_worker_models.py
RUN --mount=type=secret,id=hf_token,required=true \
    /opt/venv/bin/python -I scripts/fetch_worker_models.py /opt/models /run/secrets/hf_token

FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
RUN useradd --create-home --uid 10001 prpilot
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/models /opt/models
COPY --from=build /opt/hf /opt/hf
WORKDIR /app
COPY services services
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 HF_HOME=/opt/hf HF_HUB_OFFLINE=1 \
    PROMPT_GUARD_DIR=/opt/models/prompt-guard
USER prpilot
EXPOSE 8080
CMD ["uvicorn", "services.worker.main:app", "--host", "0.0.0.0", "--port", "8080"]
