FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --create-home --uid 10001 aegis
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[postgres]"
USER aegis
# Default: paper-trading worker. Override the command for the API: ["aegis", "api", "--host", "0.0.0.0"]
CMD ["aegis", "run"]
