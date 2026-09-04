"""What the database actually enforces, read from the catalogue.

Shared by the runtime verification (first request), the configuration checks
(``manage.py check``) and ``manage.py rls_status``. Everything here runs as
whatever role the connection has: the report says which side that role is on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings

from rls import registry, sql
from rls.context import GUC

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")


@dataclass
class RoleReport:
    name: str
    superuser: bool
    bypass_rls: bool
    create_role: bool
    create_db: bool
    #: The configured application role.
    app_role: str
    app_role_exists: bool
    #: Whether this connection may ``SET ROLE`` to the application role.
    can_set_app_role: bool
    #: The connection IS the application role (or inherits it): the runtime side.
    is_app_role: bool
    #: What ``current_setting`` returns outside any transaction: must be NULL or ''.
    session_value: str | None
    #: A role- or database-level default for the setting exists.
    preset: bool
    #: The connection's role owns (or is a member of the owner of) the current database.
    owns_database: bool


@dataclass
class TableReport:
    label: str
    table: str
    exists: bool = False
    owner: str = ""
    rls_enabled: bool = False
    policy: str | None = None
    #: Privileges the connection's role lacks on the table.
    missing_privileges: tuple[str, ...] = ()
    #: Privileges the application role lacks (when it exists).
    app_missing_privileges: tuple[str, ...] = ()


@dataclass
class Report:
    role: RoleReport
    tables: list[TableReport] = field(default_factory=list)
    #: Protected tables the connection's role owns (or administers): it bypasses their policies.
    owned: list[str] = field(default_factory=list)
    #: Every table of the schema the connection's role owns.
    owned_any: list[str] = field(default_factory=list)
    #: Tables the application role can read that carry no policy, outside the allow-list.
    unprotected: list[str] = field(default_factory=list)
    #: Bookkeeping tables the application role can write.
    writable_bookkeeping: list[str] = field(default_factory=list)

    @property
    def maintenance_side(self) -> bool:
        """The role bypasses RLS by nature (superuser, BYPASSRLS, owner of a
        protected table) — or, before the first migration, the role that
        administers the still-empty database (the Azure admin): it is about
        to become the owner. The runtime role owns nothing, ever."""
        if self.role.superuser or self.role.bypass_rls or self.owned:
            return True
        pending = not any(table.exists for table in self.tables)
        return pending and (self.role.owns_database or self.role.create_role or self.role.create_db)

    @property
    def owners(self) -> set[str]:
        return {t.owner for t in self.tables if t.exists}


def _missing(cursor, grantee: str | None, table: str) -> tuple[str, ...]:
    if grantee is None:
        cursor.execute(
            "SELECT " + ", ".join("has_table_privilege(%s, %s)" for _ in PRIVILEGES),
            [value for privilege in PRIVILEGES for value in (table, privilege)],
        )
    else:
        cursor.execute(
            "SELECT " + ", ".join("has_table_privilege(%s, %s, %s)" for _ in PRIVILEGES),
            [value for privilege in PRIVILEGES for value in (grantee, table, privilege)],
        )
    granted = cursor.fetchone()
    return tuple(privilege for privilege, ok in zip(PRIVILEGES, granted) if not ok)


def inspect(connection) -> Report:
    app_role = sql.role_name(settings.RLS_APP_ROLE)
    in_transaction = connection.in_atomic_block
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT current_user,
                   r.rolsuper,
                   r.rolbypassrls,
                   r.rolcreaterole,
                   r.rolcreatedb,
                   EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s),
                   current_setting('server_version_num')::int >= 160000,
                   current_setting(%s, true),
                   EXISTS (SELECT 1 FROM pg_catalog.pg_db_role_setting s
                            WHERE EXISTS (SELECT 1 FROM unnest(s.setconfig) c WHERE c LIKE %s)),
                   pg_has_role(current_user,
                               (SELECT d.datdba FROM pg_catalog.pg_database d WHERE d.datname = current_database()),
                               'USAGE')
              FROM pg_catalog.pg_roles r
             WHERE r.rolname = current_user
            """,
            [app_role, GUC, GUC + "=%"],
        )
        (name, superuser, bypass, createrole, createdb, app_exists, modern, value, preset,
         owns_database) = cursor.fetchone()
        can_set = False
        is_app = name == app_role
        if app_exists:
            cursor.execute(
                "SELECT pg_has_role(current_user, %s, %s), pg_has_role(current_user, %s, 'USAGE')",
                [app_role, "SET" if modern else "MEMBER", app_role],
            )
            can_set, inherits = cursor.fetchone()
            # ``USAGE`` follows INHERIT: an Entra identity granted the role
            # is the runtime side; the owner's SET-only membership is not.
            is_app = is_app or (inherits and not superuser)
        role = RoleReport(
            name, superuser, bypass, createrole, createdb, app_role, app_exists, can_set, is_app,
            # Inside a transaction the value is the one being announced, not the session's.
            None if in_transaction else value,
            preset,
            bool(owns_database),
        )
        report = Report(role)
        grantee = app_role if app_exists else None
        for rule in registry.rules():
            table = TableReport(rule.label, rule.table)
            cursor.execute(
                """
                SELECT pg_get_userbyid(c.relowner),
                       pg_has_role(current_user, c.relowner, 'USAGE'),
                       c.relrowsecurity,
                       (SELECT pg_get_expr(p.polqual, p.polrelid)
                          FROM pg_catalog.pg_policy p
                         WHERE p.polrelid = c.oid AND p.polname = %s)
                  FROM pg_catalog.pg_class c
                  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                 WHERE c.relname = %s AND n.nspname = ANY (current_schemas(false))
                """,
                [sql.POLICY, rule.table],
            )
            row = cursor.fetchone()
            if row is not None:
                table.exists = True
                table.owner, owns, table.rls_enabled, table.policy = row
                if owns:
                    report.owned.append(rule.table)
                table.missing_privileges = _missing(cursor, None, rule.table)
                if app_exists:
                    table.app_missing_privileges = _missing(cursor, app_role, rule.table)
            report.tables.append(table)
        # The rest of the schema: what the application role can read without
        # a policy, what the connection's role owns, and what it may write
        # that it should not.
        cursor.execute(
            """
            SELECT c.relname,
                   c.relrowsecurity,
                   pg_has_role(current_user, c.relowner, 'USAGE'),
                   CASE WHEN %s IS NULL THEN has_table_privilege(c.oid, 'SELECT')
                        ELSE has_table_privilege(%s, c.oid, 'SELECT') END,
                   CASE WHEN %s IS NULL THEN has_table_privilege(c.oid, 'INSERT')
                        ELSE has_table_privilege(%s, c.oid, 'INSERT') END
              FROM pg_catalog.pg_class c
              JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind IN ('r', 'p') AND n.nspname = ANY (current_schemas(false))
             ORDER BY c.relname
            """,
            [grantee, grantee, grantee, grantee],
        )
        registered = {t.table for t in report.tables}
        for relname, rls_on, owns, readable, writable in cursor.fetchall():
            if owns:
                report.owned_any.append(relname)
            if relname in sql.BOOKKEEPING_TABLES and writable:
                report.writable_bookkeeping.append(relname)
            if not rls_on and readable and relname not in registered and relname not in sql.UNPROTECTED_ALLOWED:
                report.unprotected.append(relname)
    return report


