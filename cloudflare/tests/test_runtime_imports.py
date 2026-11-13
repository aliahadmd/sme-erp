"""Keep the native D1 application free of the old server's ORM import graph."""

import os
import subprocess
import sys
from pathlib import Path


def test_application_does_not_import_sqlalchemy_at_startup():
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from erp_cf.app import create_app; import sys; app=create_app(); "
            "assert not any(k.startswith('sqlalchemy') for k in sys.modules); "
            "from app.shared.money import MONEY, QUANTITY, UNIT_PRICE; "
            "assert [(t.precision,t.scale) for t in [MONEY,QUANTITY,UNIT_PRICE]]"
            "==[(18,2),(18,4),(18,6)]",
        ],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(root / "src"), str(root.parent / "api")]),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
