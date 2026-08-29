# One deployable (ADR-001/ADR-015) — this image is used for BOTH the web
# service and the outbox worker (docker-compose.yml's `app` and `worker`
# services), same as render.yaml's two Render services already share one
# codebase. Different process, not a second deployable.

# --- frontend build stage ------------------------------------------------
FROM node:22-slim AS frontend-build
WORKDIR /build/frontend

# Manifests first, for Docker layer caching — `npm ci` at the workspace
# root needs every member's package.json to resolve against the lockfile,
# so all three have to be in place before it runs, not just the root one.
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/apps/dms/package.json apps/dms/package.json
COPY frontend/packages/ui-kit/package.json packages/ui-kit/package.json
RUN npm ci

COPY frontend/ .
RUN VITE_API_BASE_URL=/v1 npm run build

# --- python runtime stage -------------------------------------------------
FROM python:3.13-slim AS runtime
WORKDIR /app

# System deps for psycopg[binary]/cryptography wheels, plus WeasyPrint
# (WP-6b's PDF backend): its own pip wheel is pure Python, but at IMPORT
# time it dlopen()s these exact shared libraries (app/platform/services/
# document_render.py is the only file that imports it — verified against
# .venv/lib/*/site-packages/weasyprint/text/ffi.py's own _dlopen() calls,
# not assumed from general WeasyPrint docs). libharfbuzz0b is the Debian
# bookworm package name (this base image); a future bump to a Debian
# release that renames it to libharfbuzz0 needs this line updated too.
# fonts-dejavu-core supplies the actual glyphs — DE/FR/IT/EN are all
# Latin-script-with-diacritics, which that one family covers completely.
# --no-install-recommends keeps the image from also pulling in everything
# Debian marks as merely "suggested".
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libfontconfig1 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app/ app/
RUN pip install --no-cache-dir -e .

COPY alembic.ini ./
COPY alembic/ alembic/
COPY scripts/ scripts/

# Built SPA, same path app/main.py already looks for — see its own comment
# on why this has to be frontend/apps/dms/dist specifically.
COPY --from=frontend-build /build/frontend/apps/dms/dist frontend/apps/dms/dist

EXPOSE 8000

# Same two commands render.yaml's web service runs, so `docker compose up`
# and the real deploy target behave identically rather than diverging into
# two things that happen to both be called "the app". The worker service
# in docker-compose.yml overrides this with its own command.
CMD ["sh", "-c", "alembic upgrade heads && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
