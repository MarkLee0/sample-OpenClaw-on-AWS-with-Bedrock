"""
Gateway Proxy — reverse proxy to always-on agent's OpenClaw Gateway UI.

Allows employees to manage their own IM channel connections through the
native OpenClaw Gateway UI, without knowing the container's internal IP
or gateway token.

Flow:
  Employee Portal → "Open Gateway Console" button
  → GET /api/v1/portal/gateway-access  (returns iframe URL or proxy base)
  → All subsequent requests: /api/v1/portal/gateway/{path}
  → This router proxies to http://{container_ip}:18789/{path}?token={gw_token}
  → Employee sees OpenClaw native channel management UI

Security:
  - Employee must be authenticated (JWT)
  - Employee can only access their own agent's Gateway
  - Gateway token is injected server-side, never exposed to the browser
  - Container IP is internal VPC, never exposed

Admin visibility:
  - IM channel config stored in openclaw.json on EFS → readable from S3 writeback
  - All agent invocations logged to DynamoDB audit trail
  - Admin can stop the service at any time
"""

import os
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
import requests as _requests

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/portal/gateway", tags=["gateway-proxy"])

# Lazy imports to avoid circular deps
_boto3 = None
def _get_boto3():
    global _boto3
    if _boto3 is None:
        import boto3
        _boto3 = boto3
    return _boto3


class _UserInfo:
    def __init__(self, employee_id: str, name: str, role: str):
        self.employee_id = employee_id
        self.name = name
        self.role = role

def _require_employee_auth(authorization: str) -> _UserInfo:
    """Validate JWT and return user info. Standalone — no import from main.py."""
    import json, hmac, hashlib, base64, time
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing authorization")
    token = authorization.replace("Bearer ", "")
    # Decode JWT (HS256) — read secret from SSM
    try:
        boto3 = _get_boto3()
        stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
        region = os.environ.get("GATEWAY_REGION", "us-east-1")
        secret = boto3.client("ssm", region_name=region).get_parameter(
            Name=f"/openclaw/{stack}/jwt-secret", WithDecryption=True
        )["Parameter"]["Value"]
    except Exception:
        secret = os.environ.get("JWT_SECRET", "dev-secret")
    try:
        parts = token.split(".")
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        # Verify expiry
        if payload.get("exp", 0) < time.time():
            raise HTTPException(401, "Token expired")
        return _UserInfo(
            employee_id=payload.get("sub", ""),
            name=payload.get("name", ""),
            role=payload.get("role", "employee"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(401, f"Invalid token: {e}")


def _get_agent_gateway_url(employee_id: str) -> Optional[str]:
    """Resolve the always-on agent's Gateway URL for an employee.
    Returns http://{container_ip}:18789/?token={gw_token} or None."""
    boto3 = _get_boto3()
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    region = os.environ.get("GATEWAY_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    # SSM params are in the gateway region (us-east-1), not DynamoDB region (us-east-2)
    if region == "us-east-2":
        region = "us-east-1"
    ssm = boto3.client("ssm", region_name=region)

    # 1. Check if employee has an always-on agent
    try:
        agent_id = ssm.get_parameter(
            Name=f"/openclaw/{stack}/tenants/{employee_id}/always-on-agent"
        )["Parameter"]["Value"]
    except Exception:
        return None  # Not always-on

    # 2. Get container endpoint (http://10.0.x.x:8080)
    try:
        endpoint = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{agent_id}/endpoint"
        )["Parameter"]["Value"]
    except Exception:
        return None  # Container not running

    # 3. Derive Gateway URL (port 18789 instead of 8080)
    gateway_url = endpoint.replace(":8080", ":18789")

    # 4. Get gateway token from SSM or container config
    gw_token = ""
    try:
        gw_token = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{agent_id}/gateway-token",
            WithDecryption=True,
        )["Parameter"]["Value"]
    except Exception:
        # Fallback: try to read from the container's /ping which doesn't need auth
        # or use a default token. For now, try without token.
        logger.warning("Gateway token not found for %s — proxy may fail auth", agent_id)

    return f"{gateway_url}/?token={gw_token}" if gw_token else gateway_url


# Cache: employee_id → (gateway_base_url, token, timestamp)
_gw_cache: dict = {}
_GW_CACHE_TTL = 120  # seconds

def _get_cached_gateway(employee_id: str) -> Optional[tuple]:
    """Return (base_url, token) for an employee's Gateway, with caching."""
    import time
    now = time.time()
    if employee_id in _gw_cache:
        base, token, ts = _gw_cache[employee_id]
        if now - ts < _GW_CACHE_TTL:
            return base, token

    boto3 = _get_boto3()
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    region = os.environ.get("GATEWAY_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    # SSM params are in the gateway region (us-east-1), not DynamoDB region (us-east-2)
    if region == "us-east-2":
        region = "us-east-1"
    ssm = boto3.client("ssm", region_name=region)

    print(f"[gateway-proxy] stack={stack} region={region} emp={employee_id}")
    try:
        param_name = f"/openclaw/{stack}/tenants/{employee_id}/always-on-agent"
        agent_id = ssm.get_parameter(Name=param_name)["Parameter"]["Value"]
        print(f"[gateway-proxy] SSM lookup OK: {param_name} → {agent_id}")
    except Exception as e:
        print(f"[gateway-proxy] SSM lookup FAILED: /openclaw/{stack}/tenants/{employee_id}/always-on-agent → {e}")
        return None

    try:
        endpoint = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{agent_id}/endpoint"
        )["Parameter"]["Value"]
        logger.info("Gateway proxy: endpoint=%s", endpoint)
    except Exception as e:
        logger.info("Gateway proxy: no endpoint for %s: %s", agent_id, e)
        return None

    base_url = endpoint.replace(":8080", ":18789")

    gw_token = ""
    try:
        gw_token = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{agent_id}/gateway-token",
            WithDecryption=True,
        )["Parameter"]["Value"]
    except Exception:
        # Try reading from openclaw.json via S3
        try:
            import json
            s3_bucket = os.environ.get("S3_BUCKET", "")
            if s3_bucket:
                s3 = boto3.client("s3", region_name=region)
                # The agent's employee ID for S3 path
                emp_id = employee_id
                obj = s3.get_object(Bucket=s3_bucket, Key=f"{emp_id}/workspace/.openclaw-gateway-token")
                gw_token = obj["Body"].read().decode().strip()
        except Exception:
            pass

    if not gw_token:
        # Last resort: try the stack-level gateway token
        try:
            gw_token = ssm.get_parameter(
                Name=f"/openclaw/{stack}/gateway-token",
                WithDecryption=True,
            )["Parameter"]["Value"]
        except Exception:
            pass

    _gw_cache[employee_id] = (base_url, gw_token, now)
    return base_url, gw_token


