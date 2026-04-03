from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from devchat.security import create_server_ssl_context, hash_password, verify_password
from devchat.storage import ServerStore
from devchat.validation import is_valid_username, username_rules


@dataclass(slots=True)
class ChatMessage:
    sender: str
    target: str | None
    body: str
    kind: str
    sent_at: str

    def as_dict(self) -> dict[str, str | None]:
        return {
            "type": "message",
            "kind": self.kind,
            "from": self.sender,
            "to": self.target,
            "body": self.body,
            "sent_at": self.sent_at,
        }


class ChatServer:
    def __init__(self, store: ServerStore, invite_base: str, history_size: int = 1000) -> None:
        self.connected: dict[str, asyncio.StreamWriter] = {}
        self._history = store.load_recent_messages(limit=history_size)
        self._history_size = history_size
        self._store = store
        self._invite_base = invite_base.rstrip("/")
        self._lock = asyncio.Lock()

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        active_user: str | None = None
        try:
            join = await self._read_event(reader)
            if join.get("type") != "join":
                await self._send_error(writer, "First event must be join")
                return

            username = str(join.get("username", "")).strip()
            password = str(join.get("password", ""))
            if not username or not password:
                await self._send_error(writer, "Username and password are required")
                return
            if not is_valid_username(username):
                await self._send_error(writer, username_rules())
                return

            ok, message = self._authenticate(username, password)
            if not ok:
                await self._send_error(writer, message)
                return

            async with self._lock:
                if username in self.connected:
                    await self._send_error(writer, f"Username '{username}' is already active")
                    return
                self.connected[username] = writer
                active_user = username

            await self._send(writer, {"type": "joined", "username": username, "sent_at": _ts()})
            await self._send(writer, {"type": "rooms", "items": self._store.list_rooms_for_user(username), "sent_at": _ts()})
            await self._send(writer, {"type": "history", "items": self._history[-100:], "sent_at": _ts()})
            await self._broadcast_users()

            while True:
                event = await self._read_event(reader)
                if not event:
                    break
                await self._dispatch(username, event)
        except (OSError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if active_user:
                async with self._lock:
                    self.connected.pop(active_user, None)
                await self._broadcast_users()
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    def _authenticate(self, username: str, password: str) -> tuple[bool, str]:
        row = self._store.get_user(username)
        if row is None:
            salt, pwh = hash_password(password)
            self._store.create_user(username, salt, pwh, _ts())
            return True, "registered"
        if verify_password(password, row["password_salt"], row["password_hash"]):
            return True, "authenticated"
        return False, "Invalid username or password"

    async def _dispatch(self, sender: str, event: dict[str, Any]) -> None:
        t = event.get("type")
        async with self._lock:
            sender_writer = self.connected.get(sender)

        if t == "users":
            await self._broadcast_users()
            return

        if t == "list_rooms":
            if sender_writer:
                await self._send(sender_writer, {"type": "rooms", "items": self._store.list_rooms_for_user(sender), "sent_at": _ts()})
            return

        if t == "create_room":
            room = str(event.get("room", "")).strip().lower().replace(" ", "-")
            if not room:
                return
            code = secrets.token_urlsafe(12)
            created = self._store.create_room(room, code, sender, _ts())
            if not sender_writer:
                return
            if not created:
                await self._send_error(sender_writer, f"Room '{room}' already exists")
                return
            invite = f"{self._invite_base}/invite/{code}"
            await self._send(sender_writer, {"type": "room_created", "room": room, "invite": invite, "sent_at": _ts()})
            await self._send(sender_writer, {"type": "rooms", "items": self._store.list_rooms_for_user(sender), "sent_at": _ts()})
            return

        if t == "join_room":
            invite = str(event.get("invite", "")).strip()
            code = invite.rsplit("/", 1)[-1]
            room = self._store.resolve_invite(code)
            if not sender_writer:
                return
            if room is None:
                await self._send_error(sender_writer, "Invalid invite link")
                return
            self._store.add_member(room, sender)
            await self._send(sender_writer, {"type": "room_joined", "room": room, "sent_at": _ts()})
            await self._send(sender_writer, {"type": "rooms", "items": self._store.list_rooms_for_user(sender), "sent_at": _ts()})
            return

        if t == "room_invite":
            room = str(event.get("room", "")).strip()
            if not sender_writer:
                return
            if not self._store.is_room_member(room, sender):
                await self._send_error(sender_writer, f"You are not a member of room '{room}'")
                return
            code = self._store.get_invite_by_room(room)
            if code is None:
                await self._send_error(sender_writer, "Room not found")
                return
            await self._send(sender_writer, {"type": "room_invite", "room": room, "invite": f"{self._invite_base}/invite/{code}", "sent_at": _ts()})
            return

        if t == "room_message":
            room = str(event.get("room", "")).strip()
            body = str(event.get("body", "")).strip()
            if not room or not body:
                return
            if not self._store.is_room_member(room, sender):
                if sender_writer:
                    await self._send_error(sender_writer, f"You are not a member of room '{room}'")
                return
            msg = ChatMessage(sender=sender, target=room, body=body, kind="room", sent_at=_ts())
            self._persist(msg)
            recipients = self._room_online_members(room)
            for writer in recipients:
                await self._send(writer, msg.as_dict())
            return

        if t == "dm":
            target = str(event.get("to", "")).strip()
            body = str(event.get("body", "")).strip()
            if not target or not body:
                return
            async with self._lock:
                target_writer = self.connected.get(target)
                sender_writer = self.connected.get(sender)
            if not target_writer:
                if sender_writer:
                    await self._send_error(sender_writer, f"User '{target}' is not online")
                return
            msg = ChatMessage(sender=sender, target=target, body=body, kind="dm", sent_at=_ts())
            self._persist(msg)
            await self._send(target_writer, msg.as_dict())
            if sender_writer and sender_writer is not target_writer:
                await self._send(sender_writer, msg.as_dict())

    def _room_online_members(self, room: str) -> list[asyncio.StreamWriter]:
        members = set()
        for username in self.connected:
            if self._store.is_room_member(room, username):
                members.add(username)
        return [self.connected[u] for u in members if u in self.connected]

    def _persist(self, msg: ChatMessage) -> None:
        payload = msg.as_dict()
        self._history.append(payload)
        if len(self._history) > self._history_size:
            self._history = self._history[-self._history_size :]
        self._store.save_message(msg.sender, msg.target, msg.body, msg.kind, msg.sent_at)

    async def _broadcast_users(self) -> None:
        users = sorted(self.connected.keys())
        await self._broadcast({"type": "users", "items": users, "sent_at": _ts()})

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            recips = list(self.connected.values())
        for writer in recips:
            try:
                await self._send(writer, payload)
            except Exception:
                pass

    async def _read_event(self, reader: asyncio.StreamReader) -> dict[str, Any]:
        raw = await reader.readline()
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    async def _send(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await writer.drain()

    async def _send_error(self, writer: asyncio.StreamWriter, message: str) -> None:
        await self._send(writer, {"type": "error", "body": message, "sent_at": _ts()})


async def run_server(host: str, port: int, certfile: Path, keyfile: Path, db_path: Path, invite_base: str) -> None:
    store = ServerStore(db_path)
    chat = ChatServer(store=store, invite_base=invite_base)
    context = create_server_ssl_context(certfile, keyfile)
    server = await asyncio.start_server(chat.handle_client, host, port, ssl=context)
    print(f"DevChat server listening on tls://{host}:{port}")
    async with server:
        await server.serve_forever()


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
