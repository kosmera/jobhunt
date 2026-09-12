"""Schémas Pydantic des sorties structurées des agents.

Les descriptions guident le modèle : elles font partie du prompt effectif.
Le texte produit est destiné à l'interface, donc les descriptions demandent
du français partout où le contenu est montré tel quel.

Règle d'or : **tous les champs sont requis** — « inconnu » se dit ``""`` ou
``[]``. Un champ optionnel (défaut Pydantic) sort de la liste ``required``
du schéma JSON, et la grammaire de décodage contraint doit alors encoder
toutes les combinaisons de clés : l'API répond « Schema is too complex »
ou compile pendant plusieurs minutes. Tout requis → grammaire triviale,
acceptée en quelques secondes (vérifié sur l'API le 2026-09-01).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Analyse de CV
# ---------------------------------------------------------------------------


class ProfileSkill(BaseModel):
    name: str = Field(description="Nom canonique de la compétence, ex. « Ansible »")
    category: str = Field(description="Famille courte en français, ex. « DevOps », « Langages »")
    level: str = Field(
        description="débutant / confirmé / expert si le CV le précise, sinon vide"
    )
    years: str = Field(description="Années de pratique si déductibles, sinon vide")


class ProfileExperience(BaseModel):
    title: str
    company: str = Field(description="Vide si non précisé")
    location: str = Field(description="Vide si non précisé")
    start: str = Field(description="Début au format AAAA-MM ou AAAA, vide si inconnu")
    end: str = Field(description="Fin au même format, vide si poste actuel ou inconnu")
    current: bool = Field(description="Vrai si c'est le poste actuel")
    description: str = Field(description="Résumé du poste, en français")
    achievements: list[str] = Field(
        description="Réalisations marquantes, une phrase chacune"
    )
    skills: list[str] = Field(description="Compétences mobilisées")


class ProfileEducation(BaseModel):
    degree: str
    school: str = Field(description="Vide si non précisé")
    year: str = Field(description="Vide si non précisé")


class ProfileLanguage(BaseModel):
    name: str
    level: str = Field(description="Niveau CECR (A1…C2) ou descriptif court, vide si inconnu")


class ProfileCertification(BaseModel):
    name: str
    issuer: str = Field(description="Vide si non précisé")
    year: str = Field(description="Vide si non précisé")


class ParsedProfile(BaseModel):
    """Contenu structuré d'un CV. Ne rien inventer : vide vaut mieux que faux.

    Aucun champ d'identité ici — ni nom, ni e-mail, ni téléphone, ni lieu, ni
    liens. Le texte remis au modèle est anonymisé par le cœur
    (host privacy adapter) : ces champs ne pourraient contenir que des
    marqueurs ``[NOM]`` / ``[EMAIL]``. L'identité du profil candidat vient du
    compte (host identity adapter), voir ``agents/cv_parser.py``.
    """

    headline: str = Field(description="Titre professionnel du candidat")
    summary: str = Field(description="Résumé du profil en 3-4 phrases, en français")
    skills: list[ProfileSkill]
    experiences: list[ProfileExperience] = Field(
        description="De la plus récente à la plus ancienne"
    )
    education: list[ProfileEducation]
    languages: list[ProfileLanguage]
    certifications: list[ProfileCertification]
    detected_language: str = Field(
        description="Langue du CV : code ISO à deux lettres (fr, en, nl)"
    )


# ---------------------------------------------------------------------------
# Grille de qualifications
# ---------------------------------------------------------------------------


class QualificationRubric(BaseModel):
    """Les qualifications du candidat en version courte, pour le pré-tri.

    Dérivée une fois du profil complet : la veille compare des dizaines
    d'offres à cette grille sans resérialiser tout le CV à chaque fois.
    """

    seniority: str = Field(
        description="Niveau et années d'expérience, ex. « senior, 15 ans »"
    )
    target_roles: list[str] = Field(
        description="Intitulés de poste qui correspondent vraiment au profil"
    )
    core_skills: list[str] = Field(
        description="Compétences maîtrisées, démontrées par l'expérience"
    )
    secondary_skills: list[str] = Field(
        description="Compétences côtoyées mais sans profondeur"
    )
    languages: list[str] = Field(description="Langues et niveau, ex. « français C2 »")
    mobility: str = Field(
        description="Base géographique, rayon acceptable et rapport au télétravail"
    )
    deal_breakers: list[str] = Field(
        description="Ce qui disqualifie une offre pour ce candidat, vide si rien"
    )


# ---------------------------------------------------------------------------
# Évaluation de compatibilité
# ---------------------------------------------------------------------------


class SkillGapItem(BaseModel):
    name: str = Field(description="Compétence manquante ou trop juste")
    severity: str = Field(description="« bloquante », « importante » ou « secondaire »")
    why_it_matters: str = Field(description="Pourquoi l'offre y tient, une phrase en français")
    action_plan: str = Field(
        description="Piste concrète pour la combler ou la contourner, en français"
    )


class MatchVerdict(BaseModel):
    """Évaluation honnête du recouvrement entre un profil et une offre."""

    score: int = Field(ge=0, le=100, description="Compatibilité globale de 0 à 100")
    summary: str = Field(description="Verdict en 2-3 phrases, en français, sans langue de bois")
    strengths: str = Field(
        description="Ce qui joue pour le candidat, en liste à puces markdown, en français"
    )
    weaknesses: str = Field(
        description="Ce qui joue contre lui, en liste à puces markdown, en français"
    )
    strategy: str = Field(
        description="Comment aborder la candidature (angle, réseau, points à préparer), en français"
    )
    matched_skills: list[str] = Field(description="Compétences exigées que le profil couvre")
    partial_skills: list[str] = Field(
        description="Compétences exigées partiellement couvertes"
    )
    missing_skills: list[str] = Field(description="Compétences exigées absentes du profil")
    gaps: list[SkillGapItem] = Field(
        description="Les lacunes qui méritent un plan d'action"
    )


# ---------------------------------------------------------------------------
# Génération de CV ATS
# ---------------------------------------------------------------------------


class CVExperience(BaseModel):
    title: str
    company: str = Field(description="Vide si non pertinent")
    location: str = Field(description="Vide si non pertinent")
    period: str = Field(description="Ex. « 2019 – 2023 », vide si inconnu")
    bullets: list[str] = Field(
        description="Réalisations orientées résultats, reprenant les mots-clés de l'offre"
    )


class CVSkillGroup(BaseModel):
    category: str
    items: list[str]


class CVSection(BaseModel):
    title: str
    lines: list[str]


class ATSResume(BaseModel):
    """Un CV sobre et lisible par les ATS, ciblé sur une offre précise.

    Uniquement du contenu véridique issu du profil : reformuler et
    prioriser, jamais inventer.
    """

    full_name: str
    headline: str = Field(description="Titre aligné sur l'intitulé de l'offre")
    contact_line: str = Field(description="Ligne unique : localisation · téléphone · e-mail")
    links: list[str]
    summary: str = Field(description="Accroche de 3-4 lignes ciblée sur l'offre")
    skill_groups: list[CVSkillGroup] = Field(
        description="Compétences groupées, celles de l'offre en premier"
    )
    experiences: list[CVExperience]
    education: list[str]
    certifications: list[str]
    languages: list[str]
    extra_sections: list[CVSection] = Field(
        description="Sections supplémentaires seulement si nécessaires, sinon vide"
    )
    ats_keywords: list[str] = Field(
        description="Mots-clés de l'offre intégrés au CV (pour contrôle)"
    )


# ---------------------------------------------------------------------------
# Veille d'offres
# ---------------------------------------------------------------------------


class ScoutQueries(BaseModel):
    queries: list[str] = Field(
        description="Requêtes de recherche courtes (2-4 mots), sans la localisation"
    )


class ScrapedOffer(BaseModel):
    title: str
    company_name: str = Field(description="Vide si la page ne le montre pas")
    location: str = Field(description="Vide si la page ne le montre pas")
    url: str = Field(description="Lien absolu vers l'annonce, vide si absent")
    description: str = Field(
        description="Ce que la page dit du poste, condensé en quelques phrases"
    )
    language: str = Field(description="Langue de l'annonce (fr, en, nl), vide si incertaine")


class ScrapedOffers(BaseModel):
    """Les offres d'emploi réellement présentes sur la page, sans doublons."""

    offers: list[ScrapedOffer]


class LeadScore(BaseModel):
    index: int = Field(description="Index de l'offre dans la liste fournie")
    score: int = Field(ge=0, le=100)
    reason: str = Field(description="Justification en une phrase, en français")
    confidence: str = Field(
        description="Fiabilité du pré-tri vu le peu de matière : « élevée » si "
        "l'extrait couvre l'essentiel, « moyenne », ou « faible » s'il faut "
        "lire l'annonce pour se prononcer"
    )
    blockers: list[str] = Field(
        description="Exigences dures visibles dans l'extrait que le profil ne "
        "couvre pas ; liste vide si aucune n'apparaît"
    )


class LeadScores(BaseModel):
    scores: list[LeadScore]
