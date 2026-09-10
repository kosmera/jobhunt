# Automatic Azure deployment

`azure-pipelines.yml` deploys the GitHub repository `kosmera/jobhunt` to the
existing Linux App Service **tonjobideal**, in **tonjobideal_group**. The Azure
DevOps project is <https://dev.azure.com/kosmera/jobhunt>.

## What happens after a merge

A push to `main`, including a merged pull request, starts the pipeline. Changes
arriving during a run are batched into the next run. Pull requests continue to
use the existing GitHub validation workflow. A manual run on another branch
validates and packages the app but cannot deploy it.

The pipeline uses Python 3.12 and `uv.lock`, runs the existing pre-commit gates,
then tests the core against SQLite and a disposable PostgreSQL 17 database,
including row-level security. A failure prevents deployment.

It archives committed application code, templates, and static assets, and
generates a production `requirements.txt` from the lockfile with the `postgres`,
`azure`, and `deploy` extras. The ZIP excludes local databases, uploaded files,
personal documents, `.env`, Git metadata, and development environments. The
optional JobHunt-AI package is not included in this core deployment.

Azure installs the production requirements during ZIP deployment. `bash
startup.sh` applies migrations using `JOBHUNT_MIGRATE_DATABASE_URL`, collects
static files, and starts Gunicorn using the application database role. The
pipeline checks `/connexion/` after deployment. Production settings and secrets
stay in App Service; the build agent uses only disposable test configuration.

## One-time Azure DevOps setup

1. In **Project settings → Service connections**, create an **Azure Resource
   Manager** connection named `tonjobideal-azure`, using **workload identity
   federation**. Scope its deployment access to `tonjobideal_group` (or the
   App Service itself with appropriate deployment permissions). Authorize only
   this deployment pipeline, rather than every pipeline in the project.
2. In **Pipelines → Environments**, create `tonjobideal-production` and add an
   **Exclusive lock** check. The YAML uses sequential lock behavior to prevent
   overlapping deployments, including manually queued runs. Do not add a
   manual approval check if deployments should remain automatic.
3. Create a pipeline with **GitHub** as the source and select `kosmera/jobhunt`.
   Authorize the Azure Pipelines GitHub app for this repository. Select
   **Existing Azure Pipelines YAML file**, branch `main`, path
   `/azure-pipelines.yml`. Set the default branch for manual builds to `main`.
4. Authorize the pipeline to use its service connection and environment, and
   verify a Microsoft-hosted agent is available. New organizations may need
   their free hosted parallel-job grant enabled before jobs can start.
5. Merge the pipeline file into `main` and verify the first run completes,
   including the deployed login-page check.

The existing App Service must keep `SCM_DO_BUILD_DURING_DEPLOYMENT=1`, runtime
`PYTHON|3.12`, and startup command `bash startup.sh`. The migration URL must be
configured for schema updates. Do not enable `WEBSITE_RUN_FROM_PACKAGE` for
this Python deployment; it uses ZIP deployment with server-side builds.

## Operations

The pipeline run retains the deployed ZIP as the `webapp` artifact. Review
build logs in Azure DevOps and application/startup logs in App Service. A
failed post-deployment check marks the run failed but does not automatically
roll back code or database migrations. Deployments restart the existing app;
this setup does not provide a staging-slot swap or zero downtime.

To revert code, revert the relevant commit through a pull request into `main`.
Database migrations require a separate compatibility and recovery decision;
reverting code does not reverse a migration already applied.

References: [Azure Pipelines for Python web apps](https://learn.microsoft.com/en-us/azure/devops/pipelines/ecosystems/python-webapp?view=azure-devops),
[Python build automation on App Service](https://learn.microsoft.com/en-us/azure/app-service/configure-language-python#customize-build-automation),
and [GitHub CI triggers and batching](https://learn.microsoft.com/en-us/azure/devops/pipelines/repos/github?view=azure-devops#ci-triggers).
