# Hyperion Technical Design

**Status:** Final implementation contract  
**Scope:** Single-owner, single-VPS, read-only service atlas  
**Companion documents:** `PRODUCT.md`, `DESIGN.md`, `.impeccable/design.json`, `schema/services.schema.json`, `schema/openapi.yaml`

Implementation may refine internal names, but changing a decision marked **Final** requires updating this document and its formal schema in the same change.

## 1. Purpose

Hyperion is the service-level control surface for the VPS. It answers four questions quickly:

1. What services are deployed?
2. Where do I open or address each service?
3. Is each service currently reachable and operational?
4. What have its runtime components logged recently?

Hyperion is not a container inventory with friendlier styling. Containers, systemd units, databases, workers, and sidecars are implementation details attached to logical services.

## 2. Product Boundary

### Included in v1

- Curated catalog of logical services.
- Direct launch links or copyable machine endpoints.
- Docker Compose and systemd runtime inspection.
- Public-route probing.
- Docker stdout/stderr and systemd journal logs.
- Service-level state derived from runtime components and route probes.
- Search, territory grouping, service dossier, and on-demand logs.
- Authentik-protected access through the existing Caddy reverse proxy.

### Explicitly excluded from v1

- Restart, stop, delete, deploy, scale, or shell controls.
- Container environment values or secret inspection.
- Distributed hosts, Kubernetes, or cloud-provider discovery.
- Long-term metrics, log retention, alerts, incident management, or tracing.
- Arbitrary Docker API access, arbitrary `journalctl` units, or user-authored probe URLs.
- A required database. Current state may be held in memory and reconstructed.

## 3. Core Model

Hyperion uses four distinct concepts.

| Concept | Meaning | Atlas treatment |
|---|---|---|
| Service | Something intentionally opened, addressed, or operated as one product | Top-level route |
| Component | A process that makes one service run: app, worker, database, sidecar | Inside the service dossier |
| Foundation | Shared infrastructure used by multiple services | Foundation territory or dependency reference |
| Unmapped runtime | A discovered container or unit with no catalog ownership | Diagnostic notice, never promoted automatically |

The catalog defines meaning. Runtime discovery supplies evidence.

```text
services.yaml                       Docker / systemd
human-owned product structure      machine-owned runtime facts
             \                         /
              \                       /
               ---- reconciliation ----
                          |
                  normalized services
                          |
                      Atlas UI
```

### 3.1 Authority rules

1. Only catalog entries become top-level services.
2. A runtime component is attached through an explicit selector.
3. Docker Compose labels are discovery keys, not product definitions.
4. A component may have one owner. Multiple matches are a configuration error.
5. An unmatched runtime remains visible only as an unmapped diagnostic.
6. A missing declared component remains attached to its service and is reported as missing.
7. A service can exist without Docker, as T3 Code does through systemd.
8. A service can have an endpoint without a human-facing UI, as The Vault does.

## 4. Current VPS Service Map

The initial catalog is expected to describe the following logical services. This table records the current evidence, not a permanent naming contract.

| Service | Kind | Route/action | Runtime ownership |
|---|---|---|---|
| T3 Code | Web application | Open `https://t3.carlos-santos.es` | `t3code.service` |
| Authentik | Web application | Open `https://auth.carlos-santos.es` | Compose project `authentik` |
| LibreChat | Web application | Open `https://ai.carlos-santos.es` | Compose project `librechat` |
| RareCord | Web application | Open `https://rare.carlos-santos.es` | Compose project `rarecord` |
| Langfuse | Web application | Open `https://langfuse.carlos-santos.es` | Compose project `langfuse` |
| MCP Observatory | Web tool | Open `https://mcp.carlos-santos.es` | Compose project `mcp_observatory` |
| The Vault | MCP service | Copy endpoint `https://mcp.carlos-santos.es/the-vault/mcp` | Compose project `the-vault` |
| Shared PostgreSQL | Foundation | No launch action | Compose project `postgres` |

Caddy, Docker, and Tailscale are host infrastructure. They should be added only if their state is useful in Hyperion, not merely because discovery finds them.

## 5. Catalog Manifest

The manifest is a versioned, human-maintained metadata and systemd overlay. `schema/services.schema.json` is the normative structural schema. Pydantic models are the runtime implementation and must generate an equivalent JSON Schema in CI. The file is loaded at startup and revalidated every runtime cycle; valid changes replace the overlay atomically and invalid changes leave the last valid catalog active. Docker Compose projects and components are discovered from Docker labels every runtime cycle and merged into the overlay before reconciliation.

