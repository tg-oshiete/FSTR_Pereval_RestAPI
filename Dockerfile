ARG PIP_VERSION=26.2
FROM python:3.13.9-slim AS wheels
ARG PIP_VERSION

# psycopg2 stays at its existing version; compile it outside the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev libpq-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt .
RUN python -m pip install --no-cache-dir "pip==${PIP_VERSION}" \
    && python -m pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt "pip==${PIP_VERSION}"

FROM python:3.13.9-slim
ARG PIP_VERSION
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=wheels /wheels /wheels
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels "pip==${PIP_VERSION}" \
    && python -m pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels \
    && useradd --create-home appuser
COPY app/ ./app/
COPY alembic.ini .
COPY alembic/ ./alembic/
USER appuser
EXPOSE 8000
CMD ["python", "-m", "app.main"]
