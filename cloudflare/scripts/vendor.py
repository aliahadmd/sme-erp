"""Resolve and vendor Pyodide-compatible wheels for cf prebuilt deployment.

Uses the same uv target and package index as workers-py 1.17.9 for Python 3.14.
No host-native extension may enter the Worker bundle. The lock is committed;
the installed vendor directory is generated. No Wrangler configuration is used.
"""

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = "cpython-3.14.2-emscripten-wasm32-musl"
INDEX = "https://index.pyodide.org/314.0.7"
VENV = ROOT / ".venv-workers" / "pyodide-venv"


def run(*args: str, env=None) -> None:
    subprocess.run(args, cwd=ROOT, check=True, env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-lock", action="store_true")
    options = parser.parse_args()
    if options.update_lock:
        run(
            "uv",
            "pip",
            "compile",
            "pyproject.toml",
            "--python",
            TARGET,
            "--extra-index-url",
            INDEX,
            "--index-strategy",
            "unsafe-best-match",
            "--no-build",
            "--no-header",
            "-o",
            "pylock.toml",
        )
    elif not (ROOT / "pylock.toml").is_file():
        raise SystemExit("Missing pylock.toml; resolve dependencies with --update-lock first")
    if not VENV.exists():
        run("uv", "venv", str(VENV), "--python", TARGET)
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {"VIRTUAL_ENV", "CONDA_PREFIX", "UV_PYTHON", "UV_PROJECT_ENVIRONMENT", "PYTHONHOME"}
    }
    env["VIRTUAL_ENV"] = str(VENV)
    run(
        "uv",
        "pip",
        "sync",
        "--only-binary",
        ":all:",
        "pylock.toml",
        "--preview-features",
        "pylock",
        env=env,
    )
    vendor = ROOT / "python_modules"
    if vendor.exists():
        shutil.rmtree(vendor)
    shutil.copytree(VENV / "lib" / "python3.14" / "site-packages", vendor)
    (vendor / "pyvenv.cfg").touch()


if __name__ == "__main__":
    main()
