---
name: tonjobidéal / JobHunt
description: A calm, honest logbook for a job search — forest green on paper, an italic human aside, numbers in mono.
colors:
  forest-deep: "#153f36"
  leaf: "#287253"
  mint-wash: "#edf6ee"
  lime: "#d7f2bb"
  paper: "#f7faf5"
  paper-deep: "#edf3e8"
  card: "#ffffff"
  card-alt: "#f4f8ef"
  rail: "#fcfdfb"
  ink: "#243e35"
  ink-2: "#4f6658"
  ink-3: "#627369"
  rule: "#dfe7de"
  rule-strong: "#b4c4b3"
  overlay: "rgba(21, 63, 54, 0.48)"
  moss: "#426b3b"
  moss-wash: "#eaf4e3"
  amber: "#856017"
  amber-wash: "#f8efd9"
  rust: "#a04535"
  rust-wash: "#faeae4"
  violet: "#71538c"
  violet-wash: "#f1eaf7"
  blue: "#3f698c"
  blue-wash: "#eaf1f8"
  slate: "#5a6c60"
  slate-wash: "#edf1e9"
typography:
  display:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(44px, 5.5vw, 76px)"
    fontWeight: 550
    lineHeight: 1.07
    letterSpacing: "-3.7px"
  headline:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(1.8rem, 1.3rem + 1.5vw, 2.65rem)"
    fontWeight: 550
    lineHeight: 1.16
    letterSpacing: "-0.045em"
  title:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.82rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0.01em"
  body:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
    fontFeature: "\"kern\", \"liga\""
  prose:
    fontFamily: "Newsreader, ui-serif, Georgia, serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.62
    letterSpacing: "normal"
  aside:
    fontFamily: "Newsreader, ui-serif, Georgia, serif"
    fontSize: "1.2rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 550
    lineHeight: 1.5
    letterSpacing: "0.11em"
  numeral:
    fontFamily: "IBM Plex Mono, ui-monospace, Menlo, monospace"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.05em"
    fontVariation: "tabular-nums"
rounded:
  xs: "6px"
  sm: "9px"
  md: "14px"
  lg: "16px"
  pill: "999px"
spacing:
  xs: "0.4rem"
  sm: "0.75rem"
  md: "1.2rem"
  lg: "2rem"
  xl: "2.4rem"
  section: "105px"
components:
  button-primary:
    backgroundColor: "{colors.forest-deep}"
    textColor: "{colors.card}"
    typography: "{typography.title}"
    rounded: "{rounded.sm}"
    padding: "0.65rem 1rem"
    height: "42px"
  button-primary-hover:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.card}"
  button-secondary:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    typography: "{typography.title}"
    rounded: "{rounded.sm}"
    padding: "0.65rem 1rem"
    height: "42px"
  button-secondary-hover:
    backgroundColor: "{colors.card-alt}"
    textColor: "{colors.ink}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    padding: "0.65rem 1rem"
    height: "42px"
  button-danger:
    backgroundColor: "{colors.card}"
    textColor: "{colors.rust}"
    rounded: "{rounded.sm}"
    padding: "0.65rem 1rem"
    height: "42px"
  landing-button:
    backgroundColor: "{colors.forest-deep}"
    textColor: "{colors.card}"
    rounded: "{rounded.sm}"
    padding: "14px 23px"
    height: "54px"
  landing-button-hover:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.card}"
  input:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0.7rem 0.8rem"
    height: "44px"
  chip:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    padding: "0.22rem 0.5rem"
    height: "32px"
  chip-selected:
    backgroundColor: "{colors.mint-wash}"
    textColor: "{colors.leaf}"
    rounded: "{rounded.pill}"
  pill:
    backgroundColor: "{colors.slate-wash}"
    textColor: "{colors.slate}"
    typography: "{typography.label}"
    rounded: "{rounded.xs}"
    padding: "0.24rem 0.55rem"
  panel:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "1.2rem"
  app-card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "1rem 0.95rem 0.85rem"
  rail-link:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    padding: "0.7rem 0.75rem"
    height: "44px"
  rail-link-active:
    backgroundColor: "{colors.mint-wash}"
    textColor: "{colors.leaf}"
    rounded: "{rounded.sm}"
  option-card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "1rem 1.1rem"
  option-card-selected:
    backgroundColor: "{colors.mint-wash}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
