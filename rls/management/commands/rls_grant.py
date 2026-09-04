"""``manage.py rls_grant``: (re)grant the tables to the application role.

The same statements as the migration, idempotent. For the day a migration is
run by a different owner than the one whose default privileges cover new
tables (``rls_status`` then reports the missing grants), or after the role
was recreated or a database restored. Runs with the owner's credentials —
any other role would "succeed" while granting nothing — and verifies the
result. Exit status 1 when a grant is still missing.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from rls import sql, verify


class Command(BaseCommand):
    help = "Donne (à nouveau) les tables au rôle applicatif ; idempotent."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("Seul PostgreSQL a des rôles et des politiques.")
        report = verify.inspect(connection)
        if not report.maintenance_side:
            raise CommandError(
                f"Le rôle {report.role.name} ne possède pas les tables : lance rls_grant "
                "avec les identifiants du propriétaire."
            )
        role = sql.role_name(settings.RLS_APP_ROLE)
        with connection.cursor() as cursor:
            for statement in sql.application_role_statements(role):
                cursor.execute(statement)
        remaining = [p for p in verify.problems(connection, runtime=False) if role in p]
        if remaining:
            for text in remaining:
                self.stdout.write(self.style.ERROR(f"✗ {text}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(f"Droits posés pour {role}."))
