# Hyperion

A calm, instrument-dense service atlas for everything deployed on the VPS.

Read-only control surface: automatic Docker Compose discovery, a curated YAML
metadata/systemd overlay, public-route probing, bounded on-demand logs, and an
authenticated single-page atlas client.

## Service discovery

Docker Compose is the operational source of truth. Hyperion discovers Compose
projects and their components every 15 seconds, so a newly deployed project
appears without editing `services.yaml` or restarting Hyperion. Existing YAML
entries enrich discovered projects with names, actions, dependency roles, and
route probes; YAML remains required for systemd services because Docker cannot
discover them.

Discovery uses the standard `com.docker.compose.project` and
`com.docker.compose.service` labels. Optional `hyperion.*` labels can describe
a project when no YAML overlay exists. Put project metadata on the primary
Compose service:

```yaml
services:
  api:
    labels:
      hyperion.primary: "true"
      hyperion.id: "example-api"
      hyperion.name: "Example API"
      hyperion.description: "Internal example service"
      hyperion.territory: "services"       # applications | services | foundations
      hyperion.kind: "api"                 # web | api | mcp | worker | infrastructure
      hyperion.url: "https://api.example.com"
      hyperion.action: "open"              # open | copy | none
      hyperion.probe: "true"
```

Component labels are `hyperion.role`, `hyperion.required`,
`hyperion.component-name`, and `hyperion.logs`. Projects that are implementation
details can opt out with `hyperion.enabled: "false"`. Without labels, Hyperion
uses safe defaults: one inferred primary, remaining components as optional
sidecars, all logs enabled, no public action, and no route probe.

The YAML overlay is also checked every 15 seconds. Valid changes are applied
without a process restart; invalid changes are rejected while the last valid
catalog remains active.

## Documents

- `PRODUCT.md` — product boundary and priorities
- `DESIGN.md`, `.impeccable/design.json` — the approved Service Atlas visual system
- `TECHNICAL-DESIGN.md` — the final implementation contract
- `docs/superpowers/plans/2026-08-04-hyperion-v1-part*.md` — implementation plan

## Development

Fixture mode (no Docker needed):

    HYPERION_FIXTURE_MODE=1 HYPERION_CATALOG_PATH=tests/fixtures/fixture-services.yaml \
      uv run uvicorn hyperion.main:app --host 127.0.0.1 --port 8787 --workers 1
