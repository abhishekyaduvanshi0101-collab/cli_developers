from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from devchat.client import launch_default_session, run_client
from devchat.config import ClientConfig
from devchat.security import generate_self_signed_cert
from devchat.server import run_server
from devchat.validation import is_valid_username, username_rules


HELP_TEXT = """
DevChat help
-----------
Start chat immediately:
  devchat

Initialize production-ready local config + certs:
  devchat init --username alice --host 127.0.0.1 --port 8765

Run server:
  devchat serve --host 0.0.0.0 --port 8765 --certfile ~/.devchat/certs/server.crt --keyfile ~/.devchat/certs/server.key

Run explicit client:
  devchat chat --username alice --server tls://127.0.0.1:8765 --ca-cert ~/.devchat/certs/server.crt
""".strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devchat", description="Secure realtime terminal chat for developers")
    parser.add_argument("command", nargs="?", choices=["serve", "chat", "cert", "init", "help"], help="Optional command")

    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--certfile", type=Path)
    parser.add_argument("--keyfile", type=Path)
    parser.add_argument("--db", type=Path, default=Path.home() / ".devchat" / "server" / "devchat.db")
    parser.add_argument("--invite-base", default="https://devchat.app")

    parser.add_argument("--server", default="tls://127.0.0.1:8765")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--ca-cert", type=Path)
    parser.add_argument("--insecure-skip-verify", action="store_true")
    parser.add_argument("--history-dir", type=Path, default=Path.home() / ".devchat" / "history")
    parser.add_argument("--config", type=Path, default=Path.home() / ".devchat" / "client.json")

    parser.add_argument("--common-name", default="localhost")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--start-server", action="store_true", help="Used with init: immediately start server")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command in {None, ""}:
        launch_default_session(args.config)
        return

    if args.command == "help":
        print(HELP_TEXT)
        return

    if args.command == "init":
        _run_init(args, parser)
        return

    if args.command == "serve":
        if not args.certfile or not args.keyfile:
            parser.error("serve requires --certfile and --keyfile")
        asyncio.run(run_server(args.host, args.port, args.certfile, args.keyfile, args.db, args.invite_base))
        return

    if args.command == "chat":
        if not args.username:
            parser.error("chat requires --username")
        if not is_valid_username(args.username):
            parser.error(username_rules())
        asyncio.run(
            run_client(
                server=args.server,
                username=args.username,
                password=args.password,
                history_dir=args.history_dir,
                ca_cert=args.ca_cert,
                insecure_skip_verify=args.insecure_skip_verify,
            )
        )
        return

    if args.command == "cert":
        if not args.certfile or not args.keyfile:
            parser.error("cert requires --certfile and --keyfile")
        generate_self_signed_cert(args.certfile, args.keyfile, args.common_name, args.days)
        print(f"Generated certificate: {args.certfile}")
        print(f"Generated key: {args.keyfile}")
        return


def _run_init(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    username = args.username or input("Choose username (a-z A-Z 0-9, max 15): ").strip()
    if not is_valid_username(username):
        parser.error(username_rules())

    certfile = args.certfile or (Path.home() / ".devchat" / "certs" / "server.crt")
    keyfile = args.keyfile or (Path.home() / ".devchat" / "certs" / "server.key")

    if not certfile.exists() or not keyfile.exists():
        generate_self_signed_cert(certfile, keyfile, args.common_name, args.days)
        print(f"Generated TLS cert/key at {certfile} and {keyfile}")

    config = ClientConfig(args.config)
    config.save(
        {
            "username": username,
            "server": f"tls://{args.host}:{args.port}",
            "ca_cert": str(certfile),
            "history_dir": str(args.history_dir),
            "insecure_skip_verify": False,
        }
    )
    print(f"Wrote client profile: {args.config}")
    print("Next: start server with `devchat serve ...` then run `devchat`.")

    if args.start_server:
        asyncio.run(run_server(args.host, args.port, certfile, keyfile, args.db, args.invite_base))


if __name__ == "__main__":
    main()
