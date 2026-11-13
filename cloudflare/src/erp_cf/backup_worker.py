from workers import DurableObject

from erp_cf.backup_rpc import respond
from erp_cf.backups import BackupController


class ERPBackups(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.controller = BackupController(self.env)

    async def advance(self):
        return await respond(self.controller.advance, steps=100)

    async def retention(self):
        return await respond(self.controller.retention)

    async def import_backup(self, token, identity, key):
        return await respond(self.controller.import_backup, token, identity, key)

    async def cancel(self, token, identity):
        return await respond(self.controller.cancel, token, identity)