```yaml
version: 1

services:
  - id: langfuse
    name: Langfuse
    description: LLM observability and prompt operations
    territory: applications
    kind: web
    action:
      type: open
      url: https://langfuse.carlos-santos.es
    runtime:
      provider: docker-compose
      project: langfuse
      components:
        - selector: langfuse
          role: primary
          required: true
        - selector: langfuse-worker
          role: worker
          required: false
        - selector: redis
          role: dependency
          required: true
        - selector: clickhouse
          role: dependency
          required: true
        - selector: minio
          role: dependency
          required: true
    routeProbe:
      method: GET
      url: https://langfuse.carlos-santos.es
      acceptedStatusClasses: [2, 3, 4]
      timeoutMs: 3000
      slowAfterMs: 1200
      failureThreshold: 2
      followRedirects: false
    logs:
      sources: [langfuse, langfuse-worker]
```

### 5.1 Stable fields

- `id`: stable machine identifier; never inferred from a container ID.
- `name` and `description`: owner-authored recognition copy.
- `territory`: `applications`, `services`, or `foundations`.
- `kind`: `web`, `api`, `mcp`, `worker`, or `infrastructure`.
- `action`: `open`, `copy`, or `none`.
- `runtime`: provider and explicit component selectors.
- `routeProbe`: optional outside-in reachability check.
- `logs.sources`: allowlisted components whose output can be requested.
- `dependencies`: optional references to other catalog service IDs.
- `intent`: `active` or `dormant`; only this explicit field can produce Dormant state.

Defaults are applied during validation, before the catalog revision is computed: `intent: active`, probe status classes `[2, 3, 4]`, `timeoutMs: 3000`, `slowAfterMs: 1200`, `failureThreshold: 2`, `followRedirects: false`, `defaultTail: 100`, and `dependencies: []`.

Manifest property names are camelCase (`routeProbe`, `acceptedStatusClasses`, `timeoutMs`, `slowAfterMs`, `failureThreshold`, `followRedirects`, `defaultTail`); the snake_case forms are also accepted on load. `label` and `routeProbe` accept an explicit `null` value, which is equivalent to omitting the field. `action.type`, `runtime.provider`, and `routeProbe.method` are explicit fields with no defaults. `schema/services.schema.json` is generated-equivalent to the canonical Pydantic models in `src/hyperion/models.py`; `tests/unit/test_catalog_schema_parity.py` enforces the equivalence.

JSON Schema handles structure. The catalog loader additionally enforces these semantic invariants:

- Service IDs are unique.
- Component selectors are unique inside a runtime.
- Every service has exactly one `primary` component.
- Every log source names a component declared on the same service.
- Every dependency names an existing service and is not a self-reference.
- A Docker `(project, selector)` pair has only one owning service unless the component role is explicitly `shared`.
- `open` and `copy` actions and all route probes use HTTPS.
- Probe redirects are not followed in v1; `followRedirects` is reserved and must remain `false`.
- Unknown properties are rejected at every schema level.

### 5.2 Runtime selectors

Docker Compose components are resolved with the pair:

```text
com.docker.compose.project = runtime.project
com.docker.compose.service = component.selector
```

Container names and IDs may be displayed but must not be stable ownership keys. Systemd components are resolved by exact allowlisted unit name.

Hyperion-specific Compose labels may later validate or suggest ownership:

```yaml
labels:
  hyperion.service: langfuse
  hyperion.role: worker
```

Labels never silently override the central catalog.

## 6. Backend Architecture

### 6.1 Final stack

| Layer | Final choice | Reason |
|---|---|---|
| Runtime | Python 3.12 | Already installed on the VPS and fits Docker/system tooling well |
| Dependency management | `uv`, committed `uv.lock` | Reproducible Python environment and fast CI/install path |
| HTTP server | FastAPI + Uvicorn, one worker | Typed OpenAPI contract and one authoritative in-memory scheduler/snapshot |
| Validation | Pydantic v2 + PyYAML `safe_load` | Strict models, discriminated unions, and JSON Schema generation |
| HTTP probes | HTTPX async client | Bounded asynchronous HTTPS checks with explicit redirect behavior |
| Docker integration | Docker SDK for Python via restricted proxy | Stable high-level inspection and log APIs without exposing the raw socket to Hyperion |
| systemd integration | allowlisted `systemctl show` and `journalctl -o json` subprocesses | Uses the host's authoritative unit and journal interfaces without a native binding |
| HTML | Jinja2 application shell | Same-origin first render with no separate frontend server |
| Client behavior | TypeScript ES modules built by Vite | Typed polling, selection, filtering, disclosure, and log rendering without a component framework |
| Styling | Authored CSS using `DESIGN.md` tokens | Preserves the approved Service Atlas rather than introducing a generic UI library |
| JavaScript runtime | Node.js 22 for builds only | Matches the installed VPS toolchain; no Node process in production |
| Persistence | None in v1 | Catalog and current evidence are reconstructable; probe streaks live in memory |
| Tests | pytest + HTTPX test client; Vitest; Playwright | Unit/provider/API coverage plus bounded desktop/mobile browser verification |
| Quality | Ruff, mypy strict for application code, ESLint, TypeScript strict | Keeps provider boundaries and nullable evidence explicit |

