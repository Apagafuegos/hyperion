---
name: Hyperion
description: A calm, instrument-dense service atlas for everything deployed.
colors:
  mineral-canvas: "#F4F0E6"
  parchment-surface: "#FBF8F0"
  limestone-field: "#E4DDCF"
  weathered-rule: "#CEC4B2"
  carbon-ink: "#282824"
  graphite-copy: "#615F57"
  bearing-brass: "#876721"
  engraved-brass: "#705317"
  route-wash: "#EBE0BE"
  reachable-green: "#4F6B54"
  reachable-wash: "#E4ECE2"
  caution-ochre: "#8A5D16"
  caution-wash: "#F3E8CE"
  failure-red: "#9B433B"
  failure-wash: "#F2DFDB"
  dormant-stone: "#65665F"
  dormant-wash: "#E8E6E0"
typography:
  display:
    fontFamily: "Recursive, sans-serif"
    fontSize: "clamp(1.65rem, 3vw, 2.55rem)"
    fontWeight: 560
    lineHeight: 1.05
    letterSpacing: "-0.04em"
    fontVariation: "CASL 0.28"
  headline:
    fontFamily: "Recursive, sans-serif"
    fontSize: "1.28rem"
    fontWeight: 630
    letterSpacing: "-0.035em"
  title:
    fontFamily: "Recursive, sans-serif"
    fontSize: "0.85rem"
    fontWeight: 620
    letterSpacing: "-0.018em"
  body:
    fontFamily: "Geologica, sans-serif"
    fontSize: "0.67rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Geologica, sans-serif"
    fontSize: "0.64rem"
    fontWeight: 650
    lineHeight: 1.35
  instrument:
    fontFamily: "Recursive, monospace"
    fontSize: "0.58rem"
    fontWeight: 400
    lineHeight: 1.35
rounded:
  micro: "4px"
  keycap: "5px"
  action: "8px"
  control: "13px"
  circular: "999px"
spacing:
  hairline: "1px"
  micro: "4px"
  compact: "8px"
  control: "13px"
  panel: "16px"
  gutter: "18px"
  section: "26px"
  page: "44px"
components:
  field-search:
    backgroundColor: "{colors.parchment-surface}"
    textColor: "{colors.carbon-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 54px 0 44px"
    height: "42px"
  button-primary:
    backgroundColor: "{colors.bearing-brass}"
    textColor: "{colors.parchment-surface}"
    typography: "{typography.body}"
    rounded: "{rounded.action}"
    padding: "0 10px"
    height: "35px"
  button-route:
    backgroundColor: "{colors.parchment-surface}"
    textColor: "{colors.carbon-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.action}"
    padding: "0 10px"
    height: "31px"
  status-reachable:
    backgroundColor: "{colors.reachable-wash}"
    textColor: "{colors.reachable-green}"
    typography: "{typography.label}"
    rounded: "{rounded.circular}"
    padding: "4px 7px"
  dossier:
    backgroundColor: "{colors.parchment-surface}"
    textColor: "{colors.carbon-ink}"
    rounded: "{rounded.control}"
    padding: "16px"
---

# Design System: Hyperion

## Overview

**Creative North Star: "The Service Atlas"**

Hyperion renders one owner's deployed world as a latitude atlas: compact, legible, and ready to route the user to a known service. Its visual language comes from surveyor folios, archival indexes, thin plotted rules, and small brass bearings—not interchangeable dashboard cards or monitoring-wall chrome.

The system is deliberately dense. A compact header and search lead directly into horizontal functional territories; a selected service opens its dossier inside the band, current bearings stay narrow and peripheral, and logs remain closed until requested. Applications, Services, and Foundations preserve the atlas topology while matching the real single-VPS catalog. Warm mineral fields reduce glare, carbon carries information, rare brass establishes orientation and action, and operational pigments retain independent semantic meaning.

**Key Characteristics:**

- Warm mineral ivory and parchment fields with crisp carbon typography.
- Horizontal latitude bands, coordinate margins, and shared cartographic rules.
- Dense `.56rem–.85rem` supporting type with a compact Recursive statement scale.
- Rare brass for identity, selection, focus, and direct routes only.
- Status expressed through restrained green, ochre, red, and stone pigments.
- Greek mythology expressed as worldview and naming, never costume.

