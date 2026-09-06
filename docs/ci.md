# GitHub validation

`.github/workflows/validation.yml` runs on pushes, pull requests, and manual
dispatches. It uses Python 3.12 and the dependencies recorded in `uv.lock`.
No AI package, AI credentials, or repository secrets are required.

## Checks

- **Lint and types:** Ruff checks Python syntax, imports, unused code, and
  basic correctness (`E4`, `E7`, `E9`, `F`). Pyright checks the core with
  `.github/pyright-core.json`.
- **Tests (sqlite, without AI):** installs only the core and its optional
  PostgreSQL/Azure dependencies, verifies that `jobhunt_ai` cannot be imported,
  runs Django system and migration-drift checks, then runs all core test apps.
- **Tests (postgresql, without AI):** runs the same suite against a disposable
  PostgreSQL 17 service. The custom test runner exercises requests using the
  application database role, including the RLS policies.

The optional `jobhunt/ai_integration.py` adapter is linted here and type-checked
in JobHunt-AI's workflow, where the AI types are installed. The default local
Pyright configuration still checks the adapter when using a combined
development environment.

PostgreSQL-specific tests skip on SQLite. Tests requiring Azurite or the
private legacy spreadsheet skip when those resources are unavailable; neither
is needed by this workflow. The pipelines never use personal documents or
production databases.

## Run locally

Use a clean checkout or a separate virtual environment for the core-only run:
`uv sync` removes packages that are not in the core lockfile, including an
editable AI installation in a shared environment.

```bash
uv sync --locked --all-extras
uvx --from ruff==0.16.2 ruff check jobhunt accounts tracker rls manage.py
uvx --from pyright==1.1.410 pyright --project .github/pyright-core.json
uv run --no-sync python manage.py check
uv run --no-sync python manage.py makemigrations --check --dry-run
JOBHUNT_DATABASE_URL=sqlite:///:memory: \
  uv run --no-sync python manage.py test accounts tracker jobhunt rls --noinput
```

Set `JOBHUNT_DATABASE_URL` to a disposable PostgreSQL database to run the RLS
tests. Its maintenance user must be able to create test databases and roles,
as the workflow's PostgreSQL service user can.

Check names remain stable so they can be selected as required checks in a
GitHub branch ruleset. Adding the workflow does not change branch protection.
