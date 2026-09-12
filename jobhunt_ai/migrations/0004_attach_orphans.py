"""Rattache les lignes d'avant les comptes à un propriétaire, s'il en reste."""

from django.db import migrations


def attach_orphans(apps, schema_editor):
    """Historical account migration, using frozen models and the migration DB."""
    from accounts.migration_helpers import owner_for_orphans

    alias = schema_editor.connection.alias
    AgentRun = apps.get_model("jobhunt_ai", "AgentRun")
    models = [
        apps.get_model("jobhunt_ai", name)
        for name in ("CandidateProfile", "AgentRun", "OfferLead")
    ]
    if not any(
        model.objects.using(alias).filter(owner__isnull=True).exists()
        for model in models
    ):
        return
    for run in (
        AgentRun.objects.using(alias)
        .filter(
            owner__isnull=True,
            application__isnull=False,
        )
        .select_related("application")
    ):
        run.owner_id = run.application.owner_id
        run.save(using=alias, update_fields=["owner"])
    orphans = [
        model.objects.using(alias).filter(owner__isnull=True) for model in models
    ]
    if any(queryset.exists() for queryset in orphans):
        owner = owner_for_orphans(apps)
        for queryset in orphans:
            queryset.update(owner=owner)


class Migration(migrations.Migration):
    dependencies = [("jobhunt_ai", "0003_owner_nullable")]
    operations = [migrations.RunPython(attach_orphans, migrations.RunPython.noop)]
