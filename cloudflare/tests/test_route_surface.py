"""Keep the complete original HTTP surface visible while the port is unfinished.

This guards route presence only. Behavioral, role, runtime and workflow checks
remain separate; passing this with pending routes is not migration acceptance.
"""

import json
import re
from pathlib import Path

from erp_cf.app import create_app

PENDING = set()


def test_original_route_inventory_is_complete():
    reference = json.loads(
        (Path(__file__).parents[1] / "fixtures/reference_routes.json").read_text()
    )
    original = set(reference["routes"])
    methods = {"get", "post", "put", "patch", "delete"}
    current = {
        method.upper() + " " + re.sub(r"\{[^}]+\}", "{id}", path)
        for path, operations in create_app().openapi()["paths"].items()
        for method in operations
        if method in methods
    }
    assert original - current == PENDING
