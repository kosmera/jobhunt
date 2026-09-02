"""``runserver`` that keeps the development database in step with the code.

Django's own ``runserver`` only *warns* about unapplied migrations. Here, when
``settings.AUTO_MIGRATE`` is on (the default in development), the command
first writes the migrations for model changes that have none, then applies
everything pending — before the server starts, and again on every reload, so
a migration that just arrived with a ``git pull`` or the installation of an
extension is picked up without a restart.

Migrations are only generated when Django would not have to ask a question.
A possible rename or a mandatory field without default needs a human answer
(the wrong one drops data): those cases stop the generation with a message,
and ``manage.py makemigrations`` is yours to run.

Outside development the command behaves exactly like Django's.

Lives in ``tracker`` because Django resolves a command to the *first* app in
``INSTALLED_APPS`` that defines it: ``tracker`` is listed before
``django.contrib.staticfiles``, whose ``runserver`` this one extends.
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings
from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticfilesRunserver,
)
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
from django.db.migrations.state import ProjectState
from django.utils import translation


class NeedsHuman(Exception):
    """The change detector would have to ask something only you can answer."""


class GuardedQuestioner(NonInteractiveMigrationQuestioner):
    """Answers nothing by itself: every real question aborts the generation."""

    def ask_rename(self, model_name, old_name, new_name, field_instance):
        raise NeedsHuman(f"renommage possible de {model_name}.{old_name} en {new_name}")

    def ask_rename_model(self, old_model_state, new_model_state):
        raise NeedsHuman(
            f"renommage possible du modèle {old_model_state.name} en {new_model_state.name}"
        )

    def ask_not_null_addition(self, field_name, model_name):
        raise NeedsHuman(f"{model_name}.{field_name} est obligatoire sans valeur par défaut")

    def ask_not_null_alteration(self, field_name, model_name):
        raise NeedsHuman(
            f"{model_name}.{field_name} devient obligatoire sans valeur par défaut"
        )

    def ask_auto_now_add_addition(self, field_name, model_name):
        raise NeedsHuman(f"{model_name}.{field_name} est un auto_now_add sans valeur initiale")

    def ask_unique_callable_default_addition(self, field_name, model_name):
        raise NeedsHuman(f"{model_name}.{field_name} a une valeur par défaut unique calculée")


class Command(StaticfilesRunserver):
    def check_migrations(self):
        if not settings.AUTO_MIGRATE:
            return super().check_migrations()
        # Like ``makemigrations`` and ``migrate`` themselves, run without
        # translations: the change detector compares verbose names with the
        # English strings frozen in Django's own migrations, and a French
        # server would otherwise see every contrib app as changed.
        with translation.override(None):
            self.make_pending_migrations()
            self.apply_pending_migrations()

    def make_pending_migrations(self) -> None:
        loader = MigrationLoader(None, ignore_no_migrations=True)
        autodetector = MigrationAutodetector(
            loader.project_state(), ProjectState.from_apps(apps), GuardedQuestioner()
        )
        try:
            changes = autodetector.changes(graph=loader.graph)
        except NeedsHuman as question:
            self.stdout.write(
                self.style.WARNING(
                    f"Des modèles ont changé, mais Django doit te poser une question "
                    f"({question}) : lance « manage.py makemigrations » toi-même."
                )
            )
            return
        if not changes:
            return
        labels = sorted(changes)
        self.stdout.write(
            self.style.NOTICE(
                f"Modèles modifiés sans migration ({', '.join(labels)}) : génération…"
            )
        )
        call_command("makemigrations", *labels, interactive=False, verbosity=1)

    def apply_pending_migrations(self) -> None:
        try:
            executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
        except ImproperlyConfigured:
            return
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if not plan:
            return
        labels = ", ".join(sorted({migration.app_label for migration, _ in plan}))
        self.stdout.write(
            self.style.NOTICE(f"{len(plan)} migration(s) en attente ({labels}) : application…")
        )
        call_command("migrate", interactive=False, verbosity=1)
