.PHONY: up env down logs generate-frontend-types

# WP-2 PR-3 (closes G-13): brings the whole stack up from cold on a clean
# machine, seeded, in one command. `.env` is generated once, on first run
# only — DMS_TAX_ID_ENCRYPTION_KEY and DMS_JWT_PRIVATE_KEY have no defaults
# by design (app/core/config.py), so docker-compose.yml's ${...}
# substitution needs them from somewhere; this is that somewhere, and it's
# git-ignored, dev-only, never the same secrets a real deployment uses.
# `make env` alone writes the dev-only .env without starting anything
# (scripts/dev/bootstrap uses it for local runs and the desktop preview).
env:
	@if [ ! -f .env ]; then \
		echo "No .env found — generating dev-only secrets (first run only)."; \
		python3 -c "from cryptography.fernet import Fernet; print('DMS_TAX_ID_ENCRYPTION_KEY=' + Fernet.generate_key().decode())" > .env; \
		python3 -c "\
from cryptography.hazmat.primitives import serialization; \
from cryptography.hazmat.primitives.asymmetric import rsa; \
key = rsa.generate_private_key(public_exponent=65537, key_size=2048); \
pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(); \
print('DMS_JWT_PRIVATE_KEY=' + pem.replace(chr(10), '\\\\n'))" >> .env; \
		echo "DMS_SEED_DEMO_PASSWORD=" >> .env; \
		echo ""; \
		echo "Set DMS_SEED_DEMO_PASSWORD (12+ characters) in .env to give the demo manager a"; \
		echo "local password (KAN-196); everything else starts fine without it."; \
	fi

up: env
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

# KAN-35 — regenerates frontend/apps/dms/src/api/schema.d.ts from the
# backend's own app.openapi() (import only, no server, no DB). Same two
# commands `npm run generate:api-types` (apps/dms/package.json) runs, so
# the two entry points can't drift from each other. Needs the same
# DMS_-prefixed Settings env `make up` writes to .env (Settings has no
# defaults for these, ADR-007) — sourced here if .env exists.
generate-frontend-types:
	@if [ -f .env ]; then set -a && . ./.env && set +a; fi; PYTHONPATH=. python3 scripts/generate_openapi_schema.py
	cd frontend/apps/dms && npx openapi-typescript ./openapi.json -o ./src/api/schema.d.ts --default-non-nullable false
