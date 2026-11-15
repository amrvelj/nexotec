"""AiGateway (KAN-227, D-A-33, FR-A-36) — the shape every AI provider
adapter implements: one chat completion, a system and a user message in, the
text and the tokens it consumed out.

Deliberately not `ProviderAdapter` (vehicle-data fetch verbs) and not
`MarketplaceAdapter` (outbound feed): a third Protocol for a third direction
of travel, behind the same `services/gateway.py::call_capability` — credential
resolution, circuit breaker and one `integration_call_log` row per request,
now with the provider's token counts on it (`metering.ProviderRequest.report_tokens`).

**Bring your own key** (D-A-33): the Betrieb connects its own account at its
own provider; Nexotec holds no AI key in v1. The key is the connection's
`api_key` secret slot, resolved in memory per request like every credential;
the model (or Azure's deployment) is the dealer's configuration, never a
default of ours.

**Licence (identAPI test agreement §3, `.claude/rules/vehicle-gateway.md`):**
provider data a caller puts into a prompt travels inside the tenant's own
request and response only. This port has no tool, function-calling or
retrieval surface — the model cannot ask for more data — and none may be
added (`tests/architecture/test_no_mcp_server.py` guards the MCP side).
"""

from dataclasses import dataclass
from typing import Protocol

AI_CATEGORY = "ai"
# The one call label an AI connection is used under; also the provider row's
# `capability_codes` entry.
AI_TEXT_GENERATION_CAPABILITY = "ai_text_generation"


@dataclass(frozen=True)
class AiCompletion:
    text: str
    # As the provider reported them; None when it reported none.
    tokens_in: int | None
    tokens_out: int | None


class AiGateway(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str, max_output_tokens: int) -> AiCompletion:
        """One completion. Raises the `app.integration.errors` vocabulary,
        never a client library's exception."""
        ...

    def verify(self) -> AiCompletion:
        """«Test connection»: the smallest real completion the provider bills
        (a few tokens) — it proves the key, the model and, on Azure, the
        endpoint and deployment together, which listing models would not."""
        ...
