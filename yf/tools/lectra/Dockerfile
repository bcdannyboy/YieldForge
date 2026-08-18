# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.10.8@sha256:88234bc9e09c2b2f6d176a3daf411419eb0370d450a08129257410de9cfafd2a AS uv

FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7 AS builder
COPY --from=uv /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --only-group data --no-install-project

FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7 AS runtime
WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/nonexistent

COPY --from=builder /app/.venv /app/.venv
COPY src/yieldforge/__init__.py /app/src/yieldforge/__init__.py
COPY src/yieldforge/domain.py /app/src/yieldforge/domain.py
COPY src/yieldforge/datasets/__init__.py /app/src/yieldforge/datasets/__init__.py
COPY src/yieldforge/datasets/source_manifest.py /app/src/yieldforge/datasets/source_manifest.py
COPY src/yieldforge/datasets/lectra_audit.py /app/src/yieldforge/datasets/lectra_audit.py
COPY src/yieldforge/datasets/normalized_slice.py /app/src/yieldforge/datasets/normalized_slice.py
COPY src/yieldforge/datasets/lectra_slice.py /app/src/yieldforge/datasets/lectra_slice.py
COPY datasets/sources/lectra-7030786-v1.1.json /app/datasets/sources/lectra-7030786-v1.1.json
COPY tools/lectra/qualify.py /app/tools/lectra/qualify.py

USER 65532:65532
ENTRYPOINT ["/app/.venv/bin/python", "/app/tools/lectra/qualify.py"]
