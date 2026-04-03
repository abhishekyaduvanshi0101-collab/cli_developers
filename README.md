# DevChat CLI

A secure terminal chat app for developers (rooms, invite links, DM, TLS, sqlite persistence).

## Username rules

Usernames are strictly validated:
- only `a-z`, `A-Z`, `0-9`
- max length `15`
- must be unique in server database and unique among active sessions

## What is "client profile"?

Client profile is a local config file at `~/.devchat/client.json` that stores:
- your username
- default server URL
- CA cert path for TLS validation
- history directory path
- TLS verify preference

This lets users run only `devchat` next time without retyping setup.

## Quick start in GitHub Codespaces

1. Open Codespace terminal.
2. Install project locally:
   ```bash
   pip install -e .
   ```
3. Bootstrap everything in one command:
   ```bash
   devchat init --username alice --host 127.0.0.1 --port 8765
   ```
4. Start server:
   ```bash
   devchat serve --host 0.0.0.0 --port 8765 --certfile ~/.devchat/certs/server.crt --keyfile ~/.devchat/certs/server.key
   ```
5. Open another terminal and run:
   ```bash
   devchat
   ```


### Why does `devchat` ask for password?

DevChat uses authenticated accounts.
- First successful login with a new username registers that username with your password.
- Next logins for that username require the same password.

### TLS mismatch fix (`CERTIFICATE_VERIFY_FAILED` for 127.0.0.1)

If you see certificate IP/hostname mismatch:

```bash
# regenerate cert/profile with host you actually use
devchat init --username alice --host 127.0.0.1 --port 8765
```

`devchat init` now auto-regenerates certs if existing cert SAN does not match the host.
Then run server/client again with the same host.

## npm package direction

This repo now includes an npm wrapper so users can run `devchat` after `npm i -g devchat-cli`.
The npm CLI delegates to Python (`python3 -m devchat.cli`) and prints guidance if Python is missing.

## Commands

- `devchat` → launch app directly
- `devchat help` → usage guidance
- `devchat init --username <name>` → one-command bootstrap (cert + client profile, optional `--start-server`)
- `devchat serve ...` → run TLS server
- `devchat chat --username <name> ...` → explicit client run
- `devchat cert --certfile ... --keyfile ...` → generate cert

## In-chat commands

- `/users`
- `/rooms`
- `/create-room <name>`
- `/join-room <invite_link>`
- `/invite <room>`
- `/use-room <room>`
- Type `<username>` in main page to open DM
- In DM chat, type messages directly
- Type `back` to return to main page
- `/quit`

## Network behavior

- Same Wi-Fi/LAN: yes, if clients can reach server host/port.
- Different countries: yes, if server is publicly reachable (or via VPN/tunnel) and network/firewall allows it.

## Data locations

- Server DB: `~/.devchat/server/devchat.db`
- Client profile: `~/.devchat/client.json`
- Client histories: `~/.devchat/history/<server>/<username>/room__*.jsonl` and `dm__*.jsonl`
