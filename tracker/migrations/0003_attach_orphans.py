"""Hand rows recorded before accounts existed to their owner.

Nothing happens on a fresh database. On one with data, every row without an
owner goes to the single existing account, or to a password-less ``local``
account created for the purpose (see ``accounts.migration_helpers``); the
first visit in local mode then completes that account's profile.
"""

from django.db import migrations

OWNED_MODELS = ["Company", "Platform", "SkillGap", "Application", "Document"]


def attach_orphans(apps, schema_editor):
    from accounts.migration_helpers import owner_for_orphans

    orphans = {
        name: apps.get_model("tracker", name).objects.filter(owner__isnull=True)
        for name in OWNED_MODELS
    }
    if not any(queryset.exists() for queryset in orphans.values()):
        return
    owner = owner_for_orphans(apps)
    for queryset in orphans.values():
        queryset.update(owner=owner)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("tracker", "0002_owner_nullable"),
    ]

    operations = [
        migrations.RunPython(attach_orphans, migrations.RunPython.noop),
    ]
