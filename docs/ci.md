# GitHub validation

`.github/workflows/validation.yml` runs on pushes, pull requests, and manual
dispatches. It uses Python 3.12 and the dependencies recorded in `uv.lock`
(`uv sync --locked --all-extras`, so the PostgreSQL, Azure and deployment
extras are present). No AI credentials or repository secrets are required:
the copilot starts without a provider key and its tests use fake models.

## Jobs

- **Lint and types:** runs the same pre-commit configuration as Git —
  configuration-file validation, Ruff (`E4`, `E7`, `E9`, `F`), a plain
  `pyright` over `[tool.pyright]` in `pyproject.toml` (core and `jobhunt_ai`
  alike), Django startup checks and migration drift.
- **Tests (sqlite)** and **Tests (postgresql):** with `COPILOT_ENABLED=1` and
  `IS_SAAS_PRODUCTION=False`, run `manage.py check`, `makemigrations --check
  --dry-run`, then `test accounts tracker jobhunt rls jobhunt_ai`. The
  PostgreSQL job uses a disposable PostgreSQL 17 service; the custom test
  runner makes every request as the application database role, including the
  RLS policies on the copilot's tables.
- **Core without the copilot (sqlite):** with `COPILOT_ENABLED=0`, so that
  `jobhunt_ai` and `django_q` are not installed, runs `check`,
  `makemigrations --check --dry-run` and `test accounts tracker jobhunt rls`.
  This is the configuration of an instance that switched the copilot off;
  `jobhunt/test_copilot_toggle.py` covers both values of the switch from
  inside the suite as well, in a fresh interpreter.

PostgreSQL-specific tests skip on SQLite. Tests requiring Azurite or the
private legacy spreadsheet skip when those resources are unavailable; neither
is needed by this workflow. The pipelines never use personal documents or
production databases.

## Run locally

### Install Git hooks

After preparing the existing `.venv`, install the hooks once per clone:

```bash
uv tool install pre-commit==4.6.2
uvx --from pre-commit==4.6.2 pre-commit install
uvx --from pre-commit==4.6.2 pre-commit run --all-files
```

`.pre-commit-config.yaml` installs both commit and push hooks. Commits check
staged files for merge conflicts, malformed YAML/TOML/JSON, Ruff diagnostics,
Pyright errors, Django startup errors, and missing migrations. Before a push,
the same checks run along with the full SQLite suite, copilot included. To
run that gate manually:

```bash
uvx --from pre-commit==4.6.2 pre-commit run --all-files --hook-stage pre-push
```

Whole-project type and Django checks always run, including commits that only
delete files, so removing an imported module cannot skip validation.

`scripts/validate.py` uses the existing interpreter directly. It never syncs
dependencies, and replaces deployment database/storage settings and AI keys
with local test settings (`COPILOT_ENABLED=1`, `IS_SAAS_PRODUCTION=False`,
in-memory SQLite). PostgreSQL/RLS coverage stays in CI. Missing dependencies
fail the hook; prepare the environment using the commands below.

Git does not install hooks when cloning. Every contributor needs the install
step; CI also enforces these checks when local hooks are absent.

### The same checks by hand

```bash
uv sync --locked --all-extras
uvx --from ruff==0.16.2 ruff check .
uvx --from pyright==1.1.410 pyright
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
JOBHUNT_DATABASE_URL=sqlite:///:memory: \
  uv run python manage.py test accounts tracker jobhunt rls jobhunt_ai --noinput
COPILOT_ENABLED=0 JOBHUNT_DATABASE_URL=sqlite:///:memory: \
  uv run python manage.py test accounts tracker jobhunt rls --noinput
```

Set `JOBHUNT_DATABASE_URL` to a disposable PostgreSQL database to run the RLS
tests. Its maintenance user must be able to create test databases and roles,
as the workflow's PostgreSQL service user can.

Check names remain stable so they can be selected as required checks in a
GitHub branch ruleset. Adding the workflow does not change branch protection.