---

# Design System: tonjobidéal / JobHunt

## Overview

**Creative North Star: "Le carnet de bord"**

The interface is a logbook kept with care. Everything in it exists to answer
« what do I handle today? » without noise: thin rules instead of boxes, dates
and scores set in mono so they line up, and one italic serif voice that speaks
to the person rather than to the task. It is calm, frank and well finished.
Nothing is decorative for its own sake; the brand lives in the accuracy of
small details (the dot after the wordmark, the arrow mark, the mint welcome
panel, the way a status is always a word and a colour together).

Two greens carry the identity. Forest deep is the voice of the product: the
wordmark, headings, the single primary action. Leaf is the hand that points:
links, the selected navigation item, focus, progress. Everything else is paper,
ink and a small family of muted status tones that always come with their own
wash. Depth is drawn, not lit: surfaces stack by tint and are separated by
hairlines; shadows appear only on what genuinely floats.

Confirmed anti-reference: the overstimulated SaaS landing page (JobAssist and
its kind), with purple gradients, confetti, stacked cards, user counters and
fabricated matches. The public page shows the whole working pipeline instead.
The dark theme is neutral charcoal, never forest-tinted; a tinted dark theme
was tried and rejected for readability on September 9, 2026.

**Key Characteristics:**
- Paper ground, hairline rules, tinted surface steps; flat by default.
- Archivo for every control and heading; Newsreader italic reserved for the human aside; IBM Plex Mono for anything counted.
- One primary action per view in forest deep; leaf for pointing, never for filling.
- Status is tone + wash + text label, driven by a `.t-*` class that sets two variables.
- Rounded but not soft: 9 px controls, 14 px containers, pills only for chips and tracks.
- 120 ms state transitions on a single house easing; motion respects reduced-motion globally.

## Colors

A paper-and-forest palette: two greens with authority, a warm off-white
ground, three inks, and six muted status tones each paired with a wash.

### Primary
- **Forest Deep** (`forest-deep`): the product's own voice. Wordmark, page and section headings, the one filled primary button per view, the landing mark's background. On dark surfaces it does not exist; the primary button becomes pale mint with dark ink (see sidecar `dark` values).
- **Leaf** (`leaf`): the pointing hand. Links, the active rail item and its count, focus outlines, progress bars, the selected chip's border and text, the eyebrow above a page title, the italic highlight in a hero. Never used as a fill behind body text.
- **Mint Wash** (`mint-wash`): the tint that says « this is yours ». Selected states, the welcome panel, the rail note, the arch behind the landing hero, text selection. Always paired with Leaf or Forest text.
- **Lime** (`lime`): the stroke of the arrow mark and a landing accent only. Not an interface colour.

### Neutral
- **Paper** (`paper`): page ground in the workspace. The landing and the rail use a lighter step (`rail`, `#fcfdfb`).
- **Paper Deep** (`paper-deep`): sunken tracks and counters: gauge and funnel tracks, the segmented control's well, the onboarding progress rail, raw posting blocks, skill tags.
- **Card** (`card`) and **Card Alt** (`card-alt`): raised and secondary surfaces. Panels and inputs sit on Card; hover rows, panel footers, board columns and filter bars sit on Card Alt.
- **Ink** (`ink`), **Ink 2** (`ink-2`), **Ink 3** (`ink-3`): three text steps. Ink for content, Ink 2 for secondary lines and labels, Ink 3 for metadata, help, placeholders and eyebrows.
- **Rule** (`rule`) and **Rule Strong** (`rule-strong`): hairlines. Rule separates; Rule Strong outlines controls (button and input borders, ghost pills, document icons).
- **Overlay** (`overlay`): forest at 48 % behind dialogs, with a 2 px blur.

