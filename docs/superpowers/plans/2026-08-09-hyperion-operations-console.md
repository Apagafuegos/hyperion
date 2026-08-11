# Hyperion Operations Console Plan

**Status:** Proposed implementation roadmap  
**Date:** 2026-08-09  
**Scope:** Evolve Hyperion from a read-only Service Atlas into the single-owner operations console for one VPS.  
**Primary constraint:** The current Service Atlas design is the binding visual and interaction contract. New work extends it; it does not replace, bypass, or temporarily abandon it.

## 1. Outcome

Hyperion will grow into five coordinated workspaces:

1. **Overview** — VPS condition, attention, upcoming work, service bearings, and recent activity.
2. **Atlas** — the existing service map, service dossiers, launch/copy actions, and logs.
3. **Schedules** — unified inspection of cron entries and systemd timers, followed later by Hyperion-managed schedules.
4. **Units** — systemd service inventory, evidence, logs, and eventually controlled lifecycle operations.
5. **Activity** — meaningful state changes, schedule executions, and operator actions.

The resulting product should answer five questions:

1. Is the VPS healthy?
2. What needs attention?
3. What is deployed and where can it be opened?
4. What is scheduled to happen?
5. What changed or was operated recently?

## 2. Binding Design Contract

The current `DESIGN.md`, `.impeccable/design.json`, `index.html`, `src/hyperion/templates/index.html`, and `frontend/atlas.css` are the incumbent visual authority.

Every new workspace must preserve:

- The mineral canvas, parchment surfaces, carbon typography, and sparse brass orientation cues.
- Recursive for identity, headings, and instrument values; Geologica for compact working copy.
- Continuous ruled fields and shared seams instead of a generic dashboard-card grid.
- Dense instrument-style information with details disclosed on demand.
- Inline dossiers and route-like rows wherever the data is entity-oriented.
- Operational green, ochre, red, and stone only for actual state.
- The existing `4/5/8/13px` radius ladder and spacing cadence.
- Visible brass focus, semantic text labels, keyboard operation, and reduced-motion support.
- Responsive transitions at the established desktop, tablet, and phone ranges.
- Flat static surfaces. Elevation is reserved for genuinely lifted UI such as a confirmation dialog or toast.

The following are prohibited:

- Building generic administrative screens and styling them afterward.
- Replacing the latitude atlas with cards.
- Introducing an unrelated navigation, typography, color, shadow, or component system.
- Using decorative Greek motifs, blue dashboard conventions, or abundant gold.
- Letting host telemetry dominate service access or turning Hyperion into a monitoring wall.
- Changing the existing Atlas incidentally while adding the new shell.

## 3. Information Architecture

The global product navigation becomes:

```text
Overview | Atlas | Schedules | Units | Activity
```

`Applications`, `Services`, and `Foundations` remain local destinations within Atlas.

The header search remains part of the established shell and becomes workspace-aware:

- **Overview:** global entity search.
- **Atlas:** service, endpoint, component, and image search.
- **Schedules:** schedule, command, source, and related-service search.
- **Units:** unit name, description, state, and related-service search.
- **Activity:** target, event type, result, and identity search.

Deep links must survive reloads for every selected service, unit, schedule, and activity record.

## 4. Phase 0 — Design Extension and Approval

No production feature implementation begins until this phase is accepted.

### 4.1 Capture the Atlas baseline

Create deterministic fixture-backed visual references for:

- Desktop, tablet, and phone.
- Loading, populated, empty, stale, partial, and unavailable states.
- Selected service dossier.
- Logs open.
- Search results and no-results state.
- Reduced motion.

These references become regression gates. Later shell work must demonstrate that Atlas has not drifted unintentionally.

### 4.2 Extend the design system

Specify new descendants of the existing system for:

- Global workspace navigation.
- Host metric instruments and compact historical traces.
- Attention rows and evidence notes.
- Schedule rows, provenance labels, and next-run timelines.
- Unit rows and expanded unit dossiers.
- Execution-history records.
- Primary, secondary, unavailable, pending, successful, and failed operational actions.
- Confirmation and impact-preview surfaces.
- Activity field notes and day groupings.
- Desktop, tablet, and mobile transformations for every new surface.

This work extends `DESIGN.md`; it does not establish a replacement visual world.

### 4.3 Produce high-fidelity prototypes

Design all four new workspaces with realistic fixture data before wiring live providers:

#### Overview

- Host identity, uptime, freshness, and overall condition.
- CPU, memory, storage, and network instruments in one continuous ruled field.
- An attention band that outranks ordinary metric readings.
- Upcoming schedules.
- Service-state bearings linked to Atlas.
- Recent meaningful activity.

#### Schedules

