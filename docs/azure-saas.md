# Provider selection and hosted copilot allowances

`JOBHUNT_AI_PROVIDER` selects `openai`, `azure_openai`, or `anthropic` independently
of `IS_SAAS_PRODUCTION`. With no explicit provider, legacy behavior is preserved:
Anthropic outside SaaS mode and Azure OpenAI in SaaS mode. Provider errors never
trigger a fallback to another vendor. `IS_SAAS_PRODUCTION` still controls free
account access and the per-user quota wrapper; choosing OpenAI does not change it.

## Direct OpenAI during the Azure trial

Production selects `JOBHUNT_AI_PROVIDER=openai`, with these settings:

```dotenv
JOBHUNT_AI_PROVIDER=openai
JOBHUNT_AI_OPENAI_SIMPLE_MODEL=gpt-4o-mini-2024-07-18
JOBHUNT_AI_OPENAI_COMPLEX_MODEL=gpt-4o-2024-08-06
JOBHUNT_AI_OPENAI_MAX_INPUT_CHARS=100000
JOBHUNT_AI_OPENAI_MAX_OUTPUT_TOKENS=8192
IS_SAAS_PRODUCTION=false
```

Supply `JOBHUNT_AI_OPENAI_API_KEY` through the deployment secret store. Locally,
`OPENAI_API_KEY` is also accepted when the namespaced key is empty. Never put a
real key in this file, source control, Terraform inputs/state, logs, or browser
code. On App Service, the private infrastructure repository provisions Key Vault,
a system-assigned web identity and a versionless Key Vault app-setting reference.
Only the operator creates/rotates the secret value; Terraform manages its container
and access grants. A missing/unresolved reference fails safely when AI is invoked.

The adapter calls `https://api.openai.com/v1` with structured outputs and `store=false`.
It sends the same anonymized text as the Azure adapter, rejects raw document/image
blocks and enforces input/output limits. Automatic SDK retries are disabled so an
attempt does not silently create extra provider calls. OpenAI Platform billing,
credits, rate limits and residency settings apply independently of Azure trial
credits or Azure OpenAI quotas. Azure's EU data-zone guarantee does not transfer
to this endpoint. No API calls are made during app startup.

## SaaS access and provider composition

The existing `services.llm.parse_structured` interface remains the entry point
for every agent. Its signature and `StructuredResult` are formalized by
`jobhunt_ai.ports.AIPort`, independent of Django and provider SDKs.

`adapters.get_ai_port()` composes the implementations on each call:

- `IS_SAAS_PRODUCTION=True`: `ProductionQuotaProxy(selected_adapter)`.
- `False` (default): the selected adapter without subscription quota queries or
  counter writes. Without an explicit provider, this selects Anthropic for a
  development checkout or self-hosted instance, where the copilot
  is a Premium feature (`Profile.premium_until`). An instance that switches
  the copilot off altogether (`COPILOT_ENABLED=0`) installs neither
  `jobhunt_ai` nor `django_q`; the core's CV event adapter then has no
  subscriber and requires no quota implementation.

## Deployment

The `openai` SDK is a base dependency. Azure identity and Blob Storage drivers
belong to the `azure` extra:

```sh
uv sync --locked --extra postgres --extra azure --extra deploy
```

Configure both web and Q2 workers identically, then apply migrations with the
migration role before starting workers (`python manage.py migrate`). Migration
`jobhunt_ai.0010_user_ai_quota` creates the quota table and installs its RLS
policy (`rls.operations.EnableRowLevelSecurity`); `jobhunt_ai/apps.py`
registers the table with the `rls` registry.

```dotenv
IS_SAAS_PRODUCTION=True
JOBHUNT_AI_PROVIDER=azure_openai
JOBHUNT_AI_AZURE_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
JOBHUNT_AI_AZURE_API_VERSION=2024-10-21
JOBHUNT_AI_AZURE_SIMPLE_DEPLOYMENT=YOUR-GPT-4O-MINI-DEPLOYMENT
JOBHUNT_AI_AZURE_COMPLEX_DEPLOYMENT=YOUR-GPT-4O-DEPLOYMENT
JOBHUNT_AI_UPGRADE_URL=/your-existing-billing-page/
JOBHUNT_AI_FREE_MONTHLY_REQUESTS=10
JOBHUNT_AI_FREE_REQUESTS_PER_MINUTE=3
JOBHUNT_AI_PAID_MONTHLY_REQUESTS=500
JOBHUNT_AI_PAID_REQUESTS_PER_MINUTE=20
JOBHUNT_AI_AZURE_MAX_INPUT_CHARS=100000
JOBHUNT_AI_AZURE_MAX_OUTPUT_TOKENS=8192
```

