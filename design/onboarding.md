# JobHunt onboarding — the « Bienvenue » questionnaire

Entry: `/bienvenue/` (URL name `accounts:onboarding`, unchanged), one screen per
French slug under it. Replaces the single-form welcome page with a
questionnaire modelled on the JobAssist onboarding reviewed on September 9,
2026 (thirty screenshots of its flow, kept out of the repository), adapted to
what JobHunt actually does and to its two account modes.

## Direction

Same rhythm as the reference: one question per screen, a top bar with a back
arrow, the brand and a `n/N` counter, a thin progress bar, a centred 800 px
column, full-width option cards with a check mark when selected, one primary
button. Interstitials carry a small illustration in the brand palette.
Tutoiement throughout, never « vous ».

What was kept, renamed: situation, AI tools already tried, biggest difficulty,
expectations, job titles (chips with suggestions and « Postes proches »),
sectors (icon grid, « Voir plus », « tous les secteurs »), experience and
education (auto-advance on a pointer click), contract types, work mode,
cities and radius (skipped for remote work), minimum salary (period toggle,
slider mirrored to the number input), start horizon, a review with pencils,
the identity gate, the CV, a plan.

What was dropped, on purpose: the social-proof screen, the cover-letter
modal, the « finding your matches » scan, the matches modal and the paywall.
JobHunt never auto-applies and does not fabricate matches, reviews, user
counts or prices ([landing page rule](landing-page.md)). The one urgency
figure kept — « chaque semaine sans poste, c'est environ X € » — is computed
from the visitor's own minimum salary and hidden when the salary was skipped.
The CV screen shows facts only: file, size, readable characters, what was
masked before anything left the library, and whether an extension received
the anonymised text; no percentage, no parsed sections the core cannot
verify.

## The machine

`accounts/onboarding/machine.py` is a finite state machine with no Django
import: frozen `Step` rows form one forward chain, guarded steps only apply in
some situations, and four events move a frozen `Run` — `CONTINUE`, `STAY`
(the CV upload answers without moving), `SKIP` (writes explicit skip values)
and `EDIT` (from the review, with `return_to`). Everything else is a
projection of that table and the answers: the path, the effective current
state (a state whose guard turned false is skipped forward, never backwards),
the back target (the last *visited* step still on the path, or the review
during an edit detour), the `n/N` counter (guards are optimistic on missing
answers so N only ever shrinks), and the fill rule that brings an edit back to
the review unless it put a new, unanswered step on the path.

Illegal transitions — a stale tab, a forged POST at a state never reached,
an edit to a step never visited — raise `IllegalTransition`; the view turns
that into a message and a redirect. `Machine.check()` runs at import of
`flow.py`: unique ids and slugs, a total acyclic chain ending on a single
unguarded terminal, the review before the terminal, disjoint answer keys, skip
values matching answer keys. `accounts/test_onboarding_machine.py` enumerates
the sixteen combinations of the four guards and proves reachability,
totality and termination in each.

| # | id | slug | guard | kind |
| --- | --- | --- | --- | --- |
| 0 | welcome_back | reprise | the account already owns applications | interstitial, uncounted |
| 1 | status | situation | — | single |
| 2 | intro_pace | rythme | — | interstitial |
| 3 | ai_tools | outils-ia | — | single |
| 4 | intro_copilot | copilote | a CV analyser is installed | interstitial |
| 5 | challenge | difficulte | — | single |
| 6 | intro_profile | profil | — | interstitial |
| 7 | help | aide | — | multi |
| 8 | titles | postes | — | chips |
| 9 | industries | secteurs | — | grid, skippable |
| 10 | experience | experience | — | single, auto-advance |
| 11 | education | formation | — | single, auto-advance |
| 12 | work_type | contrat | — | multi |
| 13 | work_mode | mode | — | single |
| 14 | cities | villes | work mode is not remote | cities, skippable |
| 15 | salary | salaire | — | salary, skippable |
| 16 | timeline | horizon | — | single |
| 17 | review | bilan | — | review |
| 18 | identity | identite | anonymous, or no display name yet | gate |
| 19 | cv | cv | — | file, skippable |
| 20 | plan | plan | — | plan |
| — | done | — | — | terminal, never stored |

## Persistence and the gate

The run lives in the Django session until the last screen — the only place an
anonymous visitor may keep state under the row-level policies — and crosses
the identity gate because `auth.login()` cycles the session key while keeping
its data. Anonymous GETs never write the session. Rows are written at two
moments only: at the gate (a password-less local profile, the placeholder
`local` account named at last, an account created elsewhere given its name,
or the sign-up form in accounts mode) and at completion, in one transaction:
`Profile` (name, first title as headline, first city as home base when
blank), `Preferences` (radius, CV language, a 7-day follow-up for someone who
needs a job soon), `SearchProfile` (a new one-to-one row, RLS-registered) —
then `onboarded_at`, the session key cleared, the dashboard with the welcome
flash. The CV is filed immediately as the library's base CV through
`tracker.services.ingest_cv`, exactly as the documents page does.