Dependencies are locked, not specified as floating production ranges. The implementer selects current compatible patch versions and commits both Python and Node lockfiles. Pydantic models are canonical in code; generated OpenAPI and JSON Schema must match the committed contracts.

Hyperion is one host-native server application with bounded provider interfaces. FastAPI's lifespan context owns provider clients, the refresh supervisor, and shutdown. Exactly one Uvicorn worker is required because multiple workers would create independent probe streaks, refresh loops, and snapshots.

```text
                          +---------------------+
services.yaml ----------> | Catalog Loader      |
                          +----------+----------+
                                     |
Docker read proxy ------> Discovery  +--> Effective catalog
                          Docker     |
systemd + journal ------> Systemd    +--> Reconciler --> Snapshot Store
public HTTPS routes ----> Probes     |                      |
                          +----------+----------------------+---> HTTP API
                                                                |
                                                           Atlas client
```

### 6.2 Process and concurrency model

- The FastAPI lifespan loads the catalog overlay, discovers Docker Compose projects, creates provider clients, produces the initial snapshot, then starts one refresh supervisor.
- Readiness remains false until the catalog validates and the first snapshot is published. Docker or systemd may be unavailable inside a valid partial snapshot.
- One refresh cycle runs every 15 seconds using monotonic scheduling; overlapping cycles are skipped, never queued.
- Docker SDK calls run in a bounded thread pool of four workers because the SDK is synchronous.
- Systemd and journald calls use `asyncio.create_subprocess_exec` with fixed argv, a three-second timeout, and no shell.
- Route probes share one HTTPX client with connection pooling and a maximum of four concurrent probes.
- The reconciler constructs a new immutable snapshot and swaps one reference under a short lock.
- API handlers never call Docker, systemd, or route targets directly. Logs are the only on-demand provider operation.
- Graceful shutdown cancels the supervisor, waits up to five seconds, and closes provider clients.

### 6.3 Modules

**Catalog loader**

- Parses and validates the manifest.
- Rejects duplicate IDs, invalid actions, unsafe URLs, duplicate ownership, and unknown dependencies.
- Reloads the file overlay and merges Docker discovery every runtime cycle.
- Keeps the last valid catalog if a live reload fails.

**Docker provider**

- Lists containers and reads inspect, stats, health, Compose labels, and bounded logs.
- Promotes previously unknown Compose projects to services and appends newly discovered components to existing project overlays.
- Accepts optional `hyperion.*` metadata labels and honors `hyperion.enabled=false` for implementation-only projects.
- Does not expose a generic Docker proxy through the Hyperion API.
- Normalizes transient container IDs behind stable component keys.

**Systemd provider**

- Reads state only for exact unit names declared in the catalog.
- Retrieves bounded journal entries for declared units.
- Does not accept arbitrary unit names from client input.
- Reads unit state with `systemctl show UNIT --no-pager --property=ActiveState,SubState,MainPID,ExecMainStartTimestampMonotonic,NRestarts,LoadState`.
- Reads logs with `journalctl --unit UNIT --lines N --output json --no-pager --quiet`, adding `--until` only from a validated timestamp.

**Probe worker**

- Performs server-side checks only for validated manifest targets.
- Records latency, response class, error category, and observation time.
- Treats an authentication redirect as route reachability, not application health.

**Reconciler**

- Joins catalog entries to discovered runtime components.
- Identifies missing, duplicate, and unmapped runtimes.
- Derives component, runtime, route, and overall service state.

**Snapshot store**

- Holds the latest immutable normalized snapshot in memory.
- Swaps snapshots atomically after a successful refresh.
- Does not block API reads while Docker statistics or probes are collected.
- Computes an ETag from the canonical serialized snapshot and retains the last valid snapshot after provider errors.

**Log gateway**

- Resolves a service ID and source key through the catalog allowlist.
- Fetches bounded recent records from Docker or journald.
- Normalizes timestamps, source, stream, and message.
- Fetches stdout and stderr separately for Docker when possible, merges sources chronologically, and returns at most the requested total tail.

### 6.4 Provider contract and mappings

