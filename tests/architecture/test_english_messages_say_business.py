"""KAN-771 (Anto, KAN-760 option A): English messages say «business», like
the rest of the platform — the UI word for the tenant is Betrieb · entreprise
· azienda · business (D-A-10), and an English refusal, validation message,
connection reason, log line or export description that says «Betrieb» shows
an operator or API client two words for one thing.

Two tests:

- the guard: no string literal in `app/` that a person reads at run time says
  «Betrieb». Out of scope, by the ticket: docstrings and comments (prose for
  developers), OpenAPI and schema documentation (a `description=` /
  `summary=` / `title=` / `comment=` keyword of a FastAPI parameter, a
  route, a pydantic `Field` or a column — `_DOCUMENTING_CALLS`; the same
  keyword on any other call, such as a stored notification's `summary=`, is
  a message and is checked), and the German texts, which are
  right to say it (`_GERMAN_TEXT_FILES`). The UI translates by
  `details.rule` / `details.reason`, which this change never touched;
- the pin: each message KAN-771 changed still reads exactly so in its
  module (for an f-string, its literal parts).
"""

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"

# Keyword arguments that hold API or schema documentation, not a message —
# but only on the calls that document (a FastAPI parameter or route, a
# pydantic field, a column). The same keyword elsewhere is runtime text.
_DOC_KEYWORDS = frozenset({"description", "summary", "title", "comment", "doc"})
_DOCUMENTING_CALLS = frozenset(
    {
        # FastAPI parameters and routes
        "Query",
        "Path",
        "Body",
        "Header",
        "Cookie",
        "Form",
        "File",
        "Depends",
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "api_route",
        "APIRouter",
        # pydantic and SQLAlchemy
        "Field",
        "mapped_column",
        "Column",
    }
)


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


# Files whose texts are German on purpose (the mail a user receives in DE).
_GERMAN_TEXT_FILES = frozenset({"app/platform/identity/services/mail_texts.py"})


def _runtime_literals(path: Path) -> list[ast.Constant]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    documentation: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                documentation.add(id(first.value))
        if isinstance(node, ast.Call) and _call_name(node) in _DOCUMENTING_CALLS:
            for keyword in node.keywords:
                if keyword.arg in _DOC_KEYWORDS:
                    documentation.update(id(inner) for inner in ast.walk(keyword.value))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in documentation
    ]


def test_no_english_runtime_message_in_app_says_betrieb():
    offenders = []
    for path in sorted(_APP.rglob("*.py")):
        relative = path.relative_to(_REPO_ROOT).as_posix()
        if relative in _GERMAN_TEXT_FILES:
            continue
        offenders.extend(
            f"{relative}:{node.lineno}: {node.value[:80]!r}"
            for node in _runtime_literals(path)
            if "Betrieb" in node.value
        )
    assert not offenders, (
        "English messages say «business» (KAN-760 option A, KAN-771); a German text belongs in "
        "_GERMAN_TEXT_FILES, API documentation in a documenting call's description=:\n" + "\n".join(offenders)
    )


