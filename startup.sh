#!/usr/bin/env bash
# App Service startup command. Runs on every boot of the container.
#
# ``runserver`` migrates by itself in development; deployed, AUTO_MIGRATE is off
# (jobhunt/settings.py) and this is the explicit step that replaces it.
#
# Two roles, two connection strings — see README, « Isolation des données » :
# the owner runs the migrations and owns the tables, and the web process serves
# as ``jobhunt_app``, which is subject to the row-level policies. Serving as the
# owner would silently bypass every one of them, so the owner URL is used *only*
# for the migrate subprocess below and is never exported to gunicorn.
set -euo pipefail

if [ -n "${JOBHUNT_MIGRATE_DATABASE_URL:-}" ]; then
  echo "startup: applying migrations (as the owner role)"
  # --skip-checks works around an Azure-specific false positive, NOT a real
  # isolation problem. rls/verify.py decides "this connection is the runtime
  # side" with ``pg_has_role(current_user, app_role, 'USAGE') and not superuser``.
  # On Flexible Server the server admin answers True to that regardless of the
  # membership flags — the grant really is INHERIT FALSE, checked by hand — so
  # ``migrate`` as the owner raises rls.E004 where the design intends rls.W002.
  # Runtime enforcement is untouched: the web process serves as jobhunt_app and
  # JOBHUNT_RLS_ENFORCE=1 still verifies the role on its first query.
  # The clean fix is a dedicated owner role that is not the Azure admin.
  JOBHUNT_DATABASE_URL="$JOBHUNT_MIGRATE_DATABASE_URL" \
    python manage.py migrate --noinput --skip-checks
else
  echo "startup: JOBHUNT_MIGRATE_DATABASE_URL unset — skipping migrations"
fi

# whitenoise serves from STATIC_ROOT with hashed filenames, so the manifest has
# to exist before the first request.
echo "startup: collecting static files"
python manage.py collectstatic --noinput --clear

echo "startup: starting gunicorn (as the application role)"
exec gunicorn jobhunt.wsgi:application \
  --bind="0.0.0.0:${PORT:-8000}" \
  --workers 2 \
  --timeout 600 \
  --access-logfile '-' \
  --error-logfile '-'
