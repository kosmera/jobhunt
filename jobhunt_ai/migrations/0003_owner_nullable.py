import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def owner(related_name):
    return models.ForeignKey(
        null=True,
        on_delete=django.db.models.deletion.CASCADE,
        related_name=related_name,
        to=settings.AUTH_USER_MODEL,
        verbose_name="propriétaire",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("jobhunt_ai", "0002_candidateprofile_qualifications_and_more"),
        ("tracker", "0004_owner_required"),
        ("accounts", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="candidateprofile", name="owner", field=owner("ai_candidate_profiles")),
        migrations.AddField(model_name="agentrun", name="owner", field=owner("ai_agent_runs")),
        migrations.AddField(model_name="offerlead", name="owner", field=owner("ai_offer_leads")),
    ]
