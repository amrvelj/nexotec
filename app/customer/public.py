"""The only surface other contexts may import from customer. Import-linter's
contract allows `app.<other-context>` to import `app.customer.public`, never
`app.customer.models` / `app.customer.services` / `app.customer.api` directly.
"""

from app.customer.services.customer import get_customer_or_404

__all__ = ["get_customer_or_404"]
