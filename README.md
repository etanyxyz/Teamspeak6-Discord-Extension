# Teamspeak6-Discord-Extension

A small asyncio service that mirrors a **TeamSpeak 6** server to a **Discord**
channel via webhook: connect/disconnect events and text messages, each with the
sender (nickname + unique ID), the target (server / channel / private), and a
timestamp.

The bridge talks to TS6 over its **SSH ServerQuery** interface (port 10022,
the only query transport that can deliver a notify event stream in beta14) and
posts formatted embeds to a Discord webhook.

---

## What it mirrors (and what it can't)

This is **TeamSpeak 6 beta14** behaviour, verified empirically (see
[docs/sample-events.txt](docs/sample-events.txt)). Read this before deploying —
channel coverage is *partial by design*, not a bug.

| Event                       | Coverage                                                        |
|-----------------------------|-----------------------------------------------------------------|
| Connect / disconnect        | **Server-wide** — every client, any channel.                    |
| Server chat (`/server`)     | **Server-wide.**                                                |
| Channel chat                | **Only the channel the bridge's query client sits in.**        |
| Private messages            | **Off by default**, and limited even when on.                  |

- **Channel chat is scoped to one channel.** TS6 delivers `textchannel`
  notify events only for the channel the query client is currently in.
  Registering for a different channel is accepted but yields nothing; the
  admin query group already has full view power, so this is a server-side
  scope, not a permission you can grant away. Set `TS_BRIDGE_CHANNEL` (by
  name) to move the bridge to a specific channel; leave it empty to sit in
  the server's default channel. There is no way to mirror *all* channels.
- **Private messages are unreliable.** `FORWARD_PRIVATE_MESSAGES` defaults to
  `false`. TS6 rejects private messages *sent by* query clients
  (`error 516 invalid client type`), and whether a query client ever receives
  privates addressed *to* it is unverified in beta14. Treat private
  mirroring as best-effort/unavailable.

Everything the bridge does forward is posted as a single Discord embed with
`allowed_mentions: {parse: []}` so nothing can ping or @-mention from the TS
side.

---

## Requirements

