"""``manage.py rls_status``: what the connected database enforces, table by table.

Run it with the runtime credentials after a deployment, and with the owner's
after a migration. ``--probe`` also switches to the application role (``SET
LOCAL ROLE``, so the caller must be allowed to) and counts, for every
protected table, the rows a session with nobody bound can see: the answer
must be 0 everywhere except the tables declared ``unbound_visible``. Exit
status 1 when something is wrong.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection, transaction

from rls import context, registry, sql, verify


class Command(BaseCommand):
    help = "Vérifie l'isolation des données (RLS) sur la base connectée."

    def add_arguments(self, parser):
        parser.add_argument(
            "--probe",
            action="store_true",
            help="Passe au rôle applicatif et compte ce qu'une session sans compte voit.",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write(
                f"Moteur {connection.vendor} : pas de RLS (les politiques ne valent que sur PostgreSQL)."
            )
            return
        report = verify.inspect(connection)
        role = report.role
        side = "maintenance (ignore les politiques)" if report.maintenance_side else "applicatif"
        self.stdout.write(
            f"Rôle {role.name} — {side} ; superuser={role.superuser} bypassrls={role.bypass_rls}"
        )
        self.stdout.write(
            f"Rôle applicatif {role.app_role} : "
            + ("existe" if role.app_role_exists else "ABSENT")
            + (", SET ROLE possible depuis cette connexion" if role.can_set_app_role else "")
        )
        self.stdout.write("")
        width = max((len(t.table) for t in report.tables), default=10)
        for table in report.tables:
            if not table.exists:
                self.stdout.write(f"  {table.table:<{width}}  ABSENTE")
                continue
            flags = [
                "RLS" if table.rls_enabled else "rls OFF",
                "politique" if table.policy else "SANS POLITIQUE",
                f"propriétaire={table.owner}",
            ]
            if table.missing_privileges:
                flags.append("manque " + ",".join(table.missing_privileges))
            if table.app_missing_privileges:
                flags.append(f"manque pour {role.app_role} " + ",".join(table.app_missing_privileges))
            self.stdout.write(f"  {table.table:<{width}}  {'  '.join(flags)}")

        problems = verify.problems(connection, runtime=not report.maintenance_side)
        if options["probe"]:
            if role.can_set_app_role or not report.maintenance_side:
                problems.extend(self.probe(report))
            else:
                problems.append(
                    f"sonde impossible : {role.name} ne peut pas prendre le rôle {role.app_role}"
                )
        self.stdout.write("")
        if problems:
            for text in problems:
                self.stdout.write(self.style.ERROR(f"✗ {text}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("✓ Isolation en place."))

    def probe(self, report: verify.Report) -> list[str]:
        role = report.role.app_role
        found: list[str] = []
        self.stdout.write("")
        self.stdout.write(f"Sonde : SET LOCAL ROLE {role}, aucun compte lié")
        with transaction.atomic():
            # ``''`` rather than never-set: the state of every reused connection.
            context.apply(connection, None)
            with connection.cursor() as cursor:
                if report.maintenance_side:
                    cursor.execute(f"SET LOCAL ROLE {role}")
                for rule in registry.rules():
                    try:
                        with transaction.atomic():
                            cursor.execute(f"SELECT count(*) FROM {sql.quote(rule.table)}")
                            (count,) = cursor.fetchone()
                    except DatabaseError as exc:
                        self.stdout.write(f"  {rule.table}: ERREUR — {str(exc).strip().splitlines()[0]}")
                        found.append(f"{rule.table} : lecture impossible avec le rôle applicatif ({exc})")
                        continue
                    verdict = "ok" if count == 0 or rule.unbound_visible else "FUITE"
                    self.stdout.write(f"  {rule.table}: {count} ligne(s) visibles — {verdict}")
                    if verdict == "FUITE":
                        found.append(f"{rule.table} : {count} ligne(s) visibles sans compte lié")
        return found
