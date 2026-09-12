from django.db import migrations

from rls.operations import EnableRowLevelSecurity


class Migration(migrations.Migration):
    dependencies = [
        ("jobhunt_ai", "0008_agentrun_fanout_context_agentrun_fanout_started_at_and_more"),
    ]

    operations = [
        EnableRowLevelSecurity("jobhunt_ai.ScoutTarget", via="run", parent_owner="owner"),
    ]
