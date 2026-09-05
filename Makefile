.PHONY: up down logs generate-frontend-types

# WP-2 PR-3 (closes G-13): brings the whole stack up from cold on a clean
# machine, seeded, in one command. `.env` is generated once, on first run
# only — DMS_TAX_ID_ENCRYPTION_KEY and DMS_JWT_PRIVATE_KEY have no defaults
# by design (app/core/config.py), so docker-compose.yml's ${...}
# substitution needs them from somewhere; this is that somewhere, and it's
# git-ignored, dev-only, never the same secrets a real deployment uses.
up:
	@if [ ! -f .env ]; then \
		echo "No .env found — generating dev-only secrets (first run only)."; \
		python3 -c "from cryptography.fernet import Fernet; print('DMS_TAX_ID_ENCRYPTION_KEY=' + Fernet.generate_key().decode())" > .env; \
		python3 -c "\
from cryptography.hazmat.primitives import serialization; \
from cryptography.hazmat.primitives.asymmetric import rsa; \
key = rsa.generate_private_key(public_exponent=65537, key_size=2048); \
pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(); \
print('DMS_JWT_PRIVATE_KEY=' + pem.replace(chr(10), '\\\\n'))" >> .env; \
		python3 -c "import secrets; print('DMS_SESSION_SECRET_KEY=' + secrets.token_urlsafe(32))" >> .env; \
		echo "DMS_ZITADEL_ISSUER=" >> .env; \
		echo "DMS_ZITADEL_CLIENT_ID=" >> .env; \
		echo "DMS_ZITADEL_CLIENT_SECRET=" >> .env; \
		echo "DMS_ZITADEL_REDIRECT_URI=http://localhost:8000/v1/auth/oidc/callback" >> .env; \
		echo ""; \
		echo "Zitadel OIDC values (WP-4) can't be auto-generated — they need a real"; \
		echo "registered application. Fill in DMS_ZITADEL_ISSUER/_CLIENT_ID/_CLIENT_SECRET"; \
		echo "in .env before the login flow will work; everything else starts fine"; \
		echo "without them (login just 404s at Zitadel with a blank issuer)."; \
	fi
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
