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

## Open questions (verify in Phases 1-2)

- Exact TS6 server Docker image + first-start output format (license env var, where
  the admin token / query credentials appear).
- TS6 query interfaces: raw ServerQuery port? SSH port? HTTP WebQuery? (TS3 legacy:
  10011 raw, 10022 SSH.) TS6 may differ — verify on the running server.
- Event subscription syntax in TS6 and whether a query client sees:
  server-wide chat, channel chat in other channels, private messages between others.

## Phase status

- [x] Phase 0: Recon + skeleton
- [ ] Phase 1: TS6 server in Docker
- [ ] Phase 2: Learn query API empirically
- [ ] Phase 3: Build the bridge
- [ ] Phase 4: Testing
- [ ] Phase 5: Packaging and docs
