FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

RUN useradd --create-home --uid 10001 repoagent \
    && mkdir -p /app/.repoagent \
    && chown -R repoagent:repoagent /app
USER repoagent

EXPOSE 8000 8100 8501
CMD ["repoagent-api"]

