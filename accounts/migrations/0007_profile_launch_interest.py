from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0006_profile_subscription_level_and_more")]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="launch_email",
            field=models.EmailField(blank=True, default="", max_length=254, verbose_name="e-mail pour le lancement"),
        ),
        migrations.AddField(
            model_name="profile",
            name="launch_plan",
            field=models.CharField(
                blank=True,
                choices=[("free", "Gratuit"), ("premium", "Premium")],
                default="",
                help_text="Un intérêt déclaré pour le lancement, sans abonnement ni accès Premium accordé.",
                max_length=12,
                verbose_name="offre qui m'intéresse",
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="launch_consent_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="accord de contact au lancement le"),
        ),
    ]
