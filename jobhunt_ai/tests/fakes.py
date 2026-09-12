"""Doublures pour tester les agents sans appeler l'API ni toucher un disque.

``fake_llm`` remplace ``llm.parse_structured`` par une table schéma → réponse,
``eager_runs`` exécute les agents dans la requête au lieu d'un thread, et
``PathlessStorage`` tient le rôle d'un fournisseur distant.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta
from unittest import mock

from accounts.models import Profile
from accounts.testing import OwnedTestCase, make_user
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils import timezone
from tracker.adapters.file_storage import SignedLinkMixin
from tracker.links import DEFAULT_LINK_TTL

from jobhunt_ai.agents import schemas
from jobhunt_ai.services.llm import StructuredResult


class PathlessStorage(SignedLinkMixin, Storage):
    """Un fournisseur distant, façon Azure Blob Storage : les octets vivent
    ailleurs, donc il n'y a **pas de chemin** — ``Storage.path`` y lève
    ``NotImplementedError``.

    C'est le seul écart qui comptait pour l'extension, et celui qui faisait
    échouer une analyse de CV sur Azure. Le simuler ici tient le verrou sans
    installer le SDK ni un émulateur ; l'adaptateur Azure du cœur est bâti de
    la même façon (``StoragePortMixin`` plus un ``_open``/``_save``), et son
    propre magasin est un conteneur au lieu de ce dictionnaire.
    """

    def __init__(self, *, link_ttl: int = DEFAULT_LINK_TTL) -> None:
        self.link_ttl = link_ttl
        self.blobs: dict[str, bytes] = {}

    def _open(self, name, mode="rb"):
        try:
            return ContentFile(self.blobs[name], name=name)
        except KeyError:
            raise FileNotFoundError(name) from None

    def _save(self, name, content):
        self.blobs[name] = b"".join(content.chunks())
        return name

    def exists(self, name) -> bool:
        return name in self.blobs

    def delete(self, name) -> None:
        self.blobs.pop(name, None)

    def size(self, name) -> int:
        try:
            return len(self.blobs[name])
        except KeyError:
            raise FileNotFoundError(name) from None


#: ``STORAGES`` posé sur ce fournisseur, pour ``override_settings``.
REMOTE_STORAGES = {
    "default": {"BACKEND": "jobhunt_ai.tests.fakes.PathlessStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def make_premium_user(*args, **kwargs):
    kwargs.setdefault("premium_until", timezone.now() + timedelta(days=30))
    return make_user(*args, **kwargs)


class PremiumTestCase(OwnedTestCase):
    """A signed-in account with a current, account-scoped paid entitlement."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Profile.objects.filter(user=cls.user).update(
            premium_until=timezone.now() + timedelta(days=30)
        )

PARSED_PROFILE = schemas.ParsedProfile(
    headline="Ingénieur DevOps senior",
    summary="Quinze ans d'infrastructure et d'automatisation.",
    skills=[
        schemas.ProfileSkill(name="Ansible", category="DevOps", level="expert", years="8"),
        schemas.ProfileSkill(name="Linux", category="Systèmes", level="expert", years="15"),
        schemas.ProfileSkill(name="Python", category="Langages", level="", years="10"),
    ],
    experiences=[
        schemas.ProfileExperience(
            title="Ingénieur DevOps",
            company="Acme",
            location="Bruxelles",
            start="2019",
            end="",
            current=True,
            description="Industrialisation de la plateforme.",
            achievements=["Migration de 200 serveurs sans coupure"],
            skills=["Ansible", "Linux"],
        )
    ],
    education=[schemas.ProfileEducation(degree="Master en informatique", school="UCL", year="2008")],
    languages=[schemas.ProfileLanguage(name="Français", level="C2")],
    certifications=[schemas.ProfileCertification(name="RHCE", issuer="Red Hat", year="2015")],
    detected_language="fr",
)

MATCH_VERDICT = schemas.MatchVerdict(
    score=76,
    summary="Bon recouvrement technique, mais Terraform manque.",
    strengths="- Ansible au niveau demandé",
    weaknesses="- Pas de Terraform en production",
    strategy="Mets en avant la migration des 200 serveurs.",
    matched_skills=["Ansible", "Linux"],
    partial_skills=["CI/CD"],
    missing_skills=["Terraform"],
    gaps=[
        schemas.SkillGapItem(
            name="Terraform",
            severity="importante",
            why_it_matters="Cité trois fois dans l'annonce.",
            action_plan="Reprovisionne le homelab avec Terraform.",
        )
    ],
)