## Colors

The shipped palette combines sun-warmed stone and paper with carbon, aged brass, and desaturated operational pigments.

### Primary

- **Bearing Brass** (`#876721`): Brand mark, active-route rules, direct actions, plotted bearings, and focus outlines.
- **Engraved Brass** (`#705317`): Hovered action text and brass elements requiring stronger contrast.
- **Route Wash** (`#EBE0BE`): A translucent mix behind hovered and selected service routes.

### Neutral

- **Mineral Canvas** (`#F4F0E6`): Page ground and the base of the translucent sticky header.
- **Parchment Surface** (`#FBF8F0`): Search, action, dossier, log, and toast-contrast surfaces.
- **Limestone Field** (`#E4DDCF`): Reserved grouped-control field from the system palette.
- **Weathered Rule** (`#CEC4B2`): Band seams, record dividers, input borders, chart guides, and bearing crosshairs.
- **Carbon Ink** (`#282824`): Primary type and the strong top/bottom atlas boundaries.
- **Graphite Copy** (`#615F57`): Supporting text, metadata, coordinates, and placeholders.

### Operational

- **Reachable Green** (`#4F6B54`) on **Reachable Wash** (`#E4ECE2`): Online and available.
- **Caution Ochre** (`#8A5D16`) on **Caution Wash** (`#F3E8CE`): Slow, degraded, or warning.
- **Failure Red** (`#9B433B`) on **Failure Wash** (`#F2DFDB`): Down, failed, or requiring attention.
- **Dormant Stone** (`#65665F`) on **Dormant Wash** (`#E8E6E0`): Paused or intentionally quiet.

**The Rare Metal Rule.** Brass never indicates health and should occupy less than ten percent of a screen. Its rarity makes orientation and selection immediate.

## Typography

**Display Font:** Recursive (with sans-serif fallback)  
**Body Font:** Geologica (with sans-serif fallback)  
**Instrument Font:** Recursive (with monospace fallback) for coordinates, endpoints, timestamps, and logs.

**Character:** Recursive provides a subtly human technical voice for identity, place, service names, and instrument readings. Geologica stays compact and legible through the dense atlas. The small scale is an intentional operational choice, supported by strong contrast, short labels, and a minimum `44px` target for primary navigation.

### Hierarchy

- **Workspace statement** (Recursive 560, `clamp(1.65rem, 3vw, 2.55rem)`, 1.05, `-0.04em`): The single first-viewport sentence; this is the display ceiling, not a marketing hero.
- **Brand title** (Recursive 630, `1.28rem`, `-0.035em`): Hyperion in the sticky header.
- **Territory title** (Recursive 590, `1.08rem`, `-0.02em`): Applications, Services, and Foundations band labels.
- **Service title** (Recursive 620, `.85rem`, `-0.018em`): Primary identity in every service route.
- **Control and drawer title** (Geologica/Recursive 600–620, `.65rem–.82rem`): Search, navigation, buttons, and Logs.
- **Supporting body** (Geologica 400, `.61rem–.72rem`): Descriptions, row cells, notes, definitions, and empty-state copy.
- **Label** (Geologica 650, `.56rem–.70rem`): Status, dossier headings, counters, bearing labels, and log levels.
- **Instrument** (Recursive 400, `.58rem–.68rem`): Coordinates, endpoints, recency, timestamps, sources, and shortcut keycaps.
- **Bearing value** (Recursive 450, `1.8rem`; `1.45rem` on phone): Peripheral reachability counts.
- **Instrument value** (Recursive 450, `1.15rem`): The dominant current value inside a host metric instrument; a supporting reading sits beneath it at the label size.
- **Confirmation title** (Recursive 620, `.95rem`): The heading of the lifted confirmation surface.
- **Confirmation impact** (Geologica 400, `.72rem`): The exact-target and likely-impact line inside the confirmation surface.

