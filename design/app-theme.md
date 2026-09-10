# JobHunt workspace theme

Extends the approved [landing page direction](landing-page.md) across the daily
workspace: dashboard, pipeline, applications and detail forms, documents,
analysis, profile settings, account flows, integrated AI copilot, and Django
administration.

## Design

Forest `#153f36`, leaf `#287253`, mint `#edf6ee`, lime `#d7f2bb`, and paper
`#f7faf5` connect the product to its public page. Archivo carries the interface,
Newsreader introduces the personal welcome and editorial text, and IBM Plex Mono
keeps dates and numbers easy to scan. All fonts remain local.

The shared arrow mark, mint welcome panel, rounded surfaces, and quiet borders
form the visual identity. Existing status colors remain distinct, with text
labels alongside color. The dark theme uses neutral charcoal surfaces, white
headings, and brighter gray body text. Muted mint is reserved for links, selected
navigation, and actions. The existing manual/system theme selection remains
available; the light palette and typography are unchanged.

Shared styles live in `static/css/app.css`; administration adapts Django's
variable contract in `static/css/admin-theme.css`. The AI package retains its
existing template integration. Its fixed inline columns now use responsive
classes, with matching fallback styles for its standalone host.

## Responsive behavior and accessibility

On phones, the sidebar becomes a labeled horizontal navigation row beneath the
brand and profile controls. Boards and tables scroll inside their own regions.
Cards, forms, account pages, and copilot columns stack. Touch controls, visible
focus indicators, the skip link, reduced motion, native dialogs, and existing
HTMX form handling are retained or improved.

## Verification — September 9, 2026

Visual checks covered desktop (1280 px) and phone (375 px) screens, light and
dark themes, sign-in validation, dashboard, pipeline, application details,
documents, analysis, settings, copilot, candidate profile, leads, and admin.
No page-wide horizontal overflow was observed on the checked app routes.
Checked adding a fictional offer, advancing its status through HTMX, and
closing the add dialog with Escape and returning focus to its trigger.
No browser warnings or errors were reported on the workspace preview tabs.

Checks used an isolated temporary database and fictional applications, with no
provider calls. Django system and migration checks, Ruff, and both Pyright
configurations passed. The standalone host suite passed 25 tests; the combined
core/AI suite completed 602 tests successfully (35 skipped).

### Dark-mode readability refinement

The initial forest-tinted dark theme was replaced after user feedback. Page,
rail, card, and feature backgrounds now use `#141518`, `#191a1f`, `#1e2025`,
and `#24262c`. Body text uses `#f0f1f3`, secondary text `#d4d7dc`, and muted
text `#bfc3cb`. Input outlines are clearer and focused fields have a solid,
contrasting ring. The chart's shaded-region label no longer inherits reduced
opacity in dark mode.

Contrast calculations from rendered styles include text opacity and placeholders;
decorative separators and disabled controls are excluded. Checks cover the
dashboard, pipeline, applications, detail view, documents, analysis, settings,
copilot, profile, leads, sign-in errors, and admin. These are targeted color
checks, not a full accessibility audit.

| Dark-mode pairing | Contrast |
| --- | --- |
| Body text on cards | 14.42:1 |
| Secondary text on cards | 11.29:1 |
| Muted text on welcome panel | 8.56:1 |
| Primary button label | 11.06:1 |
| Input border on alternate surface | 3.82:1 |
| Focus ring on alternate surface | 8.85:1 |

Targets follow W3C's [text contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
(4.5:1 for normal text) and [non-text contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)
(3:1 for necessary control boundaries and indicators). A computed-style
comparison confirmed that the sampled light-mode surfaces, text, borders, and
fonts match their pre-change values. System and explicit dark tokens match.
Django system and migration checks passed after this CSS refinement.
