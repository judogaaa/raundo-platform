FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CHECKOUT_DIST=/app/admin-web/dist \
    LEGAL_DIST=/app/legal

WORKDIR /app

# Wheels cover the runtime deps; keep the image small and build reproducible.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && rm -rf /root/.cache

COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY private_media ./private_media
COPY admin-web/dist ./admin-web/dist
COPY legal ./legal

RUN useradd --create-home --uid 10001 raundo \
    && chown -R raundo:raundo /app
USER raundo

EXPOSE 8090
CMD ["python", "-m", "raundo.main"]
