# Read-only host diagnostics

The host API owns collection. The MCP adapter forwards authenticated reads;
its host networking does not provide a host process view. No process-control
operations or arbitrary commands, paths, unit names, addresses, or PIDs are
accepted by these tools.

| MCP tool | Arguments | Host API (GET query parameters) |
| --- | --- | --- |
| `get_service_runtime` | `service` | `/api/v1/diagnostics/service-runtime` |
| `get_endpoint_listeners` | `service`, `endpoint_id` | `/api/v1/diagnostics/endpoint-listeners` |
| `inspect_process` | `service`, `process_ref` | `/api/v1/diagnostics/process` |

Inputs reject additional properties and enforce resource identifier constraints.
The same Pydantic contracts define HTTP results and MCP output schemas. MCP
returns validated `structuredContent` and an equivalent JSON text block. HTTP
reads use `Cache-Control: private, no-store`. The deployment also validates
advertised output schemas and collects a fresh result for each diagnostic call.

## Resource scope and discovery

Runtime diagnostics resolve the service's primary systemd unit from the catalog.
Version 1 targets the system manager. Other runtime providers return explicit
unavailable evidence. Host TCP endpoint reads support IPv4 and IPv6 literals;
DNS names, scoped IPv6 addresses, UDP, and other network namespaces are not
accepted by the catalog contract.

```yaml
diagnostics:
  endpoints:
    primary:
      namespace: host
      protocol: tcp
      address: 127.0.0.1
      port: 3773
```

The service snapshot exposes configured endpoints as `diagnosticEndpoints`.
MCP `list_services` returns endpoint IDs and `get_service` returns their
definitions. Runtime evidence includes the same configured definitions. The
agent must use discovery's service ID rather than infer it from application
names or documentation examples.

All catalog services are authorized for the deployment's authenticated read
principal. A read credential's fingerprint binds process references upstream;
trusted administrator identities use their own principal. Credentials do not
give references access to another principal or another service. Chat memory
isolation does not create separate host authorization for chat users. A future
multi-user host product needs upstream user and tenant authorization.

## Evidence and consistency

Each result has schema version `1.0`, a unique snapshot ID and source URI, host
and boot identity, collector PID/network namespace references, UTC collection
start/end timestamps, typed data, status, and machine-readable issues with
affected fields. Unavailable observations have null data; missing fields have
null values with issues describing the limitation.

The collection is non-atomic. Stable consistency means that the defined checks
detected no relevant change: runtime properties are read before and after unit
process collection; socket identities are compared before and after owner
resolution; process start ticks and descriptor matches are checked while
resolving owners; inspection compares selected metadata, cgroups, manager
properties, and process identity before returning. Host scope is checked again
at completion. A failed consistency check is unknown; a detected change is
reported as changed. Fields are observations within an interval, not a claim
that all fields existed simultaneously.

`MainPID` is the observed current PID, including zero when absent.
`last_execution.pid` is an execution record and never issues a process reference.
Execution results, exit status, and available wall/monotonic timestamps remain
separate from current process facts. A counter increase across later samples
can establish continuing restarts; a high counter or an older log line cannot.

Owner resolution returns every visible holder of each socket, bounded by the
limits below. If no owner can be established, `owners` is null with an issue,
never an empty success. Restricted procfs visibility (`hidepid`) and inaccessible
process descriptors reduce coverage. Missing visibility is ordinary partial
evidence, not proof that a socket has no owning process.

Wildcard listeners are included when their address overlaps the configured
endpoint. Cross-family IPv6 wildcard/mapped addresses are labeled potential
overlaps. Socket sharing/reuse settings and IPv6-only behavior are not collected
in this version, so `incompatible_bind` is explicitly unknown. The collector
does not declare a bind-conflict root cause.

## Process references and disclosure

A reference identifies host, boot, collector PID namespace, PID, and start
ticks. It is an opaque random token stored in the host API for 120 seconds.
The store holds at most 4,096 grants; eviction or host API restart requires
rediscovery. Deploy the host API with one worker, as in the supplied service.
Inspection rechecks identity and unit/endpoint scope. PID reuse or process exit
returns unavailable evidence with changed consistency; expired or out-of-scope
references are request/tool errors. A permission failure does not imply that
the process stopped or changed service membership.

Ownership resolution records the observed cgroup path and inferred unit/user
scope or container identity. Expected unit membership uses the manager's
ControlGroup with an exact path or descendant boundary. Same-unit children are
recognized independently of MainPID. Ownership method and uncertainty are
returned explicitly; names alone do not prove service membership.
Process membership retains hierarchy IDs, controller names, and paths.
Ownership uses the unified hierarchy or the named systemd hierarchy on cgroup
v1; an unrelated CPU-controller path cannot establish unit ownership. Manager
unit aliases are resolved through the observed `Id` property.

Parent context includes one current parent PID and readable basic identity.
Parents receive no inspection grant and are never recursively enumerated. The
current parent may be reparented and is not necessarily the original launcher.

Selected process names, executable paths, working directories, and interpreter
script basenames use the shared redaction policy. Command summaries derive
executable identity from procfs, omit every remaining argument, and never read
environment variables or arbitrary process file contents. Metadata visibility
is limited by the Hyperion host account.

## Collection limits and deployment

Each tool has a five-second collection budget; each fixed systemd subprocess
has a two-second timeout and 64 KiB bounds on stdout/stderr. Results are limited
to 12,000 serialized JSON characters before MCP's text equivalent is added.
Lists contain at most 24 unit processes, 24 sockets, and 12 owners per socket.
Scans consider at most 8,192 PIDs, 16,384 tasks, 65,536 descriptors, and 4 MiB per
socket table. Socket ownership includes thread descriptor tables and returns
process identities, accounting for threads with unshared descriptors.
Catalogs permit at most 16 diagnostic endpoints per service. Truncation always
reduces coverage and adds an issue. If a result cannot fit without losing its
contract, it becomes unavailable rather than a false empty observation.

Fixture mode disables live host diagnostics. Known container process views and
readable mismatched PID/network namespaces are rejected. The provider must run
in the host API service; it neither enters namespaces nor grants itself extra
privileges. Namespace links for PID 1 may be permission restricted, so ordinary
host collection does not require privileged reads of those links.

Ship the Hyperion host API/catalog, rebuild its MCP adapter, and deploy the
orchestrator tool policies together. This implementation does not install a
privileged helper or change the host account's permissions. On the current
host, an isolated read under `hyperion` established the listening socket but
returned `OWNER_PERMISSION_DENIED` for ownership. Complete owner inspection
requires an independently scoped local helper or a deliberate host visibility
policy; the tools preserve that limitation until it is provided.

## Validation

Provider acceptance tests cover unit children, different system/user launchers,
shared owners, wildcard/IPv6 overlap, permission gaps, restricted procfs,
reference scope/expiry, process exit/PID reuse, metadata changes, manager changes,
truncation, timeout/output limits, disclosure, and container scope rejection.
Integration tests exercise authorized HTTP collection through MCP schemas and
structured content, strict input rejection, credential rotation, and partial
results. Deployment tests verify output validation, operations-only tool
allowlists, fresh samples with separate citations, partial evidence retention,
and diagnostic instructions that prohibit unsupported stop recommendations.

The diagnostic provider's PID identity follows
[proc_pid_stat](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html), and
runtime field separation follows the
[systemd interface](https://raw.githubusercontent.com/systemd/systemd/main/man/org.freedesktop.systemd1.xml).
Structured result validation follows the
[MCP tool contract](https://modelcontextprotocol.io/specification/2026-07-28/server/tools).