### Tertiary (status family)
Six tones, each with its wash. Assigned by a `.t-*` class that sets `--tone` and `--tone-wash`; every component reads only those two variables.
- **Moss** (`moss` / `moss-wash`): done, positive, « candidature évidente », the sweet-spot quadrant on the scatter plot.
- **Amber** (`amber` / `amber-wash`): waiting, follow-up due, review notices.
- **Rust** (`rust` / `rust-wash`): closed, refused, danger buttons, field errors.
- **Violet** (`violet` / `violet-wash`): saved / vivier stage.
- **Blue** (`blue` / `blue-wash`): sent / in progress.
- **Slate** (`slate` / `slate-wash`): neutral default when no tone is set.

### Named Rules
**The One Accent Rule.** Forest fills at most one control per view. Leaf points but never fills a surface larger than a chip. If a screen needs a second filled button, the design is wrong, not the palette.

**The Tone-and-Wash Rule.** A status colour never travels alone: it is set as a tone plus its wash through a `.t-*` class, and it is always accompanied by a text label. Colour is a reinforcement, never the only carrier.

**The Charcoal Night Rule.** Dark mode is neutral charcoal (`#141518` ground, `#1e2025` cards) with mint-green accents (`#9bd4b2`), not a darkened forest. Light tokens are unchanged by the theme; only the dark block redefines them.

## Typography

**Display Font:** Archivo (variable, 100–900), with `ui-sans-serif, system-ui` fallback
**Body Font:** Archivo
**Prose / Aside Font:** Newsreader (variable, 200–800, upright and italic), with `ui-serif, Georgia` fallback
**Numeral Font:** IBM Plex Mono (400 / 500 / 600), with `ui-monospace, Menlo` fallback

All faces are self-hosted from `static/fonts/` with a latin subset and `font-display: swap`; no external font requests.

**Character:** Archivo is tight and even, set at medium weight (550) with negative tracking for headings, so the interface reads as one steady hand. Newsreader italic is the second voice: it appears where the product speaks to the person (the welcome line, the page lede, an empty state, the italic phrase inside a headline). Plex Mono makes every counted thing sit in a column.

### Hierarchy
- **Display** (550, `clamp(44px, 5.5vw, 76px)`, 1.07, −3.7 px): the landing hero only. The italic `em` inside it is Newsreader at 1.1 em in Leaf.
- **Headline** (550, `clamp(1.8rem, 1.3rem + 1.5vw, 2.65rem)`, 1.16, −0.045 em): page titles. An `em` inside switches to Newsreader 400 at 1.1 em in the heading-accent colour. Onboarding titles use a smaller step (`clamp(1.6rem, 3vw, 2.1rem)`).
- **Title** (600, 0.82–0.95 rem, 1.4, +0.01 em): panel titles, list-row and card titles, dialog titles, field labels (0.75 rem).
- **Body** (400, 15 px, 1.6): interface text. Inputs use 0.875 rem, buttons 0.8125 rem, and 16 px in inputs on phones to prevent zoom.
- **Prose** (Newsreader 400, 1 rem, 1.62): analyses, notes, job postings, gap texts. Lists use a 5 px hairline dash instead of a bullet.
- **Aside** (Newsreader italic, 1.12–1.3 rem, 1.2–1.55): the page lede, the rail note, empty-state text, skipped review values. Never for a control.
- **Label** (550, 11 px, +0.11 em, uppercase): eyebrows and flags. Table headers use Plex Mono at 10 px with the same tracking.
- **Numeral** (Plex Mono 500, 9.5–12.5 px, `tabular-nums`): dates, counts, scores, distances, axis labels, the `n/N` onboarding counter, avatars' initials.

