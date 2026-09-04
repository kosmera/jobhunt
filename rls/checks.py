"""Configuration checks.

- ``rls.E001``–``E003`` (plain ``rls`` tag, run by every command): the
  middleware is present and in the right place, the PostgreSQL engine is the
  one that announces the account (and autocommit is left to Django), every
  registered rule names real fields.
- ``rls.E005``: ``rls`` must precede ``django.contrib.auth`` in
  ``INSTALLED_APPS`` so that a sign-in rebinds the request *before*
  ``update_last_login`` writes the user row.
- ``rls.W001``: a model holds an account's data — a relation to the user
  model, or to a model that carries a policy — but is neither registered nor
  exempted: its rows would be visible to every account.
- ``rls.W002`` / ``E004`` (``database`` tag: ``migrate``, ``check --database``,
  the test runner): what the connected database really enforces — an Error
  when connected as the application role, a Warning for any other role
  (the owner running its first ``migrate`` on an empty database included).
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register
from django.core.exceptions import FieldDoesNotExist
from django.db import connections

from rls import registry, verify

MIDDLEWARE = "rls.middleware.RowLevelSecurityMiddleware"
AUTHENTICATION = "django.contrib.auth.middleware.AuthenticationMiddleware"
ENGINE = "rls.backends.postgresql"
#: Django's own request-level middleware, which touch no protected table.
DJANGO_MIDDLEWARE_PREFIX = "django."


@register("rls")
def check_configuration(app_configs, **kwargs):
    problems = []
    middleware = list(settings.MIDDLEWARE)
    if MIDDLEWARE not in middleware:
        problems.append(Error(f"{MIDDLEWARE} manque dans MIDDLEWARE.", id="rls.E001"))
    else:
        position = middleware.index(MIDDLEWARE)
        if AUTHENTICATION in middleware and middleware.index(AUTHENTICATION) > position:
            problems.append(
                Error(f"{MIDDLEWARE} doit suivre {AUTHENTICATION}.", id="rls.E002")
            )
        # A middleware placed before ours runs its own __call__ pre-processing
        # and process_response outside the request's transaction: only
        # Django's, which never read a protected table, may sit there.
        foreign = [m for m in middleware[:position] if not m.startswith(DJANGO_MIDDLEWARE_PREFIX)]
        if foreign:
            problems.append(
                Error(
                    f"{MIDDLEWARE} doit précéder {', '.join(foreign)} : un middleware placé "
                    "avant lui travaille hors de la transaction liée au compte.",
                    id="rls.E002",
                )
            )
    default = settings.DATABASES["default"]
    engine = default["ENGINE"]
    if engine.startswith("django.db.backends.postgresql"):
        problems.append(
            Error(
                f"ENGINE vaut {engine} : utilise {ENGINE}, qui annonce le compte à chaque transaction.",
                id="rls.E003",
            )
        )
    if engine == ENGINE and default.get("AUTOCOMMIT", True) is False:
        problems.append(
            Error(
                "DATABASES['default']['AUTOCOMMIT'] = False : atomic() n'ouvrirait plus les "
                "transactions et le compte ne serait plus annoncé au serveur.",
                id="rls.E003",
            )
        )
    installed = list(settings.INSTALLED_APPS)
    if "rls" in installed and "django.contrib.auth" in installed:
        if installed.index("rls") > installed.index("django.contrib.auth"):
            problems.append(
                Error(
                    "« rls » doit précéder django.contrib.auth dans INSTALLED_APPS : une "
                    "connexion doit relier la requête au compte avant que update_last_login "
                    "n'écrive sa ligne.",
                    id="rls.E005",
                )
            )
    for rule in registry.rules():
        try:
            model = rule.model
            if rule.owner is not None:
                model._meta.get_field(rule.owner)
            else:
                assert rule.via is not None
                field = model._meta.get_field(rule.via)
                parent = registry.rule_for(field.related_model)
                if parent is None or parent.owner is None:
                    problems.append(
                        Error(
                            f"{rule.label} : via={rule.via!r} pointe sur un modèle sans règle owner.",
                            id="rls.E003",
                        )
                    )
        except (LookupError, FieldDoesNotExist) as exc:
            problems.append(Error(f"Règle RLS invalide pour {rule.label} : {exc}", id="rls.E003"))
    return problems


def _relates_to_protected(model, user_model) -> str | None:
    """The related model that makes ``model`` hold an account's data, if any."""
    for field in model._meta.get_fields(include_hidden=True):
        if not getattr(field, "concrete", False) or not field.is_relation:
            continue
        target = field.related_model
        if target is user_model:
            return user_model._meta.label
        if target is not None and target is not model and registry.rule_for(target) is not None:
            return target._meta.label
    return None


@register("rls")
def check_coverage(app_configs, **kwargs):
    """Every model that holds an account's data has a rule, or opted out.

    Holding an account's data means a relation to the user model, or to a
    model that itself carries a policy (a satellite such as an event or a
    contact — the shape ``via=`` exists for).
    """
    user_model = apps.get_model(settings.AUTH_USER_MODEL)
    problems = []
    for model in apps.get_models(include_auto_created=True):
        if registry.rule_for(model) is not None or registry.is_exempt(model):
            continue
        through = _relates_to_protected(model, user_model)
        if through is not None:
            problems.append(
                Warning(
                    f"{model._meta.label} référence {through} sans règle RLS : "
                    "rls.register(...) dans ready() (owner=... ou via=...), ou rls.exempt(...) "
                    "si c'est voulu ; en production le serveur refuse de servir tant qu'une "
                    "table lisible reste sans politique.",
                    id="rls.W001",
                )
            )
    return problems


@register(Tags.database)
def check_database(app_configs, databases=None, **kwargs):
    problems = []
    for alias in databases or ():
        connection = connections[alias]
        if connection.vendor != "postgresql":
            continue
        report = verify.inspect(connection)
        # Errors only for the runtime role itself: any other role may be the
        # owner about to run its first ``migrate`` on an empty database, and
        # ``migrate`` runs this check first.
        runtime = report.role.is_app_role
        found = verify.problems(connection, runtime=runtime)
        for text in found:
            if runtime:
                problems.append(Error(f"[{alias}] {text}", id="rls.E004"))
            else:
                problems.append(Warning(f"[{alias}] {text}", id="rls.W002"))
    return problems
