"""Organization command serialization across external D1 awaits.

The mutex only coordinates this object's live requests. Durable idempotency
and SQL guards remain authoritative through restarts or lost RPC responses.
No network email, AI or R2 work belongs inside a financial command lock.
"""

import asyncio

from workers import DurableObject


class OrganizationCommands(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self._commands = asyncio.Lock()

    async def post_command(self, payload):
        from fastapi import HTTPException

        from erp_cf.proof import PostInput, execute_post

        if self.env.ENVIRONMENT != "feasibility":
            return {"status": 404, "body": {"detail": "Proof commands are disabled"}}
        body = PostInput.model_validate(payload)
        async with self._commands:
            try:
                return {"status": 200, "body": await execute_post(body, self.env)}
            except HTTPException as exc:
                return {"status": exc.status_code, "body": {"detail": exc.detail}}