- A TeamSpeak 6 server with SSH query enabled (`TSSERVER_QUERY_SSH_ENABLED=true`).
- A Discord **webhook URL** for the target channel.
- Python 3.12 (the Docker image bundles it; for a bare install see
  [Development](#development)).

---

## Quick start (Docker)

```bash
git clone git@github.com:etanyxyz/Teamspeak6-Discord-Extension.git
cd Teamspeak6-Discord-Extension
cp .env.example .env
# fill in TS_QUERY_USER / TS_QUERY_PASSWORD / DISCORD_WEBHOOK_URL
docker compose up -d --build
```

`docker-compose.yml` starts both the TS6 server and the bridge on a shared
internal network; the bridge reaches the server by the service name `ts6`.

### First start

On first boot the TS6 server prints its credentials (query login password,
WebQuery API key, admin token) in the container log:

```bash
docker logs ts6 2>&1 | grep -iE "password|api key|token"
```

Put those into `.env`. The server state (database, keys) persists in the
`ts6-data` volume.

### Query IP allowlist (important)

TS6's ServerQuery flood-protection only trusts IPs in
`/var/tsserver/query_ip_allowlist.txt` (default: loopback). When the bridge
runs as a separate container its source IP is the container's address, so add
the compose network's subnet to that file once:

```bash
docker exec ts6 sh -c \
  "printf '172.18.0.0/16\n' >> /var/tsserver/query_ip_allowlist.txt"
docker restart ts6
```

The exact subnet depends on your Docker bridge network — inspect it with
`docker network inspect`. This file lives in the persisted volume, so it
survives restarts. (Host-side query clients still work via the loopback
entries.)

> **Flood lockout:** rapid connect/auth attempts lock the offending source IP
> for ~10 minutes (in-memory; `docker restart ts6` clears it). The bridge uses
> one persistent connection with a keepalive and a capped exponential
> backoff, so it does not trip this in normal operation.

---

## Configuration

All settings come from environment variables (via `.env`). Copy
[.env.example](.env.example) to `.env` and edit.

| Variable | Required | Default | Meaning |
|----------|:--------:|---------|---------|
| `DISCORD_WEBHOOK_URL` | yes | — | Discord webhook URL for the target channel. |
| `TS_QUERY_USER` | yes | — | TS6 query login (e.g. `serveradmin`). |
| `TS_QUERY_PASSWORD` | yes | — | TS6 query password (first-start log / admin UI). |
| `TS_HOST` | no | `ts6` | Host the bridge connects to (compose service name). |
| `TS_SSH_PORT` | no | `10022` | SSH ServerQuery port. |
| `BRIDGE_NICKNAME` | no | `TS6-Discord-Bridge` | Nickname the bridge's query client shows as. |
| `TS_BRIDGE_CHANNEL` | no | *(empty)* | Channel name the bridge sits in (see channel scoping above). Empty = default channel. |
| `FORWARD_PRIVATE_MESSAGES` | no | `false` | Forward private messages (best-effort, see above). |
| `IGNORE_CLIENT_UIDS` | no | *(empty)* | Comma-separated unique IDs to ignore (join/leave/chat). |
| `TS_QUERY_API_KEY` | no | *(empty)* | WebQuery API key. Not used by the bridge (SSH is); keep for manual HTTP use. |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

The webhook URL is only ever read into memory and used as the POST target —
it is never logged.

---

## How it works

- **Transport:** a single persistent SSH connection (via `asyncssh`, no PTY)
  to port 10022. After auth, a shell channel is opened; the 2-line banner is
  drained and commands are exchanged as CRLF lines, each response ending in
  `error id=N msg=...`.
- **Setup:** `use sid=1` → `whoami` (own `clid`, so the bridge never forwards
  itself) → `clientupdate client_nickname=…` → optional `clientmove` to
  `TS_BRIDGE_CHANNEL` → seed a `clid → (nickname, uid)` cache from
  `clientlist -uid` → `servernotifyregister` for
  `server|textserver|textchannel|textprivate`.
- **Events:** `notifycliententerview` / `notifyclientleftview` /
  `notifytextmessage` lines are routed to an `asyncio.Queue`. Leave events
  carry only the `clid`, so the client resolves the nickname/uid from its
  cache. `notifytextmessage` has no server timestamp; the bridge stamps its
  own (UTC).
- **Keepalive:** TS6 has no `ping` command, so the bridge sends `version`
  every 60 s to detect a dead channel.
- **Reconnect:** if the query stream drops, the bridge reconnects with
  exponential backoff (1 s → 60 s cap) and re-runs setup.
- **Delivery:** the Discord worker reads the queue and POSTs each embed. It
  honours `429` via `Retry-After` (header seconds or JSON ms), retries `5xx`
  / network errors with capped backoff, and drops (logging, never crashing)
  on other non-2xx. Every string is mention-neutralised, markdown-escaped,
  and truncated to Discord's limits before posting.

---

## Development

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Run the server alone for query probing:

```bash
docker compose -f docker-compose.dev.yml up -d
```

Run the bridge against it (outside Docker):

```bash
TS_HOST=127.0.0.1 tsdiscord
```

Layout: `src/tsdiscord/` holds `config`, `parser` (pure), `ts_client`
(SSH query), `formatter` (pure), `discord` (webhook worker), and `main`
(wiring + signals). `tests/` covers the parser, formatter, config, webhook
worker, and an end-to-end SSH client test against an in-process fake query
server. `NOTES.md` is the working log.

---

## Notes & limitations

- This targets **TeamSpeak 6 beta14** behaviour; event visibility may change
  between betas. The visibility matrix is captured in
  [docs/sample-events.txt](docs/sample-events.txt).
- The bridge mirrors *text and presence*; it does not touch voice, file
  transfers, or admin commands.