Providers return evidence; they never derive a service aggregate state.

```py
class RuntimeProvider(Protocol):
    async def observe(self, catalog: Catalog) -> list[ProviderObservation]: ...
    async def read_logs(
        self, binding: LogBinding, tail: int, before: datetime | None
    ) -> list[LogRecordIn]: ...

class RouteProbeProvider(Protocol):
    async def observe(
        self,
        probes: tuple[str, ...],
        config: dict[str, tuple[float, float]] | None = None,
    ) -> ProbeObservation: ...
```

The probe `observe` accepts an optional per-URL tuning map of `url -> (timeout_s, slow_after_s)` built from the catalog's `timeoutMs`/`slowAfterMs` (defaults `(3.0, 1.2)`); providers may ignore it. A probe that succeeds within the timeout but exceeds `slowAfterMs` is evidence state `slow` with a zero failure streak.

`ProviderObservation` contains `provider`, `state`, `observed_at`, normalized component evidence keyed by `(service_id, selector)`, unmapped runtimes, conflicts, and a sanitized provider error. Provider exceptions are captured at the provider boundary and do not cross into the reconciler.

Docker mapping is exact:

| Docker state | Component state |
|---|---|
| `running` | Running, unless health is `starting`, then Starting |
| `created` | Starting |
| `restarting` | Restarting |
| `paused` | Paused |
| `exited`, `dead`, `removing` | Stopped |
| Declared selector with no match | Missing |
| Anything else or provider failure | Unknown |

Docker health maps `healthy`, `starting`, and `unhealthy` directly; absent health configuration maps to Unconfigured. A Compose selector matching more than one container is Ambiguous evidence: no container is guessed as primary, the component becomes Unknown, and the reconciler emits an ambiguity reason. Stats are collected only for uniquely resolved, running catalog components. Unmapped discovery includes all containers with Compose identity that are not owned by a catalog binding; non-Compose containers are included with null project/component labels.

Systemd mapping is exact:

| `LoadState` / `ActiveState` | Component state / health |
|---|---|
| `not-found` | Missing / Unknown |
| `active` | Running / Healthy |
| `activating` | Starting / Starting |
| `reloading` | Starting / Starting |
| `deactivating` | Stopped / Unknown |
| `inactive` | Stopped / Unconfigured |
| `failed` | Stopped / Unhealthy |
| Anything else or provider failure | Unknown / Unknown |

Systemd `NRestarts` supplies restart count, monotonic start time supplies uptime when usable, and CPU/memory remain null in v1. Hyperion discovers unmapped Docker runtimes, but it does not enumerate every host systemd unit; only allowlisted units are queried, avoiding a noisy and sensitive host inventory.

Route probes use HTTPS only, no cookies, no Authentik credentials, no redirects, a fixed `User-Agent: Hyperion/1`, and a response body limit of 1 KiB because only status and timing are evidence. DNS, connect, TLS, write, read, and pool timeouts share the manifest's total upper bound.

### 6.5 Repository layout

```text
hyperion/
├── pyproject.toml
├── uv.lock
├── package.json
├── package-lock.json
├── services.yaml
├── schema/
│   ├── services.schema.json
│   └── openapi.yaml
├── src/hyperion/
│   ├── main.py
│   ├── settings.py
│   ├── catalog.py
│   ├── models.py
│   ├── state.py
│   ├── api/
│   ├── providers/
│   │   ├── base.py
│   │   ├── docker.py
│   │   ├── systemd.py
│   │   └── probe.py
│   ├── services/
│   │   ├── reconciler.py
│   │   ├── snapshots.py
│   │   └── logs.py
│   ├── templates/index.html
│   └── static/                # Vite output
├── frontend/
│   ├── atlas.ts
│   ├── api.ts
│   └── atlas.css
├── deploy/
│   ├── hyperion.service
│   ├── docker-proxy.compose.yaml
│   └── Caddyfile.example
└── tests/
    ├── fixtures/
    ├── unit/
    ├── integration/
    └── browser/
```

## 7. Normalized Data Model

The frontend consumes services, never raw Docker objects.

```ts
type ServiceSnapshot = {
  id: string
  name: string
  description: string
  territory: "applications" | "services" | "foundations"
  kind: "web" | "api" | "mcp" | "worker" | "infrastructure"
  action: ServiceAction
  state: "reachable" | "degraded" | "down" | "dormant" | "unknown"
  stateReasons: StateReason[]
  observedAt: string
  route: RouteSnapshot | null
  components: ComponentSnapshot[]
  dependencies: DependencySnapshot[]
  logSources: LogSource[]
}

type ComponentSnapshot = {
  key: string
  label: string
  provider: "docker" | "systemd"
  role: "primary" | "worker" | "dependency" | "sidecar" | "shared"
  required: boolean
  state: "running" | "starting" | "restarting" | "paused" | "stopped" | "missing" | "unknown"
  health: "healthy" | "starting" | "unhealthy" | "unconfigured" | "unknown"
  uptimeSeconds: number | null
  restartCount: number | null
  cpuPercent: number | null
  memoryBytes: number | null
}
```

