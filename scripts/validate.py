"""Run local Django gates without inheriting deployment configuration."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("checks", "tests"))
    mode = parser.parse_args().mode
    # Hooks must never use a developer's database, storage credentials, or API
    # keys. Dummy AI settings also support an optional, already-installed plugin.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("JOBHUNT_", "ANTHROPIC_", "BRIGHTDATA_", "DJANGO_"))
    }
    environment.update(
        PYTHONPATH=str(ROOT),
        DJANGO_SETTINGS_MODULE="jobhunt.settings",
        JOBHUNT_DATABASE_URL="sqlite:///:memory:",
        JOBHUNT_AUTO_MIGRATE="0",
        JOBHUNT_DEBUG="1",
        JOBHUNT_AUTH_MODE="local",
        JOBHUNT_AI_API_KEY="hook-placeholder-never-sent",
        JOBHUNT_AI_LICENSE_KEY="hook-test-license",
    )
    commands = (
        [["check"], ["makemigrations", "--check", "--dry-run"]]
        if mode == "checks"
        else [["test", "accounts", "tracker", "jobhunt", "rls", "--noinput"]]
    )
    for arguments in commands:
        subprocess.run(
            [sys.executable, "manage.py", *arguments],
            cwd=ROOT,
            env=environment,
            check=True,
        )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
