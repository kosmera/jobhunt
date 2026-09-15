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
the identity gate and, for local/development accounts, the CV and a plan.

What was dropped, on purpose: the social-proof screen, the cover-letter
modal, the « finding your matches » scan, the matches modal and the paywall.
JobHunt never auto-applies and does not fabricate matches, reviews, user
counts or prices ([landing page rule](landing-page.md)). The one urgency
figure kept — « chaque semaine sans poste, c'est environ X € » — is computed
from the visitor's own minimum salary and hidden when the salary was skipped.
The CV screen shows facts only: file, size, readable characters, what was
masked before anything left the library, and whether an extension received
the anonymised text. CV extraction is free for active accounts. When the AI
extension is installed, the card polls its owned document's durable run and
shows its real phase, then the saved summary, skills, spoken languages with
their stated proficiency, experience/education counts and the extracted roles,
employers and date ranges. Missing language levels are explicitly unspecified;
the document's language never implies proficiency. Missing dates stay
explicitly unspecified. A review notice precedes the summary and lists missing
role titles, employers and dates (an ongoing role needs no end date). It always
asks users to compare the results with their CV: complex layouts can omit whole
roles or assign incorrect dates even when every displayed field is filled.
This notice also applies to existing results without another AI call.
Only the upload has a measured percentage; processing is indeterminate.
Failed or expired runs keep the file and offer a replacement; unavailable
polling offers a refresh without resubmitting the upload.

### Final account invitation — September 15, 2026

For anonymous visitors in the passwordless accounts flow, the questionnaire and
editable review come before any name or email field. The last identity screen
shows the actual selected roles, work mode, cities/radius and start horizon,
followed by an invitation to create a free account. Skipped fields never receive
inferred values. There are no generated job results, scores, prices or
subscription choices. The overview is visible before the account form; the form
does not autofocus, so opening the page starts at the summary.

The summary is the main visual: the chosen role names in Newsreader, with a
green rule and the remaining preferences in Archivo. The existing app tokens
provide forest green, mint and the dark-theme equivalents. Below it, the free
workspace is labelled « Disponible maintenant » and the proposed copilot
functions « En préparation ». Name and email are the final action. The account
is created only after email confirmation, then opens the dashboard with the
saved preferences. Creating an account does not grant marketing consent.

The CV and old plan screen are skipped in this flow. A new empty dashboard
links to Documents with « Ajouter mon CV » and labels this action optional.
Local and development password accounts retain their previous sequence. The
landing page's new-visitor actions and the passwordless login page's signup
link enter the questionnaire directly.

On mobile, the summary, availability descriptions and form fields stack. The
submit action stays in the document flow, with no overlay over the summary or
validation errors. Every field has a visible label, and the summary's link
returns to the editable review. No new fonts, assets or animation are needed.

### Launch interest — September 14, 2026 (legacy account flow)

In the legacy accounts flow, the final `/bienvenue/plan/` screen presents the free and
planned Premium offers, then an optional launch-email signup. Local mode
keeps its existing action plan. Joining or skipping the list in accounts mode
opens the free workspace; choosing Premium records interest without buying
or enabling a subscription.

The two equal-width cards use the app's existing palette (`--paper` #f7faf5,
`--card` #ffffff, `--heading` #153f36, `--ink` #243e35, `--accent` #287253)
and their dark-theme equivalents. Archivo carries the choices and prices;
Newsreader carries the introductory and explanatory text. Their defining
detail is the honest availability label: « Disponible maintenant » beside
« En préparation ». There is no recommended-plan badge or preselection.
The view supplies the agreed recurring Premium price: « 24,90 € / mois ».

Native radio buttons retain keyboard navigation, card-level focus rings,
and a written selected state. Email is prefilled but editable independently
of the account login. The launch-email consent is unchecked and explicit;
the second button continues without that consent. Both cards stack on small
screens. Actions remain in the document flow so they never obscure the
consent, errors, or the free-access explanation. The form and skip action
work without JavaScript; server errors are linked to the relevant controls.

Verified against an isolated preview at 1280 px and 375 px: dark and light
themes, no horizontal overflow, invalid submission, plan selection, explicit
consent, edited notification email, and successful return to the dashboard.
The new flow and persistence are covered by 20 tests, including CSRF, stale
sessions, retries, cross-account submissions, and unchanged Premium access.
The complete SQLite suite passes (797 tests, 40 skipped); PostgreSQL was not
available locally for this change.

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
the thirty-two combinations of the five guard inputs and proves reachability,
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
| 19 | cv | cv | not the final-account flow | file, skippable |
| 20 | plan | plan | not the final-account flow | plan |
| — | done | — | — | terminal, never stored in the session |

## Persistence and the gate

In the passwordless flow, answers stay in the anonymous session until the final
invitation queues a confirmation link with a terminal snapshot. This snapshot
is stored only with the pending proof, never as the active session run. A
successful confirmation creates the account and persists the answers in one
transaction, then clears the pending snapshot. Older nonterminal links resume
at the review and require a POST to finish; GET never completes an account.

In the local/development flow, the run lives in the Django session until the last screen — the only place an
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
`/bienvenue/cv/analyse/` returns the progress fragment for the signed-in
visitor's session CV, with no caching or session mutation. An optional
document identifier rejects polling from a tab showing an older upload.

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
sign-in page before the first question. In the passwordless flow,
`/inscription/` enters the questionnaire; its final invitation queues email
confirmation with the saved answers. The verified account opens its workspace
directly, including when confirmation happens on another device. In development
with passwords, `/inscription/` retains its direct account form and then enters
the questionnaire, whose identity gate is skipped by its guard.

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
