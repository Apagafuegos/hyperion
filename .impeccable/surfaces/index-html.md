---
version: 1
slug: "index-html"
primary_target: "index.html"
related_targets: ["palette-examples.html"]
---

# Hyperion service atlas

## Scope and mode

- Target: `index.html`
- Mode: Operate
- Artifact: high-fidelity interactive design prototype, not production integration

## Audience, job, and constraints

- Current audience: the product owner managing several deployed services.
- Primary job: locate and open a known service in seconds without recalling its URL.
- Secondary context: understand availability at a glance.
- Tertiary action: inspect logs when needed.
- All service names, counts, endpoints, regions, and events in the prototype are illustrative.
- Preserve the warm off-white Service Atlas world, rare brass, no blue, no Greek letters, and no faux-classical decoration.

## Approved direction

- Direction: The Service Atlas — Latitude Bands.
- Approved comp: `.impeccable/mocks/02-latitude-bands.png`
- Memorable moment: deployed services form horizontal territories with a coordinate margin, while the right-side bearing instruments summarize fleet state.

## Fidelity inventory

| Comp ingredient | Commitment | Implementation medium |
|---|---|---|
| Global header | Brand at left, broad search centered, three labeled destinations at right | Semantic HTML/CSS with authored SVG icons |
| Workspace statement | Compact sentence, not a marketing hero | HTML typography |
| Territory margin | Applications, Services, and Foundations labels with coordinates and thin plotted tick lines | HTML/CSS; authored SVG geometry only where needed |
| Service atlas | Dense horizontal rows grouped into three broad functional territories | Semantic table-like HTML grid |
| Expanded dossier | First selected service expands inline with identity, ownership, dependencies, events, and a compact health trace | HTML/CSS plus precise SVG chart |
| Open actions | Every service exposes direct access; selected service action is strongest | Real buttons with SVG external-link icon |
| Bearings rail | Reachable, slow, paused, and down counts stack vertically with circular instrument marks | HTML/CSS plus authored SVG rings |
| Logs drawer | A shallow bottom drawer remains visually tertiary and can expand | Accessible disclosure control and log list |
| Mobile translation | Navigation condenses; bearings become a horizontal summary; territory label leads each band; service detail remains inline | Responsive CSS and small JavaScript state |

## Interaction commitments

- Search filters services immediately by name, description, environment, or endpoint.
- Selecting a row moves the dossier into that row without changing the atlas topology.
- Environment tabs jump to the matching band.
- The log drawer expands without a blocking modal.
- Keyboard focus remains visible in bearing brass; reduced motion receives instant state changes.

## What not to literalize

- Do not reproduce generated fictional counts, uptime claims, customer-like names, or deployment dates as product truth.
- Do not reproduce the comp's faint decorative scribbles; the atlas identity comes from rules, bearings, grouping, and typography.
- Do not turn the right rail into monitoring-heavy analytics.