### Named Rules
**The Italic Aside Rule.** Newsreader italic is the product speaking to a person. It never labels a control, never sits in a table, never carries a number.

**The Tabular Rule.** Any number a reader might compare with another is set in Plex Mono with tabular figures, right-aligned when in a column.

**The Uppercase Budget.** Uppercase exists only at 9–11 px with at least 0.05 em tracking (eyebrows, flags, table headers, axis labels). Headings and buttons are never uppercase.

## Layout

**Workspace shell.** A sticky 248 px rail (`--rail-w`) on the left, a sticky 76 px top bar with an 88 % paper background and 8 px backdrop blur, and a content column padded 2.4 rem × 2.25 rem with a 1440 px maximum (`.content--wide` removes it). The rail holds the wordmark, the navigation list, the mint welcome note and, at the bottom, the user chip.

**Grids.** Two-column layouts (`.cols--2`, `.cols--main` at 1.7 fr / 1 fr, `.cols--detail` with a 288–324 px aside) engage at 1080 px; three columns at 760 px. Gaps are 1.25 rem between panels and 0.85 rem between cards and stats. The pipeline board is a horizontal auto-flow grid of 258 px-minimum columns that scrolls inside its own region.

**Container queries.** Panels and dialog bodies are inline-size containers, so a two-up form (`460 px`), a three-up form (`680 px`) and the two-sided ledger (`540 px`) decide from their own width, not the viewport.

**Rhythm.** Panel body 1.2 rem; panel head 1 rem × 1.15 rem; list rows 1 rem × 1.15 rem; card 1 rem; stat 1.25 rem × 1.1 rem; page head margin 2 rem. Hairlines, not spacing alone, separate repeated rows.

**Breakpoints (workspace).** 960 px: the rail becomes a header with a horizontally scrolling navigation row beneath the brand and profile controls; 760 px: auth layout stacks; 620 px: phone density (44 px touch targets, 16 px inputs, two-up stats, 86 %-wide board columns, sticky onboarding actions over a paper gradient).

**Onboarding.** One question per screen in a centred 800 px column, a top bar of 860 px with back arrow, wordmark and mono counter, a 4 px progress rail in Leaf.

**Landing.** `wrap` is `min(1184px, 100% − 80px)`; sections breathe at 105 px; the hero is centred and sits on a mint arch that begins at 49 % of its height. Breakpoints at 1500, 1100, 900 and 650 px. Motion is a single 0.6 s « arrive » reveal (16 px rise, ease-out), staggered by 0.12 s for the product window.

## Elevation & Depth

Flat by default, shadow in response. Surfaces stack by tint (paper → card-alt → card) and are separated by 1 px rules; nothing at rest is lifted except the faint `shadow-sm` that keeps a panel from merging with its ground. Shadows are tinted forest at low alpha in light mode and pure black in dark mode.

### Shadow Vocabulary
- **Rest** (`box-shadow: 0 2px 5px rgba(21, 63, 54, 0.035)`): panels and stats at rest. Barely there.
- **Float** (`box-shadow: 0 3px 14px -5px rgba(21, 63, 54, 0.12)`): a card on hover, the chips search menu, the auth card.
- **Lift** (`box-shadow: 0 24px 70px -24px rgba(21, 63, 54, 0.3)`): dialogs and toasts, the only things that leave the page.
- **Landing window** (`0 20px 65px -30px #28463650, 0 0 0 7px #ffffff70`): the product window on the public page, framed by a translucent white ring.

### Named Rules
**The Flat-By-Default Rule.** Surfaces are flat at rest. A shadow is a response to state (hover) or to floating (menu, dialog, toast), never a decoration.

**The Hairline-First Rule.** When two regions must read as separate, draw a 1 px rule before adding a shadow or a second background.

## Shapes

