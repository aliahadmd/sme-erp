"""Persistent daily inference-attempt quota in a narrow SQLite-backed DO."""

import asyncio
from datetime import datetime, time, timedelta

from erp_cf.business_clock import business_time
from erp_cf.db import plain


class DailyAIBudget:
    def __init__(self, storage, env, clock=None):
        self.storage, self.env, self.clock = storage, env, clock
        self.lock = asyncio.Lock()
        self.storage.sql.exec(
            "CREATE TABLE IF NOT EXISTS ai_budget "
            "(day TEXT PRIMARY KEY NOT NULL,used INTEGER NOT NULL CHECK(used>=0)) STRICT"
        )

    def window(self):
        current = self.clock() if self.clock else business_time(self.env)
        tomorrow = datetime.combine(current.date() + timedelta(days=1), time.min, current.tzinfo)
        return current.date().isoformat(), int(tomorrow.timestamp() * 1000)

    async def reserve(self):
        limit = int(getattr(self.env, "AI_DAILY_REQUEST_LIMIT", "200"))
        if not 1 <= limit <= 100000:
            raise ValueError("Invalid AI daily request limit")
        async with self.lock:
            day, until = self.window()
            # Schedule before charging. SQL's single conditional upsert is the
            # quota commit; a lost RPC reply conservatively consumes the attempt.
            await self.storage.setAlarm(until)
            self.storage.sql.exec("DELETE FROM ai_budget WHERE day<?", day)
            result = self.storage.sql.exec(
                "INSERT INTO ai_budget(day,used) VALUES (?,1) ON CONFLICT(day) "
                "DO UPDATE SET used=used+1 WHERE used<? RETURNING used",
                day,
                limit,
            ).toArray()
            return {
                "allowed": bool(result),
                "reset_at": until,
                "used": plain(result[0])["used"] if result else limit,
            }

    async def expire(self):
        async with self.lock:
            day, until = self.window()
            self.storage.sql.exec("DELETE FROM ai_budget WHERE day<?", day)
            rows = self.storage.sql.exec("SELECT day FROM ai_budget LIMIT 1").toArray()
            if rows:
                # An early/duplicate alarm must not reset the current quota.
                await self.storage.setAlarm(until)
            else:
                await self.storage.deleteAlarm()

    async def status(self):
        async with self.lock:
            day, until = self.window()
            rows = self.storage.sql.exec("SELECT used FROM ai_budget WHERE day=?", day).toArray()
            return {
                "day": day,
                "used": plain(rows[0])["used"] if rows else 0,
                "limit": int(getattr(self.env, "AI_DAILY_REQUEST_LIMIT", "200")),
                "reset_at": until,
            }
