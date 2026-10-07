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

- [`docs/portfolio-demo.md`](docs/portfolio-demo.md) — timed demo flow, voiceover, and recording commands
- `PRODUCT.md` — product boundary and priorities
- `DESIGN.md`, `.impeccable/design.json` — the approved Service Atlas visual system
- `TECHNICAL-DESIGN.md` — the final implementation contract
- `docs/superpowers/plans/2026-08-04-hyperion-v1-part*.md` — implementation plan

## Development

Fixture mode (no Docker needed):

    HYPERION_FIXTURE_MODE=1 HYPERION_CATALOG_PATH=tests/fixtures/fixture-services.yaml \
      uv run uvicorn hyperion.main:app --host 127.0.0.1 --port 8787 --workers 1

## MCP connector

`hyperion-mcp` serves streamable HTTP at `http://127.0.0.1:8101/mcp`. It
provides the `get_service`, `get_logs`, `get_deployment`, and `restart_service`
tools expected by chat-orchestrator. The connector reads Hyperion's local HTTP
API, which must be running, and uses a fixed `X-Authentik-Username` service
identity. Keep both processes bound to loopback or place the MCP endpoint behind
TLS and an authenticated reverse proxy.

Set `HYPERION_MCP_TOKEN` to a random secret of at least 32 characters. This
credential can only read. The optional, distinct `HYPERION_MCP_WRITE_TOKEN`
allows `restart_service`; it also requires the service ID in the comma-separated
`HYPERION_MCP_RESTART_SERVICES` allowlist. Only a catalog service with exactly
one primary systemd unit can be restarted. Hyperion's unit allowlist and
protected-unit policy still apply. A durable SQLite record at
`HYPERION_MCP_IDEMPOTENCY_DB` (default
`/var/lib/hyperion/mcp-idempotency.db`) prevents a repeated approval key from
issuing a second restart. An interrupted call with an unknown result remains
ambiguous and requires inspection before any new approval.

Optional configuration:

| Variable | Default | Purpose |
|---|---|---|
| `HYPERION_MCP_UPSTREAM_URL` | `http://127.0.0.1:8787` | Hyperion API origin |
| `HYPERION_MCP_HOST` | `127.0.0.1` | MCP bind address |
| `HYPERION_MCP_PORT` | `8101` | MCP port |
| `HYPERION_MCP_ALLOWED_HOSTS` | localhost addresses | Additional comma-separated HTTP `Host` values for a trusted reverse proxy |

The chat-orchestrator Compose stack builds the read-only connector with
[`deploy/Dockerfile.mcp`](deploy/Dockerfile.mcp) and runs it on host loopback.
The stack supplies `HYPERION_MCP_TOKEN` from its `.env` file. For standalone
development, start the connector with `uv run hyperion-mcp`. The example
[`deploy/hyperion-mcp.service`](deploy/hyperion-mcp.service) is an alternative
systemd unit. `get_deployment` reads labels from the service's primary Docker
container. Put these labels on that Compose service; populate them from the
trusted deployment pipeline. `org.opencontainers.image.revision` must contain a
full Git commit SHA. The tool returns an error when the primary container or any
required label is missing. It does not infer deployment details from an image
tag or repository branch.

```yaml
services:
  app:
    labels:
      hyperion.deployment.id: "${DEPLOYMENT_ID}"
      org.opencontainers.image.revision: "${GIT_COMMIT_SHA}"
      hyperion.deployment.started_at: "${DEPLOYMENT_STARTED_AT}"
      hyperion.deployment.completed_at: "${DEPLOYMENT_COMPLETED_AT}"
      hyperion.deployment.status: "${DEPLOYMENT_STATUS}"
      hyperion.environment: "production"
```

Deployment timestamps must be timezone-aware ISO 8601 values. The current
deployment is the only one discoverable from a running container; historical
deployment lookup is not provided by Docker labels.

chat-orchestrator connects with the read token and enables `get_service`,
`get_logs`, and `get_deployment`. It excludes `restart_service` until a separate
write credential is configured and wired through the orchestrator's approval
flow.

Host-owned `get_service_runtime`, `get_endpoint_listeners`, and `inspect_process`
are available through the read credential. See [the diagnostics contract](docs/diagnostics.md)
for endpoint configuration, evidence, authorization, collection limits, and host
visibility requirements. Collection runs in the host API, independently of the
MCP container's process namespace.