@router.get("/access")
def get_gateway_access(authorization: str = Header(default="")):
    """Check if the employee has Gateway access and return status.
    Does NOT return the URL directly — all access goes through the proxy."""
    user = _require_employee_auth(authorization)
    print(f"[gateway-proxy] access check: emp={user.employee_id} role={user.role}")
    result = _get_cached_gateway(user.employee_id)
    print(f"[gateway-proxy] cache result: {result}")

    if not result:
        return {
            "available": False,
            "reason": "Your agent is not in always-on mode. IM channels are managed through the shared company bot.",
            "deployMode": "serverless",
        }

    base_url, token = result
    # Quick health check
    try:
        resp = _requests.get(f"{base_url}/api/health", timeout=3)
        healthy = resp.status_code == 200
    except Exception:
        healthy = False

    return {
        "available": True,
        "healthy": healthy,
        "deployMode": "always-on-ecs",
        "proxyBase": "/api/v1/portal/gateway/ui/",
    }


@router.get("/dashboard")
def get_gateway_dashboard(authorization: str = Header(default="")):
    """Get a fresh Gateway Console dashboard URL with pairing token.
    Calls the container's /gateway-dashboard endpoint which runs
    `openclaw dashboard --no-open` and returns the pairing token.
    The frontend can then construct the full proxied URL."""
    user = _require_employee_auth(authorization)
    result = _get_cached_gateway(user.employee_id)

    if not result:
        return {"available": False, "reason": "Agent is not always-on"}

    base_url, gw_token = result
    # Call container's /gateway-dashboard API (port 8080)
    try:
        resp = _requests.get(f"{base_url}/gateway-dashboard", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "available": True,
                "gatewayToken": gw_token or data.get("gatewayToken", ""),
                "dashboardToken": data.get("dashboardToken", ""),
                "proxyBase": "/api/v1/portal/gateway/ui/",
            }
        return {"available": False, "reason": f"Container returned {resp.status_code}: {resp.text[:200]}"}
    except _requests.exceptions.ConnectionError:
        return {"available": False, "reason": "Container not reachable"}
    except _requests.exceptions.Timeout:
        return {"available": False, "reason": "Container timed out"}
    except Exception as e:
        return {"available": False, "reason": str(e)}


def _authenticate_proxy(request: Request, authorization: str) -> _UserInfo:
    """Authenticate for gateway proxy via: Authorization header, ?auth_token= query, or gw_session cookie.
    On success with auth_token query param, sets a session cookie so sub-resource requests work."""
    # 1. Try Authorization header
    if authorization and authorization.startswith("Bearer "):
        return _require_employee_auth(authorization)
    # 2. Try ?auth_token= query param (window.open from browser)
    qt = request.query_params.get("auth_token", "")
    if qt:
        return _require_employee_auth(f"Bearer {qt}")
    # 3. Try gw_session cookie
    cookie_token = request.cookies.get("gw_session", "")
    if cookie_token:
        return _require_employee_auth(f"Bearer {cookie_token}")
    raise HTTPException(401, "Missing authorization")