Missing values remain `null`. Hyperion must not manufacture percentages or infer uptime history it did not observe.

## 8. State Derivation

State is textual and evidence-backed; color remains secondary.

The reconciler applies this precedence from top to bottom. The first matching terminal rule wins; informational reasons are still retained.

| Priority | Overall state | Exact rule |
|---:|---|---|
| 1 | Dormant | Catalog sets `intent: dormant`; runtime evidence is displayed but cannot override intent |
| 2 | Down | Primary is missing, stopped, paused, ambiguous, or Docker-health `unhealthy` |
| 3 | Down | Route has reached its configured consecutive-failure threshold |
| 4 | Unknown | Required provider evidence is unavailable or older than 45 seconds and no stronger Down evidence exists |
| 5 | Degraded | Primary is starting/restarting, or a required non-primary component is missing, stopped, paused, restarting, ambiguous, or unhealthy |
| 6 | Degraded | Route has one pending failure or exceeds `slowAfterMs` |
| 7 | Reachable | Primary is running; all required components are running and not unhealthy; configured route is reachable |
| 8 | Reachable | Same runtime condition as above and no route probe is configured |
| 9 | Unknown | No preceding rule can establish the service state |

Important rules:

- `running` is not synonymous with `reachable`.
- A Docker health check is stronger evidence than process state but does not replace the public-route probe.
- A route protected by Authentik may return a redirect and still be reachable.
- One transient probe failure produces Degraded; the default second consecutive failure produces Down. Any successful accepted response resets the streak to zero.
- Stale data becomes Unknown instead of retaining a reassuring old state.
- Every derived state includes machine-readable reasons for the dossier and accessible UI copy.
- Optional component failure creates an informational reason but does not change aggregate state.
- Docker health `unconfigured` is neutral. Health `starting` makes a primary transition Degraded and is not converted into healthy.
- HTTP status classes 2, 3, and 4 establish reachability by default. A 5xx response and transport failures count as failures.
- Redirects are not followed, so an Authentik redirect proves the route is reachable without probing through an authenticated session.

Canonical reason codes are `intent_dormant`, `primary_missing`, `primary_stopped`, `primary_paused`, `primary_ambiguous`, `primary_unhealthy`, `primary_transitioning`, `required_component_missing`, `required_component_stopped`, `required_component_paused`, `required_component_ambiguous`, `required_component_unhealthy`, `required_component_transitioning` (a required non-primary component is restarting), `optional_component_unavailable`, `route_failed`, `route_failure_pending`, `route_slow`, `provider_unavailable`, and `evidence_stale`. New codes are additive API changes.

## 9. Logs

The v1 log model reuses existing host retention.

```ts
type LogRecord = {
  timestamp: string | null
  source: string
  provider: "docker" | "journald"
  stream: "stdout" | "stderr" | "journal" | "unknown"
  message: string
}
```

Behavior:

- Logs load only when the selected service drawer is opened.
- Default tail is 100 records; allowed values are bounded, for example 50, 100, 250, and 500.
- Multi-component services expose a source filter and an aggregate chronological view.
- Messages are treated as untrusted text and never rendered as HTML.
- Very long lines and total response size are capped.
- Empty, unavailable, permission-denied, and component-stopped states are distinct.
- Historical search and live following are deferred. A later follow mode should use server-sent events rather than exposing provider streams directly.
- `tail` is one of 50, 100, 250, or 500 and applies to the merged result, not independently to every source.
- `before` is an exclusive RFC 3339 timestamp. Docker receives its Unix-second equivalent through `until`; journald receives the validated timestamp through `--until`.
- Results are ordered oldest to newest. Entries without a timestamp sort after timestamped entries while preserving provider order.
- A message is capped at 16 KiB, the full JSON response at 2 MiB, and provider execution at five seconds.
- Docker stdout and stderr are requested separately when the container is not using a TTY. TTY output uses `unknown` stream.
- Journald severity maps syslog priorities `7..0` to `debug`, `info`, `notice`, `warning`, `error`, `critical`, `alert`, and `emergency`.
- Hyperion never heuristically assigns severity by parsing Docker message text.

## 10. HTTP API