The allowances above are configurable starting values, not contractual plan
limits. A zero allowance disables calls for that tier. Periods are UTC calendar
months and fixed UTC minutes; a boundary permits the next window's allowance.
These are **request quotas**, not a monetary budget or a token allowance.

Use PostgreSQL in SaaS production: quota reservation locks the user's existing
account row, checks the current entitlement, and updates `UserAIQuota` inside a
short transaction. This serializes even simultaneous first calls from different
workers. No provider I/O happens inside that reservation transaction.
`JOBHUNT_DATABASE_URL` selects PostgreSQL; SQLite remains useful for
development but does not provide PostgreSQL row locking.

With no `JOBHUNT_AI_AZURE_API_KEY`, the adapter uses `DefaultAzureCredential`
and a renewable bearer-token provider. Grant the application's managed identity
the appropriate Azure OpenAI inference role. Alternatively supply that namespaced
key through the deployment's secret configuration (an App Service setting or a
Key Vault reference). No keys belong to customer profiles.

When `JOBHUNT_AI_PROVIDER=azure_openai`, the SDK is `openai.AzureOpenAI`. The `model` parameter is an Azure **deployment
name**, using deployments that support structured outputs (GPT-4o-mini and
GPT-4o 2024-08-06 or later compatible deployments). See
[Microsoft's structured-output documentation](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs).
Both requested model families support structured output; see
[GPT-4o-mini](https://developers.openai.com/api/docs/models/gpt-4o-mini).

## Routing and accounting

The explicit schema allowlist routes `ParsedProfile`, `ScrapedOffers`, and
`ScoutQueries` to the small deployment. Matching, guidance, qualification
reasoning, CV generation, and unrecognized schemas use the complex deployment.
The router never reads user-controlled model names or makes a billable routing
call. The adapter validates structured output through the SDK and caps input
characters and output tokens. Binary document blocks are rejected; the core
extracts and anonymizes text first (`tracker.services.ingest_cv`).

One quota request means one attempted provider invocation, across all agents
and runs owned by that user. Multi-stage work may consume several requests.
Reservations occur before provider calls. Failures, refusals, and timeouts
remain charged because the provider may already have processed them. SDK retries are
disabled; retrying a task must pass through the proxy and reserve another request.
Database failure denies the call. Reported token usage and the chosen
deployment continue to accumulate on `AgentRun`, including refusals and
truncation when the SDK returns usage, for observability.

`jobhunt_ai.adapters.quota_store.DjangoAIQuotaStore` reads server-owned
entitlement on every reservation through `accounts.services.has_premium(user)`,
a fresh `Profile.premium_until` query that also requires an active account.
A plan upgrade or expiry changes the allowance without resetting usage. The
copilot does not implement payment collection or grant Premium: billing or
administrative code must maintain the confirmed paid period for the $29/month
plan (today, the profile admin in accounts mode). Freemium users gain copilot access only in SaaS production.

## Quota responses

`QuotaExceededException` exposes `tier`, `limit`, `period`, and `reset_at`, plus
a serializable `as_dict()` payload. The existing view decorator catches it;
HTMX receives a 200 fragment so its default swap behavior works, while direct
API errors return 429. Paid exhaustion and minute limits never display an
upgrade action. `JOBHUNT_AI_UPGRADE_URL` must point to your existing billing
page; when unset, the fragment asks the user to contact the team.

Background workers persist the typed quota payload on the run. Polling returns
the same fragment, including after partial scout results. The status JSON
includes `quota`, so API clients can distinguish quota exhaustion from a provider
outage. Previously collected scout results remain available.

## Verification

`jobhunt_ai/tests/test_production_ai.py` uses the real SDK with a mock HTTP
transport: it needs no cloud credentials, only the `azure` extra (CI installs
every extra). Tests cover routing, output validation, limits, subscription
transitions, period rollover, failed calls, HTMX responses, and partial
fan-out. PostgreSQL additionally runs simultaneous quota reservations as the
restricted application role.
