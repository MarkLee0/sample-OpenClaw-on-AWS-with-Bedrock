"""Password hashing and complexity validation for OpenClaw Enterprise."""

import re

import bcrypt


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def validate_complexity(pw: str) -> str | None:
    """Return an error message if password fails complexity rules, or None if valid.

    Rules: min 8 chars, 1 uppercase, 1 lowercase, 1 digit, 1 special character.
    """
    if len(pw) < 8:
        return "Password must be at least 8 characters"
    if not re.search(r"[A-Z]", pw):
        return "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", pw):
        return "Password must contain at least one lowercase letter"
    if not re.search(r"\d", pw):
        return "Password must contain at least one digit"
    if not re.search(r"[!@#$%^&*()\-_=+\[\]{};:'\",.<>?/\\|`~]", pw):
        return "Password must contain at least one special character"
    return None
