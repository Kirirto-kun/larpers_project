FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.8
COPY server/pyproject.toml server/uv.lock /app/server/
RUN uv sync --project /app/server --locked --no-dev
RUN groupadd --gid 10001 campus && useradd --uid 10001 --gid campus --no-create-home campus \
    && install -d -m 0700 -o campus -g campus /state
COPY server/campus /app/server/campus
COPY server/alembic.ini /app/server/alembic.ini
COPY server/migrations /app/server/migrations
ENV PATH="/app/server/.venv/bin:$PATH" PYTHONPATH="/app/server" PYTHONUNBUFFERED="1"
USER campus
CMD ["uvicorn", "campus.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