Every answer is editable afterwards: the settings page (`/reglages/`) has a
« Ce que tu cherches » section — one form over the whole `SearchProfile`,
same chips combobox, toggle chips for the multiple choices, the same salary
period toggle — that runs its cleaned answers through the same
`search_profile_fields` mapping as the questionnaire, so the invariants hold
in both places. The widgets the two pages share live in `static/js/fields.js`
and the forms section of `app.css`; `onboarding.js` keeps only the flow's own
behaviours (choice gate, auto-advance, skip label, upload). Reading the
settings never creates the row; the first save of an account that skipped the
questionnaire does.

In accounts mode with sign-ups closed, an anonymous visitor is sent to the
sign-in page before the first question. `/inscription/` still creates the
account and now enters the questionnaire, whose gate is skipped by its guard.

## Accessibility

Every question is a `fieldset` with its title as `legend`; option cards wrap
a real radio or checkbox; the focus ring sits on the card; auto-advance fires
only after a pointer click, never on a keyboard change; the progress bar is a
`progressbar` with « Étape n sur N » and the counter pill is decorative; the
chips input follows the combobox/listbox pattern; every screen works without
JavaScript (comma-separated text instead of chips, a plain file input, a
native number field, a visible Continue). Reduced motion and the 44 px touch
targets come from `app.css`.

## Verification — September 10, 2026

- Core suite on SQLite (`scripts/validate.py tests`): green, 519 tests, 29
  skipped; the same suite on docker PostgreSQL 16 as the application role,
  including the `SearchProfile` policy tests: green. `manage.py check`,
  `makemigrations --check`, Pyright and pyflakes clean.
- Browser pane, local mode with a scratch database and the in-memory file
  provider: the whole flow from the first question to the dashboard, at the
  pane's width and at 375 px, light and dark. Checked: the choice gate, a real
  pointer click auto-advancing, « Tout ça » in both directions, the combobox
  (typed filter, ArrowDown + Enter, free text, removal), « Voir plus » and the
  exclusive « tous les secteurs » card with the « Passer » ↔ « Continuer »
  swap, the salary period toggle rebinding the unit and the slider, the review
  pencils (edit, « Enregistrer et revenir au bilan », back to the review), the
  identity gate creating the profile with the counter shrinking from 20 to 19,
  the CV skip, the plan's weekly cost, the dashboard flash. No console errors.
- Accounts mode with sign-ups open: the questionnaire runs anonymously, the
  gate shows the sign-up form with the « Se connecter » link, the account is
  created and the flow continues on the CV screen.
- Fabricated-number guard: a test walks the screens and asserts none of the
  reference's figures appear.
- Adversarial review (five lenses, three verifiers per finding): twenty
  findings, all fixed and covered by tests — among them a city suggestion
  that stored the province with the name, a run left past the gate when its
  account was deactivated, an abandoned edit detour skipping a newly enabled
  step, the upload script consuming the flash messages, and a ruff E741.
- Settings section « Ce que tu cherches » (same day): walked in the Browser
  pane on a scratch database — both comboboxes in one form (typed filter,
  ArrowDown + Enter, a recommendation landing in the titles row only, a
  removal handing the focus back to its own input), « Tous secteurs » and
  « Peu importe » clearing only their own group, the salary toggle rebinding
  the unit and the ceiling, a save round trip with the flash and every value
  back in place, 375 px without horizontal overflow; the questionnaire's
  titles, sectors, contract, cities and salary screens re-walked after the
  widget split. A second review round (five lenses, three verifiers) found
  six defects, fixed with tests: an unbounded comma-separated text field
  walked before its limit, recommended chips that never hid outside the
  questionnaire, a salary error focusing the period radio, a stale test
  count, a test pinned to a sector's ordinal, a period error shown twice —
  plus, from the lower-severity list: chips are now checked boxes so that
  without JavaScript unticking one removes it, NUL bytes in a chip are
  refused before PostgreSQL sees them, and the « no answer » option has a
  spoken name.

Two deliberate differences between the settings and the questionnaire, both
storing the same values: « Peu importe » (contracts) and « Tous secteurs »
are exclusive toggles in the settings where the questionnaire draws
« Peu importe » as a check-all card; and the context answers (situation,
difficulty, expectations, AI tools) may be left blank in the settings while
the questionnaire asks them once — a title and a contract type stay
required in both.