**The Dense Instrument Rule.** Supporting type intentionally lives between `.56rem` and `.85rem`; preserve this hierarchy instead of normalizing every datum to conventional body size. Keep essential actions and service names at the upper end and tertiary telemetry at the lower end.

**The Instrument-Only Mono Rule.** Monospace styling belongs to endpoints, timestamps, latency, coordinates, shortcut keys, and log data—never general navigation or decorative labels.

## Layout

The desktop first viewport is a compact `70px` sticky header—brand left, broad search center, three labeled destinations right—followed by a short workspace statement and the atlas. The page is `min(1540px, 100% - 44px)`. Its main frame is one continuous ruled field with the latitude bands at `1fr` and a narrow `178px` bearings rail, not a grid of standalone cards.

Each territory is a horizontal band with a `182px` coordinate margin and a fluid service index. Routes use a minimum `48px` row; the selected route expands an inline four-column dossier (`.86fr 1.15fr .8fr 1fr`) without leaving the atlas topology. Logs sit in a separate shallow disclosure `26px` below the atlas so operational depth stays on demand.

At `1180px`, the coordinate margin narrows and endpoint/event detail is progressively removed. At `900px`, bearings become a four-column strip below the atlas and dossier content becomes two columns. At `620px`, the search moves to a second header row, bands stack their territory labels above routes, bearings form a two-by-two grid, and the dossier becomes one column with tertiary panels hidden. The selected service, its status cue, and its circular open action remain immediately reachable.

Spacing follows the implementation's compact cadence: `4–8px` within micro controls, `10–18px` inside routes and panels, `26px` between atlas and logs, and `44px` at the page edge. Shared seams and fixed bearings provide rhythm more often than empty space.

## Elevation & Depth

Hyperion is flat by default. Depth comes from tonal layers, border continuity, translucent route washes, and an inline dossier nested within its selected band. The sticky header uses a restrained `12px` backdrop blur so the plotted field remains legible beneath it. Static atlas surfaces do not use shadows.

### Shadow Vocabulary

- **Toast lift** (`0 8px 30px rgba(40, 40, 36, .18)`): Reserved for the fixed transient toast, the only shipped elevated surface.

**The Cartographic Layer Rule.** Use tone and seams to explain hierarchy at rest. Shadow means an element has actually lifted above the atlas.

## Shapes

The shape system is intentionally compact and functional: `4px` for log-level micro tags, `5px` for shortcut and count keycaps, `8px` for route actions, and `13px` for search, dossier, log drawer, and toast controls. Status pills use `999px`; plotted dots, bearing rings, and mobile icon actions are circular. These smaller radii are part of the dense instrument language, not unnormalized leftovers.

Thin `1px` rules define nearly every grouping. Strong Carbon rules enclose the complete atlas and separate territories; Weathered Rules divide routes, dossier cells, charts, logs, and controls. Avoid ornamental frames, fluted edges, scroll shapes, faux aging, and exaggerated soft containers.

## Components

### Global Header and Search

- **Structure:** Sticky three-part header at `70px`; the search becomes a full second row below `620px`.
- **Field:** Parchment background, Weathered Rule border, `13px` radius, `42px` minimum height, `.82rem` text, inset search icon, and a `5px` shortcut keycap.
- **Focus:** Engraved Brass border with a translucent `3px` Bearing Brass outline.
- **Navigation:** Transparent labeled buttons with a `44px` minimum target. The active route uses Engraved Brass text and a `2px` Bearing Brass underline.

### Latitude Band and Service Route

- **Structure:** Territory coordinate margin plus a continuous service index; rows share dividers instead of receiving individual shells.
- **Route:** `48px` minimum height, Recursive `.85rem` service title, `.64rem` description, operational `7px` point, and compact metadata.
- **State:** Hover uses a 40% Route Wash mix; selection uses 62% and rotates the chevron over `240ms`.
- **Open action:** Parchment, Weathered Rule, `8px` radius, `31px` minimum height; it becomes a `32px` circle when metadata collapses at `900px`.

### Selected Dossier

