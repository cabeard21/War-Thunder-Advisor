# Private Raspberry Pi deployment

The production image supports `linux/arm64` (Raspberry Pi 64-bit OS) and `linux/amd64`.
CI publishes `ghcr.io/cabeard21/war-thunder-advisor:sha-<full-commit-sha>` after tests,
coverage gates, frontend checks, and an ARM64 image smoke test pass. Release tags also
receive their matching `v*` tag; there is no `latest` deployment dependency. Pin the full
commit tag, or the registry digest for an immutable deployment. Private GHCR packages
require a read-only registry credential on the deployment host; never put it in Compose
or this repository.

The dashboard and API listen on container port **8765**. `GET /health` returns
`{"status":"ok"}` once startup initialization succeeds. Docker's health check calls
this endpoint over container loopback. It does not require external provider access
or a completed advisor calculation. Normal startup runs `wt-advisor dashboard` with
bundled production frontend assets; no Node or frontend development server is needed.
Health confirms successful initialization and a responding HTTP server, not continuous
database integrity or evidence freshness.

## Runtime configuration

| Variable | Local default | Container default / requirement |
| --- | --- | --- |
| `WT_ADVISOR_DB` | `wt-advisor.sqlite` in the working directory | `/data/wt-advisor.sqlite`; mount `/data` |
| `WT_ADVISOR_BIND_ADDRESS` | `127.0.0.1` | `0.0.0.0` inside the container |
| `WT_ADVISOR_PORT` | `8765` | `8765`; integer from 1024 through 65535 |
| `WT_ADVISOR_ALLOWED_HOSTS` | Loopback hosts | Required when binding outside loopback; comma-separated exact LAN/Tailscale DNS names or IPs used in browser URLs |

Allowed hosts contain no scheme, port, path, or wildcard. Include each hostname/IP you
actually use to reach the app. Loopback remains available for health checks. Binding
outside loopback without an explicit non-loopback allowlist fails startup. This host
allowlist and same-origin write protection do not provide authentication: keep the
dashboard on private LAN/Tailscale interfaces with no public forwarding.

Set `WT_ADVISOR_PORT` when changing the container port and update both Compose port mapping
values accordingly. A CLI `dashboard --port` override takes precedence for the server; if
used in a container, set `WT_ADVISOR_PORT` to the same value so the image health check agrees.

No application API secret is required. Registry and optional tunnel credentials belong
in private host configuration or the tunnel client's environment. MCP remains a separate
stdio process; the dashboard image neither creates nor exposes an MCP tunnel.

## Compose example

Prepare the bind directory on the Pi (the image runs as UID/GID **10001**):

```sh
mkdir -p data
sudo chown 10001:10001 data
```

Use this service in the private deployment stack. Set `WT_ADVISOR_IMAGE` to a published
full SHA tag, `WT_ADVISOR_PUBLISH_ADDRESS` to the Pi's LAN or Tailscale IP, and
`WT_ADVISOR_ALLOWED_HOSTS` to the exact private hosts used by clients. Keep these values
in the deployment host's private environment, outside Git.

```yaml
services:
  war-thunder-advisor:
    image: ${WT_ADVISOR_IMAGE:?Set a pinned GHCR image tag or digest}
    ports:
      - "${WT_ADVISOR_PUBLISH_ADDRESS:?Set a LAN or Tailscale IP}:8765:8765"
    environment:
      WT_ADVISOR_DB: /data/wt-advisor.sqlite
      WT_ADVISOR_BIND_ADDRESS: 0.0.0.0
      WT_ADVISOR_PORT: "8765"
      WT_ADVISOR_ALLOWED_HOSTS: ${WT_ADVISOR_ALLOWED_HOSTS:?Set exact private hostnames or IPs}
    volumes:
      - ./data:/data
    restart: "no"
```

The default is on-demand operation. Start with `docker compose up -d war-thunder-advisor`,
then inspect `docker compose ps` and `docker compose logs war-thunder-advisor`.
Check `http://<private-host>:8765/health` from a private client. A healthy container
does not verify LAN routing or Tailscale access; perform that client check separately.

Mount the whole data directory, so the SQLite database and any journal/WAL sidecars stay
outside the container writable layer. Do not mount a single database file. Existing
databases must also be writable by UID/GID 10001. Generated profiles, imported evidence,
and advisor state remain in this database. There is no separate server-side account file
to mount. Do not copy private state into a build context.

## Startup, upgrades, and backups

Startup migrates the selected database through the retained Alembic history to the
current schema and initializes the frozen acceptance fixtures idempotently. Acceptance
fixtures are not live operational evidence. Import operational evidence deliberately:

```sh
docker compose exec war-thunder-advisor wt-advisor data import --source wt-api --json
docker compose exec war-thunder-advisor wt-advisor data refresh-community --json
```

After CLI evidence imports, restart the long-running dashboard to reload the selected
snapshots: `docker compose restart war-thunder-advisor`.

Back up existing data before changing image tags. Use SQLite's online backup API for a
live database, or stop all writers before a filesystem copy; raw-copying a live WAL
database is unsafe. For example, create a consistent backup on the mounted data directory:

```sh
docker compose exec war-thunder-advisor python -c "import sqlite3; source=sqlite3.connect('/data/wt-advisor.sqlite'); destination=sqlite3.connect('/data/wt-advisor-backup.sqlite'); source.backup(destination); destination.close(); source.close()"
```

Use a unique backup filename for each upgrade, retain it privately, and stop the service
before restoring. Rollback may require restoring the matching backup; older images are
not promised to read newer schemas. Never commit databases, sidecars, backups, or exports.

## Build and local development

```sh
docker buildx build --platform linux/arm64 --load -t wt-advisor:local .
python scripts/container_smoke.py wt-advisor:local
```

The smoke test needs Docker with ARM64 emulation or an ARM64 host. It uses a disposable
bind mount, checks the health endpoint and frontend assets, upgrades an older database,
and verifies data after restart. CI installs QEMU and performs this check before publishing.

Existing editable Python installs, `npm run dev`, frontend builds, tunnel launchers, and
loopback dashboard startup continue to work. Container-only defaults live in the Dockerfile;
local database selection and `dashboard --port` remain available.
