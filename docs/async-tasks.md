# Durable scraping and LLM jobs

The HTTP process validates a submission and commits an `AgentRun` plus its
Django-Q2 ORM message. A separately supervised `qcluster` process prepares
the queries and a frozen candidate rubric, then publishes one independent
`Chain` per search target: scrape → extract and score → persist leads.
Targets run concurrently; each page starts its analysis without waiting for
other pages. CV parsing, matching and generation use independent jobs on the
same queue. No HTTP view waits for an LLM response or task result.

## Installation and processes

The copilot (`jobhunt_ai`) ships with the core; `uv sync` installs
`django-q2>=1.11.1,<2` with the rest (the import/app name is `django_q`).
`jobhunt/settings.py` adds `django_q` and `jobhunt_ai` to `INSTALLED_APPS`
and configures `Q_CLUSTER` whenever `COPILOT_ENABLED` is on (the default);
with `COPILOT_ENABLED=0` neither app is installed and nothing below applies.

From the project directory, with local settings in `.env` (create it from
`.env.example` if needed). Django does not load this file automatically:

```sh
uv run --env-file .env manage.py migrate
uv run --env-file .env manage.py check
uv run --env-file .env manage.py qcluster
```

Run the web server in another process. Use PostgreSQL for production. Apply
migrations with the schema owner's credentials, then run web, workers and
maintenance as the restricted application role, following the README section
« Isolation des données ». Run `manage.py rls_grant` as the schema owner if
new Q2 tables lack grants. Migration `0007` adds run tracking; `0008` adds the target
manifest and frozen context; `0009` applies tenant isolation to targets
through their parent run. `migrate` also installs Django-Q2's own tables.
Drain workers before upgrading their code and schema. Old unfinished rows
without a deadline expire after the configured queue TTL; they are not
automatically re-enqueued.

Web and worker processes need identical code, database, `SECRET_KEY`,
cluster name and timeout configuration. Premium entitlement is read from the
shared database (`accounts.services.has_premium`) before queued work starts. Workers also
need the Anthropic/Bright Data credentials and access to document storage.
Use shared persistent storage or Azure Blob Storage when processes run on
different machines. Keep `JOBHUNT_AI_EAGER=0` and `Q_CLUSTER['sync']=False`.
Startup checks reject synchronous or incompatible queue settings.

Pass `--env-file .env` to local web and maintenance commands too. Exported
environment variables take precedence over that file. Restart existing
workers after changing credentials or source URLs: their settings and
already-created target manifests are snapshots. Start a new Scout run to
use corrected URLs; terminal failures are not automatically retried.

If Jobat or Indeed returns HTTP 403 via `_fetch_direct`, check that the
worker loaded `BRIGHTDATA_API_TOKEN`. A token present only in the web process
does not configure a separate worker. This read-only check prints no secret:

```sh
uv run --env-file .env python -c 'from jobhunt_ai.scraping.brightdata import is_configured; print(is_configured())'
```

If Bright Data is configured but fails, `JOBHUNT_AI_BRIGHTDATA_FALLBACK=0`
preserves its error for diagnosis instead of attempting direct HTTP. Source
HTTP errors and recognized HTTP-200 verification pages fail only their own
target; verification pages are not submitted to the LLM as job listings.

### Queue configuration

`jobhunt/settings.py` configures:

```python
Q_CLUSTER = {
    "name": "jobhunt-ai",
    "orm": "default",
    "workers": 2,
    "timeout": 1800,
    "retry": 5520,
    "queue_limit": 2,
    "bulk": 1,
    "poll": 1,
    "recycle": 50,
    "ack_failures": True,
    "max_attempts": 1,
    "save_limit": 250,
    "sync": False,
    "scheduler": False,
}
```

