from __future__ import annotations

import asyncio
import getpass
import json
import ssl
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from devchat.config import ClientConfig
from devchat.security import create_client_ssl_context
from devchat.storage import LocalHistoryStore
from devchat.validation import is_valid_username, username_rules


HELP_TEXT = """
Commands:
  /help
  /users
  /rooms
  /create-room <name>
  /join-room <invite_link>
  /invite <room>
  /use-room <room>
  /quit
DM behavior:
  - In main page, type a username to open DM chat.
  - In DM page, type `back` to return to main page.
""".strip()


async def run_client(
    server: str,
    username: str,
    password: str | None,
    history_dir: Path,
    ca_cert: Path | None,
    insecure_skip_verify: bool,
) -> None:
    if not is_valid_username(username):
        raise ValueError(username_rules())

    host, port = _parse_server(server)
    ssl_context = create_client_ssl_context(ca_cert=ca_cert, insecure_skip_verify=insecure_skip_verify)
    try:
        reader, writer = await asyncio.open_connection(host, port, ssl=ssl_context, server_hostname=host)
    except ssl.SSLCertVerificationError as exc:
        hint = (
            "TLS certificate verification failed. If you used `devchat init`, re-run it so cert SAN matches host, "
            "or connect with matching host (localhost vs 127.0.0.1)."
        )
        raise RuntimeError(hint) from exc

    if password is None:
        password = getpass.getpass("Password (first login creates account): ")

    history = LocalHistoryStore(history_dir, f"{host}_{port}", username)
    _print_main_page(history)

    await _send(writer, {"type": "join", "username": username, "password": password})

    state = {"mode": "main", "target": ""}
    recv = asyncio.create_task(_receive_loop(reader, history))
    send = asyncio.create_task(_send_loop(writer, state, history, username))

    done, pending = await asyncio.wait({recv, send}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        task.result()

    writer.close()
    await writer.wait_closed()


def launch_default_session(config_path: Path) -> None:
    cfg = ClientConfig(config_path)
    data = cfg.load()

    username = str(data.get("username", "")).strip()
    server = str(data.get("server", "tls://127.0.0.1:8765")).strip()

    while not is_valid_username(username):
        if username:
            print(username_rules())
        username = input("Choose your username: ").strip()
        data["username"] = username

    if not server:
        server = "tls://127.0.0.1:8765"
        data["server"] = server

    history_dir = Path(data.get("history_dir", str(Path.home() / ".devchat" / "history")))
    ca_cert_raw = data.get("ca_cert")
    ca_cert = Path(ca_cert_raw) if isinstance(ca_cert_raw, str) and ca_cert_raw else None
    insecure = bool(data.get("insecure_skip_verify", False))

    cfg.save(data)

    password = getpass.getpass("Password (first login creates account): ")
    asyncio.run(
        run_client(
            server=server,
            username=username,
            password=password,
            history_dir=history_dir,
            ca_cert=ca_cert,
            insecure_skip_verify=insecure,
        )
    )


async def _send_loop(
    writer: asyncio.StreamWriter,
    state: dict[str, str],
    history: LocalHistoryStore,
    current_username: str,
) -> None:
    print(HELP_TEXT)
    while True:
        prompt = "[main]> " if state["mode"] == "main" else f"[dm:{state['target']}]> " if state["mode"] == "dm" else f"[room:{state['target']}]> "
        line = await asyncio.to_thread(input, prompt)
        text = line.strip()
        if not text:
            continue

        if text == "/quit":
            return
        if text == "/help":
            print(HELP_TEXT)
            continue
        if text == "/users":
            await _send(writer, {"type": "users"})
            continue
        if text == "/rooms":
            await _send(writer, {"type": "list_rooms"})
            continue
        if text.startswith("/create-room "):
            await _send(writer, {"type": "create_room", "room": text.split(maxsplit=1)[1]})
            continue
        if text.startswith("/join-room "):
            await _send(writer, {"type": "join_room", "invite": text.split(maxsplit=1)[1]})
            continue
        if text.startswith("/invite "):
            await _send(writer, {"type": "room_invite", "room": text.split(maxsplit=1)[1]})
            continue
        if text.startswith("/use-room "):
            state["mode"] = "room"
            state["target"] = text.split(maxsplit=1)[1]
            continue

        if state["mode"] == "main":
            target = text
            if not is_valid_username(target):
                print("Invalid username. " + username_rules())
                continue
            if target == current_username:
                print("You are already logged in as this user. Open another terminal with a different username to test DM delivery.")
                continue
            state["mode"] = "dm"
            state["target"] = target
            _show_dm_history(history, current_username, target)
            continue

        if state["mode"] == "dm":
            if text.lower() == "back":
                state["mode"] = "main"
                state["target"] = ""
                _print_main_page(history)
                continue
            await _send(writer, {"type": "dm", "to": state["target"], "body": text})
            continue

        if state["mode"] == "room":
            if text.lower() == "back":
                state["mode"] = "main"
                state["target"] = ""
                _print_main_page(history)
                continue
            await _send(writer, {"type": "room_message", "room": state["target"], "body": text})


async def _receive_loop(reader: asyncio.StreamReader, history: LocalHistoryStore) -> None:
    while True:
        raw = await reader.readline()
        if not raw:
            return
        event = _safe_parse(raw)
        if not event:
            continue
        history.append(event)

        t = event.get("type")
        when = _fmt(event.get("sent_at"))
        if t == "joined":
            print(f"[{when}] Connected as {event.get('username')}")
        elif t == "users":
            print(f"[{when}] users: {', '.join(event.get('items', []))}")
        elif t == "rooms":
            print(f"[{when}] rooms: {', '.join(event.get('items', [])) or '(none)'}")
        elif t in {"room_created", "room_invite"}:
            print(f"[{when}] {t}: room={event.get('room')} invite={event.get('invite')}")
        elif t == "room_joined":
            print(f"[{when}] joined room: {event.get('room')}")
        elif t == "error":
            print(f"[{when}] [error] {event.get('body')}")
        elif t == "message":
            _print_message(event)


def _print_main_page(history: LocalHistoryStore) -> None:
    print("\n=== DevChat Main ===")
    print("Type a username to open DM. Type /help for commands.")
    dm_threads = [t for t in history.list_threads() if t.startswith("dm__")]
    if dm_threads:
        print("Existing DM chats:")
        for t in dm_threads:
            print(f" - {t.replace('dm__', '').replace('__', ' <-> ')}")


def _show_dm_history(history: LocalHistoryStore, me: str, target: str) -> None:
    thread = history.dm_thread_name(me, target)
    items = history.read_thread(thread, limit=30)
    print(f"\n--- DM with {target} ---")
    if not items:
        print("(no previous messages)")
    for item in items:
        if item.get("type") == "message":
            _print_message(item)
    print("Type message and press enter. Type `back` to return.\n")


def _print_message(item: dict[str, Any]) -> None:
    when = _fmt(item.get("sent_at"))
    kind = item.get("kind")
    if kind == "dm":
        print(f"[{when}] [dm] {item.get('from')} -> {item.get('to')}: {item.get('body')}")
    else:
        print(f"[{when}] [room:{item.get('to')}] {item.get('from')}: {item.get('body')}")


def _safe_parse(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_server(server: str) -> tuple[str, int]:
    parsed = urlparse(server)
    if parsed.scheme and parsed.scheme not in {"tcp", "tls"}:
        raise ValueError("Only tls:// or tcp:// URLs are supported")
    if parsed.scheme in {"tcp", "tls"}:
        return parsed.hostname or "127.0.0.1", parsed.port or 8765
    if ":" in server:
        host, p = server.rsplit(":", 1)
        return host or "127.0.0.1", int(p)
    return server, 8765


def _fmt(value: Any) -> str:
    if not isinstance(value, str):
        return "--:--:--"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%H:%M:%S")
    except ValueError:
        return "--:--:--"


async def _send(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    writer.write((json.dumps(payload) + "\n").encode("utf-8"))
    await writer.drain()
