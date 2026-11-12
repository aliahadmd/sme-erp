"""Exercise the isolated Python/D1 proof over HTTP without printing credentials.

Run with: uv run python scripts/verify_remote.py https://<proof-worker-url>
This intentionally writes synthetic records to the feasibility database only.
"""

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


async def verify(url: str, *, recovery: bool = False) -> None:
    secret = json.loads((ROOT / ".secrets.staging.json").read_text())["PROOF_TOKEN"]
    async with httpx.AsyncClient(
        base_url=url.rstrip("/"),
        headers={"Authorization": f"Bearer {secret}"},
        timeout=90,
    ) as client:
        health = await client.get("/healthz")
        health.raise_for_status()
        assert health.json() == {
            "status": "ok",
            "runtime": "python-workers-fastapi",
            "decimal_tie": "1.00",
        }, health.text
        unauthorized = await client.post(
            "/proof/precision", json={"value": "1.00", "scale": 2}, headers={"Authorization": ""}
        )
        assert unauthorized.status_code == 401, unauthorized.text
        print("PASS FastAPI/Pydantic/Decimal/D1 health and proof authorization")

        async def precision(scale: int, negative: bool, offset: int) -> None:
            encoded = (-1 if negative else 1) * (999999999999999999 - offset)
            value = format(Decimal(encoded).scaleb(-scale), "f")
            response = await client.post("/proof/precision", json={"value": value, "scale": scale})
            response.raise_for_status()
            result = response.json()
            assert result["coefficient"] == str(encoded), result
            assert result["orm_written_coefficient"] == str(encoded), result
            assert Decimal(result["native"]) == Decimal(value), result
            assert Decimal(result["orm"]) == Decimal(value), result
            assert result["orm_decimal"] and result["matches"], result

        # Distinct concurrent values expose accidental shared cursor/session state.
        await asyncio.gather(
            *(
                precision(scale, negative, offset)
                for scale in [0, 1, 2, 4, 6, 8]
                for negative in [False, True]
                for offset in range(2)
            )
        )
        print("PASS 24 concurrent native/ORM exact 18-digit round trips across six scales")

        run = uuid4().hex

        async def seed(label: str, quantity: int = 5) -> str:
            stock_id = f"{run}-{label}"
            response = await client.post(
                "/proof/stock", json={"id": stock_id, "quantity": quantity}
            )
            response.raise_for_status()
            return stock_id

        async def state(stock_id: str) -> dict:
            response = await client.get(f"/proof/state/{stock_id}")
            response.raise_for_status()
            return response.json()

        async def post(
            stock_id: str, key: str, *, direct: bool = False, **options
        ) -> httpx.Response:
            return await client.post(
                "/proof/post-direct" if direct else "/proof/post",
                json={
                    "key": f"{run}-{key}",
                    "stock_id": stock_id,
                    "quantity": 4,
                    "total": "9999999999999999.99",
                    **options,
                },
            )

        for position in range(9):
            stock_id = await seed(f"failure-{position}")
            before = await state(stock_id)
            failed = await post(stock_id, f"failure-{position}", fail_after=position)
            assert failed.status_code == 409, (position, failed.status_code, failed.text)
            assert await state(stock_id) == before, f"Partial write at statement {position}"
        print("PASS rollback at every command statement, including zero-row-style guard rejection")

        stock_id = await seed("race")
        before = await state(stock_id)
        responses = await asyncio.gather(
            post(stock_id, "race-a", direct=True), post(stock_id, "race-b", direct=True)
        )
        assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
        after = await state(stock_id)
        assert after["stock"]["quantity"] == 1 and after["stock"]["revision"] == 1, after
        expected_delta = {
            "cf_commands": 1,
            "proof_invoices": 1,
            "proof_journal": 2,
            "proof_audit": 1,
            "cf_outbox": 1,
        }
        assert {
            k: after["counts"][k] - before["counts"][k] for k in expected_delta
        } == expected_delta
        print("PASS competing deliveries commit one complete financial outcome")

        stock_id = await seed("duplicate")
        before = await state(stock_id)
        duplicates = await asyncio.gather(*(post(stock_id, "duplicate") for _ in range(6)))
        assert all(r.status_code == 200 for r in duplicates), [r.text for r in duplicates]
        assert all(r.json() == duplicates[0].json() for r in duplicates), [
            r.text for r in duplicates
        ]
        after = await state(stock_id)
        assert after["stock"]["quantity"] == 1 and after["stock"]["revision"] == 1, after
        assert {
            k: after["counts"][k] - before["counts"][k] for k in expected_delta
        } == expected_delta
        # Discard the original HTTP result and reconstruct it using the stable key.
        replay = await post(stock_id, "duplicate")
        assert replay.status_code == 200 and replay.json() == duplicates[0].json(), replay.text
        conflict = await post(stock_id, "duplicate", total="2.00")
        assert conflict.status_code == 409, conflict.text
        assert await state(stock_id) == after
        print("PASS simultaneous duplicate delivery, response replay, and changed-input rejection")

        stock_id = await seed("coordinator", quantity=20)
        before = await state(stock_id)
        coordinated = await asyncio.gather(
            *(post(stock_id, f"coordinator-{i}", quantity=1, total="1.00") for i in range(15))
        )
        assert all(r.status_code == 200 for r in coordinated), [r.text for r in coordinated]
        after = await state(stock_id)
        assert after["stock"]["quantity"] == 5 and after["stock"]["revision"] == 15, after
        assert {k: after["counts"][k] - before["counts"][k] for k in expected_delta} == {
            k: v * 15 for k, v in expected_delta.items()
        }
        milliseconds = sorted(r.elapsed.total_seconds() * 1000 for r in coordinated)
        print(
            "PASS 15 concurrent commands serialized across D1 awaits; "
            f"HTTP p50={milliseconds[7]:.0f}ms max={milliseconds[-1]:.0f}ms"
        )

        if recovery:
            restored = await client.post("/proof/backup-restore")
            restored.raise_for_status()
            result = restored.json()
            assert result["exact_match"] is True, result
            print(
                f"PASS exact D1 -> R2 -> fresh D1 recovery: {result['rows']} rows, "
                f"{result['tables']} tables, sha256={result['sha256']}"
            )


if __name__ == "__main__":
    if len(sys.argv) not in {2, 3} or not sys.argv[1].startswith("https://"):
        raise SystemExit(
            "Usage: uv run python scripts/verify_remote.py https://<proof-worker-url> [--recovery]"
        )
    if len(sys.argv) == 3 and sys.argv[2] != "--recovery":
        raise SystemExit("Only --recovery is supported; it requires an empty isolated restore D1")
    asyncio.run(verify(sys.argv[1], recovery="--recovery" in sys.argv))
