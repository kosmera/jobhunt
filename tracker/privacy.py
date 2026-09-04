"""What a CV may say about its owner before the text leaves the application.

Pure functions on strings — no file, no model, no setting — so the rules run
in a ``SimpleTestCase`` and the storage adapters share one implementation.
The AI layer receives the *anonymised* text: every direct identifier is
replaced by a bracketed placeholder (``[EMAIL]``, ``[TELEPHONE]``…), the
rest of the CV is left intact so the analysis stays meaningful.

Two sources of identifiers:

- **rules** — e-mail addresses, URLs (LinkedIn, GitHub…), Belgian and
  international phone numbers, IBANs, the Belgian national number, the
  date and place of birth, an age, a postal address;
- **what the profile knows** — the owner's name, username, e-mail, home
  town and phone number (:func:`known_identity`), matched as whole words,
  case- and accent-insensitively.

Heuristics, not a model: a name the profile does not know, or an employer's
address, may get through, and a rule can misfire. The tests pin the cases
that matter for a CV — years, date ranges, version numbers and postcodes
must survive; ``0470 12 34 56`` must not.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field

EMAIL = "EMAIL"
PHONE = "TELEPHONE"
URL = "URL"
IBAN = "IBAN"
NISS = "NISS"
BIRTH_DATE = "DATE-DE-NAISSANCE"
AGE = "AGE"
ADDRESS = "ADRESSE"
NAME = "NOM"
PLACE = "LIEU"

#: French wording of each placeholder, for a summary shown to the owner.
LABELS: dict[str, tuple[str, str]] = {
    EMAIL: ("e-mail", "e-mails"),
    PHONE: ("numéro de téléphone", "numéros de téléphone"),
    URL: ("lien", "liens"),
    IBAN: ("IBAN", "IBAN"),
    NISS: ("numéro national", "numéros nationaux"),
    BIRTH_DATE: ("date de naissance", "dates de naissance"),
    AGE: ("âge", "âges"),
    ADDRESS: ("adresse", "adresses"),
    NAME: ("nom", "noms"),
    PLACE: ("lieu", "lieux"),
}


def placeholder(kind: str) -> str:
    return f"[{kind}]"


@dataclass(frozen=True)
class AnonymizedText:
    """The text with placeholders, and how many of each were inserted."""

    text: str
    redactions: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.text

    @property
    def total(self) -> int:
        return sum(self.redactions.values())

    def summary(self) -> str:
        """« 2 e-mails, 1 numéro de téléphone masqués », or « rien à masquer »."""
        parts = []
        for kind, count in self.redactions.items():
            if not count:
                continue
            singular, plural = LABELS.get(kind, (kind.lower(), kind.lower()))
            parts.append(f"{count} {singular if count == 1 else plural}")
        if not parts:
            return "rien à masquer"
        return ", ".join(parts) + (" masqué" if self.total == 1 else " masqués")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

_URL = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b(?:[\w-]+\.)?(?:linkedin\.com|github\.com|gitlab\.com|bitbucket\.org|behance\.net"
    r"|x\.com|twitter\.com|facebook\.com|instagram\.com)/\S+",
    re.IGNORECASE,
)

# Two letters, two check digits, then groups of four: BE68 5390 0754 7034.
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")

# Belgian national number: YY.MM.DD-XXX.CC, separators optional.
_NISS = re.compile(r"(?<![\w+])\d{2}[.\- ]?\d{2}[.\- ]?\d{2}[.\- ]?\d{3}[.\- ]?\d{2}\b")

# A date after a birth label, whatever the notation (30/07/1985, 30 juillet
# 1985, 1985-07-30), optionally followed by the birthplace.
_DATE = (
    r"(?:\d{1,2}[./\- ]\s*(?:\d{1,2}|[a-zéû]+)[./\- ]\s*\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}(?:er)?\s+[a-zéû]+\s+\d{4})"
)
_TOWN = r"[A-ZÀ-Ý][\w'\-]+(?:[ \-](?:[A-ZÀ-Ý][\w'\-]+|sur|la|le|les|en|de|du|des|l'))*"
_BIRTH_DATE = re.compile(
    r"(?P<label>\b(?:n[ée]e?(?:\(e\))?\s+le|date\s+de\s+naissance|naissance|geboren\s+op|geboortedatum"
    r"|born(?:\s+on)?|date\s+of\s+birth|birth\s*date)\s*[:\-–]?\s*)"
    r"(?P<value>" + _DATE + r"(?:\s*(?:,|à|in|te)\s+" + _TOWN + r")?)",
    re.IGNORECASE,
)
_BIRTH_PLACE = re.compile(
    r"(?P<label>\b(?:n[ée]e?(?:\(e\))?\s+à|geboren\s+te|born\s+in|lieu\s+de\s+naissance)\s*[:\-–]?\s*)"
    r"(?P<value>" + _TOWN + r"(?:\s*,?\s*(?:le|on|op)?\s*" + _DATE + r")?)",
    re.IGNORECASE,
)
_AGE = re.compile(r"(?P<label>\b[âa]ge\s*[:\-–]?\s*)(?P<value>\d{2}(?:\s*ans)?\b)", re.IGNORECASE)

# Belgian and international phone numbers. Must start with 0, 00… or +…,
# then groups of 2–4 digits — a date range (2019-2021), an ISO date, a
# version (3.12.0) or a period (06/2019 - 08/2021) never qualifies, and
# the digit count settles the rest.
_PHONE = re.compile(
    r"(?<![\w])(?:\(?\+\d{1,3}\)?[ .\-]?(?:\(0\))?[ .\-]?|00\d{1,3}[ .\-]?(?:\(0\))?[ .\-]?|0)"
    r"\d{1,3}(?:[ .\-/]?\d{2,4}){2,4}(?![\w])"
)
_PHONE_DIGITS = (9, 15)
#: « 01/2020-12/2022 », « 06.2019-08.2021 » — a period, not a number: a year
#: right after a separator settles it. A real group of four (« +49 30 2019
#: 4567 ») is only rejected when it looks like a month/year pair, so the
#: check asks for a two-digit group before it.
_DATE_RANGE = re.compile(r"(?<!\d)\d{1,2}[./](?:19|20)\d{2}(?!\d)")

# « cours » and « voie » are left out on purpose: after the constraints below
# they still match ordinary French (« Cours de Java 2 ») and no Belgian
# address form survives that the others do not already cover.
_FRENCH_STREET = (
    r"rue|avenue|av\.|chauss[ée]e|ch\.|chemin|boulevard|bd\.?|place|clos|all[ée]e|square"
    r"|dr[èe]ve|impasse|quai|sentier|route|venelle|cit[ée]"
)
_DUTCH_STREET = r"straat|laan|steenweg|weg|plein|dreef|lei|baan|markt|vest|kaai"
_ARTICLES = r"(?:\s+(?:de\s+la|de\s+l'|des|du|de|d'|la|le|les|aux?)\b)*"
#: A house number closes an address. Anywhere else a small number is followed
#: by what it counts (« 3 ans », « 12 dossiers »), so the number must be the
#: last thing on the line or before punctuation. A bis letter belongs to the
#: number; a spaced one has to be capitalised, or « 2 h » would eat the unit.
_HOUSE_NUMBER = (
    r"\s+(?!(?:19|20)\d{2}\b)\d{1,4}(?:[A-Za-z]|(?-i:[ ][A-Z]))?(?![\w€°%])"
    r"(?:\s*(?:bte|bo[îi]te|bus|b\.|box)\s*\w+)?"
)
#: The name of a street is one to four capitalised words (particles aside),
#: not any forty characters: « Chemin de la Cure 12 » is an address,
#: « chemin critique de la version 2 » is a sentence.
_STREET_NAME = (
    r"(?:[A-ZÀ-Ý][\w'’\-]+|(?-i:de|du|des|la|le|les|d'|l'|van|von|ter|ten))"
    r"(?:[ \-](?:[A-ZÀ-Ý][\w'’\-]+|(?-i:de|du|des|la|le|les|d'|l'|van|von|ter|ten))){0,3}"
)
# « Chaussée de Namur 12 bte 3 », « Kerkstraat 12 », « Grote Markt 5 » — the
# street part of a line, up to the house number. A street name needs a word
# of its own between the keyword and the number: « Route 53 » and « Place 2 »
# are a DNS service and a ranking, not addresses. A year is never a house
# number. What follows on the line (postcode, town) is handled next.
_STREET = re.compile(
    r"\b(?i:" + _FRENCH_STREET + r")\b(?i:" + _ARTICLES + r")\s+" + _STREET_NAME + _HOUSE_NUMBER
    + r"|\b\w{2,}(?i:" + _DUTCH_STREET + r")" + _HOUSE_NUMBER
    + r"|\b\w{2,}\s+(?i:" + _DUTCH_STREET + r")" + _HOUSE_NUMBER
)
#: What a CV puts between a street and its postcode — including the « · »
#: the DOCX extractor itself inserts between table cells, which is exactly
#: how a two-column contact block arrives.
_ADDRESS_SEP = r"[\s,;·•|/–—\-]"
# « 1400 Nivelles » right after a street (same line, or the next one).
_POSTCODE_TOWN_AFTER_STREET = re.compile(
    r"(?<=\[ADRESSE\])(?P<sep>" + _ADDRESS_SEP + r"{0,6})(?:B-?)?[1-9]\d{3}\s+" + _TOWN
)
_POSTCODE_TOWN_LINE_START = re.compile(r"^(?P<sep>\s*)(?:B-?)?[1-9]\d{3}\s+" + _TOWN)
# On its own, only with a country mark: « B-1400 Nivelles », « 1400 Nivelles,
# Belgique ». No global IGNORECASE — the town's capital letter is half the
# rule — and never right after a date or a preposition: « 2015 – 2019
# Louvain-la-Neuve, Belgique » is a CV line, not an address.
_NOT_AN_ADDRESS_BEFORE = r"(?<![-–])(?<![-–] )(?<!à )(?<!to )(?<!in )(?<!en )(?<!chez )(?<!depuis )(?<!since )"
_POSTCODE_TOWN_ALONE = re.compile(
    r"\bB-\s?[1-9]\d{3}\s+" + _TOWN
    + r"|" + _NOT_AN_ADDRESS_BEFORE + r"\b[1-9]\d{3}\s+" + _TOWN
    + r"(?=\s*" + _ADDRESS_SEP + r"?\s*(?i:Belgi(?:que|um|ë)|BE\b))"
)

_ACCENTS: dict[str, str] = {
    "a": "aàáâãäå", "c": "cç", "e": "eèéêë", "i": "iìíîï", "n": "nñ",
    "o": "oòóôõö", "u": "uùúûü", "y": "yýÿ",
}
_STOP_USERNAMES = frozenset({"local", "admin", "root", "user", "test", "profil", "profile"})
#: Particles of a family name: alone they are ordinary words (« Universiteit
#: van Amsterdam »), so only the full name and the distinctive tokens count.
_NAME_PARTICLES = frozenset(
    {"van", "de", "der", "den", "von", "le", "la", "du", "da", "di", "del", "ten", "ter", "dos"}
)


def _fold(text: str) -> str:
    """Lower case, accents stripped."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def _literal_pattern(literal: str) -> re.Pattern[str]:
    """``literal`` as a whole word, ignoring case and accents on both sides."""
    parts = []
    for char in _fold(literal):
        if char in _ACCENTS:
            parts.append(f"[{_ACCENTS[char]}{_ACCENTS[char].upper()}]")
        elif char.isspace():
            parts.append(r"\s+")
        else:
            parts.append(re.escape(char))
    return re.compile(r"(?<!\w)" + "".join(parts) + r"(?!\w)", re.IGNORECASE)


