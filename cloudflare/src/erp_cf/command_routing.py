"""One command coordinator route shared by HTTP, jobs and staging proofs.

Placement hints affect only a new object. Changing generation requires a
quiesced deployment: never use generations to distribute concurrent writers.
All durable business state and receipts live in D1, not in the object instance.
"""

import json
import re


def command_stub(env, *, no_retry=False):
    generation = str(getattr(env, "COMMAND_COORDINATOR_GENERATION", ""))
    hint = str(getattr(env, "COMMAND_LOCATION_HINT", ""))
    if generation and not re.fullmatch(r"[a-z0-9-]{1,32}", generation):
        raise ValueError("Invalid command coordinator generation")
    if hint and hint not in {"wnam", "enam", "sam", "weur", "eeur", "apac", "oc", "afr", "me"}:
        raise ValueError("Invalid command coordinator location hint")
    name = str(env.ORG_ID) + (":" + generation if generation else "")
    options = {"locationHint": hint} if hint else {}
    if no_retry:
        options["retry"] = {"maxRetries": 0}
    if not options:
        return env.COMMANDS.getByName(name)
    try:
        from js import JSON
    except ImportError:
        pass
    else:
        options = JSON.parse(json.dumps(options))
    return env.COMMANDS.getByName(name, options)
