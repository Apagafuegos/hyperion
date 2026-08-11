# Hyperion deployment

Fresh-install scope: `tar` extraction below does not remove files that are no
longer in the repository — on a reinstall, start from an empty `/opt/hyperion`
(`sudo rm -rf /opt/hyperion` first) to avoid stale files.

1. Create the service account and directories (idempotent — safe to re-run):

       sudo useradd --system --no-create-home --shell /usr/sbin/nologin hyperion 2>/dev/null || true
       sudo usermod -aG systemd-journal hyperion
       sudo mkdir -p /opt/hyperion /etc/hyperion /var/lib/hyperion /run/hyperion
       sudo chown hyperion:hyperion /var/lib/hyperion
       sudo chown root:hyperion /run/hyperion
       sudo chmod 0750 /run/hyperion

2. Make `uv` reachable by the hyperion user (system-wide `node`/`npm` at
   `/usr/bin` are already available to all users). The `ubuntu` home directory
   is `drwxr-x---`, so the hyperion user cannot traverse `/home/ubuntu`; copy
   the binary to `/usr/local/bin` instead:

       sudo install -m 0755 "$(command -v uv)" /usr/local/bin/uv

3. Install the code (from a clone of this repository — `rsync` is not installed
   on this VPS, so tar is used; heavy and cache directories are excluded):

       sudo mkdir -p /opt/hyperion
       cd /path/to/repo
       tar -cf - --exclude='.git' --exclude='node_modules' --exclude='.venv' \
         --exclude='test-results' --exclude='playwright-report' \
         --exclude='.impeccable' . | sudo tar -C /opt/hyperion -xf -
       sudo chown -R hyperion:hyperion /opt/hyperion

4. Build dependencies as the hyperion user. The account has no home directory,
   so `HOME=/opt/hyperion` must be set for `uv` (cache) and `npm` (logs/cache):

       sudo -u hyperion env HOME=/opt/hyperion /usr/local/bin/uv sync --frozen
       cd /opt/hyperion
       sudo -u hyperion env HOME=/opt/hyperion npm ci --silent
       sudo -u hyperion env HOME=/opt/hyperion npm run build

5. Configure the environment (root-only):

       sudo tee /etc/hyperion/env >/dev/null <<'EOF'
HYPERION_CATALOG_PATH=/etc/hyperion/services.yaml
HYPERION_DOCKER_HOST=tcp://127.0.0.1:2375
HYPERION_BIND_HOST=127.0.0.1
HYPERION_BIND_PORT=8787
HYPERION_LOG_LEVEL=INFO
HYPERION_STATE_DIR=/var/lib/hyperion
HYPERION_OPS_SOCKET=/run/hyperion/ops.sock
HYPERION_OPS_CALLER_UID=996
HYPERION_MANAGED_UNIT_DIR=/etc/systemd/system
EOF
       sudo chmod 600 /etc/hyperion/env
       sudo chown root:hyperion /etc/hyperion/env
       sudo cp services.yaml /etc/hyperion/services.yaml
       sudo chown root:hyperion /etc/hyperion/services.yaml
       sudo chmod 640 /etc/hyperion/services.yaml

   `HYPERION_OPS_CALLER_UID` must be the UID of the `hyperion` system user that
   the web process runs as (`id hyperion`); the privileged helper refuses every
   other caller. `HYPERION_MANAGED_UNIT_DIR` is the directory where
   Hyperion-managed schedule unit files may be written — it must be
   `/etc/systemd/system` so systemd loads them, and only `hyperion-*` names in
   that allowlisted namespace are ever touched.

6. Create the operation allowlist (exact unit names an operator may start,
   stop, restart, enable, or disable — mirror the systemd components declared
   in `/etc/hyperion/services.yaml`, excluding protected units):

       sudo tee /etc/hyperion/ops-allowlist.json >/dev/null <<'EOF'
["t3code.service"]
EOF
       sudo chown root:hyperion /etc/hyperion/ops-allowlist.json
       sudo chmod 640 /etc/hyperion/ops-allowlist.json

6. Start the Docker socket proxy (pinned image + digest):

       docker compose -f deploy/docker-proxy.compose.yaml up -d
       # The digest is pinned in the compose file; if you upgrade the proxy,
       # re-pin the digest with:
       docker image inspect --format '{{index .RepoDigests 0}}' \
         tecnativa/docker-socket-proxy:v0.5.0

7. Install and start the services:

       sudo cp deploy/hyperion.service /etc/systemd/system/hyperion.service
       sudo cp deploy/hyperion-ops.service /etc/systemd/system/hyperion-ops.service
       sudo systemctl daemon-reload
       sudo systemctl enable --now hyperion hyperion-ops
       journalctl -u hyperion -n 50

8. Verify:

       curl -s http://127.0.0.1:8787/healthz          # {"status":"ok"}
       curl -s http://127.0.0.1:8787/readyz           # 200 only after first snapshot

   The operations-console workspaces are served at `/overview`, `/atlas`,
   `/schedules`, `/units`, and `/activity`, each behind the same Authentik
   identity header. The privileged operation helper listens on the root-owned
   `/run/hyperion/ops.sock` and is required for start/stop/restart operations,
   daemon reloads, and managed-schedule installs; without it those actions
   report `helper_unavailable` and everything else remains read-only.

   Exercise the privileged boundary end to end:

       curl -s http://127.0.0.1:8787/healthz                # {"status":"ok"}
       sudo -u hyperion env HOME=/opt/hyperion \
         /opt/hyperion/.venv/bin/python - <<'PY'
       import asyncio, json
       from hyperion.ops import OperationHelperClient
       async def main():
           result = await OperationHelperClient(__import__("pathlib").Path("/run/hyperion/ops.sock")).request("t3code.service", "restart")
           print(json.dumps(result, indent=2))
       asyncio.run(main())
       PY

   A non-root caller or a mismatched `HYPERION_OPS_CALLER_UID` is refused with
   `caller not permitted`.

Docker Compose projects are discovered automatically every 15 seconds. The
catalog at `/etc/hyperion/services.yaml` is a metadata/systemd overlay and is
reloaded on the same interval; changing it does not require restarting the
Hyperion service. Invalid catalog updates are logged and the last valid version
remains active.
