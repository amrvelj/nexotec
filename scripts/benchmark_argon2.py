"""Measures the argon2id parameters in settings on this machine (KAN-196,
D-A-12): how long one verify takes with DMS_ARGON2_TIME_COST,
DMS_ARGON2_MEMORY_KIB and DMS_ARGON2_PARALLELISM. The target is about
250 ms a verify on the instance that serves logins; a login does exactly
one. Read-only, no database. The Render instance is re-tuned with this
script in a follow-up ticket (Anto, 2026-10-17).

    python scripts/benchmark_argon2.py [--rounds 10]
"""

import argparse
import statistics
import time

from argon2 import PasswordHasher

from app.core.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    settings = get_settings()
    hasher = PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_kib,
        parallelism=settings.argon2_parallelism,
        hash_len=32,
        salt_len=16,
    )
    digest = hasher.hash("benchmark-only-not-a-real-password")
    samples = []
    for _ in range(args.rounds):
        started = time.perf_counter()
        hasher.verify(digest, "benchmark-only-not-a-real-password")
        samples.append((time.perf_counter() - started) * 1000)
    print(
        f"argon2id t={settings.argon2_time_cost} m={settings.argon2_memory_kib} KiB "
        f"p={settings.argon2_parallelism}: verify median {statistics.median(samples):.0f} ms, "
        f"min {min(samples):.0f} ms, max {max(samples):.0f} ms over {args.rounds} rounds"
    )


if __name__ == "__main__":
    main()