def problems(connection, *, runtime: bool = True) -> list[str]:
    """Human-readable problems. ``runtime``: the role must be the restricted one.

    With ``runtime=False`` (``migrate``, ``check`` run by the owner) the role
    is allowed to bypass the policies; the tables and the application role's
    grants are examined instead. A table that does not exist yet is not a
    problem (its migration is pending).
    """
    if connection.vendor != "postgresql":
        return []
    report = inspect(connection)
    found: list[str] = []
    role = report.role
    if runtime:
        if role.superuser:
            found.append(f"le rôle {role.name} est superutilisateur (il ignore les politiques)")
        if role.bypass_rls:
            found.append(f"le rôle {role.name} a BYPASSRLS")
        if role.create_role or role.create_db:
            found.append(f"le rôle {role.name} a CREATEROLE/CREATEDB : un rôle applicatif n'administre rien")
        if report.owned_any:
            found.append(
                f"le rôle {role.name} possède {', '.join(report.owned_any)} "
                "(un propriétaire ignore les politiques de ses tables ; migre avec un autre rôle)"
            )
        if role.session_value:
            found.append(
                f"{GUC} vaut « {role.session_value} » hors transaction : un réglage de session "
                "(options de connexion, SET) lierait chaque visiteur anonyme à ce compte"
            )
    elif not role.app_role_exists:
        found.append(f"le rôle applicatif {role.app_role} n'existe pas (migration rls non appliquée ?)")
    if role.preset:
        found.append(f"{GUC} a une valeur par défaut (ALTER ROLE/DATABASE ... SET) : à retirer")
    if not runtime and len(report.owners) > 1:
        found.append(
            "les tables protégées ont plusieurs propriétaires ("
            + ", ".join(sorted(report.owners))
            + ") : un seul rôle doit migrer (voir « Un propriétaire de groupe » dans le README)"
        )
    for table in report.tables:
        if not table.exists:
            if runtime:
                found.append(f"table {table.table} absente ({table.label})")
            continue
        if not table.rls_enabled:
            found.append(f"RLS désactivé sur {table.table}")
        if table.policy is None:
            found.append(f"politique {sql.POLICY} absente sur {table.table}")
        if runtime and table.missing_privileges:
            found.append(
                f"privilèges manquants sur {table.table} : {', '.join(table.missing_privileges)}"
            )
        if not runtime and table.app_missing_privileges:
            found.append(
                f"privilèges manquants pour {role.app_role} sur {table.table} : "
                f"{', '.join(table.app_missing_privileges)} (manage.py rls_grant)"
            )
    for table in report.unprotected:
        found.append(
            f"table {table} lisible par le rôle applicatif sans politique : "
            "rls.register(...) + EnableRowLevelSecurity, ou rls.exempt(...)"
        )
    for table in report.writable_bookkeeping:
        found.append(f"le rôle applicatif peut écrire {table} (manage.py rls_grant retire ce droit)")
    return found
