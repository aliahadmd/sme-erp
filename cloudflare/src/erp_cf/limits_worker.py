from workers import DurableObject

from erp_cf.login_budget import LoginBudget


class LoginGuard(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.budget = LoginBudget(self.ctx.storage)

    async def reserve(self):
        return await self.budget.reserve()

    async def finish(self, permit: str, success: bool):
        await self.budget.finish(permit, success)
