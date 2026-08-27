.PHONY: up down logs

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
