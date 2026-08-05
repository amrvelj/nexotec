"""UUIDv7 generation (RFC 9562).

Python's stdlib `uuid` module doesn't gain `uuid.uuid7()` until 3.14; this
project targets 3.13, so it's implemented directly rather than adding a
dependency for ~40 lines of well-specified logic.

Layout: 48-bit big-endian Unix ms timestamp | 4-bit version (0111) |
12-bit sequence/random | 2-bit variant (10) | 62-bit random.

Monotonicity: raw per-call randomness in the 12-bit field doesn't guarantee
ordering for two IDs minted in the same millisecond (RFC 9562 sec. 6.2,
method 1) — which matters here since every PK is a UUIDv7 specifically for
time-ordered index locality. This uses method 2 (monotonic counter): calls
within the same millisecond increment the 12-bit field instead of
re-randomizing it, so `uuid7()` output is strictly increasing per process
even under rapid, same-millisecond calls.
"""

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = -1
_last_seq = 0
_SEQ_MASK = 0x0FFF  # 12 bits


def uuid7() -> uuid.UUID:
    global _last_ms, _last_seq

    with _lock:
        unix_ms = time.time_ns() // 1_000_000
        if unix_ms <= _last_ms:
            unix_ms = _last_ms
            _last_seq = (_last_seq + 1) & _SEQ_MASK
            if _last_seq == 0:
                # Exhausted this millisecond's sequence space; borrow the next one
                # rather than wrapping and losing monotonicity.
                unix_ms += 1
                _last_ms = unix_ms
        else:
            _last_seq = int.from_bytes(os.urandom(2), "big") & _SEQ_MASK
            _last_ms = unix_ms
        ms, seq = unix_ms, _last_seq

    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF  # 62 random bits

    version_and_seq = (0x7 << 12) | seq  # version 0111
    variant_and_rand_b = (0b10 << 62) | rand_b  # variant 10

    value = ms << 80 | version_and_seq << 64 | variant_and_rand_b
    return uuid.UUID(int=value)


def uuid7_str() -> str:
    return str(uuid7())