- Unified systemd-timer and cron index.
- Human-readable schedule, raw source, next run, last result, owner, and enabled state.
- Inline schedule dossier.
- A restrained next-24-hours timeline to expose overlap and clustering.

#### Units

- Curated default inventory rather than an undifferentiated dump of every vendor unit.
- Failed, catalog-related, locally authored, Hyperion-managed, and recently active units.
- State, enabled status, active-since time, restart count, memory, and timer relationship.
- Inline evidence, dependencies, unit definition, and journal disclosure.

#### Activity

- Chronological field notes grouped by day.
- Operator actions, schedule executions, recoveries, state changes, and configuration changes.
- Clear target, identity, result, time, and evidence.

### 4.4 Design approval gate

Phase 0 is complete only when:

- The existing Atlas remains visually and behaviorally intact.
- The new workspaces unmistakably belong to the same Service Atlas world.
- No generic card grid or admin-template structure has appeared.
- Desktop and mobile compositions are both resolved.
- Operational actions look consequential without becoming visually theatrical.
- Loading, empty, stale, unavailable, unauthorized, pending, success, and failure states are designed.

## 5. Phase 1 — Application Shell

This phase introduces product-level navigation without introducing operational writes.

### 5.1 Frontend structure

- Establish `/overview`, `/atlas`, `/schedules`, `/units`, and `/activity` routes.
- Preserve the existing Atlas behavior as its own workspace module.
- Keep Jinja as the semantic shell and TypeScript ES modules without a component framework.
- Extract reusable shell, search, status, row, dossier, log, action, and toast primitives only where the existing implementation proves them reusable.
- Use the approved fixture prototypes for incomplete workspaces.
- Preserve focus, selected entity, search query, scroll position, and disclosure state across background refreshes.

### 5.2 Completion gate

- All current Atlas unit, integration, and browser tests pass.
- Atlas visual references show no unintended drift.
- Workspace routes reload correctly.
- Keyboard navigation and visible focus work throughout the shell.
- Tablet, phone, and reduced-motion behavior match the approved design.

## 6. Phase 2 — Read-Only Host Telemetry

This is the first live vertical slice.

### 6.1 Host evidence

Add a dedicated host provider for:

- CPU utilization.
- 1, 5, and 15-minute load averages.
- Total, available, and used memory.
- Swap usage.
- Filesystem capacity, free space, utilization, and inode pressure.
- Network ingress/egress totals and derived rates.
- Hostname, uptime, and boot time.
- Optional temperature and Linux pressure-stall evidence when available.

Missing evidence remains absent. Hyperion must not manufacture percentages, rates, temperatures, or history.

### 6.2 Sampling

- Sample bounded current evidence every 5–10 seconds.
- Retain approximately 30 minutes of samples in memory initially.
- Derive CPU utilization and network rates from monotonic counter differences.
- Detect counter resets and interface changes.
- Pause browser polling while the document is hidden and refresh immediately on return.
- Keep host sampling independent from the existing 15-second service reconciliation.

### 6.3 API boundary

Keep host evidence separate from the existing Atlas snapshot:

```text
GET /api/v1/host
GET /api/v1/host/history?window=30m
```

The existing Atlas contract remains compatible.

### 6.4 Overview behavior

Each metric instrument contains:

- One dominant current value.
- One supporting value or threshold.
- A restrained single-line trace.
- A written condition when attention is required.
- Explicit fresh, stale, partial, unsupported, and unavailable states.
- An inline detail disclosure for filesystems, interfaces, or supporting evidence.

The attention band remains visually and semantically ahead of ordinary utilization values.

### 6.5 Completion gate

- Provider tests cover normal, partial, unavailable, counter-reset, filesystem-full, and interface-change conditions.
- Host collection cannot block Atlas reconciliation.
- Payloads remain bounded.
- Overview functions without long-term database persistence.
- The rendered surface matches the approved Overview prototype at desktop and mobile widths.

## 7. Phase 3 — Read-Only Units and Schedules

Observation precedes control.

### 7.1 Systemd inventory

Expand systemd inspection beyond catalog-allowlisted components through a dedicated inventory boundary.

The default view includes:

- Failed units.
- Units backing Atlas services.
- Hyperion-managed units.
- Locally authored units.
- Recently active or changed units.

An explicit `All units` filter exposes the full system inventory without allowing vendor units to dominate the default view.

Evidence includes:

- Load, active, and sub-state.
- Unit-file enabled state.
- Description.
- Active-since time.
- Main PID.
- Restart count.
- Memory where available.
- Related timer and dependencies.
- Related Atlas service.
- Protection classification.

### 7.2 Schedule inventory

Normalize two sources into one schedule model:

