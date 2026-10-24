FROM python:3.12-slim

RUN pip install --no-cache-dir pytest ruff mypy
RUN useradd --create-home --uid 65534 runner
USER runner
WORKDIR /workspace
ENTRYPOINT []

