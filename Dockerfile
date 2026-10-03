FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY app ./app
COPY static ./static
COPY templates ./templates
RUN uv sync --frozen --no-dev

ENV DATA_DIR=/data
ENV ENABLE_FACE_COMPARE=false
EXPOSE 8000

# * Elastic Beanstalk sets PORT. One process keeps the SQLite file on this instance.
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
