"""R2/DO transport doubles; all database behavior uses actual SQLite constraints."""

import uuid
from types import SimpleNamespace

from erp_cf.backup_rpc import respond
from erp_cf.backups import BackupController
from tests.fakes import SQLiteD1


class Bucket:
    def __init__(self):
        self.objects = {}
        self.after_put = None
        self.list_limit = 1000

    async def put(self, key, value, options=None):
        self.objects[key] = value
        if self.after_put:
            await self.after_put(key, value)
        return SimpleNamespace(key=key)

    async def get(self, key):
        if key not in self.objects:
            return None
        text = self.objects[key]

        async def body():
            return text

        return SimpleNamespace(size=len(text.encode()), text=body)

    async def head(self, key):
        if key not in self.objects:
            return None
        return SimpleNamespace(key=key, size=len(self.objects[key].encode()))

    async def delete(self, keys):
        for key in [keys] if isinstance(keys, str) else keys:
            self.objects.pop(key, None)

    async def list(self, options):
        keys = sorted(key for key in self.objects if key.startswith(options.get("prefix", "")))
        if options.get("cursor"):
            keys = [key for key in keys if key > options["cursor"]]
        limit = min(options.get("limit", 1000), self.list_limit)
        return {
            "objects": [SimpleNamespace(key=key) for key in keys[:limit]],
            "truncated": len(keys) > limit,
            "cursor": keys[limit - 1] if len(keys) > limit else None,
        }


class Controls:
    def __init__(self, env):
        self.controller = BackupController(env)

    def getByName(self, name):
        return self

    async def advance(self):
        return await respond(self.controller.advance, steps=100)

    async def retention(self):
        return await respond(self.controller.retention)

    async def import_backup(self, token, identity, key):
        return await respond(self.controller.import_backup, token, identity, key)

    async def cancel(self, token, identity):
        return await respond(self.controller.cancel, token, identity)


def enable_backups(env):
    env.BACKUPS_ENABLED = "true"
    env.RESTORE_ENABLED = "true"
    env.RESTORE_DATABASE_ID = str(uuid.uuid4())
    env.BACKUPS = Bucket()
    env.RESTORE_DB = SQLiteD1(empty=True)
    env.BACKUP_CONTROL = Controls(env)
    return env.BACKUP_CONTROL.controller