# Every message KAN-771 rewrote, exactly as it now reads (f-strings: the
# literal part that names the tenant).
_CHANGED_MESSAGES = [
    ("app/core/export_core.py", "Consumer bookkeeping, no business's data."),
    ("app/core/export_core.py", "Nexotec's job scheduler, no business's data."),
    ("app/core/export_core.py", "Nexotec's nightly integrity check, no business's data."),
    ("app/customer/services/contact_groups.py", "Contact group not switched on for this business: "),
    ("app/customer/services/contact_groups.py", "Contact group cannot be removed from this business: "),
    (
        "app/integration/adapters/auto_i_dat_ident.py",
        (
            "This connection belongs to no business, so there is no user auto-i could enrol — "
            "the identAPI service account is configured on a business's connection."
        ),
    ),
    (
        "app/integration/adapters/auto_i_dat_ident.py",
        (
            "The signed-in user is not a member of this connection's business, so auto-i cannot enrol them for it — "
            "test the connection as a member of that business (platform staff included)."
        ),
    ),
    ("app/integration/api/captions.py", "Act in a business to refresh its provider's captions."),
    ("app/integration/consumers.py", " names two businesses."),
    ("app/integration/services/ai_generation.py", "No AI provider is connected for this business."),
    ("app/integration/services/ai_prompts.py", "The business has adapted "),
    ("app/integration/services/caption_refresh.py", "Captions are not in this business's provider contract."),
    ("app/integration/services/caption_refresh.py", "This business has no enabled vehicle-data provider connection."),
    (
        "app/integration/services/connections.py",
        " connection serves the whole business and is not bound to a location.",
    ),
    ("app/integration/services/connections.py", "The location is not one of this business's."),
    ("app/integration/services/data_export.py", "Nexotec's provider catalogue, the same for every business."),
    (
        "app/integration/services/mail_link.py",
        "This mailbox's organisation is already connected to a business of another dealer group.",
    ),
    (
        "app/integration/services/mail_sending.py",
        "No mailbox is connected: connect your own in My account, or a manager connects the business's.",
    ),
    (
        "app/integration/services/mail_sending.py",
        "The business's shared mailbox cannot send right now; a manager reconnects it.",
    ),
    ("app/inventory/consumers.py", " names two businesses."),
    ("app/inventory/reconciliation.py", "two live stock items of one business name one vehicle_mdm record"),
    ("app/inventory/services/data_export.py", "A one-off migration's bookkeeping, no business's data."),
    ("app/inventory/services/listing_withdrawal.py", "Business "),
    ("app/inventory/services/location_labels.py", "The location is not one of this business's."),
    (
        "app/inventory/services/vehicle_merge.py",
        "stock item %s stays on merged record %s: its business holds the survivor (KAN-524)",
    ),
    (
        "app/platform/administration/api/data_exports.py",
        "The business's own export is its manager's — staff produce an archive export on the platform path.",
    ),
    ("app/platform/administration/api/setup.py", "Only a manager of this business does this."),
    ("app/platform/administration/services/activation.py", "Only a business that is being set up can be activated."),
    (
        "app/platform/administration/services/data_export.py",
        "This business is offboarded: its archive is exported by Nexotec support.",
    ),
    (
        "app/platform/administration/services/data_export.py",
        "An archive export is for an offboarded business; a live business's manager exports it.",
    ),
    ("app/platform/administration/services/data_export.py", "Only the business's own export is acknowledged."),
    ("app/platform/administration/services/data_export.py", "the export's business is gone"),
    (
        "app/platform/administration/services/data_export_tables.py",
        "Nexotec's platform reference data, the same for every business.",
    ),
    (
        "app/platform/administration/services/data_export_tables.py",
        "Nexotec's own Terms of Service and DPA, the same for every business.",
    ),
    (
        "app/platform/administration/services/data_export_tables.py",
        "Nexotec's own platform status, the same for every business.",
    ),
    ("app/platform/administration/services/offboarding.py", "This business is offboarded already."),
    (
        "app/platform/administration/services/offboarding.py",
        "The business's data changed after its acknowledged export — the manager exports and acknowledges again.",
    ),
    (
        "app/platform/administration/services/offboarding.py",
        "A user of this business is the last manager of another business — give that business another manager first.",
    ),
    (
        "app/platform/administration/services/setup_checklist_regressions.py",
        "platform.setup_checklist.regressions: business %s could not be checked",
    ),
    (
        "app/platform/administration/services/status_notifications.py",
        "platform.status_page.notifications: business %s could not be checked",
    ),
    ("app/platform/administration/services/status_page.py", "status page: source %s failed for business %s"),
    ("app/platform/consumers.py", " names two businesses."),
    ("app/platform/identity/services/onboarding.py", "A business with this UID is already onboarded."),
    ("app/platform/organisation/api/number_ranges.py", "Only a manager of this business does this."),
    ("app/platform/organisation/schemas/publishing_profile.py", "Platform staff never receive a business's enquiries."),
    (
        "app/platform/organisation/services/dealer_dealerships.py",
        "The Nexotec platform group holds no dealer business.",
    ),
    (
        "app/platform/organisation/services/dealership.py",
        "The status of a business is not changed here: activation and offboarding are procedures of their own.",
    ),
    ("app/platform/organisation/services/dealership.py", "A business is offboarded through the offboarding procedure."),
    ("app/platform/organisation/services/dealership.py", "A business is activated through the activation procedure."),
    ("app/valuation/services/provider_quote.py", "Valuation is not in this business's provider contract."),
    ("app/valuation/services/provider_quote.py", "No vehicle-data provider is connected for this business."),
    ("app/vehicle/reconciliation.py", "vehicle_plate kept in a group that is not its recording business's"),
    (
        "app/vehicle/services/data_export.py",
        "The provider's licensed catalogue data mirrored for the business, re-fetched on demand.",
    ),
    ("app/vehicle/services/data_export.py", "Nexotec's canonical vehicle catalogue, the same for every business."),
    (
        "app/vehicle/services/data_export.py",
        "A 30-day cache of registry lookups (KAN-42); no record of the business's.",
    ),
    ("app/vehicle/services/merge.py", "Your business holds both records in stock: sell or cancel one stock car first."),
    (
        "app/worker.py",
        "inventory.location_labels: %d stock item(s) relabelled, %d left alone (no location of the item's business)",
    ),
]


@pytest.mark.parametrize(("module", "message"), _CHANGED_MESSAGES)
def test_each_rewritten_message_reads_business(module: str, message: str):
    literals = {node.value for node in _runtime_literals(_REPO_ROOT / module)}
    assert message in literals, f"{module} no longer says {message!r} (KAN-771)"
