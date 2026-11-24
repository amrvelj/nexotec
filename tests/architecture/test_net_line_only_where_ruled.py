"""Architecture test (KAN-804; ADR-212, which limits ADR-057 to vehicle
sales): only the contexts named here may present a net line.

ADR-057's presentation rule — one price, gross CHF incl. MwSt, no net line
and no VAT breakdown on screen — holds for vehicle sales: offers, contracts
and stock prices. Anto ruled on 2026-11-12 (Aftersales PRD, ruling 4) that a
workshop order shows net and gross per position and one VAT line per payer;
a parts document will do the same. So two presentation rules live in one
product, and this test keeps the second from drifting into the first: a
net-amount field may be declared in `aftersales` and `parts`, and nowhere
else.

What counts as a net line: an identifier, declared or assigned (a colon or
`=` right after it, as in `test_no_vat_treatment_field.py`), that has the
word `net` or `netto` in it — `net_price`, `net_unit_price`, `netUnitPrice`,
`subtotal_net`, `net_chf`, `"netAmount":` — or says «excluding VAT»
(`price_excl_vat`, `exkl_mwst`). Words are split on `_` and on camelCase.
Two shapes are not amounts and do not count: a flag led by `is`
(`price_is_net`, `isNet` — the provider catalogue's flag that a list price
*arrived* net, vehicle and integration), and a payment term `NET_<days>`
(contact's `NET_30`).

Where it looks: every `.py` file under `app/<context>/`, and every `.ts` /
`.tsx` file under the SPA's `src/modules/<module>/`, the module mapped to
its context (`stock` is inventory). The SPA's shared code (`src/` outside
`modules/`) and the ui-kit are no context: a net-amount field there fails
too, until a ticket names that shared file in `_SHARED_ALLOWED` with why.
Two SPA files are not scanned, because their content is decided elsewhere:
the generated API types (`src/api/schema.d.ts`, made from the backend's
schemas, which the backend scan already judges by their owning context) and
the test fake backend (`src/test/`, which mirrors those schemas).
`app/core` is not scanned: the money calculation (KAN-807, ADR-215) is
shared arithmetic that every payer total runs through, gross-only Sales
included; presenting its net figure is a context's decision, and that is
what this test rules on.

Not seen (stated, not hidden): a net amount under a name without the word
(`base`, `subtotal`, `ex_vat`), a translation key in the locale JSON, a
figure built in a template, and `app/api` (the composition root's routers,
which mount contexts' schemas and declare none of their own). `test_no_vat_treatment_field.py` stays as it is: there
is no `vatTreatment` in any context, these two included.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"
_SPA_SRC = _REPO_ROOT / "frontend" / "apps" / "dms" / "src"
_KIT_SRC = _REPO_ROOT / "frontend" / "packages" / "ui-kit" / "src"

# ADR-212: the contexts that may present a net line. A new one is an ADR.
NET_LINE_CONTEXTS = frozenset({"aftersales", "parts"})

# Shared frontend files that may carry a net amount, each with its ticket.
_SHARED_ALLOWED: dict[str, str] = {}

# Not scanned: generated from the backend, or a test double of it.
_NOT_SCANNED = (_SPA_SRC / "api" / "schema.d.ts", _SPA_SRC / "test")

# The SPA's module folders and the context each belongs to.
_MODULE_CONTEXT = {"stock": "inventory"}

_NET_WORDS = frozenset({"net", "netto"})
_EXCLUDING = frozenset({"excl", "exkl", "exklusive", "excluding"})
_TAX_WORDS = frozenset({"vat", "mwst", "tva", "iva"})

# An identifier (optionally quoted, for a JSON-shaped key) followed by `:` or `=`.
_DECLARATION = re.compile(r"[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?\s*[:=](?!=)")
_CAMEL_SPLIT = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def _words(identifier: str) -> list[str]:
    return [word.lower() for part in identifier.split("_") for word in _CAMEL_SPLIT.findall(part)]


def is_net_amount_name(identifier: str) -> bool:
    words = _words(identifier)
    for i, word in enumerate(words):
        before = words[i - 1] if i else ""
        after = words[i + 1] if i + 1 < len(words) else ""
        if word in _NET_WORDS and before != "is" and not after.isdigit():
            return True
        if word in _EXCLUDING and after in _TAX_WORDS:
            return True
    return False


def _net_declarations(path: Path, root: Path = _REPO_ROOT) -> list[str]:
    found = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip().startswith(("#", "*", "//")):
            continue
        for match in _DECLARATION.finditer(line):
            if is_net_amount_name(match.group(1)):
                found.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")
    return found


def _files(root: Path, suffixes: set[str]) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix in suffixes
        and "__pycache__" not in path.parts
        and "node_modules" not in path.parts
    ]


def _owner(path: Path) -> str:
    """The context a scanned file belongs to, or `shared`."""

    if path.is_relative_to(_APP_ROOT):
        return path.relative_to(_APP_ROOT).parts[0]
    if path.is_relative_to(_SPA_SRC / "modules"):
        module = path.relative_to(_SPA_SRC / "modules").parts[0]
        return _MODULE_CONTEXT.get(module, module)
    return "shared"


def _scanned() -> list[Path]:
    backend = [
        path
        for context_dir in sorted(_APP_ROOT.iterdir())
        if context_dir.is_dir() and context_dir.name not in {"core", "api", "__pycache__"}
        for path in _files(context_dir, {".py"})
    ]
    frontend = [
        path
        for path in _files(_SPA_SRC, {".ts", ".tsx"}) + _files(_KIT_SRC, {".ts", ".tsx"})
        if not any(path == skip or path.is_relative_to(skip) for skip in _NOT_SCANNED)
    ]
    return backend + frontend


def test_a_net_amount_is_declared_only_where_adr_212_allows_one():
    violations = []
    for path in _scanned():
        owner = _owner(path)
        if owner in NET_LINE_CONTEXTS:
            continue
        if owner == "shared" and str(path.relative_to(_REPO_ROOT)) in _SHARED_ALLOWED:
            continue
        violations.extend(f"[{owner}] {hit}" for hit in _net_declarations(path))

    assert not violations, (
        "A net amount is declared outside the contexts that may present a net line "
        f"({sorted(NET_LINE_CONTEXTS)}; ADR-212, which limits ADR-057's gross-only rule to vehicle "
        "sales). Vehicle sales show one gross price. A new context or shared file on the list is an "
        "ADR:\n" + "\n".join(violations)
    )


def test_the_allowed_contexts_exist():
    missing = sorted(context for context in NET_LINE_CONTEXTS if not (_APP_ROOT / context).is_dir())
    assert not missing, f"ADR-212 names contexts that are not under app/: {missing}"


def test_the_name_rule_on_the_shapes_it_must_judge():
    for net in (
        "net_price", "unitNetPrice", "total_net", "netAmount", "NET_TOTAL", "line_net", "nettoPrice", "priceNet",
        "net_unit_price", "netUnitPrice", "net_sale_price", "net_list_price", "net_purchase_price", "net_chf",
        "net_fee", "subtotal_net", "nettoBetrag", "net", "price_excl_vat", "amountExklMwst",
    ):
        assert is_net_amount_name(net), net
    for other in (
        "price_is_net", "isNet", "is_net", "NET_10", "net_30", "network", "gross_price", "subnet_total",
        "internet_value", "netloc", "vat_amount", "excl_reason",
    ):
        assert not is_net_amount_name(other), other


def test_the_scan_sees_a_declaration_and_skips_prose(tmp_path):
    source = tmp_path / "x.py"
    source.write_text(
        "net_price: Decimal\n"
        "    unitNetPrice = 3\n"
        "# net_price: prose in a comment\n"
        "if net_price == other:\n"
        'payload = {"netAmount": 1}\n',
        encoding="utf-8",
    )
    hits = _net_declarations(source, root=tmp_path)
    assert [hit.split(": ", 1)[1] for hit in hits] == ["net_price: Decimal", "unitNetPrice = 3", 'payload = {"netAmount": 1}']


def test_the_owner_of_a_file():
    assert _owner(_APP_ROOT / "sales" / "schemas" / "offer.py") == "sales"
    assert _owner(_APP_ROOT / "aftersales" / "public.py") == "aftersales"
    assert _owner(_SPA_SRC / "modules" / "stock" / "pages" / "x.tsx") == "inventory"
    assert _owner(_SPA_SRC / "components" / "x.tsx") == "shared"
    assert _owner(_KIT_SRC / "x.tsx") == "shared"


def test_generated_api_types_and_the_test_backend_are_not_scanned():
    scanned = set(_scanned())
    assert _SPA_SRC / "api" / "schema.d.ts" not in scanned
    assert not any(path.is_relative_to(_SPA_SRC / "test") for path in scanned)
    assert any(path.is_relative_to(_SPA_SRC / "modules" / "sales") for path in scanned)
