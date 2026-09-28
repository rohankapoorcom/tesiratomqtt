# syntax=docker/dockerfile:1

# Docker Hardened Images require `docker login dhi.io` (Docker Hub credentials).
# The build and runtime stages must use the same Python version so the venv's
# interpreter symlinks resolve in the runtime image.
ARG PYTHON_TAG=3.13-alpine

## -----------------------------------------------------
## Build stage: the -dev variant has a shell and pip and runs as root.
FROM dhi.io/python:${PYTHON_TAG}-dev AS build

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN python -m venv /app/venv

# Leverage a cache mount to /root/.cache/pip to speed up subsequent builds.
# Leverage a bind mount to requirements.txt to avoid copying it into this layer.
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    /app/venv/bin/pip install -r requirements.txt

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
