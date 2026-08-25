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
	fi
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f
