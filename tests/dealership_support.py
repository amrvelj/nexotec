"""Valid Swiss UIDs for tests (KAN-197).

`dealership.uid` is unique and checksum-validated, so a fixture that creates
more than one dealership needs distinct valid values, and the old fixture
value `CHE-123.456.789` is not a UID at all (its check digit is wrong). Every
dealership helper calls `valid_uid()` for a fresh one.
"""

import random

from app.core.validators import uid_check_digit

_rng = random.Random()

# A valid UID and one that fails only on its check digit, for the tests that
# want a fixed value to assert against.
VALID_UID = "CHE-123.456.788"
INVALID_CHECK_DIGIT_UID = "CHE-123.456.789"


def valid_uid(seed: int | None = None) -> str:
    """A random (or, with `seed`, reproducible) UID that passes the mod-11
    check digit — the same rule `validate_swiss_uid` applies."""

    rng = random.Random(seed) if seed is not None else _rng
    while True:
        first_eight = f"{rng.randrange(10_000_000, 100_000_000)}"
        check = uid_check_digit(first_eight)
        if check is not None:
            digits = f"{first_eight}{check}"
            return f"CHE-{digits[:3]}.{digits[3:6]}.{digits[6:]}"
