"""Check Argon2's real WASM/CPython boundary with a synthetic known vector.

Use Workers observability cpuTimeMs for CPU timing. The in-Worker clock does
not advance reliably during synchronous CPU work and is not a hash benchmark.
"""

import base64
import json
from pathlib import Path

import httpx
from argon2.low_level import Type, hash_secret_raw

ROOT = Path(__file__).resolve().parents[1]


def main():
    secret = json.loads((ROOT / ".secrets.staging.json").read_text())["PROOF_TOKEN"]
    response = httpx.post(
        "https://erp-feasibility.aliahad.workers.dev/proof/password-kdf",
        headers={"Authorization": "Bearer " + secret},
        timeout=120,
    )
    response.raise_for_status()
    result = response.json()
    expected = hash_secret_raw(
        "Synthetic Workers password Δ".encode(), bytes(range(16)), 2, 19456, 1, 32, Type.ID
    )
    assert result["vector"] == base64.urlsafe_b64encode(expected).decode().rstrip("=")
    assert result["correct"] is True and result["incorrect"] is False
    assert result["memory_kib"] == 19456 and result["time_cost"] == 2 and result["parallelism"] == 1
    print("PASS Argon2id WASM/native vector, correct-password and wrong-password verification")


if __name__ == "__main__":
    main()
