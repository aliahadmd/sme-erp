"""Application entrypoint. The isolated financial proof has a separate Worker."""

from urllib.parse import urlsplit

from workers import WorkerEntrypoint, asgi

from erp_cf.ai_worker import AIBudget as AIBudget
from erp_cf.app import create_app
from erp_cf.background import consume, dispatch_best_effort, tick
from erp_cf.backup_schedule import run as run_backups
from erp_cf.backup_worker import ERPBackups as ERPBackups
from erp_cf.command_worker import ERPCommands as ERPCommands
from erp_cf.limits_worker import LoginGuard as LoginGuard
from erp_cf.operations import emit_health

app = create_app()


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if (request.headers.get("upgrade") or "").lower() == "websocket":
            return await asgi.websocket(app, request, self.env)
        response = await asgi.fetch(app, request, self.env, self.ctx)
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and 200 <= response.status < 400
            and urlsplit(str(request.url)).path.startswith("/api/")
        ):
            self.ctx.waitUntil(dispatch_best_effort(self.env))
        return response

    async def queue(self, batch, env=None, ctx=None):
        # This runtime passes env/ctx despite the two-argument docs example.
        await consume(self.env, batch)

    async def scheduled(self, controller, env, ctx):
        # Python's scheduled entrypoint requires all four arguments.
        scheduled = int(controller.scheduledTime)
        try:
            if not await run_backups(self.env, scheduled):
                await tick(self.env, scheduled)
        finally:
            await emit_health(self.env)
