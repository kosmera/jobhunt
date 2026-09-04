"""Row-level security on every table holding an account's data.

No-op on SQLite. On PostgreSQL: the application role and its grants, then a
``tenant_isolation`` policy per table. The user table stays readable by a
session with nobody bound — sign-in has to find the account first — and
narrows to the account's own row once bound.
"""

from django.conf import settings
from django.db import migrations

from rls.operations import EnableRowLevelSecurity, EnsureApplicationRole


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("admin", "0003_logentry_add_action_flag_choices"),
        ("accounts", "0001_initial"),
        ("tracker", "0004_owner_required"),
    ]

    operations = [
        EnsureApplicationRole(),
        # Django's own tables.
        EnableRowLevelSecurity(settings.AUTH_USER_MODEL, owner="id", unbound_visible=True),
        EnableRowLevelSecurity("auth.User_groups", owner="user"),
        EnableRowLevelSecurity("auth.User_user_permissions", owner="user"),
        EnableRowLevelSecurity("admin.LogEntry", owner="user"),
        # Accounts.
        EnableRowLevelSecurity("accounts.Profile", owner="user"),
        EnableRowLevelSecurity("accounts.Preferences", owner="user"),
        # The tracker: root rows by their owner, satellites through their application.
        EnableRowLevelSecurity("tracker.Company", owner="owner"),
        EnableRowLevelSecurity("tracker.Platform", owner="owner"),
        EnableRowLevelSecurity("tracker.SkillGap", owner="owner"),
        EnableRowLevelSecurity("tracker.Application", owner="owner"),
        EnableRowLevelSecurity("tracker.Document", owner="owner"),
        EnableRowLevelSecurity("tracker.ActivityEvent", via="application", parent_owner="owner"),
        EnableRowLevelSecurity("tracker.Contact", via="application", parent_owner="owner"),
    ]
