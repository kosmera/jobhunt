"""The DDL behind a policy, and behind the application role.

Pure functions of names: no Django state, so a migration and the verification
command produce exactly the same text. No percent sign anywhere in a
statement: the schema editor hands them to the driver without parameters.
"""

from __future__ import annotations

import re

from rls.context import GUC

POLICY = "tenant_isolation"
#: The per-command policies of an ``unbound_visible`` table, ``POLICY`` first.
UNBOUND_VISIBLE_POLICIES = (POLICY, f"{POLICY}_insert", f"{POLICY}_update", f"{POLICY}_delete")

#: Django's bookkeeping. Readable at runtime, written by ``migrate`` only:
#: the application role gets no write privilege on them, so a compromised
#: web process cannot rewrite the migration ledger or the permission tables.
BOOKKEEPING_TABLES = (
    "django_migrations",
    "django_content_type",
    "auth_permission",
    "auth_group",
    "auth_group_permissions",
)

#: Tables that legitimately carry no policy: none holds a person's data.
UNPROTECTED_ALLOWED = BOOKKEEPING_TABLES + ("django_session",)

#: The account bound to the transaction, as PostgreSQL sees it. ``NULL`` when
#: nothing was ever set on the session, ``''`` once a previous transaction's
#: setting has expired — hence the ``NULLIF``, without which the cast would
#: raise on every unbound query of a reused connection. Written inline rather
#: than wrapped in a function: the plan is the same (an index scan on the
#: owner column) and a function would have to be declared ``PARALLEL SAFE``
#: or ban parallel plans on every protected table.
TENANT = f"NULLIF(current_setting('{GUC}', true), '')::bigint"

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def quote(name: str) -> str:
    if '"' in name:
        raise ValueError(f"Identifiant invalide : {name!r}")
    return f'"{name}"'


def role_name(name: str) -> str:
    """A role name safe to splice into DDL (no quoting needed, none allowed).

    An Entra ID identity (``id-jobhunt-web``, ``someone@tenant``) does not
    fit: it is granted membership of this role and connects itself.
    """
    if not _IDENTIFIER.match(name):
        raise ValueError(
            f"JOBHUNT_DB_APP_ROLE vaut « {name} » ; attendu : lettres minuscules, chiffres et _ "
            "(une identité Entra ID reçoit ce rôle par GRANT, elle ne le remplace pas)."
        )
    return name


def owner_predicate(column: str) -> str:
    return f"{quote(column)} = {TENANT}"


def via_predicate(fk_column: str, parent_table: str, parent_pk: str, parent_owner: str) -> str:
    """A row is visible when its parent row is.

    ``= ANY (ARRAY(subquery))`` rather than ``EXISTS``: the planner turns an
    EXISTS inside a policy into a hashed sub-plan and scans the whole child
    table when the query itself carries no parent predicate; the array form
    keeps the index on the foreign key usable. The parent's own policy
    applies inside the sub-query too (same role), so the parent rows are
    already the account's.
    """
    return (
        f"{quote(fk_column)} = ANY (ARRAY(SELECT {quote(parent_pk)} FROM {quote(parent_table)} "
        f"WHERE {quote(parent_owner)} = {TENANT}))"
    )


def unbound_visible_predicate(column: str) -> str:
    return f"({TENANT} IS NULL OR {quote(column)} = {TENANT})"


def enable_statements(table: str, predicate: str) -> list[str]:
    return [
        f"ALTER TABLE {quote(table)} ENABLE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {quote(POLICY)} ON {quote(table)}",
        f"CREATE POLICY {quote(POLICY)} ON {quote(table)} AS PERMISSIVE FOR ALL TO PUBLIC "
        f"USING ({predicate}) WITH CHECK ({predicate})",
    ]


