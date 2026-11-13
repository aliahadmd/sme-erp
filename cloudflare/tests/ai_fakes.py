"""SQL-backed quota storage and a deterministic model double, with no network."""

import json
import sqlite3

from erp_cf.ai_budget import DailyAIBudget


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def toArray(self):
        return self.rows


class SQL:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.connection.row_factory = sqlite3.Row

    def exec(self, sql, *params):
        return Cursor([dict(row) for row in self.connection.execute(sql, params).fetchall()])


class Storage:
    def __init__(self):
        self.sql, self.alarm = SQL(), None

    async def setAlarm(self, timestamp):
        self.alarm = timestamp

    async def deleteAlarm(self):
        self.alarm = None


class Budgets:
    def __init__(self, env):
        self.env, self.storage, self.instances = env, {}, {}

    def getByName(self, name):
        if name not in self.instances:
            self.storage.setdefault(name, Storage())
            self.instances[name] = DailyAIBudget(self.storage[name], self.env)
        return self.instances[name]


class AI:
    def __init__(self):
        self.calls, self.before_result = [], None
        self.error, self.output = None, None

    async def run(self, model, body):
        self.calls.append((model, body))
        if self.before_result:
            await self.before_result()
        if self.error:
            raise self.error
        if self.output is not None:
            return self.output
        prompt = body["messages"][1]["content"]
        text = "The supplied report shows the stated totals."
        if prompt.startswith("Product facts"):
            text = "Draft description for " + json.loads(prompt.split(": ", 1)[1])["name"]
        return {"response": text}


def enable(env, *, limit=200):
    env.AI_ENABLED, env.AI_DAILY_REQUEST_LIMIT = "true", str(limit)
    env.AI, env.AI_BUDGETS = AI(), Budgets(env)
    return env.AI
