import time
import uuid

from app.core.uuid7 import uuid7


def test_uuid7_sets_version_and_variant_bits():
    value = uuid7()
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_is_monotonically_time_ordered():
    values = [uuid7() for _ in range(5)]
    for a, b in zip(values, values[1:]):
        assert str(a) < str(b)


def test_uuid7_embeds_current_unix_ms_timestamp():
    before_ms = time.time_ns() // 1_000_000
    value = uuid7()
    after_ms = time.time_ns() // 1_000_000

    embedded_ms = value.int >> 80
    assert before_ms <= embedded_ms <= after_ms


def test_uuid7_values_are_unique():
    values = {uuid7() for _ in range(1000)}
    assert len(values) == 1000
