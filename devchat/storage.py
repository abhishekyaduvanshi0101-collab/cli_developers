from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any


class ServerStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    name TEXT PRIMARY KEY,
                    invite_code TEXT UNIQUE NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS room_members (
                    room_name TEXT NOT NULL,
                    username TEXT NOT NULL,
                    PRIMARY KEY(room_name, username)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender TEXT NOT NULL,
                    target TEXT,
                    body TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sent_at TEXT NOT NULL
                )
                """
            )

    def get_user(self, username: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT username, password_salt, password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()

    def create_user(self, username: str, password_salt: str, password_hash: str, created_at: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO users(username, password_salt, password_hash, created_at) VALUES(?, ?, ?, ?)",
                (username, password_salt, password_hash, created_at),
            )

    def create_room(self, name: str, invite_code: str, created_by: str, created_at: str) -> bool:
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO rooms(name, invite_code, created_by, created_at) VALUES(?, ?, ?, ?)",
                    (name, invite_code, created_by, created_at),
                )
                self._conn.execute(
                    "INSERT OR IGNORE INTO room_members(room_name, username) VALUES(?, ?)",
                    (name, created_by),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def list_rooms_for_user(self, username: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT room_name FROM room_members WHERE username = ? ORDER BY room_name ASC",
                (username,),
            ).fetchall()
        return [r["room_name"] for r in rows]

    def resolve_invite(self, invite_code: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT name FROM rooms WHERE invite_code = ?",
                (invite_code,),
            ).fetchone()
        return None if row is None else str(row["name"])

    def add_member(self, room_name: str, username: str) -> bool:
        with self._lock, self._conn:
            room = self._conn.execute("SELECT name FROM rooms WHERE name = ?", (room_name,)).fetchone()
            if room is None:
                return False
            self._conn.execute(
                "INSERT OR IGNORE INTO room_members(room_name, username) VALUES(?, ?)",
                (room_name, username),
            )
        return True

    def get_invite_by_room(self, room_name: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT invite_code FROM rooms WHERE name = ?",
                (room_name,),
            ).fetchone()
        return None if row is None else str(row["invite_code"])

    def is_room_member(self, room_name: str, username: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM room_members WHERE room_name = ? AND username = ?",
                (room_name, username),
            ).fetchone()
        return row is not None

    def save_message(self, sender: str, target: str | None, body: str, kind: str, sent_at: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO messages(sender, target, body, kind, sent_at) VALUES(?, ?, ?, ?, ?)",
                (sender, target, body, kind, sent_at),
            )

    def load_recent_messages(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT sender, target, body, kind, sent_at FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "type": "message",
                "from": row["sender"],
                "to": row["target"],
                "body": row["body"],
                "kind": row["kind"],
                "sent_at": row["sent_at"],
            }
            for row in reversed(rows)
        ]


class LocalHistoryStore:
    def __init__(self, root: Path, server_id: str, username: str) -> None:
        safe_server = "".join(ch if ch.isalnum() or ch in {'.', '-', '_'} else '_' for ch in server_id)
        self.root = root / safe_server / username
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, event: dict[str, Any]) -> None:
        thread = self._thread_name(event)
        path = self.root / f"{thread}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def list_threads(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.jsonl"))

    def read_thread(self, thread: str, limit: int = 50) -> list[dict[str, Any]]:
        path = self.root / f"{thread}.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        items: list[dict[str, Any]] = []
        for line in lines:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return items

    @staticmethod
    def dm_thread_name(user_a: str, user_b: str) -> str:
        return f"dm__{'__'.join(sorted({user_a, user_b}))}"

    @staticmethod
    def _thread_name(event: dict[str, Any]) -> str:
        event_type = event.get("type")
        if event_type == "message":
            kind = str(event.get("kind", "room"))
            if kind == "dm":
                a = str(event.get("from", "unknown"))
                b = str(event.get("to", "unknown"))
                return f"dm__{'__'.join(sorted({a, b}))}"
            if kind == "room":
                target = str(event.get("to", "lobby"))
                return f"room__{target}"
        return "system"