- **Structure:** Expands inside the selected row with a `13px` radius, Parchment surface, Weathered Rule, and `16px` cells.
- **Content:** Service facts, illustrative health trace, dependencies, and recent events in descending importance.
- **Primary action:** Full-width Bearing Brass route button with an `8px` radius and `35px` minimum height.
- **Motion:** `380ms cubic-bezier(.16, 1, .3, 1)` clip-and-settle reveal; reduced-motion collapses it to effectively instant.

### Status and Bearings

- **Status chip:** Paired operational wash/pigment, `4px 7px` padding, fully rounded shape, `.64rem` label, and a `5px` current-color dot. Meaning is always written as text.
- **Bearings rail:** `34px` circular ring over plotted crosshairs, a Recursive `1.8rem` count, and `.58rem–.64rem` label/note. It summarizes reachability without becoming a monitoring dashboard.
- **Rule:** Bearing Brass is not used for reachability; each bearing inherits the corresponding operational pigment.

### Logs Drawer

- **Structure:** Native `details/summary`, closed by default, `13px` outer radius, `56px` summary target, and ruled log rows.
- **Type:** Recursive monospace `.64rem` rows; `.56rem` micro labels use the `4px` radius.
- **Responsive:** Source hides at `900px`; level hides at `620px`; the disclosure remains available without a modal.

## Operations Console Extension

Hyperion grows from a read-only Service Atlas into the single-owner operations
console for one VPS. This extension is a descendant of the incumbent system: the
mineral canvas, parchment surfaces, carbon typography, sparse brass, ruled
fields, shared seams, and the `4/5/8/13px` radius ladder remain the visual law.
The Atlas is preserved as its own workspace; every new surface must be
unmistakably the same world.

### Global Workspace Shell

The sticky header now carries product-level destinations:

```text
Overview | Atlas | Schedules | Units | Activity
```

- Destinations use the established `44px` target, transparent labeled buttons,
  Engraved Brass active text, and a `2px` Bearing Brass underline.
- `Applications`, `Services`, and `Foundations` remain local Atlas destinations
  and are shown only while the Atlas workspace is active.
- The header search is workspace-aware: its placeholder, filters, and shortcut
  keycap stay; only the semantic targets change per workspace.
- The workspace statement below the header changes per workspace; it never
  grows beyond the incumbent display ceiling (`clamp(1.65rem, 3vw, 2.55rem)`).

**The Workspace Rule.** Navigation is a seam, not a screen change. The shell is
the same continuous ruled field; only the active latitude changes.

### Host Metric Instruments

Host telemetry uses a dedicated instrument grammar, never dashboard widgets:

- One dominant current value (Recursive 450, `.9rem–1.15rem`) with a unit.
- One supporting value or threshold (Geologica 650, `.56rem`) beneath it.
- A restrained single-line trace (`26px` tall) drawn with thin Weathered Rule
  guide lines and a Carbon or operational-pigment stroke; never a filled area
  chart.
- A written condition line when attention is required, using the operational
  wash/pigment pair and plain text.
- Explicit **Fresh**, **Stale**, **Partial**, **Unsupported**, and **Unavailable**
  states as label chips or written notes — never inferred values.

Instruments sit inside one continuous ruled field, sharing `1px` Weathered Rule
seams with the page rather than individual card shells. A selected instrument
opens an inline disclosure for filesystems, interfaces, or supporting evidence.

**The Truthful Instrument Rule.** Hyperion never manufactures a percentage,
rate, temperature, or history it did not observe. Missing evidence is written as
absent.

### Attention Band

A single attention band sits above ordinary metric readings on the Overview:

- Rows carry an operational point, a written heading, a short evidence note,
  and a route to the related record (service, unit, or schedule).
- The band uses failure/caution washes for real state and stays visually ahead
  of utilization instruments without becoming a monitoring wall.
- Empty attention renders a single quiet line: "Nothing needs attention." —
  not a decorative placeholder.

### Schedule Rows and Dossiers

- Rows: name, human-readable schedule, raw expression (instrument type), next
  run, last result, owner, and enabled state across one shared ruled row.
- Provenance labels mark **systemd timer** and **cron** sources with
  `4px` micro tags; `Not observed` is written explicitly where cron cannot
  establish a result.