def enable_unbound_visible_statements(table: str, column: str) -> list[str]:
    """The account table: every row for a session bound to nobody, its own
    row once bound — except that nobody may DELETE while unbound.

    Sign-in needs SELECT before the account is known, signup needs INSERT
    before the id exists, and ``check_password`` may UPDATE the row to
    upgrade an old hash before ``login()``; no path deletes an account
    without being signed in as it.
    """
    predicate = unbound_visible_predicate(column)
    strict = owner_predicate(column)
    t = quote(table)
    names = UNBOUND_VISIBLE_POLICIES
    return [
        f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
        *[f"DROP POLICY IF EXISTS {quote(name)} ON {t}" for name in names],
        f"CREATE POLICY {quote(names[0])} ON {t} AS PERMISSIVE FOR SELECT TO PUBLIC USING ({predicate})",
        f"CREATE POLICY {quote(names[1])} ON {t} AS PERMISSIVE FOR INSERT TO PUBLIC WITH CHECK ({predicate})",
        f"CREATE POLICY {quote(names[2])} ON {t} AS PERMISSIVE FOR UPDATE TO PUBLIC "
        f"USING ({predicate}) WITH CHECK ({predicate})",
        f"CREATE POLICY {quote(names[3])} ON {t} AS PERMISSIVE FOR DELETE TO PUBLIC USING ({strict})",
    ]


def disable_statements(table: str) -> list[str]:
    t = quote(table)
    return [
        *[f"DROP POLICY IF EXISTS {quote(name)} ON {t}" for name in UNBOUND_VISIBLE_POLICIES],
        f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY",
    ]


def application_role_statements(role: str, schema: str = "public") -> list[str]:
    """Create the runtime role if missing and give it what a web process needs.

    Table privileges only — no DDL, and by construction no ownership, so the
    policies apply to it; Django's bookkeeping tables are read-only for it.
    ``ALTER DEFAULT PRIVILEGES`` (bound to the role running this, the owner
    of the tables) covers the tables that later migrations run by the same
    owner will create; a different owner must run ``manage.py rls_grant``
    afterwards. The last block lets the owner ``SET ROLE`` to the
    application role, for verification and for the tests — it only works
    when the owner administers the role (created it, or is a superuser);
    otherwise a warning is raised and the grants still stand.
    """
    role = role_name(role)
    schema = quote(schema)
    revokes = "\n".join(
        f"    IF to_regclass('{table}') IS NOT NULL THEN\n"
        f"        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE {quote(table)} FROM {role};\n"
        f"    END IF;"
        for table in BOOKKEEPING_TABLES
    )
    return [
        f"""DO $rls$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{role}') THEN
        CREATE ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
    END IF;
END
$rls$""",
        f"GRANT USAGE ON SCHEMA {schema} TO {role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT USAGE, SELECT ON SEQUENCES TO {role}",
        f"""DO $rls$
BEGIN
{revokes}
END
$rls$""",
        f"""DO $rls$
DECLARE
    modern boolean := current_setting('server_version_num')::int >= 160000;
BEGIN
    IF modern AND pg_has_role(current_user, '{role}', 'SET') THEN
        RETURN;
    END IF;
    IF NOT modern AND pg_has_role(current_user, '{role}', 'MEMBER') THEN
        RETURN;
    END IF;
    BEGIN
        IF modern THEN
            EXECUTE 'GRANT {role} TO ' || quote_ident(current_user) || ' WITH SET TRUE, INHERIT FALSE';
        ELSE
            EXECUTE 'GRANT {role} TO ' || quote_ident(current_user);
        END IF;
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE WARNING 'rls : le rôle courant ne peut pas prendre le rôle {role} (SET ROLE), il lui faudrait l''option ADMIN dessus. Les droits sur les tables sont posés.';
    END;
END
$rls$""",
    ]


def revoke_application_role_statements(role: str, schema: str = "public") -> list[str]:
    """Undo the grants; the role itself stays (it may hold a password)."""
    role = role_name(role)
    schema = quote(schema)
    return [
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} REVOKE ALL ON TABLES FROM {role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} REVOKE ALL ON SEQUENCES FROM {role}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM {role}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM {role}",
        f"REVOKE USAGE ON SCHEMA {schema} FROM {role}",
    ]
