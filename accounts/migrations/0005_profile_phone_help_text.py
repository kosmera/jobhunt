"""Wording only: the copilot is no longer « une extension ». No schema change."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_searchprofile"),
    ]

    operations = [
        migrations.AlterField(
            model_name="profile",
            name="phone",
            field=models.CharField(
                blank=True,
                help_text="Facultatif. Sert d'en-tête aux CV que le copilote rédige, et l'anonymisation le masque partout ailleurs.",
                max_length=60,
                verbose_name="téléphone",
            ),
        ),
    ]
