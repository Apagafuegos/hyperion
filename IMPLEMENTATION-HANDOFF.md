# Hyperion Implementation Handoff

The design phase is complete. This file is the entry point for the implementation agent.

## Read in this order

1. `PRODUCT.md` — product boundary and user priorities.
2. `DESIGN.md` and `.impeccable/design.json` — normative visual system.
3. `.impeccable/surfaces/index-html.md` — approved atlas interaction commitments.
4. `TECHNICAL-DESIGN.md` — complete architecture, providers, state rules, deployment, and acceptance criteria.
5. `schema/services.schema.json` — normative manifest structure.
6. `schema/openapi.yaml` — normative HTTP contract.
7. `services.example.yaml` — validated production-catalog seed based on the inspected VPS.
8. `index.html` — approved high-fidelity visual prototype; its data is illustrative and must not be treated as product truth.

## Locked v1 decisions

- Logical services are top-level; containers and systemd units are components.
- Applications, Services, and Foundations are the three atlas territories.
- Python 3.12, FastAPI, Pydantic v2, PyYAML, HTTPX, Docker SDK, Uvicorn with exactly one worker, and `uv`.
- Jinja application shell, strict TypeScript ES modules built by Vite, and authored CSS; no component framework or UI library.
- One host-native systemd process, one in-memory snapshot, no database.
- Docker access only through a pinned localhost-only Tecnativa socket proxy with GET/HEAD container inspection enabled and POST disabled.
- Exact allowlisted `systemctl` and `journalctl` subprocesses for systemd state and logs.
- Authentik forward auth at Caddy plus application enforcement of `X-Authentik-Username`.
- Read-only inspection: no lifecycle actions, shell, environment values, or generic host APIs.
- Snapshot polling every 15 seconds, route probes every 30 seconds, evidence stale after 45 seconds.
- Logs are bounded, on demand, and non-streaming in v1.

## Implementation discipline

- Build phase by phase from Section 15 of `TECHNICAL-DESIGN.md`.
- Keep committed schemas and generated Pydantic/OpenAPI output equivalent in CI.
- Start the UI against deterministic fixture snapshots before connecting live providers.
- Treat all provider text and log messages as untrusted.
- Do not silently infer services, ownership, health, or intended dormancy.
- Do not copy MCP Observatory's environment-variable exposure behavior.
- Do not change the visual topology into a card grid or monitoring wall.
- Any contract change must update the technical design, formal schema, fixtures, and tests together.

## Inputs still required at deployment time

- Final Hyperion public hostname and Caddy TLS behavior.
- Pinned image digest for the selected Docker socket proxy release.
- Owner-approved final descriptions for any catalog entries whose current copy is intentionally generic.
- Optional explicit decision to add Caddy, Docker, or Tailscale to Foundations; they are excluded by default.

These are environment values or catalog copy, not unresolved architecture decisions.

## Completion gate

The implementation is not complete until every criterion in Section 16.2 of `TECHNICAL-DESIGN.md` passes, including exact runtime ownership, T3 journal access, denied Docker mutations, partial-provider behavior, schema/API parity, and bounded desktop/mobile visual verification.