- An inline dossier reveals the raw calendar/cron expression, execution owner,
  target, source path, related Atlas service, and a restrained next-24-hours
  timeline exposing overlap and clustering.

### Unit Rows and Expanded Dossiers

- Rows: unit name (Recursive 620), description, state chip, enabled state,
  active-since, restart count, memory, and timer relationship.
- The default inventory is curated (failed, catalog-related, locally authored,
  Hyperion-managed, recently active); an explicit **All units** filter reveals
  the full system inventory without letting vendor units dominate.
- An expanded dossier discloses dependencies, unit definition, journal
  evidence, protection classification, and related Atlas service.

### Operational Actions

Actions use the existing button grammar extended with explicit states:

- **Primary** — Bearing Brass route button (existing `button-primary`).
- **Secondary** — Parchment route button (existing `button-route`).
- **Unavailable** — the existing no-action treatment, with a written reason.
- **Pending** — Parchment with a quiet carbon label and no elevation; the action
  waits for a result, it does not spin or pulse theatrically.
- **Successful** — Reachable Green on Reachable Wash, written as text.
- **Failed** — Failure Red on Failure Wash, written as text.
- **Denied** — Caution Ochre on Caution Wash with a written reason (protected
  unit, stale state, or rate limit).

**The Consequence Rule.** Operational actions look consequential through exact
labels, expected-state checks, and confirmed impact — never through color,
glow, or motion. Brass marks intent; operational pigments report the result.

### Confirmation and Impact-Preview Surface

The confirmation surface is the one genuinely lifted UI beyond the toast:

- A parchment panel on the incumbent `toast-lift` shadow, `13px` radius,
  showing the exact target, typed operation, and likely impact in instrument
  type, with the protected-unit classification when relevant.
- **Confirm** (Bearing Brass) and **Cancel** (route button) plus an explicit
  expected-state line so stale actions are visible before submission.
- Dismissal with `Esc`, focus returns to the originating control, and reduced
  motion collapses the entrance to instant.

### Activity Field Notes

Activity is rendered as chronological field notes grouped by day:

- Day headings use the territory-title scale (Recursive 590); records use the
  dense body and instrument hierarchy.
- Each record carries a written target, identity, result chip, time, and
  evidence note.
- Grouping uses ruled seams and the day heading; no calendar grid appears.
- Operator actions, schedule executions, recoveries, state changes, and
  configuration changes all share the same record grammar, distinguished only
  by their result chip and target.

### Operational Do's and Don'ts

### Do:

- **Do** preserve the find, read state, open service sequence; reveal dossiers and logs only on demand.
- **Do** keep direct service access visually ahead of logs, health traces, and diagnostics.
- **Do** use shared horizontal bands, plotted rules, coordinates, and alignment to make the deployed world feel mapped.
- **Do** preserve the `.56rem–.85rem` supporting scale and `4/5/8/13px` radius ladder as intentional density choices.
- **Do** retain text labels, visible brass focus, and reduced-motion behavior alongside visual cues.
- **Do** keep the Atlas workspace visually and behaviorally identical to its baseline while the shell grows around it.
- **Do** keep telemetry instruments inside ruled fields with seams, never in generic card grids.
- **Do** write "Not observed", "Unavailable", and "Unsupported" literally where evidence is absent.

### Don't:

- **Don't** turn the latitude bands into an interchangeable rounded-card grid or a monitoring wall.
- **Don't** use Greek letters, blue Mediterranean palettes, columns, laurels, amphorae, or temple silhouettes.
- **Don't** make gold a background treatment, a health state, or a decorative glow.
- **Don't** enlarge every supporting label into ordinary body copy and erase the atlas's instrument hierarchy.
- **Don't** add decorative shadow to static surfaces or let logs compete with the primary route-opening workflow.
- **Don't** build generic administrative screens and style them afterward; extend the atlas world or do not ship it.
- **Don't** let host telemetry dominate service access or turn Hyperion into a monitoring wall.
- **Don't** infer metrics the provider did not observe, and don't imply precision cron cannot establish.
