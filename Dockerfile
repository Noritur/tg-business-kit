# Build stage: uv resolves the locked dependencies into /app/.venv.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Dependencies first and alone: this layer is rebuilt only when the lockfile
# changes, not on every code edit.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Runtime stage: the same Python, no uv, no build tools.
FROM python:3.12-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY kit ./kit
COPY presets ./presets
COPY migrations ./migrations

RUN useradd --create-home --uid 10001 app
# The commit for the Sentry release; compose passes it. Last, because it
# changes with every commit and would rebuild every layer after it.
ARG GIT_SHA=""
ENV GIT_SHA=$GIT_SHA
USER app

EXPOSE 8000
# Long polling by default; compose.yaml also defines a webhook service.
CMD ["python", "-m", "kit", "run", "--profile", "presets/dental-clinic.json"]