- systemd timers.
- existing cron definitions.

Each schedule exposes:

- Stable identity.
- Human-readable schedule.
- Raw calendar or cron expression.
- Next run.
- Last activation and result when knowable.
- Execution owner.
- Enabled state.
- Target unit or command.
- Source provenance.
- Related Atlas service.

Cron limitations must remain explicit. If last result, duration, or next execution cannot be established reliably, the UI shows `Not observed` rather than inferred evidence.

### 7.3 API boundary

Introduce read-only resources such as:

```text
GET /api/v1/units
GET /api/v1/units/{unitName}
GET /api/v1/units/{unitName}/logs
GET /api/v1/schedules
GET /api/v1/schedules/{scheduleId}
```

Unit and log identifiers must come from server-enumerated inventory; the API must not become an arbitrary `systemctl` or `journalctl` gateway.

### 7.4 Completion gate

- Fixture parsing covers the installed systemd version and real cron forms used by the host.
- Unit names, journal targets, cron paths, and execution identities are strictly validated.
- Read-only Units and Schedules surfaces match the approved prototypes.
- No lifecycle operation endpoint exists yet.

## 8. Phase 4 — Persistent Activity

Add a bounded SQLite store under Hyperion's writable state directory.

Record meaningful events only:

- Host thresholds entered and recovered.
- Unit state transitions.
- Timer executions and results.
- Service state transitions.
- Deployment identity changes.
- Operator-requested operations.
- Schedule or configuration changes.

Do not persist raw high-frequency telemetry initially.

### 8.1 Store requirements

- Versioned schema migrations.
- Bounded retention, initially 60–90 days.
- Automatic pruning.
- Stable event identity and timestamp ordering.
- Idempotent insertion where providers may report the same transition repeatedly.
- Backup and restore procedure.
- Clear behavior when the database is unavailable or read-only.

### 8.2 Product behavior

Activity enables:

- `Since your last visit` summaries.
- Schedule execution history.
- Unit recovery history.
- Reliable action feedback.
- The audit foundation required before write controls.

## 9. Phase 5 — Controlled Systemd Operations

Hyperion's FastAPI process remains unprivileged. It must never receive broad root or arbitrary shell access.

### 9.1 Privileged boundary

Introduce a narrow root-owned operation helper over a local Unix socket or an equivalently constrained system boundary. It accepts typed requests only:

- Start an allowed unit.
- Stop an allowed unit.
- Restart an allowed unit.
- Enable an allowed unit.
- Disable an allowed unit.
- Trigger an allowed existing service once.

The helper must:

- Verify the calling process identity.
- Validate exact unit names.
- Enforce operation and target allowlists.
- Reject arbitrary flags, paths, commands, and shell syntax.
- Enforce protected-unit policy independently from the web application.
- Return structured operation results.
- Produce structured audit evidence.

### 9.2 Web request controls

Every write request requires:

- Authenticated identity.
- Same-origin and CSRF protection.
- Exact target and typed operation.
- Idempotency key.
- Expected current revision or state to prevent stale actions.
- Rate limiting.
- A server-created operation record.

### 9.3 Protected units

Critical units are protected by default, including at least:

- SSH/access path.
- Networking.
- Docker.
- Authentik/authentication path.
- Caddy/public routing.
- Hyperion itself.

Protection must be enforced at the privileged boundary, not only hidden in the UI.

### 9.4 Interaction sequence

1. Select a unit.
2. Request an operation.
3. Show the exact target and likely impact.
4. Confirm through the approved lifted confirmation surface.
5. Submit one idempotent request.
6. Show pending state.
7. Reconcile the resulting systemd evidence.
8. Show success, partial success, timeout, or failure.
9. Record the request and result in Activity.

### 9.5 Completion gate

- No arbitrary `systemctl`, journal, path, argument, or shell access is possible.
- Duplicate submissions cannot repeat the operation accidentally.
- Interrupted and timed-out operations resolve honestly after reconciliation.
- Protected-unit policy is independently tested.
- Every action produces an Activity record.
- The UI handles pending, success, denied, stale-state, timeout, helper-unavailable, and failed states.

## 10. Phase 6 — Hyperion-Managed Schedules

New schedules are presented as `Schedules` but implemented as systemd service/timer pairs.

### 10.1 Schedule definition

A managed schedule contains:

- Name and description.
- Human schedule builder.
- Raw schedule reveal.
- Preview of the next five executions.
- Target executable and argument vector.
- Execution user.
- Working directory.
- Timeout.
- Overlap policy.
- Missed-run behavior.
- Optional related Atlas service.
- Enabled state.

### 10.2 Generated units