Rounded but firm. Two working radii: 9 px (`sm`) for everything you touch (buttons, inputs, cards, rail links, company marks, skill tags) and 14 px (`md`) for everything that contains (panels, board columns, dialogs, option cards, gap cards, stat tiles). 16 px (`lg`) is reserved for the welcome panel, the auth card and the auth journey. 6 px (`xs`) for status pills and dialog action rows. Full pills (`999px`) only for chips, the segmented control and progress tracks. Small squares (2 px) for code, tooltips and the document extension badge.

Borders are 1 px hairlines in Rule; controls outline in Rule Strong; a selected option adds an inset 1 px ring in Leaf rather than thickening the border. Dashed 2 px borders mark the dropzone and « more » cards. Tone is shown as a 2–3 px left or top edge (flash, gap card, review notice, ledger head) rather than a filled background.

The arrow mark: a 40 px forest square with 12 px corners and a lime diagonal arrow drawn with a 3.5 px round stroke. It reappears rotated in the rail note and flipped as the dropzone icon.

## Components

Restrained and precise, without effects. Every control shares the same border, radius and 120 ms transition, so difference comes from colour assignment alone.

### Buttons
- **Shape:** 9 px radius, 1 px border, 42 px minimum height (44 px on phones), 0.65 rem × 1 rem padding, Archivo 0.8125 rem at 500.
- **Primary:** Forest fill, white text, border the same as the fill. Hover shifts fill and border to Leaf. One per view.
- **Secondary (default `.btn`):** Card fill, Ink text, Rule Strong border. Hover: Card Alt fill, Ink 3 border.
- **Ghost:** transparent, Ink 2 text, no border; hover restores Card Alt and a Rule border.
- **Danger:** Rust text, Rust at 35 % border; hover fills Rust wash.
- **Small:** 36 px, 0.75 rem. **Icon button:** 36 px square, transparent, Ink 3, same hover as ghost.
- **Active:** 1 px downward translate. **Disabled:** 45 % opacity. **Focus:** 3 px Leaf outline offset 3 px (global).
- **Landing:** the same button at 54 px with 14 px × 23 px padding, 18 px gap for its arrow, and a soft hover shadow; the outline variant uses a `#cad8cd` border and fills Mint on hover.

### Chips
- **Style:** pill, transparent, Rule Strong border, Ink 2 text, 11 px, 32 px minimum (36 px as a toggle or on phones). A removable chip carries a 20 px round × button that fills Leaf on hover.
- **State:** selected chips take the tone wash, tone border and tone text (Leaf by default). Hover darkens the border to Ink 3.

### Pills / Flags
- **Pill:** 6 px radius, tone wash background, tone text, 11 px at 500, optional 5 px leading dot. Ghost pills have no fill and an inset Rule Strong ring.
- **Flag:** uppercase 11 px label with a 4 px dot, tone-coloured, for short states inside cards.

### Cards / Containers
- **Panel:** Card, Rule border, 14 px, Rest shadow, `overflow: hidden`, inline-size container. Head with a 0.82 rem title and hairline; optional sunken footer on Card Alt.
- **Application card:** Card, Rule border, 9 px, 1 rem padding; company line at 0.875 rem 600 with a 32 px tone-washed company mark; hairline footer holding the gauge and actions. Hover: Float shadow and a border mixed 45 % toward the tone. Draggable with grab cursor; 40 % opacity while dragging.
- **Stat tile:** Card, 14 px, 2 rem tabular value in the tone, 0.75 rem label.
- **Gap card:** 14 px with a 2 px tone top edge; plan quoted in Newsreader on Card Alt with a tone left edge.
- **Option card (onboarding):** full-width, 14 px, 1 rem × 1.1 rem, Leaf icon left, check mark right appears only when selected; selected state is Mint wash, Leaf border and an inset 1 px Leaf ring. In a grid, cards are centred 108 px squares.

