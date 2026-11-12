"""Persistent per-(IP,email) failure budget, including concurrent hash attempts."""

import asyncio
import secrets
import time


class LoginBudget:
    def __init__(self, storage, clock=time.time):
        self.storage = storage
        self.clock = clock
        self.lock = asyncio.Lock()

    async def state(self):
        timestamp = int(self.clock())
        state = await self.storage.get("budget")
        if state is None or state["until"] <= timestamp:
            state = {"failures": 0, "until": timestamp + 300, "active": {}}
        state["active"] = {p: expiry for p, expiry in state["active"].items() if expiry > timestamp}
        return state

    async def reserve(self):
        async with self.lock:
            state = await self.state()
            if state["failures"] + len(state["active"]) >= 10:
                return None
            permit = secrets.token_hex(16)
            state["active"][permit] = int(self.clock()) + 60
            await self.storage.put("budget", state)
            return permit

    async def finish(self, permit: str, success: bool):
        async with self.lock:
            state = await self.state()
            if permit not in state["active"]:
                return
            del state["active"][permit]
            state["failures"] = 0 if success else state["failures"] + 1
            state["until"] = int(self.clock()) + 300
            await self.storage.put("budget", state)
