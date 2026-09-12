"""Pose les politiques de lignes du copilote (voir ``rls.operations``).

Sur SQLite l'opération ne fait rien ; sur PostgreSQL elle installe la
politique ``tenant_isolation`` de chaque table du copilote.
"""

from django.db import migrations

from rls.operations import EnableRowLevelSecurity


class Migration(migrations.Migration):

    dependencies = [
        ("jobhunt_ai", "0005_owner_required"),
        # Le rôle applicatif et ses droits existent avant qu'on pose les
        # politiques qui le contraignent.
        ("rls", "0001_initial"),
    ]

    operations = [
        # Racines : profils, exécutions et pistes portent leur compte.
        EnableRowLevelSecurity("jobhunt_ai.CandidateProfile", owner="owner"),
        EnableRowLevelSecurity("jobhunt_ai.AgentRun", owner="owner"),
        EnableRowLevelSecurity("jobhunt_ai.OfferLead", owner="owner"),
        # Résultats attachés à une candidature : visibles quand elle l'est.
        EnableRowLevelSecurity("jobhunt_ai.MatchReport", via="application", parent_owner="owner"),
        EnableRowLevelSecurity("jobhunt_ai.GeneratedCV", via="application", parent_owner="owner"),
    ]
