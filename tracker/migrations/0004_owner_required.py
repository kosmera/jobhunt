"""Every root row now has an owner (0003 gave one to the orphans)."""

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
        ("tracker", "0003_attach_orphans"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(model_name="application", name="owner", field=owner("applications")),
        migrations.AlterField(model_name="company", name="owner", field=owner("companies")),
        migrations.AlterField(model_name="document", name="owner", field=owner("documents")),
        migrations.AlterField(model_name="platform", name="owner", field=owner("platforms")),
        migrations.AlterField(model_name="skillgap", name="owner", field=owner("skill_gaps")),
    ]
