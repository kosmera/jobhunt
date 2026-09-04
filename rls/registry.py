"""Which tables carry a policy, and on which column.

Declared in code by the app that owns the model (``AppConfig.ready``), so an
extension registers its own tables the same way. The registry drives the
configuration checks and the runtime verification; the migrations that create
the policies spell the same facts out explicitly (``rls.operations``), because
a migration must not change meaning when the registry does.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.apps import apps


@dataclass(frozen=True)
class Rule:
    #: ``app_label.ModelName``.
    label: str
    #: Field whose column holds the account id (``owner``, ``user``, ``id``).
    owner: str | None = None
    #: Or: foreign key to a parent model whose own rule gives the owner.
    via: str | None = None
    #: Rows visible to a session with no account bound (``auth_user`` only).
    unbound_visible: bool = False

    def __post_init__(self):
        if (self.owner is None) == (self.via is None):
            raise ValueError(f"{self.label} : donne soit owner, soit via.")

    @property
    def model(self):
        return apps.get_model(self.label)

    @property
    def table(self) -> str:
        return self.model._meta.db_table


_rules: dict[str, Rule] = {}
_exempt: set[str] = set()


def _normalise(label: str) -> str:
    app_label, _, model = label.partition(".")
    return f"{app_label}.{model.lower()}"


def register(label: str, *, owner: str | None = None, via: str | None = None, unbound_visible: bool = False) -> Rule:
    rule = Rule(label=label, owner=owner, via=via, unbound_visible=unbound_visible)
    _rules[_normalise(label)] = rule
    return rule


def exempt(label: str) -> None:
    """Declare that a model referencing the user model needs no policy."""
    _exempt.add(_normalise(label))


def rules() -> tuple[Rule, ...]:
    return tuple(_rules.values())


def rule_for(model) -> Rule | None:
    return _rules.get(_normalise(model._meta.label))


def is_exempt(model) -> bool:
    return _normalise(model._meta.label) in _exempt


def clear() -> None:
    """Tests only."""
    _rules.clear()
    _exempt.clear()
