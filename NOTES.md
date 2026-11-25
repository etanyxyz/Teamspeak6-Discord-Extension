# NOTES — TeamSpeak 6 → Discord Bridge

Working log. Re-read this first if you lose track.

## Phase 0 — Recon (done)

- Host: Linux Mint 22.3 (Zena), x86_64, kernel 7.0.0-43.
- Docker 29.10.0, Docker Compose v5.8.0, Python 3.12.3 (/usr/bin/python3).
- Free ports confirmed: UDP 9987, TCP 10011, 10022, 10080, 30033 (all free).
- Repo https://github.com/etanyxyz/Teamspeak6-Discord-Extension — empty except a
  one-line README. Cloned into the workspace; git identity etanyxyz /
  etanyxyz@users.noreply.github.com; remote switched to SSH
  (git@github.com:etanyxyz/Teamspeak6-Discord-Extension.git) — SSH auth verified OK.
- `sudo apt` needs a password (no passwordless sudo). Workaround used: created venv
  with `python3 -m venv --without-pip` + get-pip.py. python3.12-venv not installed;
  if apt becomes needed, ask user for a password or pre-install packages.
- `.env` already existed in the workspace with key `discord webhook = <url>`;
  normalized to `DISCORD_WEBHOOK_URL=<url>` in place. Value kept secret, never printed.
- Skeleton created: src/tsdiscord/{__init__,config,ts_client,parser,discord,formatter,main}.py,
  tests/, docs/, scripts/, .gitignore, .env.example, pyproject.toml, .venv (gitignored).
- Deps installed in .venv: pip 26.3 (aiohttp/dotenv/pytest to be installed as needed).

## Phase 1 — TS6 server in Docker (done)

- Image: `teamspeaksystems/teamspeak6-server:6.0.0-beta14` (official;
  verified via teamspeak6.com Docker guide + github.com/teamspeak/teamspeak6-server).
  `docker-compose.dev.yml` pins this tag.
- First start: license accepted via `TSSERVER_LICENSE_ACCEPTED=accept`;
  server log printed initial credentials (query login password, WebQuery API key,
  serveradmin token). Captured into `.env` (gitignored). Data persisted in
  named volume `ts6-data` (compose project name: teamspeak6-discord-extension).
- Ports (confirmed in server log): 9987/udp voice, 30033/tcp file transfer,
  10022/tcp **SSH query**, 10080/tcp HTTP WebQuery, 10443/tcp HTTPS query.
  **No raw TCP ServerQuery** (TS6 dropped the TS3-style 10011 interface).
- Network: shared bridge network `ts6-net` so the bridge container reaches the
  server as `ts6`.
- `query_ip_allowlist` default is 127.0.0.1/32 + ::1/128 — from the sandbox
  host the queries arrive as 127.0.0.1 (OK); from a bridge container on
  ts6-net the source IP is the container IP → allowlist must be widened
  (TSSERVER_QUERY_IP_ALLOWLIST) if the bridge runs in its own container.
  **Decision: for dev, run the bridge in the same compose project with
  allowlist `0.0.0.0/0` is too broad — use the ts6-net subnet.** Check subnet
  before finalizing compose.

## Phase 2 — query API learned empirically (done)

Probe scripts (throwaway, NOT committed): `scripts/probe.py`,
`scripts/scenario.py`, `scripts/scenario2.py`, `scripts/scenario3.py`.
Raw payloads + visibility matrix: `docs/sample-events.txt`.

Transport decision: **SSH ServerQuery (port 10022) via asyncssh**.
WebQuery (10080) is stateless (`GET /{command}?params`, header `x-api-key`);
`servernotifyregister` is out of api-key scope and there is no event stream,
so WebQuery cannot deliver events.

SSH query protocol (TS6 beta14):
- Connect with password auth (serveradmin account), open a **shell** channel
  (no PTY). asyncssh: `ssh.create_process(None, stdin=PIPE, stdout=PIPE)`.
- Banner = 2 lines (`TS3` + `Welcome to the TeamSpeak ServerQuery interface...`);
  lines are CRLF and each line is prefixed with `\r`.
- Send one command per line; each response ends with `error id=N msg=...`.
  Multi-record lines use `|`. Commands before `use sid=1` fail with
  `error id=1024 invalid serverID`.
- `whoami` after `use` gives the query client's own `client_id` (clid) —
  the bridge uses it to never forward itself.
- Events after `servernotifyregister event=server|textserver|textchannel|textprivate`:
  `notifycliententerview` (full record incl. uid/nickname),
  `notifyclientleftview` (**clid only** → cache needed),
  `notifytextmessage targetmode=3|2|1` with `invokerid/invokername/invokeruid`,
  **no server-side timestamp** (bridge adds its own).
- No `ping` command → keepalive = periodic `version`.

VISIBILITY (the key risk, empirically settled — full matrix in
docs/sample-events.txt):
- enter/leave events: server-wide.
- server text: server-wide.
- **channel text: ONLY the channel the query client is in** (registration
  `event=textchannel id={foreign cid}` is accepted but delivers nothing;
  group already has view power 100 — scoping is by design in beta14).
- private text: NOT testable — TS6 rejects private messages sent BY query
  clients (`error id=516 invalid client type`); no voice client exists in the
  sandbox. Whether a query client receives privates addressed TO it is unknown.

CONSEQUENCE FOR THE BRIDGE (document in README, not hidden):
- Full coverage: joins/leaves + server chat everywhere.
- Channel chat: mirrored for exactly ONE channel — the one the bridge's
  query client sits in (default: server's default channel). Optional config
  `TS_BRIDGE_CHANNEL` (channel name) lets the user pick a different single
  channel.
- Private messages: `FORWARD_PRIVATE_MESSAGES=false` default; only privates
  where the bridge's own query client is the recipient could possibly be
  captured (unverified); privates between other users are NOT visible.
- Flood protection: connect/auth bursts lock the source IP ~10 min
  (in-memory). Bridge: single persistent connection, keepalive every ~60 s,
  reconnect with exponential backoff (cap 60 s) after drops.

## Open questions

- (Phase 1) widen `query_ip_allowlist` for the bridge container's subnet.
- (Phase 3) decide bridge container TS_HOST: `ts6` (service name) + SSH port 10022.

## Phase status

- [x] Phase 0: Recon + skeleton
- [x] Phase 1: TS6 server in Docker
- [x] Phase 2: Learn query API empirically
- [ ] Phase 3: Build the bridge
- [ ] Phase 4: Testing
- [ ] Phase 5: Packaging and docs