- Use a Hyperion-owned unit naming convention.
- Write only within a dedicated allowlisted unit-file namespace.
- Validate the complete definition before writing.
- Write atomically.
- Run daemon reload through the privileged helper.
- Restore the previous valid files if validation or reload fails.
- Store a revision for optimistic concurrency.
- Keep generated service and timer definitions inspectable in the dossier.

### 10.3 Cron policy

Existing imported cron entries remain read-only in the first release. Hyperion does not silently rewrite files owned by the operator, packages, deployment tooling, or another configuration system.

Raw crontab editing is a separate future decision because it introduces conflict detection, file ownership, locking, syntax preservation, privilege, and rollback concerns.

### 10.4 Completion gate

- Schedule previews match actual systemd calendar behavior.
- Invalid definitions never replace the last valid pair.
- Create, edit, enable, disable, run-now, and delete flows are audited.
- Execution history and logs are linked to the schedule dossier.
- Mobile editing and confirmation flows remain fully operable.

## 11. Phase 7 — Hardening and Release

### 11.1 Security verification

Test at least:

- Unit-name, path, argument, and command injection.
- Forged identity headers.
- Cross-site write requests.
- Duplicate submissions.
- Stale-state operations.
- Privileged helper impersonation.
- Unauthorized unit and journal access.
- Protected-unit operations.
- Malicious or malformed cron content.
- Oversized journal and activity payloads.
- Secrets or environment values appearing in APIs.

### 11.2 Failure verification

Exercise:

- Host evidence partially unavailable.
- Counter reset or host reboot.
- systemd unavailable.
- Cron source unreadable.
- Activity database unavailable or corrupt.
- Privileged helper unavailable.
- Hyperion restart while an operation is pending.
- Target unit changing during confirmation.
- Schedule reload failure.
- Browser losing connectivity after an action.

### 11.3 Product and visual verification

- Run unit, integration, contract, frontend, browser, accessibility, and reduced-motion suites.
- Compare the Atlas against its baseline references.
- Inspect Overview, Schedules, Units, and Activity at desktop and mobile widths in one bounded pass.
- Correct all material findings in one batch.
- Perform at most one confirmation pass.
- Run the Impeccable design detector over changed UI targets.

### 11.4 Deployment verification

- Update the systemd hardening profile without broadening Hyperion's own privileges.
- Install and verify the operation helper independently.
- Verify Unix-socket ownership and peer validation.
- Verify database directory ownership, backup, restore, and pruning.
- Verify Caddy/AuthentiK header stripping and write-request protection.
- Rehearse rollback to the last read-only Hyperion release.

## 12. Recommended First Release

The first production increment should contain:

1. Approved multi-workspace design extension.
2. Global application shell with the existing Atlas preserved.
3. Live read-only host Overview.
4. Read-only Units inventory and unit logs.
5. Read-only systemd-timer and cron inventory.
6. No write controls.

This release validates the information architecture and provider evidence before introducing privileged behavior.

## 13. Decisions That Do Not Block the First Release

The following remain explicit decisions for the write phases:

1. Whether Hyperion may create arbitrary root-executed jobs.
2. Whether imported cron definitions will ever become editable.
3. Whether protected core units can be unlocked through stronger reauthentication or remain permanently unavailable.
4. Whether host history remains short-lived or later gains bounded long-term retention.

Initial recommendation:

- Do not allow arbitrary root-executed schedule definitions in the first managed-schedule release.
- Keep imported cron definitions read-only.
- Keep access-critical units protected at the privileged boundary.
- Retain only short in-memory host history until actual use demonstrates a need for persistence.

## 14. Definition of Complete

The operations-console initiative is complete only when:

- New surfaces conform to the existing Hyperion visual system.
- Atlas behavior and appearance remain intact except for approved global navigation integration.
- Host metrics are truthful, bounded, fresh, and resilient to partial provider loss.
- Units and schedules are discoverable without exposing arbitrary host interfaces.
- Activity provides durable, bounded, auditable history.
- FastAPI remains unprivileged.
- Every systemd write crosses a separately enforced typed privileged boundary.
- Protected units cannot be operated accidentally or by bypassing the UI.
- Managed schedules are atomic, revisioned, inspectable, and recoverable.
- Every action has explicit pending and terminal evidence.
- Desktop, mobile, keyboard, focus, reduced-motion, loading, stale, empty, unauthorized, and failure states pass verification.

## 15. First Work Package

The first authorized work package should be Phase 0 only:

1. Capture Atlas visual baselines.
2. Extend the design contract for the global shell and four new workspaces.
3. Create realistic high-fidelity Overview, Schedules, Units, and Activity prototypes.
4. Resolve desktop and mobile behavior.
5. Present the result for approval.

Backend implementation begins only after that design gate is approved.
