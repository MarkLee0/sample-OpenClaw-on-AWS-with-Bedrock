"""Cron Executor Lambda — Triggered by EventBridge Scheduler.

Receives a scheduled event payload, warms up the user's AgentCore session
if cold, sends the cron message, and delivers the response to the user's
channel (Telegram, Slack, or the Portal).

Ported from: sample-host-openclaw-on-amazon-bedrock-agentcore/lambda/cron/index.py
Adapted for the Enterprise multi-tenant architecture.
"""

import hashlib
import json
import logging
import os
import re
import time
import uuid
from urllib import request as urllib_request

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --- Configuration ---
AGENTCORE_RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
AGENTCORE_QUALIFIER = os.environ.get("AGENTCORE_QUALIFIER", "DEFAULT")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "")
TELEGRAM_TOKEN_SECRET_ID = os.environ.get("TELEGRAM_TOKEN_SECRET_ID", "")
SLACK_TOKEN_SECRET_ID = os.environ.get("SLACK_TOKEN_SECRET_ID", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
LAMBDA_TIMEOUT_SECONDS = int(os.environ.get("LAMBDA_TIMEOUT_SECONDS", "600"))
ORG_PK = os.environ.get("ORG_PK", "ORG#acme")  # configurable for multi-org

# --- Clients ---
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
ddb_table = dynamodb.Table(DYNAMODB_TABLE) if DYNAMODB_TABLE else None
agentcore_client = boto3.client(
    "bedrock-agentcore",
    region_name=AWS_REGION,
    config=Config(
        read_timeout=max(LAMBDA_TIMEOUT_SECONDS - 30, 60),
        connect_timeout=10,
        retries={"max_attempts": 0},
    ),
)
secrets_client = boto3.client("secretsmanager", region_name=AWS_REGION)

# --- Token cache (survives across warm invocations, 15-min TTL) ---
_SECRET_CACHE_TTL_SECONDS = 900
_token_cache = {}

# --- Constants ---
WARMUP_POLL_INTERVAL_SECONDS = 15
WARMUP_MAX_WAIT_SECONDS = 300


def _get_secret(secret_id):
    """Fetch a secret value, cached with a 15-minute TTL."""
    cached = _token_cache.get(secret_id)
    if cached:
        value, fetched_at = cached
        if time.time() - fetched_at < _SECRET_CACHE_TTL_SECONDS:
            return value
    if not secret_id:
        return ""
    try:
        resp = secrets_client.get_secret_value(SecretId=secret_id)
        value = resp["SecretString"]
        _token_cache[secret_id] = (value, time.time())
        return value
    except Exception as e:
        logger.warning("Failed to fetch secret %s: %s", secret_id, e)
        return ""


def _get_telegram_token():
    return _get_secret(TELEGRAM_TOKEN_SECRET_ID)


def _get_slack_tokens():
    raw = _get_secret(SLACK_TOKEN_SECRET_ID)
    if not raw:
        return "", ""
    try:
        data = json.loads(raw)
        return data.get("botToken", ""), data.get("signingSecret", "")
    except (json.JSONDecodeError, TypeError):
        return raw, ""


# ---------------------------------------------------------------------------
# AgentCore invocation
# ---------------------------------------------------------------------------

def invoke_agentcore(session_id, action, user_id, actor_id, channel, message=None):
    """Invoke AgentCore Runtime with the given action."""
    payload_dict = {
        "action": action,
        "userId": user_id,
        "actorId": actor_id,
        "channel": channel,
    }
    if message:
        payload_dict["message"] = message
        # For regular invocations, use 'prompt' key that server.py expects
        if action == "cron":
            payload_dict["prompt"] = message

    payload = json.dumps(payload_dict).encode()

    try:
        logger.info("Invoking AgentCore: action=%s session=%s user=%s", action, session_id, user_id)
        resp = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
            qualifier=AGENTCORE_QUALIFIER,
            runtimeSessionId=session_id,
            runtimeUserId=actor_id,
            payload=payload,
            contentType="application/json",
            accept="application/json",
        )
        MAX_RESPONSE_BYTES = 500_000
        body = resp.get("response")
        if body:
            if hasattr(body, "read"):
                body_bytes = body.read(MAX_RESPONSE_BYTES + 1)
                body_text = body_bytes.decode("utf-8", errors="replace")
                if len(body_bytes) > MAX_RESPONSE_BYTES:
                    body_text = body_text[:MAX_RESPONSE_BYTES]
            else:
                body_text = str(body)[:MAX_RESPONSE_BYTES]
            logger.info("AgentCore response (first 500): %s", body_text[:500])
            try:
                return json.loads(body_text)
            except json.JSONDecodeError:
                return {"response": body_text}
        return {"response": "No response from agent."}
    except Exception as e:
        logger.error("AgentCore invocation failed: %s", e, exc_info=True)
        return {"response": f"Agent invocation failed: {e}"}


