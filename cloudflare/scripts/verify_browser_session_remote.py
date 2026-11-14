"""Run account-isolation or role/navigation/form checks on the isolated demo.

Open the named erp-roles Playwright CLI session at the demo login page first.
The payload is private and temporary; CLI source echo is never printed with
credentials. The scenario signs out both actors, even when an assertion fails.
"""

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / ".cloudflare/verification/role-browser"
URL = "https://erp-demo-staging-20261114.aliahad.workers.dev"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", choices=["session-cache", "stale-response", "roles"], default="session-cache"
    )
    parser.add_argument(
        "--role", help="Limit the navigation walkthrough to one active demo profile"
    )
    args = parser.parse_args()
    private = json.loads((ROOT / ".demo.demo-staging.json").read_text())
    accounts = (
        {"admin": private["admin"], "employee": private["users"]["employee"]}
        if args.scenario != "roles"
        else {
            "admin": private["admin"],
            **{k: v for k, v in private["users"].items() if k != "disabled"},
        }
    )
    if args.role:
        if args.scenario != "roles" or args.role not in accounts:
            parser.error("--role requires --scenario roles and an active demo profile")
        accounts = {args.role: accounts[args.role]}
    script = (
        (ROOT / f"scripts/browser_{args.scenario.replace('-', '_')}.js")
        .read_text()
        .strip()
        .removesuffix(";")
    )
    source = (
        "async page => { if (new URL(page.url()).origin !== "
        + json.dumps(URL)
        + ") throw new Error('Unexpected browser origin');"
        + "const assets = await page.locator('script[src]').evaluateAll("
        + "scripts => scripts.map(script => new URL(script.src).pathname));"
        + "const result = await ("
        + script
        + ")(page,"
        + json.dumps(accounts)
        + "); return {...result, origin: new URL(page.url()).origin, assets}; }"
    )
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    payload = DIRECTORY / f"{args.scenario}-private.js"
    with os.fdopen(os.open(payload, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        out.write(source)
    evidence = DIRECTORY / f"{args.scenario}.json"
    started_at = datetime.now(UTC).isoformat()
    report = {"state": "running", "started_at": started_at}
    evidence.write_text(json.dumps(report) + "\n")
    try:
        result = subprocess.run(
            [
                "npm",
                "exec",
                "--yes",
                "--package",
                "@playwright/cli@0.1.24",
                "--",
                "playwright-cli",
                "-s=erp-roles",
                "run-code",
                "--filename=" + str(payload),
            ],
            cwd=DIRECTORY,
            capture_output=True,
            text=True,
            timeout=600,
        )
        text = result.stdout + result.stderr
        for account in accounts.values():
            text = text.replace(account["password"], "[redacted]")
        if result.returncode or "### Error" in text:
            # Failed CLI calls omit their source echo; retain only the error.
            error = text.split("### Error", 1)[-1].split("### Ran Playwright code", 1)[0]
            raise RuntimeError(error[:2000])
        value = json.loads(text.split("### Result\n", 1)[1].split("\n###", 1)[0])
        report = value
        value.update(
            state="complete", started_at=started_at, verified_at=datetime.now(UTC).isoformat()
        )
        evidence.write_text(json.dumps(value, indent=2) + "\n")
        if args.scenario == "session-cache":
            print("PASS native same-browser account switch: only the employee's own leave is shown")
        elif args.scenario == "stale-response":
            print(
                "PASS native delayed old-session response: no refresh/retry under the new account"
            )
        else:
            print(
                json.dumps(
                    [
                        {
                            "role": role["role"],
                            "pages": len(role["pages"]),
                            "forms": len(role["forms"]),
                            "failures": role["failures"],
                        }
                        for role in value["roles"]
                    ],
                    indent=2,
                )
            )
            if not value["passed"]:
                raise RuntimeError("Role browser walkthrough found inaccessible pages or requests")
    except BaseException:
        report["state"] = "failed"
        evidence.write_text(json.dumps(report, indent=2) + "\n")
        raise
    finally:
        payload.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
