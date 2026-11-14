"""Render reviewable native Cloudflare Monitor queries; creates/enables nothing."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "monitoring"


def render(mode):
    manifest = json.loads((ROOT / "monitors.json").read_text())
    worker = manifest["workers"][mode]
    return {
        "worker": worker,
        "enabled": manifest["enabled"],
        "activation": manifest["activation"],
        "monitors": [
            {
                **item,
                "query": (ROOT / item["query_file"])
                .read_text()
                .format(
                    account_id=manifest["account_id"],
                    worker=worker,
                )
                .strip(),
            }
            for item in manifest["monitors"]
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["app-staging", "demo-staging"], default="demo-staging")
    print(json.dumps(render(parser.parse_args().mode), indent=2))
