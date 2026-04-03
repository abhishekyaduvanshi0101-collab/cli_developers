from __future__ import annotations

import hashlib
import hmac
import os
import ssl
import subprocess
from pathlib import Path


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if salt is None:
        salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return salt.hex(), derived.hex()


def verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    _, actual_hex = hash_password(password, salt=salt)
    return hmac.compare_digest(actual_hex, expected_hex)


def create_server_ssl_context(certfile: Path, keyfile: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    return context


def create_client_ssl_context(ca_cert: Path | None, insecure_skip_verify: bool) -> ssl.SSLContext:
    if insecure_skip_verify:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    if ca_cert:
        return ssl.create_default_context(cafile=str(ca_cert))

    return ssl.create_default_context()


def generate_self_signed_cert(certfile: Path, keyfile: Path, common_name: str, days: int) -> None:
    certfile.parent.mkdir(parents=True, exist_ok=True)
    keyfile.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(keyfile),
        "-out",
        str(certfile),
        "-sha256",
        "-days",
        str(days),
        "-subj",
        f"/CN={common_name}",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