All endpoints require the established Authentik identity header except the internal health and readiness checks. `schema/openapi.yaml` is the normative HTTP contract.

```text
GET /healthz
GET /readyz
GET /api/v1/snapshot
GET /api/v1/services/:serviceId/logs?source=:sourceKey&tail=100&before=:timestamp
```

The snapshot contains the summary, provider state, all logical services, component details, log-source availability, and unmapped-runtime diagnostics. At this fleet size, splitting list and detail endpoints would add synchronization failure modes without meaningful payload savings.

`GET /api/v1/snapshot` supports `ETag` and `If-None-Match`, returns `Cache-Control: private, max-age=0, must-revalidate`, and may return `304`. Logs return `Cache-Control: private, no-store`.

Error responses use a stable envelope:

```json
{
  "error": {
    "code": "LOG_SOURCE_UNAVAILABLE",
    "message": "Logs are unavailable for this component.",
    "retryable": true
  }
}
```

Error codes are uppercase stable identifiers. The initial set is `AUTHENTICATION_REQUIRED`, `SNAPSHOT_UNAVAILABLE`, `SERVICE_NOT_FOUND`, `LOG_SOURCE_NOT_FOUND`, `LOG_SOURCE_UNAVAILABLE`, `INVALID_TAIL`, `INVALID_BEFORE`, `INVALID_REQUEST`, `PROVIDER_TIMEOUT`, and `RESPONSE_TOO_LARGE`.

## 11. Refresh and Performance

This is a quick-check dashboard, not a monitoring wall.

- Container and unit state: refresh every 15 seconds.
- CPU and memory: collect only for running, catalog-owned components.
- Route probes: refresh every 30 seconds with the manifest timeout, three seconds by default.
- Unmapped runtime scan: refresh less frequently or with the runtime snapshot.
- Browser: fetch one snapshot on entry and poll every 15 seconds while visible; pause when the document is hidden and refresh immediately when it becomes visible.
- Logs: request only on disclosure, selection change, or explicit refresh.
- Provider failures must not discard the last valid snapshot; affected evidence becomes stale and then Unknown.
- A snapshot is fresh through 45 seconds after its evidence observation; the API-level `fresh` flag is false when any required provider evidence crosses that threshold.
- Snapshot payload target is below 100 KiB for the current fleet. Logs have an independent 2 MiB hard limit.
- Search is entirely client-side over the current snapshot and never triggers provider work.

## 12. Security Boundary

Docker socket access is effectively host-level privilege. Mounting the Unix socket read-only does not make Docker API operations read-only. Hyperion must therefore minimize and isolate this access.

Required controls:

- Bind Hyperion to loopback and expose it only through Caddy.
- Reuse Authentik forward authentication and independently require its identity header.
- Use `ghcr.io/tecnativa/docker-socket-proxy` pinned to a release and image digest. Bind it only to `127.0.0.1:2375`; enable `CONTAINERS=1`, `INFO=1`, `PING=1`, and `VERSION=1`; keep `POST=0` and every other API section disabled.
- Keep all provider credentials and host paths server-side.
- Never return container environment values, even with heuristic secret-name filtering.
- Allowlist Docker projects, Compose services, systemd units, log sources, and probe URLs through the catalog.
- Allow only `https` public actions by default; permit loopback probe targets only through explicit server configuration.
- Disable redirects to arbitrary hosts during internal probing, or revalidate every redirect target.
- Apply response-size, log-line, timeout, and request-rate limits.
- Escape log and runtime strings in the client and use a restrictive Content Security Policy.
- Record configuration errors without leaking paths, tokens, or Docker inspect payloads.

### 12.1 Final deployment topology

Hyperion runs as a host systemd service under a dedicated unprivileged `hyperion` account. It binds `127.0.0.1:8787`, connects to the Docker proxy at `tcp://127.0.0.1:2375`, and receives membership only in `systemd-journal` for journal reads. It is not a member of the `docker` group and never mounts `/var/run/docker.sock`.

The systemd unit uses `NoNewPrivileges=yes`, `PrivateTmp=yes`, `ProtectSystem=strict`, `ProtectHome=read-only`, `ProtectKernelTunables=yes`, `ProtectKernelModules=yes`, `ProtectControlGroups=yes`, `RestrictSUIDSGID=yes`, `LockPersonality=yes`, `RestrictRealtime=yes`, `MemoryDenyWriteExecute=yes`, an explicit writable runtime directory only if required, and an environment file readable only by root. The implementation must verify that journal and project-manifest access still work under these controls.

