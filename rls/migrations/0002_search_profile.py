"""The search profile carries an account's answers: one policy, on ``user_id``."""

from django.db import migrations

from rls.operations import EnableRowLevelSecurity


class Migration(migrations.Migration):

    dependencies = [
        ("rls", "0001_initial"),
        ("accounts", "0004_searchprofile"),
    ]

    operations = [
        EnableRowLevelSecurity("accounts.SearchProfile", owner="user"),
    ]