def warmup_and_wait(session_id, user_id, actor_id, channel):
    """Send warmup action and poll until the container is ready."""
    start = time.time()
    while time.time() - start < WARMUP_MAX_WAIT_SECONDS:
        result = invoke_agentcore(session_id, "warmup", user_id, actor_id, channel)
        status = result.get("status", "")
        logger.info("Warmup status: %s (elapsed: %.0fs)", status, time.time() - start)
        if status == "ready":
            return True
        if status != "initializing":
            logger.warning("Unexpected warmup status: %s — proceeding", status)
            return True
        time.sleep(WARMUP_POLL_INTERVAL_SECONDS)
    logger.error("Warmup timed out after %ds", WARMUP_MAX_WAIT_SECONDS)
    return False


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def get_or_create_session(user_id):
    """Get or create a session ID for the user (>= 33 chars)."""
    pk = f"USER#{user_id}"
    try:
        resp = ddb_table.get_item(Key={"PK": pk, "SK": "SESSION"})
        if "Item" in resp:
            ddb_table.update_item(
                Key={"PK": pk, "SK": "SESSION"},
                UpdateExpression="SET lastActivity = :now",
                ExpressionAttributeValues={":now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
            )
            return resp["Item"]["sessionId"]
    except ClientError as e:
        logger.error("DynamoDB session lookup failed: %s", e)

    session_id = f"ses_{user_id}_{uuid.uuid4().hex[:12]}"
    if len(session_id) < 33:
        session_id += "_" + uuid.uuid4().hex[:33 - len(session_id)]
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        ddb_table.put_item(Item={
            "PK": pk, "SK": "SESSION",
            "sessionId": session_id, "createdAt": now_iso, "lastActivity": now_iso,
        })
    except ClientError as e:
        logger.error("Failed to create session: %s", e)
    logger.info("New session created: %s for %s", session_id, user_id)
    return session_id


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def _markdown_to_telegram_html(text):
    """Convert Markdown to Telegram-compatible HTML."""
    if not text:
        return text
    placeholders = []

    def _ph(content):
        idx = len(placeholders)
        placeholders.append(content)
        return f"\x00PH{idx}\x00"

    text = re.sub(r"```\w*\n?(.*?)```",
                  lambda m: _ph("<pre>{}</pre>".format(m.group(1).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))),
                  text, flags=re.DOTALL)
    text = re.sub(r"`([^`\n]+)`",
                  lambda m: _ph("<code>{}</code>".format(m.group(1).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))),
                  text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    def _safe_link(m):
        if re.match(r'^(https?://|mailto:)', m.group(2)):
            return f'<a href="{m.group(2)}">{m.group(1)}</a>'
        return m.group(0)

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _safe_link, text)
    for idx, content in enumerate(placeholders):
        text = text.replace(f"\x00PH{idx}\x00", content)
    return text


# ---------------------------------------------------------------------------
# Channel delivery
# ---------------------------------------------------------------------------

def send_telegram_message(chat_id, text, token):
    if not token:
        logger.error("No Telegram token available")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    html_text = _markdown_to_telegram_html(text)
    data = json.dumps({"chat_id": chat_id, "text": html_text, "parse_mode": "HTML"}).encode()
    req = urllib_request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib_request.urlopen(req, timeout=10)
        return
    except Exception as e:
        logger.warning("Telegram HTML failed (retrying plain): %s", e)
    data = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib_request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib_request.urlopen(req, timeout=10)
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)


def send_slack_message(channel_id, text, bot_token):
    if not bot_token:
        logger.error("No Slack bot token available")
        return
    url = "https://slack.com/api/chat.postMessage"
    data = json.dumps({"channel": channel_id, "text": text}).encode()
    req = urllib_request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {bot_token}",
    })
    try:
        urllib_request.urlopen(req, timeout=10)
    except Exception as e:
        logger.error("Failed to send Slack message: %s", e)


