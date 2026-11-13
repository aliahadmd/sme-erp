from workers import DurableObject

from erp_cf.ai_budget import DailyAIBudget


class AIBudget(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.budget = DailyAIBudget(self.ctx.storage, self.env)

    async def reserve(self):
        return await self.budget.reserve()

    async def status(self):
        return await self.budget.status()

    async def alarm(self, info=None):
        await self.budget.expire()