Caddy performs Authentik `forward_auth`, copies only the identity headers Hyperion uses, strips any client-supplied `X-Authentik-*` headers before authentication, and reverse-proxies to `127.0.0.1:8787`. Hyperion independently rejects management requests without `X-Authentik-Username`. `/healthz` and `/readyz` remain loopback-only through Caddy routing and reveal only `status`.

The production server command is one process:

```text
uv run uvicorn hyperion.main:app --host 127.0.0.1 --port 8787 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

The app logs structured JSON to stdout/stderr; systemd owns restart behavior and log retention. `Restart=on-failure` with a short bounded delay is the deployment default.

### 12.2 Configuration

Only these environment settings exist in v1:

| Variable | Default | Purpose |
|---|---|---|
| `HYPERION_CATALOG_PATH` | `/etc/hyperion/services.yaml` | Validated catalog path |
| `HYPERION_DOCKER_HOST` | `tcp://127.0.0.1:2375` | Restricted Docker proxy |
| `HYPERION_BIND_HOST` | `127.0.0.1` | Uvicorn bind host |
| `HYPERION_BIND_PORT` | `8787` | Uvicorn port |
| `HYPERION_LOG_LEVEL` | `INFO` | Application log threshold |

Refresh intervals, timeouts, limits, and state thresholds are code constants or catalog fields, not an unbounded environment-variable surface.

## 13. Atlas Mapping

The technical model preserves the approved Service Atlas interaction model:

- **Applications:** human-facing products with a strong Open action.
- **Services:** APIs, MCP endpoints, and background capabilities; Copy Endpoint may replace Open.
- **Foundations:** shared databases and platform services; state and logs without a launch action.
- **Service route:** normalized service identity, overall state, last observation, and primary action.
- **Selected dossier:** route evidence first, components second, dependencies and recent runtime events third.
- **Logs drawer:** scoped to the selected service and closed by default.
- **Bearings:** counts logical services, never containers.
- **Unmapped runtime:** small diagnostic notice outside the main bearings so accidental containers do not distort service state.

The UI must never present aggregate container CPU as a fake service-health percentage. Resource usage and health are separate facts.

### 13.1 Final client behavior

- Jinja renders the semantic application shell, approved SVG symbol set, and a no-script explanation. Runtime rows are rendered from `/api/v1/snapshot` by TypeScript.
- Client state is limited to `snapshot`, `selectedServiceId`, `query`, `activeTerritory`, `logsOpen`, `logSource`, `logTail`, and request states. URL hashes preserve the selected service as `#service=<id>`.
- Search matches service name, description, kind, territory, action URL, component label, and component image. It does not search log content.
- The first matching service becomes selected only when the current selection is filtered out. Clearing search restores the prior valid selection when possible.
- Snapshot refresh patches changed text/state in place and preserves focus, expanded dossier, search, scroll position, and loaded logs.
- Logs never auto-refresh in v1. The drawer provides explicit Refresh, source, and tail controls.
- Open actions use normal anchors with `target="_blank"` and `rel="noopener noreferrer"`. Copy actions use the Clipboard API with a text-selection fallback and a status toast.
- All provider and request states have explicit loading, empty, stale, partial, unavailable, and retry presentations. Previously loaded evidence remains visible with a stale label when safe.
- The client creates text nodes or assigns `textContent`; provider strings and logs never enter `innerHTML`.
- The three territory orders are fixed: Applications, Services, Foundations. Empty territories remain visible with a compact empty explanation so atlas topology does not jump.
- Bearings count service aggregate states only. Unmapped runtime count is a diagnostic keycap, not a fifth health bearing.
- The current `index.html` is a visual prototype and must be decomposed into the Jinja shell, TypeScript modules, and authored CSS without changing the approved visual contract.

## 14. Material Failure States

The implementation must design and test at least these conditions:

- Manifest missing or invalid at startup.
- Valid catalog with no services.
- Docker unavailable while systemd remains available.
- Journald unavailable while Docker remains available.
- Public route unavailable but runtime running.
- Public route reachable but one dependency unhealthy.
- Required component missing.
- Optional worker stopped.
- Duplicate or ambiguous runtime ownership.
- Unmapped containers discovered.
- Logs empty, unsupported, denied, too large, or temporarily unavailable.
- Snapshot stale because a provider refresh failed.
- Authentik identity header missing.
- Search with no matching services.

## 15. Implementation Sequence

Each phase ends with passing tests and a runnable vertical slice. Security controls are not deferred beyond the feature that needs them.

### Phase 1 — Contracts and fixture atlas

1. Scaffold the selected Python and TypeScript stack with locked dependencies.
2. Implement strict Pydantic catalog and API models; verify committed JSON Schema and OpenAPI parity.
3. Implement catalog semantic validation and canonical revision hashing.
4. Serve the approved atlas against deterministic fixture snapshots through `/api/v1/snapshot`.
5. Implement client polling, filtering, selection, actions, all request states, and accessible responsive behavior.