def send_portal_notification(emp_id, text):
    """Write cron result to DynamoDB so the Portal frontend can display it.

    Writes two records:
    1. CONV# turn — appears in the chat history (Session Detail view)
    2. NOTIFICATION# — a lightweight notification record the frontend polls for
    """
    import time as _t
    now_iso = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    ts_ms = int(_t.time() * 1000)
    try:
        # Find the active portal session for this employee
        session_id = f"cron__{emp_id}"

        # Write a conversation turn so it appears in Portal chat history
        ddb_table.put_item(Item={
            "PK": ORG_PK,
            "SK": f"CONV#{emp_id}#cron-{ts_ms}",
            "GSI1PK": "TYPE#conv",
            "GSI1SK": f"CONV#{emp_id}#cron-{ts_ms}",
            "sessionId": session_id,
            "seq": ts_ms,
            "role": "assistant",
            "content": f"[Scheduled Reminder] {text[:4000]}",
            "ts": now_iso,
            "model": "cron-executor",
            "source": "cron",
        })

        # Write a notification record — the Portal frontend polls NOTIFICATION# items
        notif_id = f"notif-{ts_ms}"
        ddb_table.put_item(Item={
            "PK": f"USER#{emp_id}",
            "SK": f"NOTIFICATION#{notif_id}",
            "id": notif_id,
            "type": "cron_reminder",
            "title": "Scheduled Reminder",
            "message": text[:2000],
            "read": False,
            "createdAt": now_iso,
            "employeeId": emp_id,
            "ttl": int(_t.time()) + 7 * 86400,  # auto-delete after 7 days
        })
        logger.info("Portal notification written for %s: %s", emp_id, text[:100])
    except Exception as e:
        logger.error("Failed to write portal notification for %s: %s", emp_id, e)


def deliver_response(channel, channel_target, response_text):
    """Deliver a response to the user's channel."""
    if channel == "telegram":
        token = _get_telegram_token()
        if len(response_text) <= 4096:
            send_telegram_message(channel_target, response_text, token)
        else:
            for i in range(0, len(response_text), 4096):
                send_telegram_message(channel_target, response_text[i:i + 4096], token)
    elif channel == "slack":
        bot_token, _ = _get_slack_tokens()
        send_slack_message(channel_target, response_text, bot_token)
    elif channel == "portal":
        # Portal delivery: write to DynamoDB so the admin console can display it
        send_portal_notification(channel_target, response_text)
    else:
        logger.warning("Unknown channel type: %s — response logged only", channel)


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def handler(event, context):
    """Handle EventBridge Scheduler trigger.

    Expected payload:
    {
        "userId": "emp-carol",
        "actorId": "portal:emp-carol",
        "channel": "portal",
        "channelTarget": "emp-carol",
        "message": "Check my email",
        "scheduleId": "a1b2c3d4",
        "scheduleName": "Daily email check"
    }
    """
    logger.info("Cron event received: %s", json.dumps(event)[:1000])

    user_id = event.get("userId")
    actor_id = event.get("actorId")
    channel = event.get("channel")
    channel_target = event.get("channelTarget")
    message = event.get("message")
    schedule_id = event.get("scheduleId", "unknown")
    schedule_name = event.get("scheduleName", "")

    if not all([user_id, actor_id, channel, channel_target, message]):
        logger.error("Missing required fields: userId=%s actorId=%s channel=%s target=%s msg=%s",
                     user_id, actor_id, channel, channel_target, bool(message))
        return {"statusCode": 400, "body": "Missing required fields"}

    logger.info("Processing cron: schedule=%s user=%s channel=%s:%s",
                schedule_id, user_id, channel, channel_target)

    # Verify schedule ownership
    try:
        cron_record = ddb_table.get_item(
            Key={"PK": f"USER#{user_id}", "SK": f"CRON#{schedule_id}"}
        ).get("Item")
        if not cron_record:
            logger.error("Schedule %s not owned by user %s — skipping", schedule_id, user_id)
            return {"statusCode": 403, "body": "Schedule ownership verification failed"}
    except Exception as e:
        logger.error("Failed to verify schedule ownership: %s", e)
        return {"statusCode": 500, "body": "Schedule ownership verification error"}

    # Build session ID from tenant_id pattern: cron__<userId>__<hash>
    # This lets server.py's workspace assembler resolve the employee.
    tenant_hash = hashlib.md5(user_id.encode()).hexdigest()[:8]
    session_id = f"cron__{user_id}__{tenant_hash}"
    if len(session_id) < 33:
        session_id += "_" + uuid.uuid4().hex[:33 - len(session_id)]

    # Phase 1: Warm up the container
    warmup_ok = warmup_and_wait(session_id, user_id, actor_id, channel)
    if not warmup_ok:
        error_msg = (
            f"[Scheduled: {schedule_name or schedule_id}] "
            "Your scheduled task could not run because the agent failed to start. "
            "It will try again at the next scheduled time."
        )
        deliver_response(channel, channel_target, error_msg)
        return {"statusCode": 503, "body": "Warmup timeout"}

    # Phase 2: Execute the cron message
    cron_message = f"[Scheduled task: {schedule_name or schedule_id}] {message}"
    result = invoke_agentcore(session_id, "cron", user_id, actor_id, channel, cron_message)
    response_text = result.get("response", "No response from scheduled task.")

    # Phase 3: Deliver response to channel
    logger.info("Delivering response (len=%d) to %s:%s", len(response_text), channel, channel_target)
    deliver_response(channel, channel_target, response_text)

    logger.info("Cron execution complete: schedule=%s", schedule_id)
    return {"statusCode": 200, "body": "OK"}
