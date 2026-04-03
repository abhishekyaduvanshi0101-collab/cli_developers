from __future__ import annotations

import re

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9]{1,15}$")


def is_valid_username(username: str) -> bool:
    return USERNAME_PATTERN.fullmatch(username) is not None


def username_rules() -> str:
    return "Username must be 1-15 chars and only letters/numbers (a-z, A-Z, 0-9)."
