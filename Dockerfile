FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=30 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && useradd --create-home --uid 10001 bot \
    && mkdir -p /app/data \
    && chown bot:bot /app/data

ENV PATH="/app/.venv/bin:$PATH" HOST=0.0.0.0
USER bot
EXPOSE 8080
CMD ["aling"]
