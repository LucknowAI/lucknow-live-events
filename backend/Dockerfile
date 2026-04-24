FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# uv (Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock* /app/
RUN uv sync --frozen --no-dev || uv sync --no-dev
# install email-validator separately (required by pydantic[email] / EmailStr)
RUN /opt/venv/bin/python -m ensurepip --upgrade \
    && /opt/venv/bin/python -m pip install email-validator playwright-stealth -q

# Playwright runtime: install Chromium + OS deps for scraping.
RUN /opt/venv/bin/python -m playwright install --with-deps chromium

COPY . /app

ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8000

# Cloud Run injects $PORT at runtime; fall back to 8000 for local use.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
