"""A VIN no other test row holds (KAN-446).

A pipeline stock item is bought with its VIN since KAN-446 (ADR-103): the
purchase promotes it into stock, and a VIN is in a dealership's stock at most
once (KAN-111). A fixture that buys a pipeline item passes `unique_vin()` so
each item it books is its own car."""

import uuid


def unique_vin() -> str:
    """17 characters, letters and digits, never I, O or Q — a VIN as
    `NormalisedVin` accepts it, unique per call."""

    return f"WVWZZZ1KZ{uuid.uuid4().int % 10**8:08d}"