### Phase 2 — Live providers and reconciliation

1. Deploy the restricted Docker proxy and implement Docker discovery, inspect, health, stats, and logs.
2. Implement systemd state and journald providers using fixed subprocess argv.
3. Implement the refresh supervisor, immutable snapshot store, reconciliation diagnostics, and exact state precedence.
4. Add bounded route probes and in-memory failure streaks.
5. Replace fixtures with the validated production catalog and verify every ownership selector.

### Phase 3 — Deployment and hardening

1. Install the hardened host systemd unit and Caddy + Authentik route.
2. Verify header stripping, same-origin behavior, security headers, loopback-only health endpoints, and proxy denial of Docker POST requests.
3. Exercise provider outages, stale snapshots, oversized logs, invalid catalogs, and permission failures.
4. Run unit, integration, contract, accessibility, and browser suites.
5. Perform one desktop/mobile visual comparison against the approved latitude-band reference and one bounded correction pass.

### Deferred

- Live log following.
- Persisted observation history and event timelines.
- Notifications or alerting.
- Read-write operational controls.
- Multiple VPS agents.

## 16. Finalization and Handoff

There are no unresolved architecture decisions for v1. The following are deployment inputs, not design choices, and may be supplied during implementation:

- Final public hostname and Caddy certificate behavior.
- The installed release digest for the Docker socket proxy.
- Exact product descriptions where the owner wants copy more specific than the evidence currently supports.
- Whether Caddy, Docker, or Tailscale should be intentionally added to the Foundations catalog. They remain excluded by default.

### 16.1 Final decisions

- One VPS, one Hyperion process, one in-memory snapshot, no database.
- Python 3.12, FastAPI, Pydantic v2, Uvicorn single worker, `uv` lockfile.
- Jinja shell, strict TypeScript, Vite, authored CSS, no frontend component framework.
- Central YAML catalog is authoritative; JSON Schema plus semantic validation is normative.
- Docker Compose labels resolve components; systemd units use exact allowlisted names.
- Restricted localhost Docker proxy; Hyperion never receives the raw socket.
- Read-only inspection only; no lifecycle or shell operations.
- Docker and journald logs are bounded and fetched on demand; no log store and no live tail.
- Exact state precedence and a 45-second freshness boundary.
- One snapshot API and one log API, versioned under `/api/v1`.
- Authentik at Caddy plus application-level identity-header enforcement.
- Applications, Services, and Foundations are the final atlas territories.

### 16.2 Definition of implementation complete

Implementation is complete only when:

- The committed production catalog validates structurally and semantically.
- Every declared current component resolves exactly once; unexpected containers are reported as unmapped.
- T3 Code state and journal logs work through the systemd provider.
- Every Docker-backed service exposes correct component state and allowlisted recent logs.
- Public launch/copy actions match the Caddy routes.
- State derivation passes a table-driven test for every rule and precedence collision.
- Provider loss yields partial/stale/Unknown states without losing the last valid snapshot.
- No API returns environment values, raw inspect payloads, credentials, or arbitrary host data.
- Docker POST requests through the proxy are demonstrably denied.
- The UI meets the behavior, responsive, focus, reduced-motion, and visual commitments in `DESIGN.md`.
- The project can be installed from clean lockfiles and run through the supplied systemd and Caddy deployment artifacts.

## 17. Primary Technical References

- [FastAPI lifespan events](https://fastapi.tiangolo.com/advanced/events/) for owning shared clients and the refresh supervisor.
- [FastAPI worker deployment](https://fastapi.tiangolo.com/deployment/server-workers/) for the explicit single-worker choice.
- [Pydantic JSON Schema](https://pydantic.dev/docs/validation/latest/concepts/json_schema/) for Draft 2020-12 and OpenAPI 3.1 generation.
- [Docker SDK for Python](https://docker-py.readthedocs.io/en/stable/) and its [container API](https://docker-py.readthedocs.io/en/stable/containers.html) for inspect, stats, and bounded stdout/stderr logs.
- [systemd `journalctl`](https://www.freedesktop.org/software/systemd/man/latest/journalctl.html) for allowlisted units, JSON records, time bounds, and journal permissions.
- [Caddy `forward_auth`](https://caddyserver.com/docs/caddyfile/directives/forward_auth) for the existing authentication boundary.
- [Tecnativa Docker Socket Proxy](https://github.com/Tecnativa/docker-socket-proxy) for method and API-section restrictions around the privileged Docker socket.
