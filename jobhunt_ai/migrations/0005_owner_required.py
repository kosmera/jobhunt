import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def owner(related_name):
    return models.ForeignKey(
        on_delete=django.db.models.deletion.CASCADE,
        related_name=related_name,
        to=settings.AUTH_USER_MODEL,
        verbose_name="propriétaire",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("jobhunt_ai", "0004_attach_orphans"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(model_name="candidateprofile", name="owner", field=owner("ai_candidate_profiles")),
        migrations.AlterField(model_name="agentrun", name="owner", field=owner("ai_agent_runs")),
        migrations.AlterField(model_name="offerlead", name="owner", field=owner("ai_offer_leads")),
    ]
