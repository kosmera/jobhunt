"""Read-only CSV of launch opt-ins; PostgreSQL requires the maintenance connection.

The web application's RLS account scope also applies in Django administration.
This command never changes roles or policies: an operator must supply the
existing maintenance credentials to export all profiles on PostgreSQL.
"""

import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from accounts.models import LaunchPlan, Profile
from rls import verify


def spreadsheet_cell(value):
    text = str(value)
    if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


class Command(BaseCommand):
    help = "Exporte en CSV les accords de contact au lancement ; connexion de maintenance requise sur PostgreSQL."
    requires_system_checks = []

    def handle(self, *args, **options):
        if connection.vendor == "postgresql":
            report = verify.inspect(connection)
            if not (report.role.superuser or report.role.bypass_rls or Profile._meta.db_table in report.owned):
                raise CommandError(
                    "L'export global nécessite les identifiants de maintenance du propriétaire des profils. "
                    "Le rôle applicatif reste limité par les politiques RLS."
                )
        profiles = Profile.objects.filter(
            launch_consent_at__isnull=False, launch_plan__in=LaunchPlan.values, user__is_active=True,
        ).exclude(launch_email="").order_by("user_id")
        writer = csv.writer(self.stdout, lineterminator="\n")
        writer.writerow(["user_id", "email", "plan", "consent_at"])
        for user_id, email, plan, consent_at in profiles.values_list(
            "user_id", "launch_email", "launch_plan", "launch_consent_at",
        ).iterator():
            writer.writerow([spreadsheet_cell(value) for value in (user_id, email, plan, consent_at.isoformat())])
