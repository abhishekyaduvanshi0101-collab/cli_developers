from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from devchat.security import _build_subject_alt_name, create_client_ssl_context, create_server_ssl_context, generate_self_signed_cert
from devchat.server import ChatServer
from devchat.storage import LocalHistoryStore, ServerStore


async def read_event(reader: asyncio.StreamReader, timeout: float = 3) -> dict:
    raw = await asyncio.wait_for(reader.readline(), timeout)
    return json.loads(raw.decode())


async def wait_type(reader: asyncio.StreamReader, event_type: str, timeout: float = 5) -> dict:
    end = asyncio.get_running_loop().time() + timeout
    while True:
        remain = end - asyncio.get_running_loop().time()
        if remain <= 0:
            raise TimeoutError(event_type)
        event = await read_event(reader, remain)
        if event.get("type") == event_type:
            return event


async def send_event(writer: asyncio.StreamWriter, payload: dict) -> None:
    writer.write((json.dumps(payload) + "\n").encode())
    await writer.drain()


class SecureChatE2E(unittest.IsolatedAsyncioTestCase):
    async def test_rooms_dm_history_and_unique_username(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cert = root / "server.crt"
            key = root / "server.key"
            db = root / "devchat.db"

            generate_self_signed_cert(cert, key, "127.0.0.1", 1)
            store = ServerStore(db)
            chat = ChatServer(store=store, invite_base="https://devchat.app")
            server_ctx = create_server_ssl_context(cert, key)
            server = await asyncio.start_server(chat.handle_client, "127.0.0.1", 9133, ssl=server_ctx)

            async with server:
                ctx = create_client_ssl_context(ca_cert=None, insecure_skip_verify=True)
                ar, aw = await asyncio.open_connection("127.0.0.1", 9133, ssl=ctx, server_hostname="127.0.0.1")
                br, bw = await asyncio.open_connection("127.0.0.1", 9133, ssl=ctx, server_hostname="127.0.0.1")

                await send_event(aw, {"type": "join", "username": "alice", "password": "pw"})
                await send_event(bw, {"type": "join", "username": "bob", "password": "pw"})
                await wait_type(ar, "joined")
                await wait_type(br, "joined")

                # duplicate active username
                cr, cw = await asyncio.open_connection("127.0.0.1", 9133, ssl=ctx, server_hostname="127.0.0.1")
                await send_event(cw, {"type": "join", "username": "alice", "password": "pw"})
                err = await wait_type(cr, "error")
                self.assertIn("already active", err["body"])

                # invalid username (>15 or invalid chars)
                dr, dw = await asyncio.open_connection("127.0.0.1", 9133, ssl=ctx, server_hostname="127.0.0.1")
                await send_event(dw, {"type": "join", "username": "alice_very_long_name", "password": "pw"})
                bad = await wait_type(dr, "error")
                self.assertIn("1-15", bad["body"])

                await send_event(aw, {"type": "create_room", "room": "backend"})
                created = await wait_type(ar, "room_created")
                invite = created["invite"]

                await send_event(bw, {"type": "join_room", "invite": invite})
                joined = await wait_type(br, "room_joined")
                self.assertEqual(joined["room"], "backend")

                await send_event(aw, {"type": "room_message", "room": "backend", "body": "ship it"})
                am = await wait_type(ar, "message")
                bm = await wait_type(br, "message")
                self.assertEqual(am["body"], "ship it")
                self.assertEqual(bm["to"], "backend")

                await send_event(aw, {"type": "dm", "to": "bob", "body": "check logs"})
                adm = await wait_type(ar, "message")
                bdm = await wait_type(br, "message")
                self.assertEqual(adm["kind"], "dm")
                self.assertEqual(bdm["body"], "check logs")

                history = LocalHistoryStore(root / "history", "127.0.0.1_9133", "alice")
                history.append(am)
                history.append(adm)
                threads = history.list_threads()
                self.assertTrue(any(t.startswith("room__") for t in threads))
                self.assertTrue(any(t.startswith("dm__") for t in threads))

                for w in (aw, bw, cw, dw):
                    w.close()
                    await w.wait_closed()

            reloaded = ServerStore(db)
            messages = reloaded.load_recent_messages(20)
            bodies = [m["body"] for m in messages]
            self.assertIn("ship it", bodies)
            self.assertIn("check logs", bodies)


if __name__ == "__main__":
    unittest.main()


class SecurityHelpersTest(unittest.TestCase):
    def test_subject_alt_name_includes_requested_host(self) -> None:
        san = _build_subject_alt_name("127.0.0.1")
        self.assertIn("IP:127.0.0.1", san)
        self.assertIn("DNS:localhost", san)