def known_identity(
    *,
    display_name: str = "",
    username: str = "",
    email: str = "",
    location: str = "",
    phone: str = "",
) -> dict[str, str]:
    """The literals the profile can vouch for, each with its placeholder kind.

    Name tokens shorter than three characters (« de », « Le ») are skipped,
    as are the particles of a family name (« van », « der ») and usernames
    that are plain words (``local``, ``admin``): each would eat ordinary
    text. The full display name is always a literal, so « Lionel Van Damme »
    is masked whole even when « Van » alone is not.

    ``phone`` is a backstop, not the rule: :data:`_PHONE` already catches the
    usual shapes, and this only adds the exact string the profile holds — a
    layout the pattern misses is still masked when the owner wrote it down.
    """
    known: dict[str, str] = {}
    seen: set[str] = set()

    def add(literal: str, kind: str) -> None:
        literal = literal.strip()
        if len(literal) >= 3 and _fold(literal) not in seen:
            seen.add(_fold(literal))
            known[literal] = kind

    if display_name.strip():
        add(display_name, NAME)
        for token in re.split(r"[\s\-]+", display_name):
            if token.lower() not in _NAME_PARTICLES:
                add(token, NAME)
    for token in re.split(r"[\s._\-]+", username):
        if token.lower() not in _STOP_USERNAMES and not token.isdigit():
            add(token, NAME)
    if email.strip():
        add(email, EMAIL)
        local_part = email.split("@", 1)[0]
        for token in re.split(r"[._\-+]+", local_part):
            if not token.isdigit() and token.lower() not in _STOP_USERNAMES:
                add(token, NAME)
    if location.strip():
        add(location, PLACE)
    if phone.strip():
        add(phone, PHONE)
    return known


