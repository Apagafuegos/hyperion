# Hyperion deployment

1. Create the service account and directories:

       sudo useradd --system --no-create-home --shell /usr/sbin/nologin hyperion
       sudo usermod -aG systemd-journal hyperion
       sudo mkdir -p /opt/hyperion /etc/hyperion

2. Install the code (from a clone of this repository):

       sudo rsync -a --exclude node_modules --exclude .venv --exclude .git \
         . /opt/hyperion/
       cd /opt/hyperion
       uv sync --frozen
       npm ci && npm run build

3. Configure the environment (root-only):

       sudo tee /etc/hyperion/env >/dev/null <<'EOF'
   HYPERION_CATALOG_PATH=/etc/hyperion/services.yaml
   HYPERION_DOCKER_HOST=tcp://127.0.0.1:2375
   HYPERION_BIND_HOST=127.0.0.1
   HYPERION_BIND_PORT=8787
   HYPERION_LOG_LEVEL=INFO
   EOF
       sudo chmod 600 /etc/hyperion/env
       sudo chown root:hyperion /etc/hyperion/env
       sudo cp services.yaml /etc/hyperion/services.yaml
       sudo chown root:hyperion /etc/hyperion/services.yaml
       sudo chmod 640 /etc/hyperion/services.yaml

4. Start the Docker socket proxy (pinned image + digest):

       docker compose -f deploy/docker-proxy.compose.yaml up -d
       # The digest is already pinned in the compose file; after any image
       # change, re-verify with:
       docker image inspect --format '{{index .RepoDigests 0}}' \
         tecnativa/docker-socket-proxy:v0.5.0

5. Install and start the service:

       sudo cp deploy/hyperion.service /etc/systemd/system/hyperion.service
       sudo systemctl daemon-reload
       sudo systemctl enable --now hyperion
       journalctl -u hyperion -n 50

6. Verify:

       curl -s http://127.0.0.1:8787/healthz          # {"status":"ok"}
       curl -s http://127.0.0.1:8787/readyz           # 200 only after first snapshot
