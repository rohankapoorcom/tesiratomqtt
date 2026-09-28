# syntax=docker/dockerfile:1

# Docker Hardened Images require `docker login dhi.io` (Docker Hub credentials).
# The build and runtime stages must use the same Python version so the venv's
# interpreter symlinks resolve in the runtime image.
ARG PYTHON_TAG=3.13-alpine

## -----------------------------------------------------
## Build stage: the -dev variant has a shell and runs as root.
FROM dhi.io/python:${PYTHON_TAG}-dev AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/uv

# Use the image's Python rather than a uv-managed download, which would not
# exist in the runtime stage.
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/venv

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev

## -----------------------------------------------------
## Runtime stage: no shell or package manager, runs as nonroot (UID 65532).
FROM dhi.io/python:${PYTHON_TAG}

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/venv/bin:$PATH"

WORKDIR /app

COPY --from=build /app/venv /app/venv
COPY src .

USER 65532

ENV HEALTHCHECK_URL=http://127.0.0.1:8080/health
EXPOSE 8080
# Exec form is required: the runtime image has no /bin/sh.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen(os.environ['HEALTHCHECK_URL'])"]

ENTRYPOINT ["python", "__init__.py"]
CMD ["--config", "/config/config.yaml"]
