import asyncio

from erp_cf.login_budget import LoginBudget
from tests.test_auth import Storage


async def test_login_alarm_preserves_active_failure_budget_and_cleans_expired_storage():
    clock, storage = [100], Storage()
    budget = LoginBudget(storage, clock=lambda: clock[0])
    permits = await asyncio.gather(*(budget.reserve() for _ in range(10)))
    for permit in permits:
        await budget.finish(permit, False)
    assert storage.alarm == 400000
    restarted = LoginBudget(storage, clock=lambda: clock[0])
    await restarted.expire()
    assert storage.data["budget"]["failures"] == 10
    assert await restarted.reserve() is None
    clock[0] = 401
    await restarted.expire()
    assert not storage.data and storage.alarm is None
    await restarted.expire()  # Repeated alarm is harmless.
    assert await restarted.reserve()


async def test_window_rollover_and_old_alarm_do_not_drop_in_flight_kdf_permits():
    clock, storage = [100], Storage()
    budget = LoginBudget(storage, clock=lambda: clock[0])
    first = await budget.reserve()
    await budget.finish(first, True)
    clock[0] = 399
    permits = await asyncio.gather(*(budget.reserve() for _ in range(10)))
    clock[0] = 401
    await budget.expire()
    assert await budget.reserve() is None  # All ten hashes are still in flight.
    assert storage.alarm == 459000
    await budget.finish(permits[0], False)
    assert storage.data["budget"]["failures"] == 1
    clock[0] = 460
    await budget.expire()
    assert storage.data["budget"]["failures"] == 1 and storage.alarm == 701000