### Inputs / Fields
- **Style:** Card fill, Rule Strong border, 9 px, 44 px minimum, 0.875 rem Archivo; selects draw their own chevron; search inputs carry an inline magnifier.
- **Focus:** border to Leaf plus a 3 px Mint wash ring (in dark mode the ring is the accent itself). Onboarding and interest forms use the global 3 px outline instead.
- **Label / help / error:** 0.75 rem; label 600 Ink 2, help Ink 3, error Rust 500.
- **Segmented control:** a Paper Deep well with 999 px radius; the checked segment is the forest button.
- **Dropzone:** 2 px dashed Rule Strong, 14 px, Leaf on hover, Mint wash when a file is over it.

### Navigation
- **Rail link:** 0.8125 rem at 450, Ink 2, 9 px radius, 44 px tall, 16 px icon at 85 % opacity, optional mono count on Paper Deep. Hover: Card Alt, Ink. Active: Mint wash, Leaf, 600.
- **Mobile:** below 960 px the rail becomes a header; links flow horizontally in a scrollable row, the note and labels hide, the user chip collapses to its avatar.
- **Top bar:** sticky, blurred paper, mono date in Ink 3 with wide tracking, actions right.
- **Onboarding top bar:** back arrow (36 px, 44 px on phones), centred wordmark, mono `n/N` counter on Paper Deep.

### Gauge (signature)
A calm 5 px track on Paper Deep with a tone-coloured fill and an explicit tabular value beside it; small (52 px) inside cards, large (full width, 8 px, 1.7 rem value) on the detail page. A mono legend under the large gauge gives the score bands. There is no needle.

### Board (signature)
Columns on Card Alt with a 14 px radius, a 7 px tone dot before the label, a mono count, and a Mint wash + Leaf border when a card is dragged over. Cards stack with 0.7 rem gaps.

### Dialog, toast, flash
- **Dialog:** `min(640px, 100vw − 2rem)`, 14 px, Rule Strong border, Lift shadow, forest overlay with 2 px blur, sticky head and Card Alt foot.
- **Toast:** Ink fill with Paper text, 9 px, a 6 px tone dot, 0.22 s rise-in.
- **Flash:** tone wash fill, tone border with a 3 px left edge, 9 px.

## Do's and Don'ts

### Do:
- **Do** set status with a `.t-*` class and let components read `--tone` / `--tone-wash`; never hard-code a status hex in a component.
- **Do** pair every colour-coded state with a text label (pill, flag, board label).
- **Do** keep one forest-filled action per view and make the rest secondary or ghost.
- **Do** set dates, counts, scores and distances in Plex Mono with `tabular-nums`.
- **Do** use Newsreader italic for the page lede, the welcome line and empty states, and upright Newsreader for analyses, notes and postings.
- **Do** separate repeated rows with 1 px Rule and reserve shadows for hover, menus, dialogs and toasts.
- **Do** use container queries for forms inside panels and dialogs; the viewport does not know how wide a panel is.
- **Do** honour 44 px touch targets and 16 px inputs below 620 px, and keep boards and tables scrolling inside their own region.
- **Do** keep every font local and every icon an inline SVG from the shared icon partials.
- **Do** label fictional demo content as fictional wherever it appears.

### Don't:
- **Don't** tint the dark theme with forest; it is neutral charcoal with mint accents.
- **Don't** fill a surface with Leaf; it points (links, active, focus, progress) and never carries body text.
- **Don't** add gradients, confetti, stacked-card carousels, user counters or invented scores; the anti-reference is the overstimulated SaaS page.
- **Don't** introduce a third radius between 9 and 14 px or a full pill on anything that is not a chip, segment or track.
- **Don't** put uppercase on headings or buttons; the uppercase budget is 9–11 px labels only.
- **Don't** use a needle, dial or ring for a score; the gauge is a track plus a number.
- **Don't** animate beyond the 120 ms house transition and the landing's single 0.6 s reveal; reduced-motion zeroes everything.
- **Don't** load external fonts, scripts, images or analytics on any page.
