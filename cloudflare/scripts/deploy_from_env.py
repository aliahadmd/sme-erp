"""Deploy the closed-bootstrap demo build with runtime secrets from CI.

Read-only validation is the default; --deploy performs the staging deployment.
Secrets are written to a temporary 0600 file, never printed or bundled, and
removed afterward. This cannot target production or initialize/seed a database.
"""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODE = "demo-staging"
WORKER = "erp-demo-staging-20261114"


def main(deploy=False):
    inspection = subprocess.check_output(
        [
            "node",
            "--input-type=module",
            "-e",
            """
import {readBuildOutput} from '@cloudflare/build-output-utils';
const o=await readBuildOutput(process.cwd()), c=o.workers.default.config;
console.log(JSON.stringify({name:c.name,mode:o.rootConfig.buildContext.mode,
 bootstrap:c.env.BOOTSTRAP_ENABLED.value,jobs:c.env.JOBS_ENABLED.value}));
""",
        ],
        cwd=ROOT,
        text=True,
    )
    actual = json.loads(inspection)
    if actual != {"name": WORKER, "mode": MODE, "bootstrap": "false", "jobs": "true"}:
        raise RuntimeError("Unexpected build target/initialization state")
    secrets = {
        "JWT_SECRET": os.environ.get("ERP_JWT_SECRET", ""),
        "COMMAND_HASH_KEY": os.environ.get("ERP_COMMAND_HASH_KEY", ""),
    }
    if not all(len(value) >= 32 for value in secrets.values()):
        raise RuntimeError("CI runtime secrets are missing or too short")
    if not deploy:
        print("Validated closed-bootstrap demo target and runtime secrets; no deployment")
        return
    directory = ROOT / ".cloudflare"
    directory.mkdir(exist_ok=True)
    filename = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix="ci-secrets-",
            suffix=".json",
            delete=False,
        ) as out:
            filename = Path(out.name)
            json.dump(secrets, out)
        subprocess.run(
            [
                "pnpm",
                "exec",
                "cf",
                "deploy",
                "--prebuilt",
                "--mode",
                MODE,
                "--secrets-file",
                str(filename),
            ],
            cwd=ROOT,
            check=True,
        )
    finally:
        if filename is not None:
            filename.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", action="store_true")
    main(parser.parse_args().deploy)