ATS_RESUME = schemas.ATSResume(
    full_name="Lionel Test",
    headline="Ingénieur DevOps senior",
    contact_line="Nivelles · +32 470 00 00 00 · lionel@example.org",
    links=["https://linkedin.com/in/test"],
    summary="Quinze ans d'infrastructure, orienté fiabilité.",
    skill_groups=[schemas.CVSkillGroup(category="DevOps", items=["Ansible", "CI/CD"])],
    experiences=[
        schemas.CVExperience(
            title="Ingénieur DevOps",
            company="Acme",
            location="Bruxelles",
            period="2019 – aujourd'hui",
            bullets=["Migration de 200 serveurs sans coupure"],
        )
    ],
    education=["Master en informatique — UCL, 2008"],
    certifications=["RHCE — Red Hat, 2015"],
    languages=["Français (C2)"],
    extra_sections=[],
    ats_keywords=["Ansible", "CI/CD"],
)

QUALIFICATION_RUBRIC = schemas.QualificationRubric(
    seniority="senior, 15 ans",
    target_roles=["Ingénieur DevOps", "SRE"],
    core_skills=["Ansible", "Linux"],
    secondary_skills=["Python"],
    languages=["français C2"],
    mobility="Nivelles, 40 km, télétravail partiel accepté",
    deal_breakers=["néerlandais courant exigé"],
)

SCOUT_QUERIES = schemas.ScoutQueries(queries=["devops engineer", "ingénieur système"])

SCRAPED_OFFERS = schemas.ScrapedOffers(
    offers=[
        schemas.ScrapedOffer(
            title="DevOps Engineer",
            company_name="Widgets SA",
            location="Braine-l'Alleud",
            url="https://example.org/jobs/devops",
            description="Ansible, Linux, un peu de Terraform.",
            language="fr",
        ),
        schemas.ScrapedOffer(
            title="Senior DevOps Engineer",
            company_name="Acme",
            location="Bruxelles",
            url="https://example.org/jobs/acme-devops",
            description="Le poste déjà suivi.",
            language="en",
        ),
    ]
)

LEAD_SCORES = schemas.LeadScores(
    scores=[
        schemas.LeadScore(
            index=0,
            score=81,
            reason="Stack alignée, à 15 km.",
            confidence="moyenne",
            blockers=["Terraform exigé"],
        )
    ]
)

DEFAULT_RESPONSES = {
    schemas.ParsedProfile: PARSED_PROFILE,
    schemas.MatchVerdict: MATCH_VERDICT,
    schemas.ATSResume: ATS_RESUME,
    schemas.QualificationRubric: QUALIFICATION_RUBRIC,
    schemas.ScoutQueries: SCOUT_QUERIES,
    schemas.ScrapedOffers: SCRAPED_OFFERS,
    schemas.LeadScores: LEAD_SCORES,
}


@contextlib.contextmanager
def fake_llm(responses: dict | None = None):
    table = {**DEFAULT_RESPONSES, **(responses or {})}
    calls = []

    def parse_structured(
        schema, *, system, content, max_tokens=None, run_id=None, owner_id=None
    ):
        calls.append({"schema": schema, "system": system, "content": content})
        try:
            data = table[schema]
        except KeyError:
            raise AssertionError(f"Pas de réponse factice pour {schema.__name__}")
        return StructuredResult(data=data, input_tokens=100, output_tokens=50)

    with mock.patch("jobhunt_ai.services.llm.parse_structured", side_effect=parse_structured):
        yield calls


@contextlib.contextmanager
def eager_runs():
    from django.utils.module_loading import import_string
    from django_q.exceptions import TimeoutException

    def run_chain(chain):
        # Deterministic test driver only. Production always uses Chain(sync=False).
        # Q2 advances a chain even after a failure; emulate that guard path too.
        for func, args, options in chain.chain:
            try:
                import_string(func)(*args, stage_timeout=options["stage_timeout"])
            except (Exception, TimeoutException):
                continue
        return chain.group

    with mock.patch("jobhunt_ai.conf.EAGER_RUNS", True), mock.patch(
        "django_q.tasks.Chain.run", run_chain,
    ):
        yield
