# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: a francophone job seeker in Belgium who is actively searching — without
a post or serving notice, running several applications in parallel, and needing
to regain control of follow-ups, deadlines and documents. When needs conflict,
this person wins.

Other audiences present in the product but not prioritised: employed people
searching discreetly (the copilot's scouting serves them), first-time job
seekers (the « Bienvenue » questionnaire guides them), and the project's own
author as the first self-hosted user. Nothing is designed for recruiters,
coaches or agencies.

Situation of use: alone, at a desk or on a phone, between two applications;
sessions are short and repeated, often triggered by « what do I have to do
today? ». The visitor already has offers, CVs and half-remembered follow-ups
scattered across tabs, folders and a spreadsheet.

## Product Purpose

tonjobidéal (hosted) / JobHunt (open-source core) is a French-language job
search workspace: it tracks applications from lead to offer, keeps the CVs and
targeted documents next to each application, tells the user what to handle
today, and — with the Premium copilot — evaluates an offer against the user's
profile, generates a targeted ATS CV and scouts job boards for new leads.

It exists because the author's own search lived in an Excel sheet
(`00_Suivi_candidatures.xlsx`) and per-offer folders; the app replaced them
and keeps the same honesty about what is known and what is not.

Success for the user: no missed follow-up, every application's next step
visible at a glance, and a clear-eyed view of which leads deserve effort.
Success for the product: a free tracker people actually keep using, and a
Premium copilot that earns its subscription by saving real preparation time.

## Positioning

**Honesty and control: nothing invented, nothing automatic.** The claim a
neighbouring product cannot copy without changing what it is:

- no automatic applications, ever; the user prepares, applies and follows up;
- no fabricated matches, scores, reviews, user counts or prices anywhere,
  including demos (fictional examples are labelled as such);
- one single score definition (`SCORE_SCALE`) shared by scouting and
  evaluation, with the scout stating its reliability rather than dressing up
  a partial reading;
- the CV file never leaves the user's space; only an anonymised text goes to
  the AI, and the interface says so in plain words;
- every AI result comes with what was missing and an invitation to check it
  against the original document.

Supporting truths (not the lead claim): tracking first, AI second; built for
the Belgian francophone market rather than translated; the core is open
source and self-hostable.

## Operating Context

- Two account modes: `local` (one machine, no password, first profile is
  admin) and `accounts` (shared instance, passwordless magic links valid 15
  minutes, single use; registration starts with an anonymous questionnaire).
- The « Bienvenue » questionnaire (`/bienvenue/`, one question per screen,
  finite-state machine in `accounts/onboarding/`) collects situation, AI tools
  tried, main difficulty, expectations, target roles, sectors, experience,
  education, contract types, work mode, cities and radius, minimum salary,
  horizon, then a review. In production the account is created only after
  email confirmation; the CV is added later in Documents.
- Daily workspace pages: Tableau de bord, Pipeline (drag to change status),
  Candidatures (instant search over notes and analyses), Documents, Analyse
  (gaps, platforms, discarded offers, compatibility × distance cloud),
  Copilote, Réglages. Keyboard: `n` adds an offer, `/` focuses search.
- Automations the user relies on: sending dates the application and schedules
  a follow-up at +10 days (7 for people in a hurry); « Relance faite » logs it
  and pushes the next; closing removes the follow-up; 14 days without news
  surfaces the application in « À traiter maintenant ». Both delays are
  per-profile settings.
- Copilot runs are asynchronous (Django-Q2 worker); the UI polls a durable run
  and shows its real phase. Providers: Anthropic locally, OpenAI or Azure
  OpenAI on the hosted instance, chosen by `JOBHUNT_AI_PROVIDER`.
- Public page `/accueil/` for signed-out visitors; signed-in users land on the
  dashboard at `/`.
- Legacy material still in the repo and imported idempotently by
  `import_legacy`: the workbook, `Offres/NN_.../` folders, `CV_base/*.docx`.
- Locale `fr-be`, time zone Europe/Brussels; provinces, cities and radius are
  Belgian.

## Capabilities and Constraints

Confirmed:

- Free tier: full tracker, documents, analysis, CV analysis into a candidate
  profile (free for active accounts, including during onboarding).
- Premium: offer evaluation (score, verdict, strengths/weaknesses/strategy,
  gaps synced to Analyse), targeted ATS CV generation (single-column DOCX,
  no tables), scouting of configured job boards via Bright Data with
  pre-sorted, importable leads. Score bands shown in the UI: ≥ 80 « candidature
  évidente », 65–79 « ça vaut le coup », 50–64 « pari risqué ».
- Self-hosted `local` mode grants Premium by default; hosted `accounts` mode is
  free until a subscription exists. Subscriptions are set in the Django admin;
  no Stripe checkout or payment webhook exists yet.
- Premium pages answer 402 to free accounts; the copilot can be switched off
  entirely (`COPILOT_ENABLED=0`).
- Every row belongs to a profile; PostgreSQL enforces it with row-level
  security (two roles, transaction-scoped `set_config`). Documents are served
  only through owner-checked views, never from `media/` paths.
- Stack: Django 6 + HTMX, server-rendered templates, vanilla JS, local fonts,
  no build step, no React. SQLite locally, PostgreSQL on Azure App Service
  (B1, francecentral), gunicorn + whitenoise, Brevo SMTP.
- Dev entry: `uv run manage.py runserver` (launch config `jobhunt` on port
  8017); copilot needs `uv run --env-file .env manage.py qcluster` too.
- Terminology (French UI, English identifiers): candidature, offre, piste
  (lead), relance, lacune (skill gap), plateforme, vivier, écartée, copilote,
  veille (scouting), profil candidat, « À traiter maintenant ».

Explicitly undecided:

- Premium price: 24,90 € / month is the *announced planned* price shown in
  the legacy launch-interest screen and welcome email; not final, no billing.
- Which job boards the hosted scouting will cover at launch.
- Public launch date of the hosted service.

## Brand Commitments

- Two names by design (GitLab / GitLab.com pattern): **tonjobidéal**
  (rendered lowercase with the accent and a trailing green dot, domain
  `tonjobideal.com`) is the hosted, paid product; **JobHunt** is the
  open-source self-hosted core. A discovery-flavoured name is only honest
  where the copilot's scouting ships.
- **Voice: tutoiement everywhere, including the public landing page.**
  Confirmed 2026-09-15; `/accueil/` was realigned the same day.
- Tone: plain, warm, factual; says what is known and what is not; never
  guarantees an employment outcome; never claims « trouve un job garanti ».
- Mark: `static/images/landing-mark.svg` (arrow mark), used in the app brand
  partial `tracker/templates/tracker/partials/brand.html`.
- Existing visual system (recorded in `design/landing-page.md`,
  `design/app-theme.md`, `design/onboarding.md`; not re-documented here):
  forest / leaf / mint / lime / paper palette, Archivo for interface,
  italic Newsreader for the human promise, IBM Plex Mono for dates and
  numbers, all self-hosted. Light and dark themes, manual or system.
- Legal: market is Belgium; trademark route is BOIP (Benelux), consumer law
  is the Belgian Code de droit économique; `.com` is the only canonical
  domain.

## Evidence on Hand

- Real product: the full workspace, onboarding and copilot exist and run;
  `design/*.md` record verified checks at 375–1440 px, light and dark, with
  measured contrast ratios.
- Real origin story: the author's own spreadsheet and offer folders
  (`00_LISEZMOI.md`, `00_Suivi_candidatures.xlsx`, `Offres/`, `CV_base/`)
  — private, not to be shown publicly without anonymising.
- Landing demo content (companies, jobs, scores, documents) is fictional and
  labelled as such; keep the label.
- Launch-interest signups exist as data (`export_launch_interest`) but are
  private; no count may be published.
- **Absent, never to be fabricated:** customer testimonials, user counts,
  success rates, press, partner logos, benchmark comparisons, final pricing,
  screenshots of real users' data.

## Product Principles

1. **Say only what is true.** No invented matches, numbers, quotes or
   guarantees; fictional examples are labelled; missing data is shown as
   missing.
2. **The user keeps the hand.** The product prepares, reminds and evaluates;
   it never applies, sends or decides for the user, and every AI output is
   reviewable and editable.
3. **Today first.** Every surface should answer « what do I handle now? »
   before anything else; dates and follow-ups are maintained by the product,
   not by the user's memory.
4. **Private by construction.** Files stay in the user's space, only
   anonymised text reaches a model, rows are isolated per owner at the
   database level, and the interface states this where it matters.
5. **Free tracker, honest upgrade.** The free workspace must be complete and
   pleasant on its own; Premium sells the copilot's time savings, never
   withheld basics.

## Accessibility & Inclusion

Established for the existing surfaces and to be preserved: WCAG 2.2 text
contrast ≥ 4.5:1 and non-text ≥ 3:1 (measured in `design/app-theme.md`),
visible focus rings, skip link, native `dialog` with focus return, keyboard
navigation of tabs and boards, status conveyed by text labels alongside colour,
`prefers-reduced-motion` honoured, no page-wide horizontal scroll from 375 px,
French `lang` attributes. The audience is not assumed to be technical; copy
must stay readable by someone meeting the job-search process for the first
time.
