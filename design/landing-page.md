# JobHunt landing page

Public page: `/accueil/`. Signed-out visitors to `/` are redirected here in both
account modes. Signed-in visitors still reach the private dashboard at `/`.
Start links use the current account mode: local onboarding, open registration,
sign-in when registration is closed, or the dashboard for a signed-in visitor.

## Direction

Subject: a French-language job search workspace for people managing applications.
Purpose: help visitors understand the product and open their personal workspace.

Palette: forest `#153f36`, leaf `#287253`, mint `#edf6ee`, lime `#d7f2bb`,
paper `#fcfdfb`, white `#ffffff`. Muted text uses `#627369`.
Archivo provides clear interface typography; italic Newsreader marks the human,
forward-looking promise. Both are already hosted locally by the app.

The centered editorial hero leads into a wide, working application board.
The board’s changing application stages, ending with an interview and a drawn
arrow, are the signature. The supporting sections stay quiet and practical.
On phones, all stages remain available in a vertical layout without horizontal
scrolling. Motion is limited to the opening reveal and respects reduced motion.

Selected over a split hero: showing the whole pipeline makes JobHunt’s tracking
workflow immediately clear and distinguishes it from the JobAssist card stack.
No fabricated customer quotes, user counts, prices, or automatic-application claims.
All example companies, jobs, scores, and documents are clearly labeled fictitious.

## Watermelon UI sources

Catalog discovery used `get_inspiration`, `compose_page`, and `get_component`.

- [Hero 22](https://ui.watermelon.sh/block/hero-22): source read in the block’s
  Source Code / index.tsx view after its registry endpoint returned HTTP 404.
  Adapted its deep green editorial hierarchy, italic highlight, primary/secondary
  calls to action, and staggered entrance into a centered product composition.
- [Accordion 1](https://ui.watermelon.sh/components/accordion): working registry
  source returned by `get_component` for `accordion-1` at
  `https://registry.watermelon.sh/r/accordion-1.json`.
  Adapted the leading expand icon, indented answer, multiple-open behavior, and
  initially expanded first question to native `details` / `summary` elements.
- FAQ 1 was considered but its registry endpoint also returned HTTP 404.

These are source-informed adaptations in native Django HTML/CSS/JavaScript;
React, Motion, and Radix are not installed into the Django/HTMX application.
Custom code implements the pipeline, local demo panels, dialog, and responsive menu.

Visual reference: [JobAssist](https://jobassist.com), reviewed September 9, 2026.
The reference informs the clear product story and visible product demonstration;
the layout, palette, copy, and brand treatment are specific to JobHunt.

## Verification

Check `/accueil/` at desktop and phone widths. Exercise all demo tabs, arrow-key
navigation, each job detail dialog, Escape, mobile menu, FAQ disclosures, and
start links. The demo never reads private application data or submits requests.
No external fonts, scripts, images, or analytics are needed by the landing page.

Verified on September 9, 2026 at 375, 768, 1024, 1280, and 1440 pixels: no
horizontal overflow. Browser checks covered tab clicks and arrow/Home keys,
opening and dismissing a job dialog with Escape, mobile menu navigation,
and FAQ expansion. All anchor targets resolve and the browser reports no
console errors or warnings. Reduced-motion styles disable animation and
smooth scrolling.

Django system and migration checks, Ruff, and Pyright passed. The core suite
completed 424 tests successfully (32 skipped for optional integrations).