`JOBHUNT_Q_WORKERS`, `JOBHUNT_Q_TIMEOUT` and `JOBHUNT_Q_RETRY` override those
values. The local queue limit follows the worker count. A broker receipt's
clock starts when it is fetched, including time waiting for an available
worker. The check therefore requires `retry >= 3 * timeout + 120`, allowing
for prefetched batches as well as execution. See the
[Django-Q2 broker documentation](https://django-q2.readthedocs.io/en/master/brokers.html)
and [configuration reference](https://django-q2.readthedocs.io/en/master/configure.html).

Fan-out requires at least two workers. `JOBHUNT_AI_SCRAPE_TIMEOUT` defaults
to 180 seconds per target; `JOBHUNT_AI_ANALYZE_TIMEOUT` defaults to 900
seconds per target. Both must be positive and no greater than the cluster
timeout. The analysis deadline includes extraction, scoring and persistence.

The ORM broker uses the same `default` connection as `AgentRun`. `launch()`
inserts both inside `transaction.atomic()`; workers cannot see uncommitted
messages. A failure or an outer request rollback removes both. Enqueuing
inside this transaction is intentional: there is no external broker write
and no post-commit callback that can be lost when the web process crashes.
See the [ORM broker implementation](https://github.com/django-q2/django-q2/blob/master/django_q/brokers/orm.py)
and [Django transaction semantics](https://docs.djangoproject.com/en/6.0/topics/db/transactions/).
Switching to Redis or another database requires a durable outbox and a
publisher; replacing this with a bare `on_commit(async_task)` loses that
atomicity guarantee.

Fan-out publication has the same guarantee: the full target manifest, parent
handoff and all initial Chain messages commit together. Each Chain has its
own group and explicit stage timeout. Chain does not forward task return
values, and Q2 can advance a chain after failure. Scraping therefore commits
text to its tenant-protected `ScoutTarget` before returning; analysis claims
only that target's `scraped` state. It never reads Q2's result table or waits
for a sibling. Explicit `save=True` preserves Q2's continuation handling.

## Application state and concurrency

`jobhunt_ai.models.AgentRun` is the source of truth. Django-Q2's task table
provides operational diagnostics and can be pruned independently.

| Stored state | JSON state | Meaning |
|---|---|---|
| `pending` | `PENDING` | Committed to the queue, awaiting a worker |
| `running` | `PROCESSING` | Atomically claimed by a worker |
| `succeeded` | `SUCCESS` | Completed; Scout has at least one successful target |
| `failed` | `FAILED` | Preparation failed, all targets failed, or an ordinary job failed |

The existing stored values remain compatible with templates and historical
rows. `task_id` connects a run to queue diagnostics; `phase`,
`progress_current` and nullable `progress_total` describe the current stage.
After fan-out, progress counts terminal targets, including failed targets;
it is not a time estimate. Successful targets retain their leads even when
another target fails. Scout summaries include `targets_total`,
`targets_succeeded`, `targets_failed`, `partial` and per-target warnings.
`created_at`, `started_at`, `finished_at` and `deadline_at` support operations.
`params` and `result` retain structured application data. Polling never
returns parameters or raw CV text.

An account row lock serializes submissions. A repeated Scout submission
returns the account's existing active Scout run; match/generate requests
share one active run per application. Different CV uploads may queue
independently. A POST after a terminal run creates a new run: this is active
work deduplication, not a permanent request-idempotency key.

Workers claim `PENDING → PROCESSING` with one conditional UPDATE including
both run ID and owner ID. Duplicate deliveries of running or terminal jobs
do not execute the pipeline again. Terminal updates also require an active
state, so a stale worker cannot overwrite a recorded failure with success.

The queue payload contains only `(run_id, owner_id)`. Every protected read or
write runs in a short `rls.as_user(owner_id)` block, with explicit owner
filters for claiming and API access. Scraping and model calls occur outside
database transactions. Scout saves each target's leads with `bulk_create`,
its terminal status and parent progress in the same short transaction.
Writers lock the parent before rechecking deduplication so concurrent sources
cannot insert the same offer. The owner/status/kind/creation index supports active-job queries;
existing owner/run foreign-key indexes support result lookup. JSON result
responses paginate 50 leads and omit bulky descriptions.

Q2's `OrmQ`, `Task` and `Schedule` models are explicitly registered as shared
infrastructure exemptions in the `rls` registry (`rls.exempt`, from
`jobhunt_ai/apps.py`). Its poller must see
all queue messages without a tenant binding. No CV, prompt or application
result is sent to those tables by the runner, and public endpoints never
query them. Limit Django-Q2 admin access to trusted operators. Tenant data
remains protected by the existing application-table policies.

## HTTP API

These routes use the existing session authentication, CSRF protection and
per-account Premium entitlement checks. Send the session cookie and `X-CSRFToken` when submitting.

```http
POST /copilote/api/scout/
Content-Type: application/json
X-CSRFToken: <session CSRF token>

{"keywords":"Django","location":"Bruxelles","radius_km":40}
```

The response is `202 Accepted` with `Location` pointing to the status URL
and `Retry-After: 3`. It contains the run ID, current state and result URLs:

```json
{
  "id": 42,
  "kind": "scout",
  "status": "PENDING",
  "status_url": "/copilote/api/runs/42/",
  "leads_url": "/copilote/api/runs/42/leads/"
}
```

The example omits timestamps, phase, progress and initially null result/error
fields. Poll `GET /copilote/api/runs/42/` every three seconds, backing off on
transport errors. It returns `200` for all known job states; a failed job is
a successful status lookup with `status: "FAILED"`. Stop polling on SUCCESS
or FAILED. Only SUCCESS includes the result summary; only FAILED includes
the application error. Responses use `private, no-store`.

Fetch `GET /copilote/api/runs/42/leads/?page=1` for that run's saved leads.
The response includes `results`, `count` and `next_page`. Other accounts'
run IDs return `404` on both endpoints. Invalid parameters return `400`, a
missing candidate profile returns `409`, and queue submission database
failure returns `503` rather than `202`. Non-POST submission returns `405`.
Unauthenticated sessions follow the `accounts` sign-in flow.

The existing `/copilote/hx/...` endpoints remain HTML/HTMX endpoints and keep
their `200` fragment contract. Their two-second poll now distinguishes
queued work from processing and displays stage progress.

## Failures, timeouts and recovery

An ordinary job or Scout coordinator has a queue deadline (24 hours by default,
`JOBHUNT_AI_QUEUE_TTL`). Claiming replaces it with the worker timeout plus a
60-second recovery margin. A queued message delivered after expiry does no
work. Queue TTL is an admission-to-start deadline, not an execution timeout.

After publishing the manifest, Scout clears the parent deadline. Each
target owns its queue and execution deadlines: `pending` → `scraping` →
`scraped` → `analyzing` → `succeeded`, or `failed` from any unfinished state.
Claims replace that stage's queue deadline with its timeout plus the recovery
margin. Completion rechecks the deadline under the parent lock and rejects
late results even before maintenance runs. A malformed source, scrape error,
analysis exception or expired deadline closes only that target. Parent
completion waits for every target to reach a terminal state. A late
coordinator error cannot overwrite a dispatched run's state.

Ordinary pipeline exceptions record FAILED and a completion timestamp.
Django-Q2's cooperative `TimeoutException` inherits `SystemExit`; the runner
handles it explicitly, records failure and re-raises it so Q2 recycles the
worker. SIGKILL, machine failure and OOM cannot execute this handler: the
ORM broker redelivers unacknowledged messages, while guarded claims prevent
duplicate LLM execution. Reconciliation expires abandoned work, including
targets whose Chain continuation was lost. Other targets keep running.

Also run the independent command **every minute**, even if the worker is
down or no browser is polling:

```sh
uv run manage.py reconcile_ai_runs
# Optional single-account maintenance:
uv run manage.py reconcile_ai_runs --owner-id 42
```

Status polling performs the same bounded, per-account check. Web restarts
never determine whether a worker is alive. The command also resolves expired
pending jobs when an outage prevents the worker from starting at all.

Failed whole pipelines are not retried automatically. The Anthropic SDK
retries eligible network/API failures at the individual call level (default
two retries, `JOBHUNT_AI_LLM_MAX_RETRIES`; network timeout 120 seconds,
`JOBHUNT_AI_LLM_TIMEOUT`). These are network-operation limits, not a total
pipeline deadline; Django-Q2 enforces the latter.

Delivery is at least once; pipeline execution is guarded against duplicate
claims. External API calls and database writes are not one atomic operation.
A Scout target commits its outputs and completion together; a crash cannot
commit one without the other. Ordinary CV parsing, matching and generation
can leave partial outputs after an interrupted pipeline. Scout also checks
saved leads on a subsequent launch. Inspect failed jobs before retrying costly work;
exactly-once LLM billing is not guaranteed. To add automatic whole-pipeline
retries later, first add per-stage checkpoints and idempotent output keys
for all four agents, including generated files.

## Production supervision

Templates in `deploy/` run the worker and a separate reconciliation timer.
Set their user, paths and protected `/etc/jobhunt.env` for your deployment,
then install them using your deployment system. They are examples; nothing
in the repository installs or enables system services. The worker stop timeout
allows the current and prefetched tasks to drain with the default settings;
adjust it with your task timeout and deployment termination grace period.

Monitor worker exits, oldest PENDING age, count of expired/FAILED runs,
processing duration and provider token usage. Alert if no worker is running
while jobs are pending. Start with two workers to bound database connections
and provider concurrency. Q2's `save_limit` bounds successful task history,
not failed task history or `AgentRun` records; apply your retention policy
to those separately. Database backups include the durable queue.

Pin the resolved dependencies in your deployment build: `uv.lock` records
them, and `uv sync --locked --extra postgres --extra azure --extra deploy`
installs exactly that set (a bare `uv sync` prunes the extras, psycopg included).

## Validation

```sh
uv run manage.py test jobhunt_ai accounts tracker jobhunt rls
uv run manage.py makemigrations --check --dry-run
uv run manage.py check
```

Tests cover actual signed ORM queue messages, outer rollback, broker failure,
duplicate delivery, safe terminal transitions, Q2 timeouts, orphan recovery,
owner-scoped API responses, pagination and no open transaction during network
work. Fan-out tests exercise actual Q2 worker/monitor continuation, independent
target completion and failures, late-result rejection, lost continuations,
source configuration errors and atomic output persistence. PostgreSQL also
tests simultaneous submissions, deliveries and analyses using separate
database connections, plus target RLS. Agent/provider tests use fake responses;
no paid scraping or LLM requests are part of verification.