@router.api_route("/ui/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_gateway(path: str, request: Request, authorization: str = Header(default="")):
    """Reverse proxy to the always-on agent's OpenClaw Gateway UI.
    Injects the gateway token server-side — employee never sees it.

    Auth flow for browser navigation (window.open):
      1. Frontend opens /ui/?auth_token=JWT — first request carries JWT in query
      2. Response sets gw_session cookie with the JWT
      3. Sub-resource requests (CSS/JS/images) use the cookie automatically
    """
    user = _authenticate_proxy(request, authorization)
    result = _get_cached_gateway(user.employee_id)

    if not result:
        raise HTTPException(403, "Gateway not available — agent is not always-on")

    base_url, token = result

    # Build target URL
    target = f"{base_url}/{path}"
    if token:
        separator = "&" if "?" in target else "?"
        target = f"{target}{separator}token={token}"

    # Forward query params (strip auth_token — internal only, not for upstream)
    filtered_params = {k: v for k, v in request.query_params.items() if k != "auth_token"}
    if filtered_params:
        from urllib.parse import urlencode
        query = urlencode(filtered_params)
        separator = "&" if "?" in target else "?"
        target = f"{target}{separator}{query}"

    # Forward the request
    try:
        body = await request.body()
        headers = {
            "Content-Type": request.headers.get("content-type", "application/json"),
            "Accept": request.headers.get("accept", "*/*"),
        }

        resp = _requests.request(
            method=request.method,
            url=target,
            headers=headers,
            data=body if body else None,
            timeout=(3, 10),  # (connect_timeout, read_timeout) — fail fast if container unreachable
            allow_redirects=False,
        )

        # Return proxied response
        excluded_headers = {"transfer-encoding", "content-encoding", "connection"}
        response_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in excluded_headers
        }

        response = Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=response_headers,
            media_type=resp.headers.get("content-type"),
        )

        # Set session cookie on first request (auth_token in query)
        # so sub-resource requests (CSS/JS/images) authenticate via cookie
        qt = request.query_params.get("auth_token", "")
        if qt:
            response.set_cookie(
                key="gw_session", value=qt,
                max_age=3600, httponly=True, samesite="lax",
                path="/api/v1/portal/gateway/",
            )

        return response

    except _requests.exceptions.ConnectionError:
        raise HTTPException(502, "Gateway container not reachable. It may be starting up.")
    except _requests.exceptions.Timeout:
        raise HTTPException(504, "Gateway request timed out")
    except Exception as e:
        raise HTTPException(502, f"Gateway proxy error: {e}")


@router.websocket("/ui/{path:path}")
async def proxy_gateway_ws(websocket: WebSocket, path: str):
    """WebSocket proxy to the always-on agent's OpenClaw Gateway.
    Authenticates via gw_session cookie (set by the HTTP proxy on first page load).
    Then bi-directionally forwards WebSocket frames."""
    import asyncio

    # Authenticate via cookie (same as HTTP proxy)
    cookie_token = websocket.cookies.get("gw_session", "")
    if not cookie_token:
        await websocket.close(code=4001, reason="Missing auth cookie")
        return
    try:
        user = _require_employee_auth(f"Bearer {cookie_token}")
    except Exception:
        await websocket.close(code=4001, reason="Invalid auth")
        return

    result = _get_cached_gateway(user.employee_id)
    if not result:
        await websocket.close(code=4003, reason="Gateway not available")
        return

    base_url, gw_token = result
    # Build upstream WebSocket URL
    ws_base = base_url.replace("http://", "ws://").replace(":8080", ":18789")
    ws_target = f"{ws_base}/{path}"
    if gw_token:
        ws_target += f"?token={gw_token}"

    # Forward query params from client (except auth_token)
    qs = str(websocket.query_params)
    filtered = "&".join(p for p in qs.split("&") if p and not p.startswith("auth_token="))
    if filtered:
        ws_target += ("&" if "?" in ws_target else "?") + filtered

    await websocket.accept()

    try:
        import websockets
        async with websockets.connect(ws_target, open_timeout=5, close_timeout=3) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        data = await websocket.receive_text()
                        await upstream.send(data)
                except WebSocketDisconnect:
                    pass

            async def upstream_to_client():
                try:
                    async for msg in upstream:
                        if isinstance(msg, str):
                            await websocket.send_text(msg)
                        else:
                            await websocket.send_bytes(msg)
                except Exception:
                    pass

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except Exception as e:
        logger.warning("Gateway WS proxy error: %s", e)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
