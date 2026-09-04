"""Migration operations: the policies and the role, as frozen schema changes.

Explicit arguments rather than a lookup in ``rls.registry``: a migration
already applied must keep meaning the same thing when the registry evolves.
Every operation is a no-op on an engine without row-level security (SQLite in
development) and reversible on PostgreSQL.
"""

from __future__ import annotations

from django.conf import settings
from django.db.migrations.operations.base import Operation

from rls import sql


def _split(label: str) -> tuple[str, str]:
    app_label, _, model = label.partition(".")
    return app_label, model


class EnableRowLevelSecurity(Operation):
    """Enable RLS on a model's table and install its ``tenant_isolation`` policy.

    ``owner`` names the field holding the account id. ``via`` names a foreign
    key to a parent model and ``parent_owner`` the parent's field: the row is
    visible when its parent is. ``unbound_visible`` makes every row visible to
    a session with no account bound (the user table, for sign-in).
    """

    reversible = True
    reduces_to_sql = True

    def __init__(
        self,
        model: str,
        *,
        owner: str | None = None,
        via: str | None = None,
        parent_owner: str = "owner",
        unbound_visible: bool = False,
    ):
        if (owner is None) == (via is None):
            raise ValueError(f"{model} : donne soit owner, soit via.")
        self.model = model
        self.owner = owner
        self.via = via
        self.parent_owner = parent_owner
        self.unbound_visible = unbound_visible

    def deconstruct(self):
        kwargs: dict[str, object] = {"unbound_visible": self.unbound_visible}
        if self.owner is not None:
            kwargs["owner"] = self.owner
        else:
            kwargs["via"] = self.via
            kwargs["parent_owner"] = self.parent_owner
        return self.__class__.__name__, [self.model], kwargs

    def state_forwards(self, app_label, state):
        pass

    def predicate(self, apps) -> tuple[str, str]:
        """``(table, predicate)`` for the model as it exists in ``apps``."""
        model = apps.get_model(*_split(self.model))
        table = model._meta.db_table
        if self.owner is not None:
            column = model._meta.get_field(self.owner).column
            return table, sql.owner_predicate(column)
        assert self.via is not None
        field = model._meta.get_field(self.via)
        parent = field.related_model
        return table, sql.via_predicate(
            field.column,
            parent._meta.db_table,
            parent._meta.pk.column,
            parent._meta.get_field(self.parent_owner).column,
        )

    def statements(self, apps) -> list[str]:
        if self.unbound_visible:
            model = apps.get_model(*_split(self.model))
            assert self.owner is not None
            return sql.enable_unbound_visible_statements(
                model._meta.db_table, model._meta.get_field(self.owner).column
            )
        table, predicate = self.predicate(apps)
        return sql.enable_statements(table, predicate)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        for statement in self.statements(to_state.apps):
            schema_editor.execute(statement, params=None)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        model = from_state.apps.get_model(*_split(self.model))
        for statement in sql.disable_statements(model._meta.db_table):
            schema_editor.execute(statement, params=None)

    def describe(self):
        return f"Enable row-level security on {self.model}"

    @property
    def migration_name_fragment(self):
        return f"rls_{self.model.replace('.', '_').lower()}"


class EnsureApplicationRole(Operation):
    """Create the runtime role (``settings.RLS_APP_ROLE``) if missing, grant it.

    Read from settings at migration time like ``AUTH_USER_MODEL`` is: the
    role is deployment configuration, not code. Reversing revokes the grants
    and keeps the role, which may carry a password.
    """

    reversible = True
    reduces_to_sql = True

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        for statement in sql.application_role_statements(settings.RLS_APP_ROLE):
            schema_editor.execute(statement, params=None)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        for statement in sql.revoke_application_role_statements(settings.RLS_APP_ROLE):
            schema_editor.execute(statement, params=None)

    def describe(self):
        return "Ensure the application role exists and is granted the tables"

    @property
    def migration_name_fragment(self):
        return "rls_application_role"
