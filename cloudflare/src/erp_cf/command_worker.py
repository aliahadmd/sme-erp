import uuid

from workers import DurableObject

from erp_cf.command_bus import Coordinator


class ERPCommands(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.coordinator = Coordinator(self.env)
        self.instance_id = uuid.uuid4().hex

    async def execute(self, envelope):
        return await self.coordinator.execute(envelope)

    async def run_system(self, envelope):
        return await self.coordinator.run_system(envelope)

    async def inspect_instance(self, envelope):
        from erp_cf.restart_proof import administrator

        async with self.coordinator.lock:
            await administrator(self.env, envelope.get("token", ""))
            return {"instance_id": self.instance_id}

    async def restart_instance(self, envelope):
        from erp_cf.restart_proof import administrator

        async with self.coordinator.lock:
            await administrator(self.env, envelope.get("token", ""))
            self.ctx.abort("ERP isolated coordinator restart proof")
