from workers import DurableObject

from erp_cf.command_bus import Coordinator


class ERPCommands(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.coordinator = Coordinator(self.env)

    async def execute(self, envelope):
        return await self.coordinator.execute(envelope)