def anonymize(text: str, *, known: Mapping[str, str] | None = None) -> AnonymizedText:
    """Replace every identifier the rules and ``known`` recognise."""
    counts: dict[str, int] = {}

    def replace(
        kind: str, pattern: re.Pattern[str], subject: str, *, keep_label: bool = False, count: int = 0
    ) -> str:
        def substitute(match: re.Match[str]) -> str:
            counts[kind] = counts.get(kind, 0) + 1
            if keep_label:
                # The rule keeps what introduces the value (« Né le », a separator).
                groups = match.groupdict()
                return (groups.get("label") or groups.get("sep") or "") + placeholder(kind)
            return placeholder(kind)

        return pattern.sub(substitute, subject, count=count)

    def replace_phone(match: re.Match[str]) -> str:
        candidate = match.group(0)
        digits = sum(char.isdigit() for char in candidate)
        if not _PHONE_DIGITS[0] <= digits <= _PHONE_DIGITS[1]:
            return candidate
        if _DATE_RANGE.search(candidate):
            return candidate
        counts[PHONE] = counts.get(PHONE, 0) + 1
        return placeholder(PHONE)

    result = replace(EMAIL, _EMAIL, text)
    result = replace(URL, _URL, result)
    result = replace(IBAN, _IBAN, result)
    result = replace(NISS, _NISS, result)
    result = replace(BIRTH_DATE, _BIRTH_DATE, result, keep_label=True)
    result = replace(PLACE, _BIRTH_PLACE, result, keep_label=True)
    result = replace(AGE, _AGE, result, keep_label=True)
    result = _PHONE.sub(replace_phone, result)

    lines = []
    previous_was_street = False
    for line in result.split("\n"):
        is_street = bool(_STREET.search(line))
        if is_street:
            line = replace(ADDRESS, _STREET, line)
            line = replace(ADDRESS, _POSTCODE_TOWN_AFTER_STREET, line, keep_label=True, count=1)
        elif previous_was_street:
            line = replace(ADDRESS, _POSTCODE_TOWN_LINE_START, line, keep_label=True, count=1)
        line = replace(ADDRESS, _POSTCODE_TOWN_ALONE, line)
        lines.append(line)
        previous_was_street = is_street
    result = "\n".join(lines)

    # Longest literal first: « Lionel Dupont » before « Lionel ».
    for literal, kind in sorted((known or {}).items(), key=lambda item: -len(item[0])):
        result = replace(kind, _literal_pattern(literal), result)

    return AnonymizedText(result, counts)

