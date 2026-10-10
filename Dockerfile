FROM python:3.12-slim-bookworm AS builder

# Install uv for fast, reliable dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Copy project definition and install dependencies into a dedicated venv
COPY pyproject.toml /app/
RUN uv venv /app/.venv && \
    uv pip install --no-cache -r pyproject.toml

# Final runtime image
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

# Security: create non-root user and group
RUN groupadd -g 10001 nabu && \
    useradd -u 10001 -g nabu -s /bin/sh -M -d /app nabu && \
    mkdir -p /app/data && \
    chown -R nabu:nabu /app

# Copy virtual environment and source code
COPY --from=builder --chown=nabu:nabu /app/.venv /app/.venv
COPY --chown=nabu:nabu src/ /app/src/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH="/app/data/books.db"

USER nabu:nabu

VOLUME ["/app/data"]

CMD ["python", "src/bot.py"]
