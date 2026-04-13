"""
Authentication module for OpenClaw Enterprise.
Simple JWT-based auth with role enforcement.
In production: replace with Cognito/OIDC.
"""
import os
import time
import hashlib
import hmac
import json
import base64
from typing import Optional
from dataclasses import dataclass

# JWT secret — MUST be set via environment variable
JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    import sys
    print("ERROR: JWT_SECRET environment variable not set. Generate one with: openssl rand -hex 32", file=sys.stderr)
    JWT_SECRET = "dev-only-" + hashlib.sha256(os.urandom(16)).hexdigest()[:32]
JWT_EXPIRY_HOURS = 24


@dataclass
class UserContext:
    employee_id: str
    name: str
    role: str  # admin | manager | employee
    department_id: str
    position_id: str
    must_change_password: bool = False


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def create_token(employee: dict, must_change_password: bool = False) -> str:
    """Create a simple JWT token from employee record."""
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_data = {
        "sub": employee.get("id", ""),
        "name": employee.get("name", ""),
        "role": employee.get("role", "employee"),
        "departmentId": employee.get("departmentId", ""),
        "positionId": employee.get("positionId", ""),
        "mustChangePassword": must_change_password,
        "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600,
    }
    payload = _b64encode(json.dumps(payload_data).encode())
    signature = hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    sig = _b64encode(signature)
    return f"{header}.{payload}.{sig}"


def verify_token(token: str) -> Optional[UserContext]:
    """Verify JWT and return UserContext, or None if invalid."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, sig = parts

        # Verify signature
        expected = hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        actual = _b64decode(sig)
        if not hmac.compare_digest(expected, actual):
            return None

        # Decode payload
        data = json.loads(_b64decode(payload))

        # Check expiry
        if data.get("exp", 0) < time.time():
            return None

        return UserContext(
            employee_id=data.get("sub", ""),
            name=data.get("name", ""),
            role=data.get("role", "employee"),
            department_id=data.get("departmentId", ""),
            position_id=data.get("positionId", ""),
            must_change_password=data.get("mustChangePassword", False),
        )
    except Exception:
        return None


def get_user_from_request(authorization: str = "") -> Optional[UserContext]:
    """Extract user from Authorization header. Returns None for unauthenticated requests."""
    if not authorization:
        return None
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        return None
    return verify_token(token)
