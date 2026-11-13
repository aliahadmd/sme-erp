"""Queue transport doubles; database behavior still runs against real SQLite."""

import uuid
from types import SimpleNamespace

from erp_cf.background import consume, dispatch


class Queue:
    def __init__(self):
        self.messages = []
        self.after_send = None

    async def sendBatch(self, messages):
        self.messages.extend(messages)
        if self.after_send:
            await self.after_send()


class Message:
    def __init__(self, body, *, attempts=1, identity=None):
        self.id = identity or uuid.uuid4().hex
        self.body, self.attempts = body, attempts
        self.acknowledged, self.retry_options = False, None

    def ack(self):
        self.acknowledged = True

    def retry(self, options):
        self.retry_options = options


def enable(env, *, mode="disabled"):
    env.JOBS_ENABLED = "true"
    env.EVENT_QUEUE_NAME, env.DEAD_LETTER_QUEUE_NAME = "events", "dead"
    env.QUEUE_MAX_RETRIES, env.EMAIL_MODE = "3", mode
    env.EVENTS = Queue()
    return env.EVENTS


async def deliver(env, *messages, dead=False):
    await consume(
        env,
        SimpleNamespace(
            queue=env.DEAD_LETTER_QUEUE_NAME if dead else env.EVENT_QUEUE_NAME,
            messages=list(messages),
        ),
    )


async def drain(env):
    await dispatch(env)
    messages = [Message(item["body"]) for item in env.EVENTS.messages]
    env.EVENTS.messages.clear()
    await deliver(env, *messages)
    assert all(message.acknowledged for message in messages)
    return messages
