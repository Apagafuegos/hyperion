# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Hyperion is currently for its owner, who manages multiple deployed services and needs a quick, reliable way to recall and reach them.

## Product Purpose

Hyperion is a personal hub for deployed systems. It keeps every service close at hand, exposes whether each system is available, and provides direct access without requiring the user to remember where everything lives. Success means the desired service can be found and opened within seconds.

## Positioning

The product is an owned map of one person's deployed world: service discovery and direct access are the primary mechanism, while health and logs add operational context without turning the product into a heavyweight observability suite.

## Operating Context

The product is used mainly for brief visits and quick checks rather than continuous monitoring. The primary workflow is opening Hyperion, locating a service, confirming its state, and following its link. Logs are a secondary workflow.

## Capabilities and Constraints

- Present all deployed services in one place.
- Provide direct links to those services.
- Show whether systems are up.
- Provide access to service logs as a secondary capability.
- Treat logical services as the primary product entity; containers, workers, databases, and systemd units are runtime components.
- Target one VPS in the initial architecture, with Docker Compose and systemd as the first runtime providers.
- Keep the initial operational surface inspection-only: no restart, stop, deploy, delete, or shell controls.
- Longer-term hub and cross-system communication behavior remains open.
- The v1 implementation contract uses Python 3.12, FastAPI/Pydantic, a single Uvicorn worker, Docker and systemd providers, and a Jinja + TypeScript client without a component framework.

## Brand Commitments

- Product name: Hyperion, inspired by the Greek figure associated with watching from above.
- Greek mythology should inform the identity without using Greek-letter motifs.
- Avoid the blue palette convention commonly associated with Greek-themed designs.
- Favor an off-white, washed, or lightly beige environment.
- Use gold sparingly as an elegant detail, never as an overflowing decorative treatment.
- The interface must remain comfortable and legible as a working tool.

## Evidence on Hand

- An approved high-fidelity latitude-band prototype exists in `index.html`, with its design system in `DESIGN.md` and `.impeccable/design.json`.
- The current VPS inventory, Caddy routes, Docker Compose ownership labels, Docker health/logging configuration, and T3 Code systemd unit were inspected during technical design.
- The approved production-catalog seed is `services.example.yaml`; operational values must continue to come from the manifest and providers rather than invented UI data.
- No final product logo or imagery is required by the approved Service Atlas direction.

## Product Principles

- Access before analysis: getting to a service is the main job.
- Recognition over recall: Hyperion should remember the deployed landscape for the user.
- State at a glance: status must be readable without inspecting details.
- Operational depth on demand: logs and deeper information stay available without dominating the default view.
- Catalog meaning over runtime accidents: deployed processes are grouped through explicit service ownership, never promoted automatically.
- Mythology as structure and atmosphere, not costume.
